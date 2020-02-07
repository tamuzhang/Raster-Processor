#!/usr/bin/env python
'''
Raster processor

Author (s): Shuai Zhang (UNC) and Alexander Corben (JPL)
'''

import utm
import logging
import numpy as np
import geoloc_raster
import raster_products
import SWOTWater.aggregate as ag

from datetime import datetime
from SWOTWater.constants import PIXC_CLASSES

LOGGER = logging.getLogger(__name__)

# Internal class values used in processing
INTERIOR_WATER_KLASS = 1
WATER_EDGE_KLASS = 2
LAND_EDGE_KLASS = 3

# Center easting coordinate for utm zones
UTM_EASTING_CENTER = 500000

class Worker(object):
    '''Turns PixelClouds into Rasters'''
    def __init__( self,
                  config=None,
                  pixc=None,
                  debug_flag=False ):
        '''Initialize'''
        LOGGER.info('Initializing')
        self.config = config
        self.pixc = pixc
        self.debug_flag = debug_flag
        self.proj_info = None

    def process(self):
        self.parse_config_defaults()

        # Do improved geolocation if specified in config
        if self.config['do_improved_geolocation']:
            LOGGER.info('Rasterizing for improved geolocation')
            improved_geoloc_raster = self.rasterize(
                self.config['improved_geolocation_res'],
                self.config['improved_geolocation_buffer_size'])

            new_lat, new_lon, new_height = geoloc_raster.geoloc_raster(
                self.pixc, improved_geoloc_raster, self.config)

            self.pixc['pixel_cloud']['height'] = new_height
            self.pixc['pixel_cloud']['latitude'] = new_lat
            self.pixc['pixel_cloud']['longitude'] = new_lon

        # Rasterize at final resolution
        output_raster = self.rasterize(
            self.config['resolution'],
            self.config['buffer_size'])

        return output_raster

    def rasterize(self, resolution, buffer_size):
        '''Rasterize'''
        LOGGER.info('Rasterizing at resolution: {}'.format(resolution))

        # Get pixelcloud variables
        pixc_group = self.pixc['pixel_cloud']
        lats = pixc_group['latitude'][:]
        lons = pixc_group['longitude'][:]
        heights = pixc_group['height'][:]
        klass = pixc_group['classification'][:]
        pixel_area = pixc_group['pixel_area'][:]
        num_rare_looks = pixc_group['eff_num_rare_looks'][:]
        num_med_looks = pixc_group['eff_num_medium_looks'][:]
        ifgram = pixc_group['interferogram'][:]
        power1 = pixc_group['power_minus_y'][:]
        power2 = pixc_group['power_plus_y'][:]
        dlat_dphi = pixc_group['dlatitude_dphase'][:]
        dlon_dphi = pixc_group['dlongitude_dphase'][:]
        dh_dphi = pixc_group['dheight_dphase'][:]
        phase_noise_std = pixc_group['phase_noise_std'][:]
        water_fraction = pixc_group['water_frac'][:]
        water_fraction_uncert = pixc_group['water_frac_uncert'][:]
        darea_dheight = pixc_group['darea_dheight'][:]
        Pfd = pixc_group['false_detection_rate'][:]
        Pmd = pixc_group['missed_detection_rate'][:]
        cross_trk = pixc_group['cross_track'][:]
        sig0 = pixc_group['sig0'][:]

        height_std_pix = np.abs(phase_noise_std * dh_dphi)
        # set bad pix height std to high number to deweight
        # instead of giving infs/nans
        bad_num = 1.0e5
        height_std_pix[height_std_pix<=0] = bad_num
        height_std_pix[np.isinf(height_std_pix)] = bad_num
        height_std_pix[np.isnan(height_std_pix)] = bad_num

        looks_to_efflooks = self.pixc['pixel_cloud'].looks_to_efflooks

        mask = get_pixc_mask(self.pixc)

        # Set temp classes using those defined in the config
        klass_tmp = get_raster_classes(klass, self.config)

        # If the pixelcloud is empty, return an empty raster
        if pixc_group['latitude'].size == 0:
            return raster_products.Raster()

        LOGGER.info('Calculating Projection Parameters')

        corners = ((self.pixc.inner_first_latitude, lon_360to180(self.pixc.inner_first_longitude)),
                   (self.pixc.inner_last_latitude, lon_360to180(self.pixc.inner_last_longitude)),
                   (self.pixc.outer_first_latitude, lon_360to180(self.pixc.outer_first_longitude)),
                   (self.pixc.outer_last_latitude, lon_360to180(self.pixc.outer_last_longitude)))

        self.proj_info = create_projection_from_bbox(corners,
                                                     self.config['projection_type'],
                                                     resolution,
                                                     buffer_size)

        LOGGER.info(self.proj_info)

        proj_mapping = get_raster_mapping(lats, lon_360to180(lons), klass_tmp,
                                          mask, self.proj_info)

        # Create a product with additional fields if in debug mode
        if self.debug_flag:
            raster_data = raster_products.RasterDebug()
        else:
            raster_data = raster_products.Raster()

        ones_result = np.array([[1 for i in range(self.proj_info['size_x'])]
                                for i in range(self.proj_info['size_y'])])

        out_h = raster_data['height'].fill_value*ones_result
        out_h_uc = raster_data['height_uncert'].fill_value*ones_result
        out_area = raster_data['water_area'].fill_value*ones_result
        out_area_uc = raster_data['water_area_uncert'].fill_value*ones_result
        out_area_frac = raster_data['water_frac'].fill_value*ones_result
        out_area_frac_uc = raster_data['water_frac_uncert'].fill_value*ones_result
        out_cross_trk = raster_data['cross_track'].fill_value*ones_result
        out_sig0 = raster_data['sig0'].fill_value*ones_result
        out_sig0_std = raster_data['sig0_uncert'].fill_value*ones_result
        out_num_pixels = raster_data['num_pixels'].fill_value*ones_result
        out_dark_frac = raster_data['dark_frac'].fill_value*ones_result

        if self.debug_flag:
            out_classification = raster_data['classification'].fill_value*ones_result

        LOGGER.info('Rasterizing data')
        for i in range(0, self.proj_info['size_y']):
            for j in range(0, self.proj_info['size_x']):
                if len(proj_mapping[i][j]) != 0:
                    good = mask[proj_mapping[i][j]]
                    # exclude land edges from height aggregation
                    good_heights = np.logical_and(good,
                        klass_tmp[proj_mapping[i][j]]!=LAND_EDGE_KLASS)
                    grid_height = ag.height_with_uncerts(
                        heights[proj_mapping[i][j]],
                        good_heights,
                        num_rare_looks[proj_mapping[i][j]],
                        num_med_looks[proj_mapping[i][j]],
                        ifgram[proj_mapping[i][j]],
                        power1[proj_mapping[i][j]],
                        power2[proj_mapping[i][j]],
                        looks_to_efflooks,
                        dh_dphi[proj_mapping[i][j]],
                        dlat_dphi[proj_mapping[i][j]],
                        dlon_dphi[proj_mapping[i][j]],
                        height_std_pix[proj_mapping[i][j]],
                        method=self.config['height_agg_method'])

                    out_h[i][j] = grid_height[0]
                    out_h_uc[i][j] = grid_height[2]

                    grid_area = ag.area_with_uncert(
                        pixel_area[proj_mapping[i][j]],
                        water_fraction[proj_mapping[i][j]],
                        water_fraction_uncert[proj_mapping[i][j]],
                        darea_dheight[proj_mapping[i][j]],
                        klass_tmp[proj_mapping[i][j]],
                        Pfd[proj_mapping[i][j]],
                        Pmd[proj_mapping[i][j]],
                        good,
                        method=self.config['area_agg_method'],
                        interior_water_klass=INTERIOR_WATER_KLASS,
                        water_edge_klass=WATER_EDGE_KLASS,
                        land_edge_klass=LAND_EDGE_KLASS)

                    out_area[i][j] = grid_area[0]
                    out_area_uc[i][j] = grid_area[1]
                    out_area_frac[i][j] = grid_area[0]/(self.proj_info['proj_res']**2)
                    out_area_frac_uc[i][j] = grid_area[1]/(self.proj_info['proj_res']**2)

                    out_cross_trk[i][j] = ag.simple(
                        cross_trk[proj_mapping[i][j]][good])

                    out_sig0[i][j] = ag.simple(
                        sig0[proj_mapping[i][j]][good], metric='mean')
                    out_sig0_std[i][j] = ag.height_uncert_std(
                        sig0[proj_mapping[i][j]],
                        good,
                        num_rare_looks[proj_mapping[i][j]],
                        num_med_looks[proj_mapping[i][j]])

                    out_num_pixels[i][j] = ag.simple(good, metric='sum')
                    out_dark_frac[i][j] = self.calc_dark_frac(
                        pixel_area[proj_mapping[i][j]][good],
                        klass[proj_mapping[i][j]][good],
                        water_fraction[proj_mapping[i][j]][good])

                    if self.debug_flag:
                        out_classification[i][j] = ag.simple(
                            klass[proj_mapping[i][j]][good], metric='mode')

        # TODO: rethink handling of this, but for now uncert can be inf or nan
        # if water area is 0. Set to fill value.
        out_area_frac_uc[np.logical_or(np.isnan(out_area_frac_uc),
            np.isinf(out_area_frac_uc))] = raster_data['water_frac_uncert'].fill_value
        out_h_uc[np.isnan(out_h_uc)] = raster_data['height_uncert'].fill_value

        # Assemble the product
        LOGGER.info('Assembling Raster Product')
        current_datetime = datetime.utcnow()
        raster_data.history = "{:04d}-{:02d}-{:02d}".format(current_datetime.year,
                                                            current_datetime.month,
                                                            current_datetime.day)
        raster_data.cycle_number = self.pixc.cycle_number
        raster_data.pass_number = self.pixc.pass_number
        raster_data.tile_numbers = self.pixc.tile_number
        raster_data.proj_type = self.proj_info['proj_type']
        raster_data.proj_res = self.proj_info['proj_res']
        raster_data.utm_num = self.proj_info['utm_num']
        raster_data.x_min = self.proj_info['x_min']
        raster_data.x_max = self.proj_info['x_max']
        raster_data.y_min = self.proj_info['y_min']
        raster_data.y_max = self.proj_info['y_max']
        raster_data['x'] = np.linspace(self.proj_info['x_min'],
                                       self.proj_info['x_max'],
                                       self.proj_info['size_x'])
        raster_data['y'] = np.linspace(self.proj_info['y_min'],
                                       self.proj_info['y_max'],
                                       self.proj_info['size_y'])
        raster_data['height'] = out_h
        raster_data['height_uncert'] = out_h_uc
        raster_data['water_area'] = out_area
        raster_data['water_area_uncert'] = out_area_uc
        raster_data['water_frac'] = out_area_frac
        raster_data['water_frac_uncert'] = out_area_frac_uc
        raster_data['cross_track'] = out_cross_trk
        raster_data['sig0'] = out_sig0
        raster_data['sig0_uncert'] = out_sig0_std
        raster_data['num_pixels'] = out_num_pixels
        raster_data['dark_frac'] = out_dark_frac

        if self.debug_flag:
            raster_data['classification'] = out_classification

        return raster_data

    def parse_config_defaults(self):
        config_defaults = {'projection_type':'utm',
                           'resolution':100,
                           'buffer_size':100,
                           'height_agg_method':'weight',
                           'area_agg_method':'composite',
                           'interior_water_classes':[PIXC_CLASSES['open_water'],
                                                     PIXC_CLASSES['dark_water']],
                           'water_edge_classes':[PIXC_CLASSES['water_near_land'],
                                                 PIXC_CLASSES['dark_water_edge']],
                           'land_edge_classes':[PIXC_CLASSES['land_near_water'],
                                                PIXC_CLASSES['land_near_dark_water']]}

        for key in config_defaults:
            try:
                tmp = self.config[key]
            except KeyError:
                self.config[key] = config_defaults[key]

    def calc_dark_frac(self, pixel_area, klass, water_frac):
        water_frac[water_frac<0] = 0
        klass_dark = np.isin(klass, self.config['dark_water_classes'])
        dark_area = np.sum(pixel_area[klass_dark]*water_frac[klass_dark])
        total_area = np.sum(pixel_area*water_frac)

        # If we don't have any water at all, we have no dark water...
        if np.sum(total_area)==0:
            return 0

        return dark_area/total_area


def lon_360to180(longitude):
    return np.mod(longitude + 180, 360) - 180


def get_raster_classes(klass, config):
    klass_tmp = np.zeros_like(klass)
    klass_tmp[np.isin(klass, config['interior_water_classes'])] = \
        INTERIOR_WATER_KLASS
    klass_tmp[np.isin(klass, config['water_edge_classes'])] = \
        WATER_EDGE_KLASS
    klass_tmp[np.isin(klass, config['land_edge_classes'])] = \
        LAND_EDGE_KLASS
    return klass_tmp


def get_pixc_mask(pixc):
    lats = pixc['pixel_cloud']['latitude'][:]
    lons = pixc['pixel_cloud']['longitude'][:]
    area = pixc['pixel_cloud']['pixel_area'][:]
    klass = pixc['pixel_cloud']['classification'][:]
    mask = np.ones(np.shape(lats))

    if np.ma.is_masked(lats):
        mask[lats.mask] = 0
    if np.ma.is_masked(lons):
        mask[lons.mask] = 0
    if np.ma.is_masked(area):
        mask[area.mask] = 0

    mask[np.isnan(lats)] = 0
    mask[np.isnan(lons)] = 0
    mask[np.isnan(klass)] = 0

    # bounds for valid utc
    mask[lats >= 84.0] = 0
    mask[lats <= -80.0] = 0

    return mask==1


def create_projection_from_bbox(
        corners, proj_type='utm', proj_res=100.0, buff=250.0):
    """ Needed to get same sampling for gdem truth and pixc,
    also simplifies the projection computation
    Modified from Shuai Zhang's raster.py """

    # catch invalid projection type
    if proj_type != 'utm' and proj_type != 'geo':
        raise Exception('Unknown projection type: {}'.format(proj_type))

    # get corners separately
    in_1st = corners[0]
    in_last = corners[1]
    out_1st = corners[2]
    out_last = corners[3]

    x_min = np.min(np.array([in_1st[1],in_last[1],out_1st[1],out_last[1]]))
    y_min = np.min(np.array([in_1st[0],in_last[0],out_1st[0],out_last[0]]))
    x_max = np.max(np.array([in_1st[1],in_last[1],out_1st[1],out_last[1]]))
    y_max = np.max(np.array([in_1st[0],in_last[0],out_1st[0],out_last[0]]))
    proj_center_x = 0

    # find the UTM zone number for the middle of the swath-tile
    if proj_type=='utm':
        lat_mid = (in_1st[0] + in_last[0] + out_1st[0] + out_last[0])/4.0
        lon_mid = (in_1st[1] + in_last[1] + out_1st[1] + out_last[1])/4.0
        x_mid, y_mid, utm_num, zone_mid = utm.from_latlon(lat_mid, lon_mid)

        x_min, y_min, u_num, u_zone = utm.from_latlon(y_min, x_min,
            force_zone_number=utm_num)
        x_max, y_max, u_num1, u_zone1 = utm.from_latlon(y_max, x_max,
            force_zone_number=utm_num)
        proj_center_x = UTM_EASTING_CENTER

    # round limits to the nearest bin (centered at proj_center_x) and add buffer
    x_min = round((x_min - proj_center_x) / proj_res) * proj_res \
            + proj_center_x - buff
    x_max = round((x_max - proj_center_x) / proj_res) * proj_res \
            + proj_center_x + buff
    y_min = round(y_min  / proj_res) * proj_res - buff
    y_max = round(y_max / proj_res) * proj_res + buff

    proj_info = {}
    proj_info['proj_type'] = proj_type
    proj_info['proj_res'] = proj_res
    proj_info['x_min'] = x_min
    proj_info['x_max'] = x_max
    proj_info['y_min'] = y_min
    proj_info['y_max'] = y_max
    proj_info['size_x'] = round((x_max - x_min) / proj_res).astype(int) + 1
    proj_info['size_y'] = round((y_max - y_min) / proj_res).astype(int) + 1

    if proj_type=='utm':
        proj_info['utm_num'] = utm_num
    else:
        proj_info['utm_num'] = None

    return proj_info


def get_raster_mapping(lats, lons, klass, mask, proj_info):
    # maps all pixels to the corresponding raster bins
    x_tmp=[]
    y_tmp=[]
    mapping_tmp = []

    if proj_info['proj_type']=='geo':
        x_tmp = lons
        y_tmp = lats
    elif proj_info['proj_type']=='utm':
        for x in range(0,len(lats)):
            if mask[x]:
                u_x, u_y, u_num, u_zone = utm.from_latlon(
                    lats[x], lons[x], force_zone_number=proj_info['utm_num'])
                x_tmp.append(u_x)
                y_tmp.append(u_y)
            else:
                x_tmp.append(0)
                y_tmp.append(0)

    for i in range(0, proj_info['size_y']):
        mapping_tmp.append([])
        for j in range(0, proj_info['size_x']):
            mapping_tmp[i].append([])

    for x in range(0,len(lats)):
        i = round((y_tmp[x] - proj_info['y_min']) / proj_info['proj_res']).astype(int)
        j = round((x_tmp[x] - proj_info['x_min']) / proj_info['proj_res']).astype(int)
        # check bounds
        if (i >= 0 and i < proj_info['size_y'] and
            j >= 0 and j < proj_info['size_x']):
            # exclude classes not defined in config
            if klass[x] != 0:
                mapping_tmp[i][j].append(x)

    return mapping_tmp
