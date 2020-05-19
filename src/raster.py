#!/usr/bin/env python
'''
Copyright (c) 2017-, California Institute of Technology ("Caltech"). U.S.
Government sponsorship acknowledged.
All rights reserved.

Author (s): Shuai Zhang (UNC) and Alexander Corben (JPL)
'''

import utm
import logging
import raster_crs
import numpy as np
import geoloc_raster
import raster_products
import SWOTWater.aggregate as ag
import cnes.modules.geoloc.lib.geoloc as geoloc

from osgeo import osr
from datetime import datetime
from SWOTWater.constants import PIXC_CLASSES
from cnes.common.lib.my_variables import GEN_RAD_EARTH_EQ, GEN_RAD_EARTH_POLE

LOGGER = logging.getLogger(__name__)

# Internal class values used in area aggregation
INTERIOR_WATER_KLASS = 1
WATER_EDGE_KLASS = 2
LAND_EDGE_KLASS = 3

class L2PixcToRaster(object):
    '''Turns PixelClouds into Rasters'''
    def __init__( self,
                  pixc=None,
                  improved_geoloc_pixc=None,
                  algorithmic_config=None,
                  runtime_config=None):
        self.algorithmic_config = algorithmic_config
        self.runtime_config = runtime_config
        self.pixc = pixc
        self.improved_geoloc_pixc = improved_geoloc_pixc

    def process(self):
        # Parse the algorithmic config file and set defaults
        self.parse_config_defaults()

        # Do improved geolocation if specified in config
        if self.algorithmic_config['do_improved_geolocation']:
            self.do_improved_geolocation()

        product = self.do_raster_processing()

        return product

    def do_improved_geolocation(self):
        LOGGER.info('Rasterizing for improved geolocation')
        improved_geoloc_raster_proc = RasterProcessor(
            self.runtime_config['output_sampling_grid_type'],
            self.runtime_config['raster_resolution'] \
            * self.algorithmic_config['improved_geolocation_smooth_factor'],
            self.runtime_config['utm_zone_adjust'],
            self.runtime_config['latitude_band_adjust'],
            self.algorithmic_config['buffer_size'],
            self.algorithmic_config['height_agg_method'],
            self.algorithmic_config['area_agg_method'],
            self.algorithmic_config['interior_water_classes'],
            self.algorithmic_config['water_edge_classes'],
            self.algorithmic_config['land_edge_classes'],
            self.algorithmic_config['dark_water_classes'],
            self.algorithmic_config['debug_flag'])

        improved_geoloc_raster = improved_geoloc_raster_proc.rasterize(
            self.pixc, self.improved_geoloc_pixc)

        # if the improved geoloc raster is empty, do not call geoloc_raster
        if improved_geoloc_raster.is_empty():
            return

        new_lat, new_lon, new_height = geoloc_raster.geoloc_raster(
            self.pixc, improved_geoloc_raster, self.algorithmic_config)
        self.improved_geoloc_pixc = self.pixc.copy()
        self.improved_geoloc_pixc['pixel_cloud']['latitude'] = new_lat
        self.improved_geoloc_pixc['pixel_cloud']['longitude'] = new_lon
        self.improved_geoloc_pixc['pixel_cloud']['height'] = new_height

    def do_raster_processing(self):
        LOGGER.info('Rasterizing')
        raster_proc = RasterProcessor(
            self.runtime_config['output_sampling_grid_type'],
            self.runtime_config['raster_resolution'],
            self.runtime_config['utm_zone_adjust'],
            self.runtime_config['latitude_band_adjust'],
            self.algorithmic_config['buffer_size'],
            self.algorithmic_config['height_agg_method'],
            self.algorithmic_config['area_agg_method'],
            self.algorithmic_config['interior_water_classes'],
            self.algorithmic_config['water_edge_classes'],
            self.algorithmic_config['land_edge_classes'],
            self.algorithmic_config['dark_water_classes'],
            self.algorithmic_config['debug_flag'])

        out_raster = raster_proc.rasterize(self.pixc, self.improved_geoloc_pixc)
        return out_raster

    def parse_config_defaults(self):
        config_defaults = {'buffer_size':0,
                           'height_agg_method':'weight',
                           'area_agg_method':'composite',
                           'interior_water_classes':[PIXC_CLASSES['open_water'],
                                                     PIXC_CLASSES['dark_water']],
                           'water_edge_classes':[PIXC_CLASSES['water_near_land'],
                                                 PIXC_CLASSES['dark_water_edge']],
                           'land_edge_classes':[PIXC_CLASSES['land_near_water'],
                                                PIXC_CLASSES['land_near_dark_water']],
                           'dark_water_classes':[PIXC_CLASSES['dark_water'],
                                                 PIXC_CLASSES['dark_water_edge'],
                                                 PIXC_CLASSES['land_near_dark_water']],
                           'do_improved_geolocation':True,
                           'improved_geolocation_method':'taylor',
                           'improved_geolocation_smooth_factor':5,
                           'debug_flag':False}

        for key in config_defaults:
            try:
                tmp = self.algorithmic_config[key]
            except KeyError:
                self.algorithmic_config[key] = config_defaults[key]


class RasterProcessor(object):
    def __init__(self, projection_type, resolution, utm_zone_adjust,
                 latitude_band_adjust, buffer_size,
                 height_agg_method, area_agg_method, interior_water_classes,
                 water_edge_classes, land_edge_classes, dark_water_classes,
                 debug_flag=False):
        '''Initialize'''
        self.projection_type = projection_type

        if projection_type=='geo':
            # Geodetic resolution is given in arcsec
            self.resolution = resolution/(60*60)
        elif projection_type=='utm':
            self.resolution = resolution
            self.utm_zone_adjust = utm_zone_adjust
            self.latitude_band_adjust = latitude_band_adjust

        self.buffer_size = buffer_size
        self.height_agg_method = height_agg_method
        self.area_agg_method = area_agg_method
        self.interior_water_classes = interior_water_classes
        self.water_edge_classes = water_edge_classes
        self.land_edge_classes = land_edge_classes
        self.dark_water_classes = dark_water_classes
        self.debug_flag = debug_flag

    def rasterize(self, pixc, improved_geoloc_pixc=None):
        '''Rasterize'''
        self.cycle_number = pixc.cycle_number
        self.pass_number = pixc.pass_number
        self.tile_numbers = pixc.tile_numbers
        self.tile_names = pixc.tile_names
        self.tile_polarizations = pixc.tile_polarizations
        self.time_coverage_start = pixc.time_coverage_start
        self.time_coverage_end = pixc.time_coverage_end
        self.geospatial_lon_min = pixc.geospatial_lon_min
        self.geospatial_lon_max = pixc.geospatial_lon_max
        self.geospatial_lat_min = pixc.geospatial_lat_min
        self.geospatial_lat_max = pixc.geospatial_lat_max
        self.left_first_longitude = pixc.left_first_longitude
        self.left_first_latitude = pixc.left_first_latitude
        self.left_last_longitude = pixc.left_last_longitude
        self.left_last_latitude = pixc.left_last_latitude
        self.right_first_longitude = pixc.right_first_longitude
        self.right_first_latitude = pixc.right_first_latitude
        self.right_last_longitude = pixc.right_last_longitude
        self.right_last_latitude = pixc.right_last_latitude

        LOGGER.info('Calculating projection parameters')
        corners = ((self.geospatial_lat_min, lon_360to180(self.geospatial_lon_min)),
                   (self.geospatial_lat_min, lon_360to180(self.geospatial_lon_max)),
                   (self.geospatial_lat_max, lon_360to180(self.geospatial_lon_max)),
                   (self.geospatial_lat_max, lon_360to180(self.geospatial_lon_min)))
        self.create_projection_from_bbox(corners)

        # Get mask of valid pixc values
        pixc_mask = get_pixc_mask(pixc)
        # Exclude classes not defined in the processor
        pixc_mask = np.logical_and(
            pixc_mask,
            np.isin(pixc['pixel_cloud']['classification'],
                    np.concatenate((self.interior_water_classes,
                                    self.water_edge_classes,
                                    self.land_edge_classes,
                                    self.dark_water_classes))))

        # Create an empty Raster
        empty_product = self.build_product(populate_values=False)
        # Return empty product if pixc is empty
        if len(pixc['pixel_cloud']['latitude'])==0:
            LOGGER.warn('Empty Pixel Cloud: returning empty raster')
            return empty_product

        LOGGER.info('Mapping pixc pixels to raster bins')
        # If we have improved geolocations, use them for raster mapping
        if improved_geoloc_pixc is not None:
            improved_geoloc_pixc_mask = get_pixc_mask(improved_geoloc_pixc)
            pixc_mask = np.logical_and(pixc_mask, improved_geoloc_pixc_mask)
            self.proj_mapping = empty_product.get_raster_mapping(
                improved_geoloc_pixc, pixc_mask)
        else:
            self.proj_mapping = empty_product.get_raster_mapping(
                pixc, pixc_mask)

        LOGGER.info('Rasterizing data')
        self.aggregate_wse(pixc, pixc_mask, improved_geoloc_pixc)
        self.aggregate_water_area(pixc, pixc_mask)
        self.aggregate_cross_track(pixc, pixc_mask)
        self.aggregate_sig0(pixc, pixc_mask)
        self.aggregate_inc(pixc, pixc_mask)
        self.aggregate_dark_frac(pixc, pixc_mask)
        self.aggregate_illumination_time(pixc, pixc_mask)
        self.aggregate_corrections(pixc, pixc_mask)
        self.apply_wse_corrections()
        if self.projection_type == 'utm':
            self.aggregate_lat_lon(pixc_mask)

        if self.debug_flag:
            self.aggregate_classification(pixc, pixc_mask)

        return self.build_product()

    def create_projection_from_bbox(self, corners):
        # catch invalid projection type
        if self.projection_type != 'utm' and self.projection_type != 'geo':
            raise Exception('Unknown projection type: {}'.format(
                self.projection_type))

        # get corners separately
        lower_left = corners[0]
        lower_right = corners[1]
        upper_right = corners[2]
        upper_left = corners[3]

        x_min = np.min(np.array([lower_left[1], lower_right[1],
                                 upper_right[1], upper_left[1]]))
        x_max = np.max(np.array([lower_left[1], lower_right[1],
                                 upper_right[1], upper_left[1]]))
        y_min = np.min(np.array([lower_left[0], lower_right[0],
                                 upper_right[0], upper_left[0]]))
        y_max = np.max(np.array([lower_left[0], lower_right[0],
                                 upper_right[0], upper_left[0]]))
        proj_center_x = 0
        proj_center_y = 0
        output_crs = raster_crs.wgs84_crs()
        self.input_crs = output_crs

        # find the UTM zone number for the middle of the swath-tile
        if self.projection_type=='utm':
            lat_mid = (lower_left[0] + lower_right[0] \
                       + upper_right[0] + upper_left[0])/4.0
            lon_mid = (lower_left[1] + lower_right[1] \
                       + upper_right[1] + upper_left[1])/4.0
            utm_zone = utm.latlon_to_zone_number(lat_mid, lon_mid)
            mgrs_band = utm.latitude_to_zone_letter(lat_mid)
            # adjust the utm zone (-1 and +1 as zone numbers are 1 indexed)
            utm_zone = np.mod(utm_zone + self.utm_zone_adjust - 1,
                              raster_crs.UTM_NUM_ZONES) + 1

            # adjust the latitude band
            band_num = raster_crs.UTM_VALID_BANDS.find(mgrs_band) \
                       + self.latitude_band_adjust
            if band_num < 0:
                band_num = 0
            elif band_num >= len(raster_crs.UTM_VALID_BANDS):
                band_num = len(raster_crs.UTM_VALID_BANDS)-1

            mgrs_band = raster_crs.UTM_VALID_BANDS[band_num]
            output_crs = raster_crs.utm_crs(utm_zone, mgrs_band)
            transf = osr.CoordinateTransformation(self.input_crs, output_crs)
            x_min, y_min = transf.TransformPoint(y_min, x_min)[:2]
            x_max, y_max = transf.TransformPoint(y_max, x_max)[:2]

            proj_center_x = output_crs.GetProjParm('false_easting')
            proj_center_y = output_crs.GetProjParm('false_northing')

        # round limits to the nearest bin (centered at proj_center_x and add buffer
        x_min = int(round((x_min - proj_center_x) / self.resolution)) * self.resolution \
                + proj_center_x - self.buffer_size
        x_max = int(round((x_max - proj_center_x) / self.resolution)) * self.resolution \
                + proj_center_x + self.buffer_size
        y_min = int(round((y_min - proj_center_y) / self.resolution)) * self.resolution \
                + proj_center_y - self.buffer_size
        y_max = int(round((y_max - proj_center_y) / self.resolution)) * self.resolution \
                + proj_center_y + self.buffer_size

        self.output_crs = output_crs
        self.x_min = x_min
        self.x_max = x_max
        self.y_min = y_min
        self.y_max = y_max
        self.size_x = int(round((x_max - x_min) / self.resolution)) + 1
        self.size_y = int(round((y_max - y_min) / self.resolution)) + 1
        if self.projection_type=='utm':
            self.utm_zone = utm_zone
            self.utm_hemisphere = raster_crs.utm_hemisphere(mgrs_band)
            self.mgrs_band = mgrs_band

        LOGGER.info({'proj': self.projection_type,
                     'res': self.resolution,
                     'x_min': self.x_min,
                     'x_max': self.x_max,
                     'size_x': self.size_x,
                     'y_min': self.y_min,
                     'y_max': self.y_max,
                     'size_y': self.size_y})

    def aggregate_wse(self, pixc, mask, improved_geoloc_pixc=None):
        pixc_height = pixc['pixel_cloud']['height']
        pixc_num_rare_looks = pixc['pixel_cloud']['eff_num_rare_looks']
        pixc_num_med_looks = pixc['pixel_cloud']['eff_num_medium_looks']

        pixc_power_plus_y = pixc['pixel_cloud']['power_plus_y']
        pixc_power_minus_y = pixc['pixel_cloud']['power_minus_y']

        pixc_dh_dphi = pixc['pixel_cloud']['dheight_dphase']
        pixc_dlat_dphi = pixc['pixel_cloud']['dlatitude_dphase']
        pixc_dlon_dphi = pixc['pixel_cloud']['dlongitude_dphase']
        pixc_phase_noise_std = pixc['pixel_cloud']['phase_noise_std']

        pixc_height_std = np.abs(pixc_phase_noise_std * pixc_dh_dphi)
        # set bad pix height std to high number to deweight
        # instead of giving infs/nans
        bad_num = 1.0e5
        pixc_height_std[pixc_height_std<=0] = bad_num
        pixc_height_std[np.isinf(pixc_height_std)] = bad_num
        pixc_height_std[np.isnan(pixc_height_std)] = bad_num

        looks_to_efflooks = pixc['pixel_cloud'].looks_to_efflooks

        if improved_geoloc_pixc is not None:
            # flatten the interferogram using the improved geolocation
            target_xyz = geoloc.convert_llh2ecef(
                improved_geoloc_pixc['pixel_cloud']['latitude'],
                improved_geoloc_pixc['pixel_cloud']['longitude'],
                improved_geoloc_pixc['pixel_cloud']['height'],
                GEN_RAD_EARTH_EQ, GEN_RAD_EARTH_POLE)
        else:
            # if we don't have an improved geolocation, we can at least
            # flatten with the actual geolocations to estimate the uncertainty
            LOGGER.warning('No improved geolocation provided, '
                           'flattening ifgram with measured geolocations')
            target_xyz = geoloc.convert_llh2ecef(
                pixc['pixel_cloud']['latitude'],
                pixc['pixel_cloud']['longitude'],
                pixc['pixel_cloud']['height'],
                GEN_RAD_EARTH_EQ, GEN_RAD_EARTH_POLE)

        pixc_ifgram = pixc['pixel_cloud']['interferogram']
        tvp_plus_y_antenna_xyz = (pixc['tvp']['plus_y_antenna_x'],
                                  pixc['tvp']['plus_y_antenna_y'],
                                  pixc['tvp']['plus_y_antenna_z'])
        tvp_minus_y_antenna_xyz = (pixc['tvp']['minus_y_antenna_x'],
                                   pixc['tvp']['minus_y_antenna_y'],
                                   pixc['tvp']['minus_y_antenna_z'])
        pixc_tvp_index = ag.get_sensor_index(pixc)
        pixc_wavelength = pixc.wavelength
        flat_ifgram = ag.flatten_interferogram(pixc_ifgram,
                                               tvp_plus_y_antenna_xyz,
                                               tvp_minus_y_antenna_xyz,
                                               target_xyz,
                                               pixc_tvp_index,
                                               pixc_wavelength)

        # Only aggregate heights for interior water and water edges
        pixc_klass = pixc['pixel_cloud']['classification']
        mask = np.logical_and(mask,
                              np.isin(pixc_klass, np.concatenate((
                                  self.interior_water_classes,
                                  self.water_edge_classes))))

        self.wse = np.ma.masked_all((self.size_y, self.size_x))
        self.wse_u = np.ma.masked_all((self.size_y, self.size_x))
        self.n_wse_pix = np.ma.masked_all((self.size_y, self.size_x))

        for i in range(0, self.size_y):
            for j in range(0, self.size_x):
                if len(self.proj_mapping[i][j]) != 0:
                    good = mask[self.proj_mapping[i][j]]
                    grid_height = ag.height_with_uncerts(
                        pixc_height[self.proj_mapping[i][j]],
                        good,
                        pixc_num_rare_looks[self.proj_mapping[i][j]],
                        pixc_num_med_looks[self.proj_mapping[i][j]],
                        flat_ifgram[self.proj_mapping[i][j]],
                        pixc_power_minus_y[self.proj_mapping[i][j]],
                        pixc_power_plus_y[self.proj_mapping[i][j]],
                        looks_to_efflooks,
                        pixc_dh_dphi[self.proj_mapping[i][j]],
                        pixc_dlat_dphi[self.proj_mapping[i][j]],
                        pixc_dlon_dphi[self.proj_mapping[i][j]],
                        pixc_height_std[self.proj_mapping[i][j]],
                        method=self.height_agg_method)

                    self.wse[i][j] = grid_height[0]
                    self.wse_u[i][j] = grid_height[2]
                    self.n_wse_pix[i][j] = ag.simple(good, metric='sum')

    def aggregate_water_area(self, pixc, mask):
        pixc_pixel_area = pixc['pixel_cloud']['pixel_area']
        pixc_water_fraction = pixc['pixel_cloud']['water_frac']
        pixc_water_fraction_uncert = pixc['pixel_cloud']['water_frac_uncert']
        pixc_darea_dheight = pixc['pixel_cloud']['darea_dheight']
        pixc_pfd = pixc['pixel_cloud']['false_detection_rate']
        pixc_pmd = pixc['pixel_cloud']['missed_detection_rate']
        pixc_klass = pixc['pixel_cloud']['classification']

        # Aggregate areas using interior water and edges
        tmp_klass = np.zeros_like(pixc_klass)
        tmp_klass[np.isin(pixc_klass, self.interior_water_classes)] = \
            INTERIOR_WATER_KLASS
        tmp_klass[np.isin(pixc_klass, self.water_edge_classes)] = \
            WATER_EDGE_KLASS
        tmp_klass[np.isin(pixc_klass, self.land_edge_classes)] = \
            LAND_EDGE_KLASS

        self.water_area = np.ma.masked_all((self.size_y, self.size_x))
        self.water_area_u = np.ma.masked_all((self.size_y, self.size_x))
        self.water_frac = np.ma.masked_all((self.size_y, self.size_x))
        self.water_frac_u = np.ma.masked_all((self.size_y, self.size_x))
        self.n_area_pix = np.ma.masked_all((self.size_y, self.size_x))

        for i in range(0, self.size_y):
            for j in range(0, self.size_x):
                if len(self.proj_mapping[i][j]) != 0:
                    good = mask[self.proj_mapping[i][j]]
                    grid_area = ag.area_with_uncert(
                        pixc_pixel_area[self.proj_mapping[i][j]],
                        pixc_water_fraction[self.proj_mapping[i][j]],
                        pixc_water_fraction_uncert[self.proj_mapping[i][j]],
                        pixc_darea_dheight[self.proj_mapping[i][j]],
                        tmp_klass[self.proj_mapping[i][j]],
                        pixc_pfd[self.proj_mapping[i][j]],
                        pixc_pmd[self.proj_mapping[i][j]],
                        good,
                        method=self.area_agg_method,
                        interior_water_klass=INTERIOR_WATER_KLASS,
                        water_edge_klass=WATER_EDGE_KLASS,
                        land_edge_klass=LAND_EDGE_KLASS)

                    self.water_area[i][j] = grid_area[0]
                    self.water_area_u[i][j] = grid_area[1]

                    if self.projection_type == 'utm':
                        pixel_area = self.resolution**2
                    elif self.projection_type == 'geo':
                        px_latitude = self.y_min + self.resolution*i
                        pixel_area = raster_crs.wgs84_px_area(px_latitude,
                                                       self.resolution)

                    self.water_frac[i][j] = grid_area[0]/pixel_area
                    self.water_frac_u[i][j] = grid_area[1]/pixel_area
                    self.n_area_pix[i][j] = ag.simple(good, metric='sum')

    def aggregate_cross_track(self, pixc, mask):
        pixc_cross_track = pixc['pixel_cloud']['cross_track']

        self.cross_track = np.ma.masked_all((self.size_y, self.size_x))

        for i in range(0, self.size_y):
            for j in range(0, self.size_x):
                if len(self.proj_mapping[i][j]) != 0:
                    good = mask[self.proj_mapping[i][j]]
                    self.cross_track[i][j] = ag.simple(
                        pixc_cross_track[self.proj_mapping[i][j]][good],
                        metric='mean')

    def aggregate_sig0(self, pixc, mask):
        pixc_sig0 = pixc['pixel_cloud']['sig0']
        pixc_num_rare_looks = pixc['pixel_cloud']['eff_num_rare_looks']
        pixc_num_med_looks = pixc['pixel_cloud']['eff_num_medium_looks']

        self.sig0 = np.ma.masked_all((self.size_y, self.size_x))
        self.sig0_u = np.ma.masked_all((self.size_y, self.size_x))

        for i in range(0, self.size_y):
            for j in range(0, self.size_x):
                if len(self.proj_mapping[i][j]) != 0:
                    good = mask[self.proj_mapping[i][j]]
                    self.sig0[i][j] = ag.simple(
                        pixc_sig0[self.proj_mapping[i][j]][good], metric='mean')
                    self.sig0_u[i][j] = ag.height_uncert_std(
                        pixc_sig0[self.proj_mapping[i][j]],
                        good,
                        pixc_num_rare_looks[self.proj_mapping[i][j]],
                        pixc_num_med_looks[self.proj_mapping[i][j]])

    def aggregate_inc(self, pixc, mask):
        pixc_inc = pixc['pixel_cloud']['inc']

        self.inc = np.ma.masked_all((self.size_y, self.size_x))

        for i in range(0, self.size_y):
            for j in range(0, self.size_x):
                if len(self.proj_mapping[i][j]) != 0:
                    good = mask[self.proj_mapping[i][j]]
                    self.inc[i][j] = ag.simple(
                        pixc_inc[self.proj_mapping[i][j]][good], metric='mean')

    def aggregate_dark_frac(self, pixc, mask):
        pixc_klass = pixc['pixel_cloud']['classification']
        pixc_pixel_area = pixc['pixel_cloud']['pixel_area']
        pixc_water_fraction = pixc['pixel_cloud']['water_frac']

        self.dark_frac = np.ma.masked_all((self.size_y, self.size_x))

        for i in range(0, self.size_y):
            for j in range(0, self.size_x):
                if len(self.proj_mapping[i][j]) != 0:
                    good = mask[self.proj_mapping[i][j]]
                    self.dark_frac[i][j] = self.calc_dark_frac(
                        pixc_pixel_area[self.proj_mapping[i][j]][good],
                        pixc_klass[self.proj_mapping[i][j]][good],
                        pixc_water_fraction[self.proj_mapping[i][j]][good])

    def calc_dark_frac(self, pixel_area, klass, water_frac):
        klass_dark = np.isin(klass, self.dark_water_classes)
        dark_area = np.sum(pixel_area[klass_dark]*water_frac[klass_dark])
        total_area = np.sum(pixel_area*water_frac)

        # If we don't have any water at all, we have no dark water...
        if np.sum(total_area)==0:
            return 0

        return dark_area/total_area

    def aggregate_classification(self, pixc, mask):
        pixc_klass = pixc['pixel_cloud']['classification']

        self.classification = np.ma.masked_all((self.size_y, self.size_x))

        for i in range(0, self.size_y):
            for j in range(0, self.size_x):
                if len(self.proj_mapping[i][j]) != 0:
                    good = mask[self.proj_mapping[i][j]]
                    self.classification[i][j] = ag.simple(
                        pixc_klass[self.proj_mapping[i][j]][good], metric='mode')

    def aggregate_illumination_time(self, pixc, mask):
        pixc_illumination_time = pixc['pixel_cloud']['illumination_time']
        pixc_illumination_time_tai = pixc['pixel_cloud']['illumination_time_tai']

        self.illumination_time = np.ma.masked_all((self.size_y, self.size_x))
        self.illumination_time_tai = np.ma.masked_all((self.size_y, self.size_x))

        for i in range(0, self.size_y):
            for j in range(0, self.size_x):
                if len(self.proj_mapping[i][j]) != 0:
                    good = mask[self.proj_mapping[i][j]]
                    self.illumination_time[i][j] = ag.simple(
                        pixc_illumination_time[self.proj_mapping[i][j]][good],
                        metric='mean')
                    self.illumination_time_tai[i][j] = ag.simple(
                        pixc_illumination_time_tai[self.proj_mapping[i][j]][good],
                        metric='mean')

        self.tai_utc_difference = \
            self.illumination_time_tai[0] - self.illumination_time[0]

    def aggregate_corrections(self, pixc, mask):
        pixc_geoid = pixc['pixel_cloud']['geoid']
        pixc_solid_earth_tide = pixc['pixel_cloud']['solid_earth_tide']
        pixc_load_tide_sol1 = pixc['pixel_cloud']['load_tide_sol1']
        pixc_load_tide_sol2 = pixc['pixel_cloud']['load_tide_sol2']
        pixc_pole_tide = pixc['pixel_cloud']['pole_tide']
        pixc_model_dry_tropo_cor = pixc['pixel_cloud']['model_dry_tropo_cor']
        pixc_model_wet_tropo_cor = pixc['pixel_cloud']['model_wet_tropo_cor']
        pixc_iono_cor_gim_ka = pixc['pixel_cloud']['iono_cor_gim_ka']

        self.geoid = np.ma.masked_all((self.size_y, self.size_x))
        self.solid_earth_tide = np.ma.masked_all((self.size_y, self.size_x))
        self.load_tide_sol1 = np.ma.masked_all((self.size_y, self.size_x))
        self.load_tide_sol2 = np.ma.masked_all((self.size_y, self.size_x))
        self.pole_tide = np.ma.masked_all((self.size_y, self.size_x))
        self.model_dry_tropo_cor = np.ma.masked_all((self.size_y, self.size_x))
        self.model_wet_tropo_cor = np.ma.masked_all((self.size_y, self.size_x))
        self.iono_cor_gim_ka = np.ma.masked_all((self.size_y, self.size_x))

        for i in range(0, self.size_y):
            for j in range(0, self.size_x):
                if len(self.proj_mapping[i][j]) != 0:
                    good = mask[self.proj_mapping[i][j]]
                    self.geoid[i][j] = ag.simple(
                        pixc_geoid[self.proj_mapping[i][j]][good], metric='mean')
                    self.solid_earth_tide[i][j] = ag.simple(
                        pixc_solid_earth_tide[self.proj_mapping[i][j]][good], metric='mean')
                    self.load_tide_sol1[i][j] = ag.simple(
                        pixc_load_tide_sol1[self.proj_mapping[i][j]][good], metric='mean')
                    self.load_tide_sol2[i][j] = ag.simple(
                        pixc_load_tide_sol2[self.proj_mapping[i][j]][good], metric='mean')
                    self.pole_tide[i][j] = ag.simple(
                        pixc_pole_tide[self.proj_mapping[i][j]][good], metric='mean')
                    self.model_dry_tropo_cor[i][j] = ag.simple(
                        pixc_model_dry_tropo_cor[self.proj_mapping[i][j]][good], metric='mean')
                    self.model_wet_tropo_cor[i][j] = ag.simple(
                        pixc_model_wet_tropo_cor[self.proj_mapping[i][j]][good], metric='mean')
                    self.iono_cor_gim_ka[i][j] = ag.simple(
                        pixc_iono_cor_gim_ka[self.proj_mapping[i][j]][good], metric='mean')

    def apply_wse_corrections(self):
        self.wse -= (
            self.geoid +
            self.solid_earth_tide +
            self.load_tide_sol1 +
            self.pole_tide)

    def aggregate_lat_lon(self, mask):
        x_vec = np.linspace(self.x_min, self.x_max, self.size_x)
        y_vec = np.linspace(self.y_min, self.y_max, self.size_y)
        self.latitude = np.ma.masked_all((self.size_y, self.size_x))
        self.longitude = np.ma.masked_all((self.size_y, self.size_x))
        for i in range(0, self.size_y):
            for j in range(0, self.size_x):
                if len(self.proj_mapping[i][j]) != 0:
                    good = mask[self.proj_mapping[i][j]]
                    # get the lat and lon if there are any good pixels at all
                    if np.any(good):
                        transf = osr.CoordinateTransformation(self.output_crs,
                                                              self.input_crs)
                        lon, lat = transf.TransformPoint(x_vec[j], y_vec[i])[:2]
                        self.latitude[i][j] = lon
                        self.longitude[i][j] = lat


    def build_product(self, populate_values=True):
        # Assemble the product
        LOGGER.info('Assembling Raster Product - populated?: {}'.format(populate_values))

        if self.projection_type == 'utm':
            if self.debug_flag:
                product = raster_products.RasterUTMDebug()
            else:
                product = raster_products.RasterUTM()
        elif self.projection_type == 'geo':
            if self.debug_flag:
                product = raster_products.RasterGeoDebug()
            else:
                product = raster_products.RasterGeo()

        current_datetime = datetime.utcnow()
        product.history = "{:04d}-{:02d}-{:02d}".format(current_datetime.year,
                                                        current_datetime.month,
                                                        current_datetime.day)
        product.cycle_number = self.cycle_number
        product.pass_number = self.pass_number
        product.tile_numbers = self.tile_numbers
        product.tile_names = self.tile_names
        product.tile_polarizations = self.tile_polarizations
        product.resolution = self.resolution
        product.time_coverage_start = self.time_coverage_start
        product.time_coverage_end = self.time_coverage_end
        product.geospatial_lon_min = self.geospatial_lon_min
        product.geospatial_lon_max = self.geospatial_lon_max
        product.geospatial_lat_min = self.geospatial_lat_min
        product.geospatial_lat_max = self.geospatial_lat_max
        product.left_first_longitude = self.left_first_longitude
        product.left_first_latitude = self.left_first_latitude
        product.left_last_longitude = self.left_last_longitude
        product.left_last_latitude = self.left_last_latitude
        product.right_first_longitude = self.right_first_longitude
        product.right_first_latitude = self.right_first_latitude
        product.right_last_longitude = self.right_last_longitude
        product.right_last_latitude = self.right_last_latitude

        if self.projection_type == 'utm':
            product.utm_zone_num = self.utm_zone
            product.mgrs_latitude_band = self.mgrs_band
            product.x_min = self.x_min
            product.x_max = self.x_max
            product.y_min = self.y_min
            product.y_max = self.y_max
            product['x'] = np.linspace(self.x_min, self.x_max, self.size_x)
            product['y'] = np.linspace(self.y_min, self.y_max, self.size_y)
            coordinate_system = self.output_crs
            product.VARIABLES['crs']['projected_crs_name'] = \
                coordinate_system.GetName()
            product.VARIABLES['crs']['false_northing'] = \
                coordinate_system.GetProjParm('false_northing')
            product.VARIABLES['crs']['longitude_of_central_meridian'] = \
                coordinate_system.GetProjParm('central_meridian')

        elif self.projection_type == 'geo':
            product.longitude_min = self.x_min
            product.longitude_max = self.x_max
            product.latitude_min = self.y_min
            product.latitude_max = self.y_max
            product['longitude'] = np.linspace(self.x_min,
                                               self.x_max,
                                               self.size_x)
            product['latitude'] = np.linspace(self.y_min,
                                              self.y_max,
                                              self.size_y)
            coordinate_system = raster_crs.wgs84_crs()

        product.VARIABLES['crs']['crs_wkt'] = coordinate_system.ExportToWkt()
        product.VARIABLES['crs']['spatial_ref'] = \
            product.VARIABLES['crs']['crs_wkt']

        if populate_values:
            if self.projection_type == 'utm':
                product['longitude'] = self.longitude
                product['latitude'] = self.latitude

            product['illumination_time'] = self.illumination_time
            product['illumination_time_tai'] = self.illumination_time_tai
            product['illumination_time'].tai_utc_difference = \
                self.tai_utc_difference
            product['wse'] = self.wse
            product['wse_uncert'] = self.wse_u
            product['water_area'] = self.water_area
            product['water_area_uncert'] = self.water_area_u
            product['water_frac'] = self.water_frac
            product['water_frac_uncert'] = self.water_frac_u
            product['cross_track'] = self.cross_track
            product['sig0'] = self.sig0
            product['sig0_uncert'] = self.sig0_u
            product['inc'] = self.inc
            product['n_wse_pix'] = self.n_wse_pix
            product['n_area_pix'] = self.n_area_pix
            product['dark_frac'] = self.dark_frac
            product['geoid'] = self.geoid
            product['solid_earth_tide'] = self.solid_earth_tide
            product['load_tide_sol1'] = self.load_tide_sol1
            product['load_tide_sol2'] = self.load_tide_sol2
            product['pole_tide'] = self.pole_tide
            product['model_dry_tropo_cor'] = self.model_dry_tropo_cor
            product['model_wet_tropo_cor'] = self.model_wet_tropo_cor
            product['iono_cor_gim_ka'] = self.iono_cor_gim_ka

            if self.debug_flag:
                product['classification'] = self.classification

        return product


def get_pixc_mask(pixc):
    lats = pixc['pixel_cloud']['latitude']
    lons = pixc['pixel_cloud']['longitude']
    height = pixc['pixel_cloud']['height']
    area = pixc['pixel_cloud']['pixel_area']
    klass =pixc['pixel_cloud']['classification']
    mask = np.ones(np.shape(pixc['pixel_cloud']['latitude']))

    if np.ma.is_masked(lats):
        mask[lats.mask] = 0
    if np.ma.is_masked(lons):
        mask[lons.mask] = 0
    if np.ma.is_masked(height):
        mask[height.mask] = 0
    if np.ma.is_masked(area):
        mask[area.mask] = 0

    mask[np.isnan(lats)] = 0
    mask[np.isnan(lons)] = 0
    mask[np.isnan(klass)] = 0

    # bounds for valid utc
    mask[lats >= 84.0] = 0
    mask[lats <= -80.0] = 0

    return mask==1


def lon_360to180(longitude):
    return np.mod(longitude + 180, 360) - 180
