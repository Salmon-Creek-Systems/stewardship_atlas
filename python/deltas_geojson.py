"""
This module provides functionality for creating and processing delta files as local geojson files.
This implementation also depends on DuckDB for spatial joins and data processing.
The module includes the following main functions:

- create: Create the deltas directory structure for a layer.
- transform: Apply transformations to a GeoJSON feature.
- add_deltas: Add a new delta file to the layer's deltas directory.

- apply_deltas: Apply delta batch to a layer.
- build_layer: Return a FeatureCollection from a queue of delta batches.

"""




import json
import os
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Iterator, Dict, Any, List, Tuple
import geojson
from geojson import Feature, FeatureCollection
import duckdb

import versioning

# Configure logging
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)
logger.setLevel(logging.INFO)

class InvalidDelta(Exception):
    """Exception raised for invalid delta data"""
    def __init__(self, message: str, details: Dict[str, Any] = None):
        self.message = message
        self.details = details or {}
        super().__init__(self.message)

def create(config: Dict[str, Any]) -> None:
    """
    Create the deltas directory structure for a layer.
    
    Args:
        config: Configuration dictionary containing at least:
            - name: Layer name
            - data_root: Root directory for data storage
    
    Raises:
        ValueError: If required config fields are missing
    """
    if not config.get('name'):
        raise ValueError("Configuration must include 'name' field")
    if not config.get('data_root'):
        print(f"WWTF: config: {config}")
        raise ValueError("Configuration must include 'data_root' field")
    
    deltas_dir = Path(config['data_root']) / f"deltas_{config['name']}"
    processed_dir = deltas_dir / "processed"
    
    # Create directories if they don't exist
    deltas_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(exist_ok=True)
    
    logger.info(f"Created/verified deltas directory structure at {deltas_dir}")

def transform(feature: Feature, config: Dict[str, Any]) -> Feature:
    """
    Apply transformations to a GeoJSON feature.
    
    Args:
        feature: GeoJSON Feature to transform
        config: Configuration dictionary containing transformation settings
    
    Returns:
        Transformed GeoJSON Feature
    """
    if not feature.get('properties'):
        feature['properties'] = {}
    
    # Set vector width from config or default
    vector_width = config.get('vector_width', 2)
    feature['properties']['vector_width'] = vector_width
    
    return feature

def add_deltas(config: Dict[str, Any], asset_name: str, feature_collection: FeatureCollection, delta_action: str) -> Tuple[int, str]:
    """
    Add a new delta file to the layer's deltas directory.
    
    Args:
        feature_collection: GeoJSON FeatureCollection to store
        config: Configuration dictionary
        asset_name: the asset generating the delta
    Returns:
        Tuple of (number of features written, path to written file)
    
    Raises:
        InvalidDelta: If the feature collection is invalid
    """    
    # Create timestamp-based filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    layer_name = config['assets'][asset_name]['config']['out_layer']
    local_filename = f"deltas/{layer_name}/{asset_name}_{timestamp}"
    outpath = versioning.atlas_path(config, local_filename)

    with versioning.atlas_file(outpath + ".geojson", mode="wt") as outfile:
        json.dump(feature_collection, outfile)
    with versioning.atlas_file(outpath + ".delta.json", mode="wt") as outfile:
        dc = {"action": delta_action, "join": "simple_intersect"}    
        json.dump(dc, outfile)
    
    logger.info(f"Wrote {len(feature_collection['features'])} features to {outpath}")
    return len(feature_collection['features']), str(outpath)

def apply_deltas(config: Dict[str, Any], layer: FeatureCollection) -> FeatureCollection:
    """
   Apply all delta file sin order.

    """
    deltas_dir = versioning.atlas_path(config, f"{config['name']}/deltas/{layer_name}")
    processed_dir = deltas_dir / "processed"
    
    # Get all .geojson files in the deltas directory
    delta_files = sorted(deltas_dir.glob("*.geojson"))
    
    # start a local duckdb  



    # start an empypty layer file
    layer_path = versioning.atlas_path(config, f"{config['name']}/")
    with versioning.atlas_file(layer_path / "layer.geojson", mode="wt") as outfile:
        geojson.dump(FeatureCollection(features=[]), outfile)

    for i,filepath in enumerate(delta_files):
        asset_name, ts, action = filepath.stem.split("_")

        if action == "create":
                # read the layer file
                with versioning.atlas_file(layer_path / "layer.geojson", mode="rt") as infile:
                    layer = geojson.load(infile)

                with versioning.atlas_file(filepath, mode="rt") as infile:
                    delta = geojson.load(infile)

                with versioning.atlas_file(layer_path / "layer.geojson", mode="wt") as outfile:
                    geojson.dump(FeatureCollection(features=layer['features'] + delta['features']), outfile)

            elif action == "update":
                con = duckdb.connect(":memory:")    
                # Load the delta file into a table called delta
                con.sql(f"CREATE TABLE layer AS SELECT * FROM read_json('{layer_path}/layer.geojson')")
                # Perform a spatial join between layer and delta
                con.sql(f"CREATE TABLE delta AS SELECT * FROM read_json('{filepath}')")
                # join the two tables on the geometry column    
                con.sql("""
                CREATE TABLE new_layer AS 
                SELECT layer.*, delta.* 
                FROM layer 
                JOIN delta 
                ON ST_Intersects(layer.geometry, delta.geometry)
                """)

                # write the new layer to the layer file
                with versioning.atlas_file(layer_path / "layer.geojson", mode="wt") as outfile:
                    geojson.dump(FeatureCollection(features=new_layer['features']), outfile)
            else:
                raise InvalidDelta(f"Invalid action: {action}") 

            return geojson.load(open(layer_path / "layer.geojson"))
    
    

     
