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

import dataswale_geojson as dataswale

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
        # Get the asset configuration
        asset_config = config['assets'][asset_name]
        
        # Extract configuration parameters
        in_layer = asset_config['in_layer']
        out_layer = asset_config['out_layer']
        starting_resolution = asset_config.get('starting_resolution', 11)
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
        else:
            raise Exception(f"Unsupported geometry type '{geometry_type}' for layer '{in_layer}'. Only 'polygon' and 'linestring' are supported.")
        
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
        fc = geojson.FeatureCollection(0)
        fc['features'] = h3_cells_with_properties
                                       
        with open(out_path, 'w') as f:
            geojson.dump(fc, f)

        logger.info(f"Successfully created hexagonal layer '{out_layer}' with {len(h3_cells_with_properties)} features")
        
        return hex_layer_data
        
    except Exception as e:
        logger.error(f"H3 cells eddy failed: {str(e)}")
        raise Exception(f"H3 cells eddy failed: {str(e)}")


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
    ac = config['assets'][asset_name]
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

    # Write burns_simulation.geojson — all burn cells with per-cell metrics
    sites_layer_path = versioning.atlas_path(config, 'layers') / 'processing_sites' / 'processing_sites.geojson'
    layer_dir = Path(sites_layer_path).parent
    layer_dir.mkdir(parents=True, exist_ok=True)

    sim_path = layer_dir / 'burns_simulation.geojson'
    with open(sim_path, 'w') as f:
        json.dump({'type': 'FeatureCollection', 'features': burns}, f)
    logger.info(f"biochar_simulation: wrote {sim_path}")

    # Write biochar_summary.csv — one row per treatment
    import csv
    csv_path = layer_dir / 'biochar_summary.csv'
    with open(csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['treatment'] + metric_keys)
        writer.writeheader()
        for t_key, vals in totals.items():
            writer.writerow({'treatment': t_key, **vals})
    logger.info(f"biochar_simulation: wrote {csv_path}")

    # Update processing_sites GeoJSON — annotate each site point with totals
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


asset_methods = {
    "derived_hillshade": hillshade_gdal,
    "gdal_contours": contours_gdal,
    "contours": contours_gdal,  # legacy name, kept for config compatibility
    "h3_cells": h3_cells,
    "hillshade_tiles": hillshade_to_pmtiles,
    "terrain_rgb_tiles": terrain_rgb_to_pmtiles,
    "biochar_simulation": biochar_simulation,
}
