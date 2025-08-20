from typing import Iterator, Dict, Any, List, Tuple
import os, glob, json, sys
from pathlib import Path
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
import outlets
import h3

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

    utils.alter_geojson(output_path, eddy['config']['alterations'])
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

def centroid_gdal(config:Dict[str, Any], eddy_name:str):
    eddy = config['assets'][eddy_name]
    in_layer = eddy['in_layer']
    out_layer = eddy['out_layer']
    

    in_path = versioning.atlas_path(config, 'layers') / eddy['in_layer'] / f'{eddy["in_layer"]}.geojson'
    out_path = versioning.atlas_path(config, 'layers') / eddy['out_layer'] / f'{eddy["out_layer"]}.geojson'
    
    ds = gdal.Open(str(in_path))
    fc = ds.GetLayer()
    for feature in fc:
        geom = feature.GetGeometryRef()
        if geom is not None:
            centroid = geom.Centroid()
            feature['properties']['centroid'] = geojson.dumps(centroid)
    
    with open(out_path, 'w') as f:
        geojson.dump(fc, f)
    
    return out_path

def delta_annotate_spatial_duckdb(config:Dict[str, Any], layer_name:str, delta_name:str, anno_type: str = "deltas", anno_in_path: Path = None, updated_properties: List[str] = []):
    #in_layer = eddy['in_layer']
    
    # annotation data path
    if not anno_in_path:
        if anno_type == "deltas":
            anno_in_path = versioning.atlas_path(config, anno_type) / layer_name / f"{delta_name}.geojson"
        elif anno_type == "layers":
            anno_in_path = versioning.atlas_path(config, anno_type) / delta_name / f"{delta_name}.geojson"
        else:
            logger.error(f"Unknown annotation type: {anno_type}!")
    # target data path
    feat_in_path = versioning.atlas_path(config, 'layers') / layer_name / f"{layer_name}.geojson"

    anno_out_path = anno_in_path.parent / "work" /  anno_in_path.name
    
    anno_prefix = "anno_"
    anno_prefix_len = len(anno_prefix)
    feat_prefix = "feat_"
    feat_prefix_len = len(feat_prefix)

    # get duckdb

    # make temp table for delta
    duckdb.sql("INSTALL spatial; LOAD spatial; ")
    duckdb.sql( f"DROP TABLE IF EXISTS anno; CREATE TABLE anno AS SELECT COLUMNS('.*') AS \"anno_\\0\" FROM ST_Read('{anno_in_path}');")
    duckdb.sql( f"DROP TABLE IF EXISTS feat; CREATE TABLE feat AS SELECT COLUMNS('.*') AS \"feat_\\0\" FROM ST_Read('{feat_in_path}');")

    # join delta to layers
    res = duckdb.sql(f"""
SELECT * EXCLUDE (anno_geom, feat_geom), 
    ST_AsGeoJSON(feat_geom) AS geometry,
    anno_geom AS anno_geom
    FROM anno RIGHT JOIN feat 
ON ST_Intersects(anno_geom, feat_geom);
""")

    # update layer features with delta feature properties
    matching_features = [dict(zip(res.columns, row)) for row in res.fetchall()] 
    logger.info(f"DID Anno join!  {len(matching_features)}")
    # logger.info(f"Anno join RESULT!  {matching_features}")
    
    # rewrite featuers with new attributes added
    features = []
    

    for mf in matching_features:
        logger.debug(f"about to assign geomtry from: {mf.get('geometry')}")
        if mf.get('anno_geom'):
            logger.info(f"NOT Skipping empty geometry for: {mf}")
            
            # continue
        f = geojson.Feature(geometry=geojson.loads(mf['geometry']))
        for k,v	in ( (k[feat_prefix_len:],v) for k,v in mf.items() if k.startswith(feat_prefix) and k not in ['feat_geom', 'anno_geom'] ):
            f['properties'][k] = v
        for k,v	in ( (k[anno_prefix_len:],v) for k,v in mf.items() if k.startswith(anno_prefix) and k not in [ 'feat_geom', 'anno_geom'] ):
            if len(updated_properties) == 0 or k in updated_properties:
                if v:
                    f['properties'][k] = v
        features.append(f)
    logger.info(f"Post merge  RESULT!  {len(features)}")

        
    feature_collection = geojson.FeatureCollection(features)
    geojson.dump(feature_collection, open(feat_in_path, 'w'))
    logger.info(f"moving consumed delta anno: {anno_in_path} -> {anno_out_path}")
    anno_in_path.rename(anno_out_path)
    return feat_in_path



    # write out dlayer

    # delete temp table


    
    conn = duckdb.connect(database=config['database'])
    # Do intersection with parcels
    query = f"""
    SELECT 
        a.id,
        b.id as parcel_id
    FROM {in_layer} a
    """


def h3_for_linestring(geometry, starting_res=8, swap_coordinates=True, max_num_cells=10):
    """
    Generate H3 indices for a GeoJSON LineString geometry.
    
    Args:
        geometry: GeoJSON LineString geometry object with type "LineString" and coordinates
        starting_res: Starting H3 resolution (default: 8)
        swap_coordinates: Whether to swap lat/lng to lng/lat order (default: True)
        max_num_cells: Maximum number of H3 cells before reducing resolution (default: 10)
        
    Returns:
        Dictionary containing H3 cells, resolution used, and representative index
        
    Raises:
        Exception: If geometry is invalid or processing fails
    """
    try:
        # Validate input geometry
        if not isinstance(geometry, dict):
            raise Exception("Geometry must be a dictionary")
        
        if geometry.get('type') != 'LineString':
            raise Exception(f"Geometry type must be 'LineString', got '{geometry.get('type')}'")
        
        if 'coordinates' not in geometry:
            raise Exception("Geometry must contain 'coordinates' field")
        
        coordinates = geometry['coordinates']
        if not coordinates or not isinstance(coordinates, list):
            raise Exception("Coordinates must be a non-empty list")
        
        if len(coordinates) < 2:
            raise Exception("LineString must have at least 2 coordinate pairs")
        
        # Handle coordinate swapping if requested
        if swap_coordinates:
            # Swap from [lat, lng] to [lng, lat] order
            linestring_coords = [[coord[1], coord[0]] for coord in coordinates]
        else:
            # Keep original order
            linestring_coords = coordinates
        
        # Start with the specified resolution
        res = starting_res
        
        # Try to find a resolution that gives us <= max_num_cells
        while res >= 0:
            try:
                # Create H3 line and get cells
                h3_line = h3.LatLngLine(linestring_coords)
                h3_cells = h3.h3shape_to_cells(h3_line, res)
                
                # Convert to list and check count
                cell_list = list(h3_cells)
                cell_count = len(cell_list)
                
                # If we're under the threshold, we're done
                if cell_count <= max_num_cells:
                    representative_index = cell_list[0] if cell_list else None
                    
                    return {
                        "cells": cell_list,
                        "resolution": res,
                        "cell_count": cell_count,
                        "representative_index": representative_index
                    }
                
                # Otherwise, reduce resolution and try again
                res -= 1
                
            except Exception as e:
                # If this resolution fails, try a lower one
                res -= 1
                continue
        
        # If we get here, we couldn't find a suitable resolution
        raise Exception(f"Could not find H3 resolution <= {starting_res} that produces <= {max_num_cells} cells")
        
    except Exception as e:
        raise Exception(f"Failed to generate H3 indices for LineString: {str(e)}")


def h3_for_polygon(geometry, starting_res=8, swap_coordinates=True, max_num_cells=10):
    """
    Generate H3 indices for a GeoJSON polygon geometry.
    
    Args:
        geometry: GeoJSON polygon geometry object with type "Polygon" and coordinates
        starting_res: Starting H3 resolution (default: 8)
        swap_coordinates: Whether to swap lat/lng to lng/lat order (default: True)
        max_num_cells: Maximum number of H3 cells before reducing resolution (default: 10)
        
    Returns:
        Dictionary containing H3 cells, resolution used, and representative index
        
    Raises:
        Exception: If geometry is invalid or processing fails
    """
    try:
        # Validate input geometry
        if not isinstance(geometry, dict):
            raise Exception("Geometry must be a dictionary")
        
        if geometry.get('type') != 'Polygon':
            raise Exception(f"Geometry type must be 'Polygon', got '{geometry.get('type')}'")
        
        if 'coordinates' not in geometry:
            raise Exception("Geometry must contain 'coordinates' field")
        
        coordinates = geometry['coordinates']
        if not coordinates or not isinstance(coordinates, list):
            raise Exception("Coordinates must be a non-empty list")
        
        # Extract the outer ring (first polygon)
        outer_ring = coordinates[0]
        if len(outer_ring) < 3:
            raise Exception("Polygon must have at least 3 coordinate pairs")
        
        # Handle coordinate swapping if requested
        if swap_coordinates:
            # Swap from [lat, lng] to [lng, lat] order
            polygon_coords = [[coord[1], coord[0]] for coord in outer_ring]
        else:
            # Keep original order
            polygon_coords = outer_ring
        
        # Start with the specified resolution
        res = starting_res
        
        # Try to find a resolution that gives us <= max_num_cells
        while res >= 0:
            try:
                # Create H3 polygon and get cells
                h3_poly = h3.LatLngPoly(polygon_coords)
                h3_cells = h3.h3shape_to_cells(h3_poly, res)
                
                # Convert to list and check count
                cell_list = list(h3_cells)
                cell_count = len(cell_list)
                
                # If we're under the threshold, we're done
                if cell_count <= max_num_cells:
                    representative_index = cell_list[0] if cell_list else None
                    
                    return {
                        "cells": cell_list,
                        "resolution": res,
                        "cell_count": cell_count,
                        "representative_index": representative_index
                    }
                
                # Otherwise, reduce resolution and try again
                res -= 1
                
            except Exception as e:
                # If this resolution fails, try a lower one
                res -= 1
                continue
        
        # If we get here, we couldn't find a suitable resolution
        raise Exception(f"Could not find H3 resolution <= {starting_res} that produces <= {max_num_cells} cells")
        
    except Exception as e:
        raise Exception(f"Failed to generate H3 indices for polygon: {str(e)}")


def h3_cells(config, in_layer, out_layer):
    """
    Eddy function to generate H3 cell indices for features in a layer.
    
    Args:
        config: Eddy configuration dictionary
        in_layer: Input layer name
        out_layer: Output layer name (must be same as in_layer for now)
        
    Returns:
        Updated layer with H3 properties added to each feature
        
    Raises:
        Exception: If input/output layers differ or processing fails
    """
    try:
        # For now, input and output layers must be the same
        if in_layer != out_layer:
            raise Exception(f"Input and output layers must be identical for now. Got in_layer='{in_layer}', out_layer='{out_layer}'")
        
        # Get configuration parameters with defaults
        starting_resolution = config.get('starting_resolution', 8)
        algorithm = config.get('algorithm', 'max_num_cells')
        max_cells = config.get('max_cells', 10)
        swap_coordinates = config.get('swap_coordinates', True)
        
        logger.info(f"Processing H3 cells for layer '{in_layer}' with resolution {starting_resolution}, algorithm '{algorithm}', max_cells {max_cells}")
        
        # Load the input layer
        layer_data = utils.load_layer(in_layer)
        if not layer_data or 'features' not in layer_data:
            raise Exception(f"Could not load layer '{in_layer}' or layer has no features")
        
        # Get layer definition to determine geometry type
        layer_def = utils.get_layer_definition(in_layer)
        if not layer_def:
            raise Exception(f"Could not get layer definition for '{in_layer}'")
        
        geometry_type = layer_def.get('geometry_type', '').lower()
        logger.info(f"Layer '{in_layer}' has geometry type: {geometry_type}")
        
        # Validate geometry type and select appropriate H3 function
        if geometry_type == 'polygon':
            h3_function = h3_for_polygon
        elif geometry_type == 'linestring':
            h3_function = h3_for_linestring
        else:
            raise Exception(f"Unsupported geometry type '{geometry_type}' for layer '{in_layer}'. Only 'polygon' and 'linestring' are supported.")
        
        # Process each feature
        features = layer_data['features']
        logger.info(f"Processing {len(features)} features in layer '{in_layer}'")
        
        for i, feature in enumerate(features):
            try:
                if 'geometry' not in feature:
                    logger.warning(f"Feature {i} has no geometry, skipping")
                    continue
                
                geometry = feature['geometry']
                if not geometry or 'type' not in geometry:
                    logger.warning(f"Feature {i} has invalid geometry, skipping")
                    continue
                
                # Generate H3 indices for this feature
                h3_result = h3_function(
                    geometry,
                    starting_res=starting_resolution,
                    swap_coordinates=swap_coordinates,
                    max_num_cells=max_cells
                )
                
                # Add H3 properties to the feature (preserving existing properties)
                feature['properties'] = feature.get('properties', {})
                feature['properties'].update({
                    'h3_cells': h3_result['cells'],
                    'h3_resolution': h3_result['resolution'],
                    'h3_cardinality': h3_result['cell_count'],
                    'h3_cell': h3_result['representative_index']
                })
                
                logger.debug(f"Feature {i}: Added H3 properties with {h3_result['cell_count']} cells at resolution {h3_result['resolution']}")
                
            except Exception as e:
                logger.error(f"Failed to process feature {i}: {str(e)}")
                # Continue processing other features instead of failing completely
                continue
        
        # Save the updated layer back to the same location
        utils.save_layer(out_layer, layer_data)
        logger.info(f"Successfully updated layer '{out_layer}' with H3 properties")
        
        return layer_data
        
    except Exception as e:
        logger.error(f"H3 cells eddy failed: {str(e)}")
        raise Exception(f"H3 cells eddy failed: {str(e)}")


asset_methods = {
    "derived_hillshade": hillshade_gdal,
    "gdal_contours": contours_gdal,
    "h3_cells": h3_cells,
}
