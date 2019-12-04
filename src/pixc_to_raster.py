#!/usr/bin/env python
'''
Script to rasterize a pixelcloud

Author (s): Alexander Corben
'''

import os
import rdf
import raster
import logging
import argparse

from SWOTWater.products.product import MutableProduct

description = """
description:
    pixc_to_raster.py rasterizes a given pixelcloud using configuration 
    paramaters in a given rdf file

example RDF variables:
    projection_type           (-) = utm
    resolution                (-) = 100
    interior_water_classes    (-) = [4, 24]
    water_edge_classes        (-) = [3, 23]
    land_edge_classes         (-) = [2, 22]
    height_agg_method         (-) = weight
    area_agg_method           (-) = composite
"""

def main():
    logging.basicConfig(level=logging.DEBUG)
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description = description)
    parser.add_argument("pixc_file", type=str, help='input pixelcloud file')
    parser.add_argument("rdf_file", type=str, help='rdf config file')
    parser.add_argument("out_file", type=str, help='output raster file')
    parser.add_argument("-d", "--debug", action='store_true',
                        help='flag to write debug version of raster product')
    args = parser.parse_args()
    
    cfg = rdf.parse(os.path.abspath(args.rdf_file), comment='!')
    pixc_data = MutableProduct.from_ncfile(args.pixc_file)
    processor = raster.Worker(cfg, pixc_data, args.debug)
    raster_data = processor.rasterize()
    raster_data.to_ncfile(args.out_file)

if __name__ == '__main__':
    main()
