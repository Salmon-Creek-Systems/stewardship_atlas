from typing import Iterator, Dict, Any, List, Tuple
import os, glob, json, sys
import logging
from matplotlib.colors import LightSource
import matplotlib.pyplot as plt
import matplotlib as mpl
import numpy as np
import duckdb
import geojson
from osgeo import gdal, ogr
import subprocess
import versioning
import utils

# Configure logging
logger = logging.getLogger(__name__)
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.DEBUG)



def contours_gdal(config:Dict[str, Any],eddy_name:str):
    """
    Generate contour lines from DEM data and save as GeoJSON.
    
    Args:
        dem_path (str): Path to input DEM file
        output_dir (str): Directory to save output contours
        interval (float): Contour interval in meters
    """
    eddy = config['assets'][eddy_name]
    in_path = versioning.atlas_path(config, 'layers') / eddy['in_layer'] / f'{eddy["in_layer"]}.tiff'
    output_path = str(versioning.atlas_path(config, 'layers') / eddy['out_layer'] / f'{eddy["out_layer"]}.geojson')
    interval = eddy['config']['interval']
    
    # Open the DEM dataset
    dem_path = str(in_path)
    dem_ds = gdal.Open(dem_path)
    if dem_ds is None:
        raise ValueError(f"Could not open DEM file: {dem_path}")
    
    # Get the DEM band
    dem_band = dem_ds.GetRasterBand(1)
    
    # Create a temporary memory layer for contours
    mem_driver = ogr.GetDriverByName("Memory")
    contour_ds = mem_driver.CreateDataSource("contours")
    
    # Create the layer in memory
    contour_layer = contour_ds.CreateLayer(
        "contours",
        dem_ds.GetSpatialRef(),
        ogr.wkbLineString
    )
    
    # Add elevation field
    field_defn = ogr.FieldDefn("elevation", ogr.OFTReal)
    contour_layer.CreateField(field_defn)

    # Add ID field for contour generation
    id_field_defn = ogr.FieldDefn("id", ogr.OFTInteger)
    contour_layer.CreateField(id_field_defn)
    
    # Generate contours into memory layer
    gdal.ContourGenerate(
        dem_band,      # input band
        interval,      # contour interval
        0,            # fixed level count
        [],           # fixed levels
        0,            # nodata value
        0,            # index field
        contour_layer, # output layer
        1,
        0
    )
    
    # Create GeoJSON driver and output file
    geojson_driver = ogr.GetDriverByName("GeoJSON")
    if os.path.exists(output_path):
        geojson_driver.DeleteDataSource(output_path)
    
    # Copy the memory layer to GeoJSON
    geojson_ds = geojson_driver.CreateDataSource(output_path)
    geojson_layer = geojson_ds.CopyLayer(contour_layer, "contours")
    
    # Clean up
    geojson_ds = None
    contour_ds = None
    dem_ds = None

    return output_path



def hillshade_gdal(  config:Dict[str, Any], eddy_name:str):
    # Open DEM and get data
    eddy = config['assets'][eddy_name]
    in_path = versioning.atlas_path(config, 'layers') / eddy['in_layer'] / f'{eddy["in_layer"]}.tiff'
    out_path = versioning.atlas_path(config, 'layers') / eddy['out_layer'] / f'{eddy["out_layer"]}.tiff'
    intensity = eddy['config']['intensity']
    ds = gdal.Open(str(in_path))
    elevation = ds.ReadAsArray()
    
    # Calculate hillshade
    ls = LightSource(azdeg=315, altdeg=45)
    hillshade = ls.hillshade(elevation, vert_exag=1.0)
    
    # Apply intensity/fade
    if intensity < 1.0:
        # Create a white background
        background = np.ones_like(hillshade)
        # Blend hillshade with white background based on intensity
        hillshade = hillshade * intensity + background * (1 - intensity)
    
    logger.debug("Calculated hillshade: {in_path} -> {out_path} ({intensity})")
    
    # Save hillshade
    driver = gdal.GetDriverByName('GTiff')
    out_ds = driver.Create(str(out_path), ds.RasterXSize, ds.RasterYSize, 1, gdal.GDT_Float32)
    out_ds.SetGeoTransform(ds.GetGeoTransform())
    out_ds.SetProjection(ds.GetProjection())
    out_ds.GetRasterBand(1).WriteArray(hillshade)
    logger.debug(f"Saved hillshade to: {out_path}")
    
    # Clean up
    ds = None
    out_ds = None
    
    return out_path


asset_methods = {
    "gdal_contours": contours_gdal,
    'derived_hillshade': hillshade_gdal}
