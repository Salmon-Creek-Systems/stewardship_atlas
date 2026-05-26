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
from datetime import datetime

import requests
import dataswale_geojson as dataswale
import networkx as nx
from pyproj import Geod
from shapely.geometry import shape
import rasterio

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
    
    # Create RGB image from grayscale hillshade
    # Convert hillshade to 0-255 range
    hillshade_scaled = (hillshade * 255).astype(np.uint8)
    
    # Create RGB bands (initially all grayscale)
    rgb_bands = np.stack([hillshade_scaled, hillshade_scaled, hillshade_scaled], axis=0)
    
    # Create mask for zero and below elevation (water/ocean)
    water_mask = elevation <= 0
    
    # Apply dark blue color to water areas (RGB: 0, 50, 100)
    # Preserve some hillshade intensity for underwater terrain
    if np.any(water_mask):
        water_intensity = hillshade[water_mask]
        rgb_bands[0][water_mask] = 0 #(water_intensity * 0).astype(np.uint8)      # Red: 0
        rgb_bands[1][water_mask] = 50 #(water_intensity * 50).astype(np.uint8)     # Green: 0-50 based on hillshade
        rgb_bands[2][water_mask] = 100 # (water_intensity * 100).astype(np.uint8)    # Blue: 0-100 based on hillshade
        logger.info(f"Colored {np.sum(water_mask)} water pixels with dark blue")
    
    logger.debug(f"Calculated hillshade: {in_path} -> {out_path} ({intensity})")
    
    # Save RGB hillshade
    driver = gdal.GetDriverByName('GTiff')
    out_ds = driver.Create(str(out_path), ds.RasterXSize, ds.RasterYSize, 3, gdal.GDT_Byte)
    out_ds.SetGeoTransform(ds.GetGeoTransform())
    out_ds.SetProjection(ds.GetProjection())
    
    # Write each RGB band
    for i in range(3):
        out_ds.GetRasterBand(i + 1).WriteArray(rgb_bands[i])
    
    logger.debug(f"Saved RGB hillshade to: {out_path}")
    
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



json_serial = utils.json_serial  # shared serializer — handles datetime, date, Path


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
        #print(f"F: {f}")
        features.append(f)
    logger.info(f"Post merge  RESULT!  {len(features)}")

        
    feature_collection = geojson.FeatureCollection(features)
    #print(f"FC for Anno: {feature_collection} END")
    geojson.dump(feature_collection, open(feat_in_path, 'w'), default=json_serial)
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

        logger.info(f"LS corrds: {len(linestring_coords)}")
        # Start with the specified resolution
        res = starting_res
        last_cell_count = 0
        # Try to find a resolution that gives us <= max_num_cells
        while res >= 0:
            cell_list = []
            try:
                # Create H3 line and get cells
                #h3_line = h3.LatLngLine(linestring_coords)
                old_endpoint = None
                for lng, lat in linestring_coords:
                    
                    # h3_cells = h3.h3shape_to_cells(h3_line, res)
                    new_endpoint =  h3.latlng_to_cell(lng,lat, res)
                    if old_endpoint:
                        fill_cells = h3.grid_path_cells(old_endpoint, new_endpoint)
                        cell_list += fill_cells
                    cell_list.append(new_endpoint)
                    old_endpoint = new_endpoint

                # Convert to list and check count
                #cell_list = list(h3_cells)
                #cell_count = len(cell_list)

                #last_cell_count = cell_count
                # If we're under the threshold, we're done
                #if cell_count <= max_num_cells:
                cell_set = list(set(cell_list))
                representative_index = cell_set[0] if cell_set else None


                return {
                    "all_cells": cell_list,
                    "cells": cell_set,
                    "resolution": res,
                    "cell_count": len(cell_set),
                    "representative_index": representative_index
                    }
                
                # Otherwise, reduce resolution and try again
                logger.info(f"too many cells: {cell_count}")
                res -= 1
                
            except Exception as e:
                # If this resolution fails, try a lower one
                logger.error(f"LineString conversion (res: {res} coords: {len(linestring_coords)}) failed: {e}")
                res -= 1
                continue
        
        # If we get here, we couldn't find a suitable resolution
        raise Exception(f"LineString [{len(linestring_coords)}]: Could not find H3 resolution <= {starting_res} that produces <= {max_num_cells} cells [{last_cell_count}]")
        
    except Exception as e:
        raise Exception(f"Failed to generate H3 indices for LineString: {str(e)}")


def h3_for_point(geometry, starting_res=8, swap_coordinates=True, max_num_cells=None):
    """
    Generate an H3 index for a GeoJSON Point geometry.

    Args:
        geometry: GeoJSON Point geometry object with type "Point" and coordinates [lng, lat]
        starting_res: H3 resolution to use (default: 8)
        swap_coordinates: Whether coordinates are [lng, lat] order (default: True);
                          if True, swaps to lat, lng before calling H3

    Returns:
        Dictionary containing the single H3 cell, resolution used, and representative index

    Raises:
        Exception: If geometry is invalid or processing fails
    """
    try:
        if not isinstance(geometry, dict):
            raise Exception("Geometry must be a dictionary")

        if geometry.get('type') != 'Point':
            raise Exception(f"Geometry type must be 'Point', got '{geometry.get('type')}'")

        if 'coordinates' not in geometry:
            raise Exception("Geometry must contain 'coordinates' field")

        coordinates = geometry['coordinates']
        if not coordinates or len(coordinates) < 2:
            raise Exception("Point coordinates must have at least [lng, lat]")

        if swap_coordinates:
            # GeoJSON is [lng, lat]; H3 wants (lat, lng)
            lng, lat = coordinates[0], coordinates[1]
        else:
            lat, lng = coordinates[0], coordinates[1]

        cell = h3.latlng_to_cell(lat, lng, starting_res)
        return {
            'cells': [cell],
            'resolution': starting_res,
            'cell_count': 1,
            'representative_index': cell
        }

    except Exception as e:
        raise Exception(f"Failed to generate H3 index for Point: {str(e)}")


def h3_for_polygon(geometry, starting_res=11, swap_coordinates=True, max_num_cells=10):
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

                # logger.info(f"Hape conversion: {polygon_coords} -> {h3_cells}")
                
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
        raise Exception(f"Could not find H3 resolution <= {starting_res} that produces <= {max_num_cells} cells [{cell_count}]")
        
    except Exception as e:
        raise Exception(f"Failed to generate H3 indices for polygon: {str(e)}")


def h3_cells(config, asset_name):
    """
    Eddy function to generate hexagonal H3 cell features from input layer geometries. 
    
    Args:
        config: Configuration dictionary containing assets
        asset_name: Name of the asset configuration to use
        
    Returns:
        New FeatureCollection with hexagonal features, one for each H3 cell.
        Each hexagonal feature preserves all properties from the original feature
        that created it, plus H3 metadata (h3_index, h3_resolution, etc.).
        
    Raises:
        Exception: If configuration is invalid or processing fails
    """
    try:
        # Get the asset configuration — use resolved 'config' subdict which has merged shared + overrides
        asset_config = config['assets'][asset_name].get('config', config['assets'][asset_name])

        # Extract configuration parameters
        in_layer = asset_config['in_layer']
        out_layer = asset_config['out_layer']
        starting_resolution = asset_config.get('starting_resolution', config.get('default_h3_resolution', 11))
        algorithm = asset_config.get('algorithm', 'max_num_cells')
        max_cells = asset_config.get('max_cells', 10)
        swap_coordinates = asset_config.get('swap_coordinates', True)
        
        logger.info(f"Processing H3 cells for layer '{in_layer}' with resolution {starting_resolution}, algorithm '{algorithm}', max_cells {max_cells}")

        layers_dict = {x['name']: x for x in config['dataswale']['layers']}
        # Load the input layer
        layer_data = dataswale.layer_as_featurecollection(config, in_layer)
        if not layer_data or 'features' not in layer_data:
            raise Exception(f"Could not load layer '{in_layer}' or layer has no features")
        
        # Get layer definition to determine geometry type
        layer_def = layers_dict[in_layer]
        if not layer_def:
            raise Exception(f"Could not get layer definition for '{in_layer}'")
        
        geometry_type = layer_def.get('geometry_type', '').lower()
        logger.info(f"Layer '{in_layer}' has geometry type: {geometry_type}")
        
        # Validate geometry type and select appropriate H3 function
        if geometry_type == 'polygon':
            h3_function = h3_for_polygon
        elif geometry_type == 'linestring':
            h3_function = h3_for_linestring
        elif geometry_type == 'point':
            h3_function = h3_for_point
        else:
            raise Exception(f"Unsupported geometry type '{geometry_type}' for layer '{in_layer}'. Only 'polygon', 'linestring', and 'point' are supported.")
        
        # Process each feature and collect H3 cells with properties
        features = layer_data['features']
        logger.info(f"Processing {len(features)} features in layer '{in_layer}'")
        
        h3_cells_with_properties = []
        
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
                logger.info(f"Indexing Geom {geometry['type']}): {len(geometry['coordinates'])} coords, got {h3_result}")
                
                # Create a hexagonal feature for each H3 cell, preserving all original properties
                for h3_cell in h3_result['cells']:
                    # Convert H3 cell to hexagonal geometry
                    try:
                        hex_coords = h3.cell_to_boundary(h3_cell)
                        # hex_geometry = h3.cells_to_geo(h3_cell)
                        # Convert to GeoJSON format (swap coordinates if needed)
                        if swap_coordinates:
                            # h3.cells_to_geo returns [lat, lng], convert to [lng, lat] for GeoJSON
                            hex_coords = [[coord[1], coord[0]] for coord in hex_coords]
                        #else:
                        #    hex_coords = hex_geometry
                        
                        # Create hexagonal feature with all original properties plus H3 metadata
                        hex_feature = {
                            'type': 'Feature',
                            'geometry': {
                                'type': 'Polygon',
                                'coordinates': [hex_coords]
                            },
                            'properties': {
                                **feature.get('properties', {}),  # Preserve all original properties
                                'h3_index': h3_cell,
                                'h3_resolution': h3_result['resolution'],
                                'h3_cell_count': h3_result['cell_count'],
                                'source_feature_id': i  # Track which original feature created this cell
                            }
                        }
                        h3_cells_with_properties.append(hex_feature)
                        
                    except Exception as e:
                        logger.error(f"Feature Conv ERR: Failed to convert H3 cell {h3_cell} to geometry: {str(e)}")
                        continue
                
                logger.debug(f"FeatureConv {i}: Created {len(h3_cells_with_properties)} hexagonal features")
                
            except Exception as e:
                logger.error(f"Failed to process feature {i}: {str(e)}")
                # Continue processing other features instead of failing completely
                continue
        
        # Create new feature collection with hexagonal features
        hex_layer_data = {
            'type': 'FeatureCollection',
            'features': h3_cells_with_properties
        }
        
        # Save the new hexagonal layer to the output location
        # utils.save_layer(out_layer, hex_layer_data)
    
        out_path = versioning.atlas_path(config, 'layers') / out_layer / f"{out_layer}.geojson"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fc = geojson.FeatureCollection(0)
        fc['features'] = h3_cells_with_properties

        with open(out_path, 'w') as f:
            geojson.dump(fc, f)

        logger.info(f"Successfully created hexagonal layer '{out_layer}' with {len(h3_cells_with_properties)} features")
        
        return hex_layer_data
        
    except Exception as e:
        logger.error(f"H3 cells eddy failed: {str(e)}")
        raise Exception(f"H3 cells eddy failed: {str(e)}")


def road_lrs(config, asset_name):
    """
    Eddy: annotate road segments with cumulative network distance from an anchor point.

    Uses H3 cell IDs as topology nodes — each segment's start and end coordinates are
    snapped to H3 cells at `h3_resolution`. Dijkstra from the anchor cell assigns
    m_start/m_end (meters) to every reachable segment.

    Required config: lrs_anchor_coordinates [lat, lng]
    """
    asset_config = config['assets'][asset_name].get('config', config['assets'][asset_name])
    in_layer = asset_config.get('in_layer', 'roads')
    out_layer = asset_config.get('out_layer', 'roads_lrs')
    anchor_coords = asset_config.get('lrs_anchor_coordinates')
    resolution = asset_config.get('h3_resolution', 12)

    if not anchor_coords:
        raise ValueError(f"road_lrs: lrs_anchor_coordinates [lat, lng] is required in asset config for '{asset_name}'")

    anchor_lat, anchor_lng = anchor_coords

    layer_data = dataswale.layer_as_featurecollection(config, in_layer)
    if not layer_data or 'features' not in layer_data:
        raise Exception(f"road_lrs: could not load layer '{in_layer}'")

    features = layer_data['features']
    geod = Geod(ellps='WGS84')
    G = nx.Graph()
    segment_nodes = []  # (start_cell, end_cell) per feature index

    for feature in features:
        geom = feature.get('geometry')
        if not geom or geom.get('type') != 'LineString':
            segment_nodes.append((None, None))
            continue

        coords = geom['coordinates']  # GeoJSON: [lng, lat]
        start_lng, start_lat = coords[0][0], coords[0][1]
        end_lng, end_lat = coords[-1][0], coords[-1][1]

        start_cell = h3.latlng_to_cell(start_lat, start_lng, resolution)
        end_cell = h3.latlng_to_cell(end_lat, end_lng, resolution)

        length_m = abs(geod.geometry_length(shape(geom)))

        if not G.has_edge(start_cell, end_cell):
            G.add_edge(start_cell, end_cell, weight=length_m)
        elif length_m < G[start_cell][end_cell]['weight']:
            G[start_cell][end_cell]['weight'] = length_m

        segment_nodes.append((start_cell, end_cell))

    # Find the graph node nearest to the anchor
    anchor_cell = h3.latlng_to_cell(anchor_lat, anchor_lng, resolution)
    if anchor_cell not in G:
        found = False
        for ring in range(1, 30):
            for candidate in h3.grid_disk(anchor_cell, ring):
                if candidate in G:
                    anchor_cell = candidate
                    found = True
                    break
            if found:
                break
        if not found:
            raise Exception("road_lrs: no graph node found within 30 rings of anchor — check lrs_anchor_coordinates and road data")

    logger.info(f"road_lrs: anchor={anchor_cell} resolution={resolution} nodes={G.number_of_nodes()} edges={G.number_of_edges()}")

    distances = nx.single_source_dijkstra_path_length(G, anchor_cell, weight='weight')

    annotated = []
    for feature, (start_cell, end_cell) in zip(features, segment_nodes):
        props = dict(feature.get('properties') or {})
        props['m_start'] = distances.get(start_cell)
        props['m_end'] = distances.get(end_cell)
        props['lrs_anchor'] = anchor_cell
        annotated.append({
            'type': 'Feature',
            'geometry': feature['geometry'],
            'properties': props
        })

    out_path = versioning.atlas_path(config, 'layers') / out_layer / f"{out_layer}.geojson"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fc = geojson.FeatureCollection(annotated)
    with open(out_path, 'w') as f:
        geojson.dump(fc, f)

    logger.info(f"road_lrs: wrote {len(annotated)} features to {out_path}")
    return str(out_path)


def road_lrs_markers(config, asset_name):
    """
    Eddy: generate a point layer of distance markers at regular intervals along a road LRS layer.

    Reads an roads_lrs-style layer (with m_start/m_end per segment), then for each marker
    distance d uses shapely interpolation to place a point at the exact position on each
    segment that spans that distance.
    """
    asset_config = config['assets'][asset_name].get('config', config['assets'][asset_name])
    in_layer = asset_config.get('in_layer', 'roads_lrs')
    out_layer = asset_config.get('out_layer', 'lrs_markers')
    interval = asset_config.get('marker_interval_m', 1000)
    label_template = asset_config.get('label_template', '{d_km:.0f} km')
    marker_bbox = asset_config.get('marker_bbox')  # [west, south, east, north] or None
    road_names = asset_config.get('road_names')    # list of road names to include, or null for all

    layer_data = dataswale.layer_as_featurecollection(config, in_layer)
    if not layer_data or 'features' not in layer_data:
        raise Exception(f"road_lrs_markers: could not load layer '{in_layer}'")

    features = layer_data['features']
    if road_names:
        # Prefix match: "Thomas" matches "Thomas Road", "Thomas Drive", etc.
        road_names_lower = [p.lower() for p in road_names]
        def _name_matches(feature_name):
            if not feature_name:
                return False
            n = feature_name.lower()
            for prefix in road_names_lower:
                if n == prefix or n.startswith(prefix + ' '):
                    return True
            return False
        all_names = sorted({f.get('properties', {}).get('name') for f in features if f.get('properties', {}).get('name')})
        features = [f for f in features if _name_matches(f.get('properties', {}).get('name'))]
        if not features:
            raise Exception(
                f"road_lrs_markers: road_names filter matched 0 segments. "
                f"Available road names in '{in_layer}': {all_names}"
            )
        logger.info(f"road_lrs_markers: road_names filter active, {len(features)} segments match {road_names}")

    # Determine maximum reachable distance
    max_d = 0.0
    for feature in features:
        props = feature.get('properties') or {}
        m_start = props.get('m_start')
        m_end = props.get('m_end')
        if m_start is not None:
            max_d = max(max_d, m_start)
        if m_end is not None:
            max_d = max(max_d, m_end)

    if max_d == 0:
        raise Exception(f"road_lrs_markers: no reachable distances found in '{in_layer}' — run road_lrs first")

    marker_distances = range(0, int(max_d) + interval, interval)
    logger.info(f"road_lrs_markers: {len(features)} segments, max_d={max_d:.0f}m, {len(marker_distances)} marker distances")

    points = []
    for d in marker_distances:
        for feature in features:
            props = feature.get('properties') or {}
            m_start = props.get('m_start')
            m_end = props.get('m_end')
            geom = feature.get('geometry')

            if m_start is None or m_end is None or not geom:
                continue
            if m_start == m_end:
                continue

            lo, hi = min(m_start, m_end), max(m_start, m_end)
            if not (lo <= d <= hi):
                continue

            if m_start <= m_end:
                frac = (d - m_start) / (m_end - m_start)
            else:
                frac = 1.0 - (d - m_end) / (m_start - m_end)

            pt = shape(geom).interpolate(frac, normalized=True)

            if marker_bbox:
                west, south, east, north = marker_bbox
                lng, lat = pt.x, pt.y
                if not (west <= lng <= east and south <= lat <= north):
                    continue

            label = label_template.format(d_m=int(d), d_km=d / 1000, d_mi=d / 1609.34)
            points.append({
                'type': 'Feature',
                'geometry': {'type': 'Point', 'coordinates': [pt.x, pt.y]},
                'properties': {
                    'name': label,
                    'd_m': int(d),
                    'd_km': round(d / 1000, 3),
                }
            })

    out_path = versioning.atlas_path(config, 'layers') / out_layer / f"{out_layer}.geojson"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fc = geojson.FeatureCollection(points)
    with open(out_path, 'w') as f:
        geojson.dump(fc, f)

    logger.info(f"road_lrs_markers: wrote {len(points)} markers to {out_path}")
    return str(out_path)


def _auto_min_zoom(bounds: dict, configured_min: int) -> int:
    """Compute a safe minimum zoom level based on the DEM's extent.

    At zoom levels where the DEM is a tiny fraction of a tile, gdal2tiles
    fills most of the tile with zeros → -10,000m terrain artefacts around
    the atlas. We want tiles where the DEM covers at least ~25% of a tile.

    Formula: at zoom z, a tile covers 360/2^z degrees longitude.
    Require: dem_width / tile_width >= 0.25  →  z >= log2(90 / dem_width)
    """
    import math
    dem_width = abs(bounds['east'] - bounds['west'])
    safe_min = math.ceil(math.log2(90.0 / dem_width))
    result = max(configured_min, safe_min)
    if result > configured_min:
        logger.info(f"_auto_min_zoom: raising min_zoom {configured_min}→{result} for {dem_width:.4f}° wide DEM")
    return result


def _dem_to_terrain_rgb(in_path: Path, out_path: Path):
    """Encode a DEM GeoTIFF to Mapbox terrain-RGB format.

    Mapbox encoding: height = -10000 + (R*65536 + G*256 + B) * 0.1
    Valid elevation range: -10000m to +6553.4m (covers all Earth terrain).

    NoData pixels are replaced with 0m (sea level) before encoding.
    Without this, NoData encodes to (0,0,0) = -10,000m, producing
    a pit around the atlas area and tall walls at DEM boundaries.
    """
    import rasterio

    with rasterio.open(str(in_path)) as src:
        elevation = src.read(1).astype(np.float64)
        profile = src.profile.copy()
        nodata = src.nodata

    # Replace nodata and non-finite values with the mean of valid pixels.
    # Using 0m (sea level) creates a severe visual pit in mountainous regions;
    # mean elevation blends much better with the actual terrain at boundaries.
    #
    # COP30 quirk: coverage gaps are sometimes written as 0.0 with no declared
    # nodata value. Treat exact zeros as nodata when nodata is None — safe
    # because genuine 0m elevation won't appear in fire atlas (mountainous) regions.
    if nodata is not None:
        nodata_mask = (elevation == nodata) | ~np.isfinite(elevation)
    else:
        nodata_mask = (elevation == 0.0) | ~np.isfinite(elevation)

    valid = elevation[~nodata_mask]
    fill_value = float(np.mean(valid)) if len(valid) > 0 else 100.0
    logger.info(f"terrain-RGB: fill value = {fill_value:.1f}m (mean of {len(valid)} valid pixels, {nodata_mask.sum()} nodata)")

    elevation = np.where(nodata_mask, fill_value, elevation)
    elevation = np.nan_to_num(elevation, nan=fill_value, posinf=8848.0, neginf=fill_value)

    # Encode to integer before bit-shifting to avoid float precision errors
    encoded = np.round((elevation + 10000) / 0.1).astype(np.uint32)
    encoded = np.clip(encoded, 0, 16777215)

    r = (encoded // 65536).astype(np.uint8)
    g = ((encoded % 65536) // 256).astype(np.uint8)
    b = (encoded % 256).astype(np.uint8)

    profile.update(count=3, dtype='uint8', driver='GTiff')
    with rasterio.open(str(out_path), 'w', **profile) as dst:
        dst.write(r, 1)
        dst.write(g, 2)
        dst.write(b, 3)


def _tile_dir_to_pmtiles(tile_dir: Path, out_path: Path, min_zoom: int, max_zoom: int, bounds: list):
    """Package an XYZ tile directory (from gdal2tiles --xyz) into a PMTiles archive.

    bounds: [min_lon, min_lat, max_lon, max_lat]
    """
    from pmtiles.writer import Writer
    from pmtiles.tile import TileType, Compression, zxy_to_tileid

    center_lon = (bounds['west'] + bounds['east']) / 2
    center_lat = (bounds['south'] + bounds['north']) / 2

    header = {
        "tile_type": TileType.PNG,
        "tile_compression": Compression.NONE,
        "min_zoom": min_zoom,
        "max_zoom": max_zoom,
        "min_lon_e7": int(bounds['west'] * 1e7),
        "min_lat_e7": int(bounds['south'] * 1e7),
        "max_lon_e7": int(bounds['east'] * 1e7),
        "max_lat_e7": int(bounds['north'] * 1e7),
        "center_zoom": (min_zoom + max_zoom) // 2,
        "center_lon_e7": int(center_lon * 1e7),
        "center_lat_e7": int(center_lat * 1e7),
    }

    with open(out_path, 'wb') as f:
        writer = Writer(f)
        for z in range(min_zoom, max_zoom + 1):
            z_dir = tile_dir / str(z)
            if not z_dir.exists():
                continue
            for x_dir in sorted(z_dir.iterdir()):
                if not x_dir.is_dir():
                    continue
                x = int(x_dir.name)
                for tile_file in sorted(x_dir.glob('*.png')):
                    y = int(tile_file.stem)
                    writer.write_tile(zxy_to_tileid(z, x, y), tile_file.read_bytes())
        writer.finalize(header, metadata={})


def hillshade_to_pmtiles(config: Dict[str, Any], eddy_name: str):
    """Convert a hillshade (or any RGB raster) layer to a PMTiles archive.

    Produces a raster-type PMTiles file suitable for use as a webmap basemap
    via the MapLibre pmtiles:// protocol.

    Per-atlas config keys:
        in_layer:  source raster layer (e.g. "basemap" or "lidar_basemap")
        out_layer: output layer name (e.g. "hillshade_tiles")
    Shared config keys:
        min_zoom (default 8), max_zoom (default 18)
    """
    import tempfile, shutil

    eddy = config['assets'][eddy_name]
    in_layer = eddy['in_layer']
    out_layer = eddy['out_layer']
    min_zoom = eddy['config'].get('min_zoom', 8)
    max_zoom = eddy['config'].get('max_zoom', 18)
    bounds = config['dataswale']['bbox']
    min_zoom = _auto_min_zoom(bounds, min_zoom)

    in_path = versioning.atlas_path(config, 'layers') / in_layer / f'{in_layer}.tiff'
    out_dir = versioning.atlas_path(config, 'layers') / out_layer
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f'{out_layer}.pmtiles'

    logger.info(f"hillshade_to_pmtiles: {in_path} -> {out_path} (z{min_zoom}-z{max_zoom})")

    with tempfile.TemporaryDirectory() as tmp:
        tile_dir = Path(tmp) / 'tiles'
        result = subprocess.run(
            ['gdal2tiles.py', '--xyz', '--resampling=bilinear',
             f'--zoom={min_zoom}-{max_zoom}', '--processes=4',
             str(in_path), str(tile_dir)],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"gdal2tiles failed:\n{result.stderr}")
        logger.info(f"gdal2tiles complete: {result.stdout[-500:] if result.stdout else '(no output)'}")

        _tile_dir_to_pmtiles(tile_dir, out_path, min_zoom, max_zoom, bounds)

    logger.info(f"hillshade_to_pmtiles complete: {out_path}")
    return out_path


def terrain_rgb_to_pmtiles(config: Dict[str, Any], eddy_name: str):
    """Convert a DEM elevation layer to terrain-RGB PMTiles for 3D terrain rendering.

    Encodes raw elevation values (metres) into Mapbox terrain-RGB format, then
    tiles and packages into PMTiles. The result is consumed by MapLibre as a
    raster-dem source for both the 3D view and the webmap terrain toggle.

    Per-atlas config keys:
        in_layer:  elevation layer (e.g. "elevation")
        out_layer: output layer name (e.g. "terrain_rgb_tiles")
    Shared config keys:
        min_zoom (default 8), max_zoom (default 14)
    """
    import tempfile, rasterio

    eddy = config['assets'][eddy_name]
    in_layer = eddy['in_layer']
    out_layer = eddy['out_layer']
    min_zoom = eddy['config'].get('min_zoom', 8)
    max_zoom = eddy['config'].get('max_zoom', 14)
    bounds = config['dataswale']['bbox']
    min_zoom = _auto_min_zoom(bounds, min_zoom)

    in_path = versioning.atlas_path(config, 'layers') / in_layer / f'{in_layer}.tiff'
    out_dir = versioning.atlas_path(config, 'layers') / out_layer
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f'{out_layer}.pmtiles'

    logger.info(f"terrain_rgb_to_pmtiles: {in_path} -> {out_path} (z{min_zoom}-z{max_zoom})")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        terrain_rgb_path = tmp_path / 'terrain_rgb.tiff'

        _dem_to_terrain_rgb(in_path, terrain_rgb_path)
        logger.info(f"Terrain-RGB encoding complete: {terrain_rgb_path}")

        tile_dir = tmp_path / 'tiles'
        result = subprocess.run(
            ['gdal2tiles.py', '--xyz', '--resampling=bilinear',
             f'--zoom={min_zoom}-{max_zoom}', '--processes=4',
             str(terrain_rgb_path), str(tile_dir)],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"gdal2tiles failed:\n{result.stderr}")
        logger.info(f"gdal2tiles complete: {result.stdout[-500:] if result.stdout else '(no output)'}")

        _tile_dir_to_pmtiles(tile_dir, out_path, min_zoom, max_zoom, bounds)

    logger.info(f"terrain_rgb_to_pmtiles complete: {out_path}")
    return out_path


def terrain_rgb_tiff(config: Dict[str, Any], eddy_name: str) -> Path:
    """Convert a DEM to Mapbox terrain-RGB GeoTIFF saved on disk.

    Unlike terrain_rgb_tiles (which discards the RGB TIFF after tiling),
    this eddy writes it to the layers directory so tiff_to_cog can consume it.
    Use when serving 3D terrain as a COG from S3 instead of as PMTiles.

    Per-atlas config keys:
        in_layer:  elevation layer (e.g. "terrain_dem")
        out_layer: output layer name (e.g. "terrain_rgb_tiles")
    """
    import rasterio  # noqa: F401 — imported by _dem_to_terrain_rgb

    eddy = config['assets'][eddy_name]
    in_layer = eddy['in_layer']
    out_layer = eddy.get('out_layer', eddy_name)

    in_path = versioning.atlas_path(config, 'layers') / in_layer / f'{in_layer}.tiff'
    out_dir = versioning.atlas_path(config, 'layers') / out_layer
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f'{out_layer}.tiff'

    logger.info(f"terrain_rgb_tiff: {in_path} -> {out_path}")
    _dem_to_terrain_rgb(in_path, out_path)
    logger.info(f"terrain_rgb_tiff complete: {out_path}")
    return out_path


def tiff_to_cog(config: Dict[str, Any], eddy_name: str) -> Path:
    """Convert a GeoTIFF layer to a Cloud-Optimized GeoTIFF (COG).

    Produces a .cog.tif suitable for serving via HTTP range requests from S3,
    consumed by maplibre-cog-protocol in the webmap/3D view and GDAL /vsicurl/
    in QGIS PDF generation. Run before s3_upload.

    Per-atlas config keys:
        in_layer:      source raster layer name
        out_layer:     output layer name (defaults to in_layer)
        log_transform: if true, apply log1p to pixel values before COG conversion
                       (stopgap for skewed distributions; see issue #97 for setColorFunction approach)
    """
    eddy = config['assets'][eddy_name]
    in_layer = eddy['in_layer']
    out_layer = eddy.get('out_layer', in_layer)

    layers_path = versioning.atlas_path(config, 'layers')
    in_path = layers_path / in_layer / f'{in_layer}.tiff'
    out_dir = layers_path / out_layer
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f'{out_layer}.cog.tif'

    logger.info(f"tiff_to_cog: {in_path} -> {out_path}")

    in_path_for_warp = in_path
    temp_path = None

    if eddy.get('log_transform'):
        temp_path = out_dir / f'{out_layer}_log_tmp.tif'
        with rasterio.open(in_path) as src:
            profile = src.profile.copy()
            data = src.read(1, masked=True)
            log_data = np.log1p(data.astype(np.float32))
            profile.update(dtype='float32', nodata=-9999.0)
            with rasterio.open(temp_path, 'w', **profile) as dst:
                dst.write(log_data.filled(-9999.0), 1)
        in_path_for_warp = temp_path
        logger.info(f"tiff_to_cog: log1p transform applied, temp={temp_path}")

    # gdalwarp reprojects to EPSG:3857 (required by maplibre-cog-protocol) and
    # produces a COG in one pass. -r bilinear is appropriate for continuous rasters.
    result = subprocess.run(
        ['gdalwarp', '-t_srs', 'EPSG:3857', '-r', 'bilinear',
         '-of', 'COG',
         '-co', 'COMPRESS=DEFLATE',
         '-co', 'BLOCKSIZE=512',
         '-co', 'OVERVIEW_RESAMPLING=BILINEAR',
         str(in_path_for_warp), str(out_path)],
        capture_output=True, text=True
    )

    if temp_path:
        temp_path.unlink()

    if result.returncode != 0:
        raise RuntimeError(f"gdalwarp COG failed:\n{result.stderr}")

    with rasterio.open(out_path) as ds:
        data = ds.read(1, masked=True)
        stats = {
            'min': float(data.min()),
            'max': float(data.max()),
            'nodata': ds.nodata,
            'log_transformed': bool(eddy.get('log_transform', False)),
        }
    (out_dir / 'stats.json').write_text(json.dumps(stats, indent=2))
    logger.info(f"tiff_to_cog complete: {out_path}, stats={stats}")
    return out_path


def fuel_mass_from_landfire(config: Dict[str, Any], asset_name: str):
    """
    Annotates burns_index H3 cells with fuel_mass (tons/acre) derived from LANDFIRE EVC.
    Downloads a single EVC raster patch covering the atlas bbox, samples at each cell centroid.
    """
    import rasterio
    import requests
    import tempfile

    ac = config['assets'][asset_name].get('config', config['assets'][asset_name])
    bbox = config['dataswale']['bbox']
    landfire_layer = ac.get('landfire_layer', 'Landfire_LF2024/LF2024_EVC_CONUS')

    # Load burns_index layer
    burns_index_path = versioning.atlas_path(config, 'layers') / 'burns_index' / 'burns_index.geojson'
    with open(burns_index_path) as f:
        fc = geojson.load(f)

    features = fc.get('features', [])
    if not features:
        logger.warning(f"fuel_mass_from_landfire: burns_index has no features, nothing to annotate")
        return burns_index_path

    # Build LANDFIRE EVC export URL
    url = (
        f"https://lfps.usgs.gov/arcgis/rest/services/{landfire_layer}/ImageServer/exportImage"
        f"?bbox={bbox['west']},{bbox['south']},{bbox['east']},{bbox['north']}"
        f"&bboxSR=4326&imageSR=4326&size=2048,2048&format=tiff"
        f"&pixelType=S16&noDataInterpretation=esriNoDataMatchAny&f=image"
    )
    logger.info(f"fuel_mass_from_landfire: fetching EVC raster from {url}")

    # Load EVC → fuel_mass lookup table
    config_dir = Path(__file__).parent.parent / 'configuration'
    lookup_path = config_dir / 'landfire_evc_fuel_loads.json'
    with open(lookup_path) as f:
        evc_lookup = json.load(f)
    default_fuel = float(evc_lookup.get('_default', 2.0))
    nodata_value = 32767  # S16 nodata for LANDFIRE

    with tempfile.TemporaryDirectory() as tmp:
        tmp_tiff = Path(tmp) / 'evc.tiff'

        response = requests.get(url, timeout=120)
        response.raise_for_status()
        with open(tmp_tiff, 'wb') as f:
            f.write(response.content)
        logger.info(f"fuel_mass_from_landfire: downloaded EVC raster ({tmp_tiff.stat().st_size // 1024}KB)")

        annotated = 0
        nodata_count = 0
        out_of_bounds_count = 0

        with rasterio.open(str(tmp_tiff)) as dataset:
            raster_data = dataset.read(1)
            transform = dataset.transform
            raster_height, raster_width = raster_data.shape

            for feature in features:
                h3_index = feature.get('properties', {}).get('h3_index')
                if not h3_index:
                    logger.warning(f"fuel_mass_from_landfire: feature missing h3_index, skipping")
                    continue

                lat, lng = h3.cell_to_latlng(h3_index)

                try:
                    row, col = dataset.index(lng, lat)
                except Exception:
                    # centroid outside raster bounds
                    feature['properties']['fuel_mass'] = 0.0
                    out_of_bounds_count += 1
                    continue

                if row < 0 or row >= raster_height or col < 0 or col >= raster_width:
                    feature['properties']['fuel_mass'] = 0.0
                    out_of_bounds_count += 1
                    continue

                pixel_value = int(raster_data[row, col])

                if pixel_value == nodata_value:
                    feature['properties']['fuel_mass'] = 0.0
                    nodata_count += 1
                    continue

                fuel_load = float(evc_lookup.get(str(pixel_value), default_fuel))
                feature['properties']['fuel_mass'] = fuel_load
                annotated += 1

    logger.info(
        f"fuel_mass_from_landfire: annotated {annotated} cells, "
        f"{nodata_count} nodata, {out_of_bounds_count} out-of-bounds"
    )

    # Write updated burns_index back in place
    burns_index_path.parent.mkdir(parents=True, exist_ok=True)
    with open(burns_index_path, 'w') as f:
        geojson.dump(fc, f)

    return burns_index_path


def _h3_grid_distance_safe(cell_a, cell_b):
    """H3 grid distance handling resolution mismatches by upsampling lower-res cell."""
    res_a = h3.get_resolution(cell_a)
    res_b = h3.get_resolution(cell_b)
    if res_a == res_b:
        return h3.grid_distance(cell_a, cell_b)
    elif res_a < res_b:
        cell_a = h3.cell_to_center_child(cell_a, res_b)
    else:
        cell_b = h3.cell_to_center_child(cell_b, res_a)
    return h3.grid_distance(cell_a, cell_b)


def _get_cell_path_distances(burns_features, target_features, distance_name):
    """
    For each burn cell, find minimum H3 grid distance to any cell in target_features.
    Attaches result as burn['properties'][distance_name].
    """
    target_cells = [f['properties']['h3_index'] for f in target_features
                    if f.get('properties', {}).get('h3_index')]
    if not target_cells:
        for burn in burns_features:
            burn['properties'][distance_name] = 0
        return burns_features
    for burn in burns_features:
        burn_cell = burn['properties'].get('h3_index')
        if not burn_cell:
            burn['properties'][distance_name] = 0
            continue
        min_dist = min(_h3_grid_distance_safe(burn_cell, t) for t in target_cells)
        burn['properties'][distance_name] = min_dist
    return burns_features


def _cell_yield(burn_props, treatment_key, tc, biomass_price, biochar_price):
    """Compute per-treatment metrics for a single burn cell."""
    fuel_mass = burn_props.get('fuel_mass', 1.0) or 1.0
    water_distance = burn_props.get('creeks_distance', 0)
    drag_distance = burn_props.get('roads_distance', 0)
    processing_rate = (tc['production_rate']
                       + water_distance * tc['water_distance_cost']
                       + drag_distance * tc['drag_distance_cost'])
    processed = fuel_mass * tc['biomass_rate']
    biochar = tc['biochar_rate'] * processed
    cost_rate = tc['cost_rate'] / 8.0  # daily -> hourly
    budget_cost = processing_rate * cost_rate
    risk_reduced = fuel_mass * tc['risk_reduction_rate']
    air_pollution = tc['air_pollution'] * processed
    biomass_sale = biomass_price * processed
    biochar_sale = biochar_price * biochar
    return {
        'budget_cost': budget_cost,
        'biomass_extracted': processed,
        'biochar_extracted': biochar,
        'air_pollution': air_pollution,
        'risk_reduced': risk_reduced,
        'biomass_sale': biomass_sale,
        'biochar_sale': biochar_sale,
    }


def biochar_simulation(config, asset_name):
    """
    Biochar logistics simulation eddy.
    Reads burns_index, roads_index, creeks_index, processing_sites_index.
    For each burn cell, computes H3 grid distances to infrastructure and evaluates
    all treatment options. Writes results to the processing_sites layer directory.
    """
    ac = config['assets'][asset_name].get('config', config['assets'][asset_name])
    biomass_price = ac.get('biomass_price', 0.001)
    biochar_price = ac.get('biochar_price', 0.01)

    # Load treatments file — path relative to atlas app root
    app_root = versioning.atlas_path(config, version='app')
    treatments_path = Path(app_root) / ac['treatments_file']
    with open(treatments_path) as f:
        treatments_raw = json.load(f)
    treatments = {k: v for k, v in treatments_raw.items() if not k.startswith('_')}

    # Load layers
    def load_layer(name):
        path = versioning.atlas_path(config, 'layers') / name / f'{name}.geojson'
        with open(path) as f:
            fc = json.load(f)
        return fc.get('features', [])

    burns = load_layer('burns_index')
    roads = load_layer('roads_index')
    creeks = load_layer('creeks_index')
    sites = load_layer('processing_sites_index')

    if not burns:
        logger.warning(f"biochar_simulation: burns_index is empty, nothing to simulate")
        return

    # Compute distances from each burn cell to nearest infrastructure
    logger.info(f"biochar_simulation: computing distances for {len(burns)} burn cells")
    burns = _get_cell_path_distances(burns, roads, 'roads_distance')
    burns = _get_cell_path_distances(burns, creeks, 'creeks_distance')
    burns = _get_cell_path_distances(burns, sites, 'sites_distance')

    # Evaluate all treatments per burn cell; accumulate per-treatment totals
    metric_keys = ['budget_cost', 'biomass_extracted', 'biochar_extracted',
                   'air_pollution', 'risk_reduced', 'biomass_sale', 'biochar_sale']
    totals = {t: {k: 0.0 for k in metric_keys} for t in treatments}

    for burn in burns:
        burn['properties']['treatments'] = {}
        for t_key, tc in treatments.items():
            result = _cell_yield(burn['properties'], t_key, tc, biomass_price, biochar_price)
            burn['properties']['treatments'][t_key] = result
            for k in metric_keys:
                totals[t_key][k] += result[k]

    # Write outputs into the processing_sites layer directory
    sites_layer_dir = versioning.atlas_path(config, 'layers') / 'processing_sites'
    sites_layer_dir.mkdir(parents=True, exist_ok=True)

    # burns_simulation.geojson — burn hex cells annotated with per-cell metrics (sidecar file)
    sim_path = sites_layer_dir / 'burns_simulation.geojson'
    with open(sim_path, 'w') as f:
        json.dump({'type': 'FeatureCollection', 'features': burns}, f)
    logger.info(f"biochar_simulation: wrote {sim_path}")

    # biochar_summary.csv — per-treatment totals
    import csv
    csv_path = sites_layer_dir / 'biochar_summary.csv'
    with open(csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['treatment'] + metric_keys)
        writer.writeheader()
        for t_key, vals in totals.items():
            writer.writerow({'treatment': t_key, **vals})
    logger.info(f"biochar_simulation: wrote {csv_path}")

    # Update processing_sites GeoJSON — annotate each site point with totals
    sites_layer_path = sites_layer_dir / 'processing_sites.geojson'
    try:
        with open(sites_layer_path) as f:
            sites_fc = json.load(f)
        for feature in sites_fc.get('features', []):
            feature['properties']['simulation_totals'] = totals
        with open(sites_layer_path, 'w') as f:
            json.dump(sites_fc, f)
        logger.info(f"biochar_simulation: annotated processing_sites with simulation totals")
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning("biochar_simulation: processing_sites layer not found or empty, skipping annotation")


def ssurgo_enrich(config: Dict[str, Any], asset_name: str):
    """
    Enrich a point layer with SSURGO soil properties via NRCS Soil Data Access REST API.

    Two-phase: per-point spatial query to resolve mukey, then one batched SQL query
    for component/horizon properties across all unique mukeys. Features that fail
    lookup get ssurgo_error instead of soil fields.

    Config: in_layer (required), out_layer (optional, defaults to in_layer)
    """
    SDA_URL = 'https://sdmdataaccess.nrcs.usda.gov/tabular/post.rest'

    asset_config = config['assets'][asset_name].get('config', config['assets'][asset_name])
    in_layer = asset_config['in_layer']
    out_layer = asset_config.get('out_layer', in_layer)

    layer_data = dataswale.layer_as_featurecollection(config, in_layer)
    if not layer_data or not layer_data.get('features'):
        raise Exception(f"ssurgo_enrich: could not load layer '{in_layer}'")
    features = layer_data['features']
    logger.info(f"ssurgo_enrich: looking up {len(features)} points")

    def _sda_post(sql, timeout):
        body = {'query': sql, 'format': 'json+columnname'}
        resp = requests.post(SDA_URL, json=body, timeout=timeout)
        if not resp.ok:
            raise Exception(
                f"HTTP {resp.status_code}\n"
                f"  request body: {json.dumps(body)}\n"
                f"  response: {resp.text[:500]}"
            )
        return resp.json().get('Table', [])

    # Phase 1: per-point mukey lookup
    mukeys = {}
    for i, feature in enumerate(features):
        geom = feature.get('geometry', {})
        geom_type = geom.get('type')
        if geom_type == 'Point':
            coords = geom['coordinates']
            lon, lat = coords[0], coords[1]
        elif geom_type in ('Polygon', 'MultiPolygon'):
            centroid = shape(geom).centroid
            lon, lat = centroid.x, centroid.y
        else:
            mukeys[i] = None
            continue
        if lon is None or lat is None:
            mukeys[i] = None
            continue
        sql = f"SELECT mukey FROM SDA_Get_Mukey_from_intersection_with_WktWgs84('POINT({float(lon):.8f} {float(lat):.8f})')"
        try:
            rows = _sda_post(sql, timeout=15)
            mukeys[i] = rows[1][0] if len(rows) > 1 else None
        except Exception as e:
            logger.warning(f"ssurgo_enrich: mukey lookup failed for feature {i}: {e}")
            mukeys[i] = None

    # Phase 2: batch properties query for all unique mukeys
    unique_mukeys = [m for m in set(mukeys.values()) if m is not None]
    props_by_mukey = {}
    if unique_mukeys:
        mukey_list = ','.join(f"'{m}'" for m in unique_mukeys)
        sql = f"""
            SELECT co.mukey, co.compname, co.drainagecl,
                   ch.ph1to1h2o_r, ch.om_r, ch.sandtotal_r, ch.silttotal_r, ch.claytotal_r
            FROM component co
            INNER JOIN chorizon ch ON co.cokey = ch.cokey
            WHERE co.mukey IN ({mukey_list})
            AND co.majcompflag = 'Yes'
            AND ch.hzdept_r = (SELECT MIN(h2.hzdept_r) FROM chorizon h2 WHERE h2.cokey = co.cokey)
            ORDER BY co.mukey, co.comppct_r DESC
        """
        try:
            rows = _sda_post(sql, timeout=30)
            if len(rows) > 1:
                headers = rows[0]
                seen = set()
                for row in rows[1:]:
                    row_dict = dict(zip(headers, row))
                    mk = row_dict.get('mukey')
                    if mk and mk not in seen:
                        seen.add(mk)
                        props_by_mukey[mk] = row_dict
        except Exception as e:
            logger.warning(f"ssurgo_enrich: batch properties query failed: {e}")

    # Phase 3: annotate features
    enriched = []
    for i, feature in enumerate(features):
        props = dict(feature.get('properties') or {})
        mukey = mukeys.get(i)
        if mukey is None:
            props['ssurgo_error'] = 'no mukey found'
        elif mukey not in props_by_mukey:
            props['ssurgo_mukey'] = mukey
            props['ssurgo_error'] = 'no component data'
        else:
            soil = props_by_mukey[mukey]
            props['ssurgo_mukey'] = mukey
            props['ssurgo_compname'] = soil.get('compname')
            props['ssurgo_drainagecl'] = soil.get('drainagecl')
            props['ssurgo_ph'] = soil.get('ph1to1h2o_r')
            props['ssurgo_om'] = soil.get('om_r')
            props['ssurgo_sand'] = soil.get('sandtotal_r')
            props['ssurgo_silt'] = soil.get('silttotal_r')
            props['ssurgo_clay'] = soil.get('claytotal_r')
        enriched.append({'type': 'Feature', 'geometry': feature['geometry'], 'properties': props})

    out_path = versioning.atlas_path(config, 'layers') / out_layer / f"{out_layer}.geojson"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fc = geojson.FeatureCollection(enriched)
    with open(out_path, 'w') as f:
        geojson.dump(fc, f)
    logger.info(f"ssurgo_enrich: wrote {len(enriched)} features to {out_path}")
    return str(out_path)


def merge_h3(config: Dict[str, Any], asset_name: str):
    """
    Enrich a vector layer's features with properties from one or more H3 polygon layers.

    For each feature, computes its centroid, maps it to an H3 cell index at each
    source layer's resolution, and copies matching cell properties onto the feature.
    Features with no H3 match pass through unchanged.

    Config:
      h3_layers: list of H3 layer names to source properties from (required)
      in_layer: vector layer to enrich (required)
      out_layer: output layer name, defaults to in_layer
      merge_operation: "include" or "exclude" (default "exclude")
      properties: list of property names used by merge_operation (default [])
    """
    asset_config = config['assets'][asset_name].get('config', config['assets'][asset_name])
    h3_layer_names = asset_config['h3_layers']
    in_layer = asset_config['in_layer']
    out_layer = asset_config.get('out_layer', in_layer)
    merge_operation = asset_config.get('merge_operation', 'exclude')
    properties_set = set(asset_config.get('properties', []))

    def _filter_props(props):
        if merge_operation == 'include':
            return {k: v for k, v in props.items() if k in properties_set}
        return {k: v for k, v in props.items() if k not in properties_set}

    # Build per-layer lookup: {h3_index: filtered_props}
    h3_lookups = []
    for layer_name in h3_layer_names:
        layer_data = dataswale.layer_as_featurecollection(config, layer_name)
        if not layer_data or not layer_data.get('features'):
            logger.warning(f"merge_h3: H3 layer '{layer_name}' is empty or missing, skipping")
            continue
        h3_features = layer_data['features']
        first_idx = (h3_features[0].get('properties') or {}).get('h3_index')
        if not first_idx:
            logger.warning(f"merge_h3: H3 layer '{layer_name}' has no h3_index property, skipping")
            continue
        resolution = h3.get_resolution(first_idx)
        lookup = {
            props['h3_index']: _filter_props(props)
            for f in h3_features
            if (props := (f.get('properties') or {})) and props.get('h3_index')
        }
        h3_lookups.append((resolution, lookup))
        logger.info(f"merge_h3: loaded {len(lookup)} cells from '{layer_name}' (r{resolution})")

    target_data = dataswale.layer_as_featurecollection(config, in_layer)
    if not target_data or not target_data.get('features'):
        raise Exception(f"merge_h3: could not load target layer '{in_layer}'")
    features = target_data['features']
    logger.info(f"merge_h3: enriching {len(features)} features from '{in_layer}'")

    enriched = []
    n_enriched = 0
    for feature in features:
        props = dict(feature.get('properties') or {})
        geom = feature.get('geometry')
        got_match = False
        if geom:
            centroid = shape(geom).centroid
            lat, lng = centroid.y, centroid.x
            for resolution, lookup in h3_lookups:
                cell = h3.latlng_to_cell(lat, lng, resolution)
                if cell in lookup:
                    props.update(lookup[cell])
                    got_match = True
        if got_match:
            n_enriched += 1
        enriched.append({'type': 'Feature', 'geometry': geom, 'properties': props})

    out_path = versioning.atlas_path(config, 'layers') / out_layer / f"{out_layer}.geojson"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fc = geojson.FeatureCollection(enriched)
    with open(out_path, 'w') as f:
        geojson.dump(fc, f)
    logger.info(f"merge_h3: wrote {len(enriched)} features to {out_path} ({n_enriched} enriched)")
    return str(out_path)


def dst_match_point(soil_ph: float, soil_om: float, goal: str, biochar_records: list,
                    target_ph: float = None) -> list:
    """Simplified Phillips 2020 pH-matching suitability calculation (point mode).

    Implements the pH liming path only. Other goals return a placeholder list.
    Calibration: median biochar at 6 t/ac predicts ΔpH ≈ +0.5 on silt loam.

    Returns top-5 biochars sorted by suitability_score descending.
    Each dict: biochar_id, name, feedstock, suitability_score (0-1),
                recommended_rate_t_ac, predicted_dpH.
    """
    if goal != 'raise_ph':
        return [{'placeholder': True, 'message': f"Goal '{goal}' requires full Phillips 2020 — coming in production."}]

    if soil_ph is None:
        return []

    _target_ph = target_ph if target_ph is not None else soil_ph + 1.0
    delta_needed = max(0.0, _target_ph - soil_ph)

    # k calibrated so median liming_potential (≈0.5) at 6 t/ac → ΔpH ≈ +0.5
    K = 0.1667  # 0.5 / (0.5 * 6)

    results = []
    liming_potentials = []
    for bc in biochar_records:
        ph_val = bc.get('biochar_ph')
        ash_val = bc.get('ash_content_pct')
        if ph_val is None or ash_val is None:
            liming_potentials.append(None)
            continue
        # Normalize pH to 0-1 (biochar pH typically 4-12)
        ph_norm = max(0.0, min(1.0, (float(ph_val) - 4.0) / 8.0))
        ash_norm = max(0.0, min(1.0, float(ash_val) / 60.0))
        liming_potentials.append(0.7 * ph_norm + 0.3 * ash_norm)

    # Normalize liming_potential across the database (0–1 relative to max)
    valid = [v for v in liming_potentials if v is not None]
    lp_max = max(valid) if valid else 1.0
    lp_min = min(valid) if valid else 0.0
    lp_range = lp_max - lp_min if lp_max > lp_min else 1.0

    for i, bc in enumerate(biochar_records):
        lp_raw = liming_potentials[i]
        if lp_raw is None:
            continue
        lp = (lp_raw - lp_min) / lp_range  # normalized 0-1

        if lp < 0.01:
            continue

        # Solve for rate that achieves target ΔpH; clamp to [3, 12] t/ac
        if delta_needed > 0 and K * lp > 0:
            rate = delta_needed / (K * lp)
        else:
            rate = 3.0
        rate = max(3.0, min(12.0, rate))

        predicted_dpH = K * lp * rate
        # Suitability: high liming potential + low rate (more economical) = better score
        rate_score = 1.0 - (rate - 3.0) / 9.0  # 1.0 at min rate, 0.0 at max
        suitability = 0.6 * lp + 0.4 * rate_score

        results.append({
            'biochar_id': bc.get('biochar_id', i + 1),
            'name': bc.get('sample_id') or bc.get('name') or f"Biochar-{bc.get('biochar_id', i+1)}",
            'feedstock': bc.get('feedstock') or bc.get('feedstock_type'),
            'production_temp_c': bc.get('production_temp_c'),
            'biochar_ph': bc.get('biochar_ph'),
            'ash_content_pct': bc.get('ash_content_pct'),
            'suitability_score': round(suitability, 4),
            'recommended_rate_t_ac': round(rate, 2),
            'predicted_dpH': round(predicted_dpH, 3),
        })

    results.sort(key=lambda x: x['suitability_score'], reverse=True)
    return results[:5]


def dst_match_simplified(config: Dict[str, Any], asset_name: str):
    """Batch-mode biochar suitability eddy.

    For each polygon in the input soil layer, runs dst_match_point and writes
    the top biochar's scores as polygon properties. Produces a derived layer
    suitable for landscape choropleth rendering.

    Config: in_layer (default: ssurgo_polk_or), out_layer, goal (default: raise_ph)
    """
    import pandas as pd

    asset_config = config['assets'][asset_name].get('config', config['assets'][asset_name])
    in_layer = asset_config.get('in_layer', 'ssurgo_polk_or')
    out_layer = asset_config.get('out_layer', f"suitability_{in_layer.replace('ssurgo_', '')}__raise_ph")
    goal = asset_config.get('goal', 'raise_ph')
    biochar_layer = asset_config.get('biochar_layer', 'biochar_properties_pnw')

    soil_fc = dataswale.layer_as_featurecollection(config, in_layer)
    if not soil_fc or not soil_fc.get('features'):
        raise Exception(f"dst_match_simplified: could not load layer '{in_layer}'")

    biochar_fc = dataswale.layer_as_featurecollection(config, biochar_layer)
    if not biochar_fc or not biochar_fc.get('features'):
        raise Exception(f"dst_match_simplified: could not load biochar layer '{biochar_layer}'")

    biochar_records = [f['properties'] for f in biochar_fc['features'] if f.get('properties')]
    logger.info(f"dst_match_simplified: {len(soil_fc['features'])} soil polygons, {len(biochar_records)} biochars, goal={goal}")

    enriched = []
    for feature in soil_fc['features']:
        props = dict(feature.get('properties') or {})
        soil_ph = props.get('soil_ph')
        soil_om = props.get('soil_om')

        if soil_ph is not None:
            try:
                ranked = dst_match_point(float(soil_ph), soil_om, goal, biochar_records)
                if ranked and not ranked[0].get('placeholder'):
                    top = ranked[0]
                    props['top_biochar_id'] = top['biochar_id']
                    props['top_biochar_name'] = top['name']
                    props['suitability_score'] = top['suitability_score']
                    props['predicted_dpH'] = top['predicted_dpH']
                    props['recommended_rate'] = top['recommended_rate_t_ac']
            except Exception as e:
                logger.warning(f"dst_match_simplified: failed for mukey {props.get('mukey')}: {e}")

        enriched.append({'type': 'Feature', 'geometry': feature.get('geometry'), 'properties': props})

    out_path = versioning.atlas_path(config, 'layers') / out_layer / f'{out_layer}.geojson'
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fc = geojson.FeatureCollection(enriched)
    with open(out_path, 'w') as f:
        geojson.dump(fc, f)
    logger.info(f"dst_match_simplified: wrote {len(enriched)} features to {out_path}")
    return str(out_path)


asset_methods = {
    "derived_hillshade": hillshade_gdal,
    "gdal_contours": contours_gdal,
    "contours": contours_gdal,  # legacy name, kept for config compatibility
    "h3_cells": h3_cells,
    "hillshade_tiles": hillshade_to_pmtiles,
    "terrain_rgb_tiles": terrain_rgb_to_pmtiles,
    "fuel_mass_landfire": fuel_mass_from_landfire,
    "biochar_simulation": biochar_simulation,
    "tiff_to_cog": tiff_to_cog,
    "terrain_rgb_tiff": terrain_rgb_tiff,
    "road_lrs": road_lrs,
    "road_lrs_markers": road_lrs_markers,
    "ssurgo_enrich": ssurgo_enrich,
    "merge_h3": merge_h3,
    "dst_match_simplified": dst_match_simplified,
}
