"""
This module provides functionality for creating and processing atlas data.
This implementation  depends on GeoJSON andDuckDB for spatial joins and data processing.
The module includes the following main functions:

- create: Create the deltas directory structure for a stewart atlas and a config file.
- new_version: Create a new version of the stewart atlas and a config file.
- asset: Create an asset for a stewart atlas and a config file.

"""

import json
import os
import logging
import shutil
from datetime import datetime
from pathlib import Path
import utils

# Configure logging
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)
logger.setLevel(logging.INFO)
from typing import List, Dict, Tuple, Any

DEFAULT_LAYERS = [
    {"name": "basemap", "geometry_type": "raster"},
	 {"name": "roads", "geometry_type": "linestring", "color": [100, 255, 80]},
	 {"name": "creeks", "geometry_type": "linestring"}
	 ]


DEFAULT_CONFIG = {
    "data_root": "/root/swales",
    "name": "Nameless",
    "description": "Underscripted",
    "dataswale": {
        "crs": "EPSG:4269",     
        "bbox": {"north": 0, "south": 0, "east": 0, "west": 0},
        "versions": ["staging"],
        "layers": [
        {"name": "elevation", "geometry_type": "raster"},
        {"name": "contours", "geometry_type": "linestring", "color": [100, 255, 80]},
        {"name": "basemap", "geometry_type": "raster"},
        {"name": "roads", "geometry_type": "linestring", "color": [100, 55, 50], "add_labels": True},
        {"name": "creeks", "geometry_type": "linestring", "add_labels": True, "color": [50, 50, 200]}
        ]
    
    }
}
DEFAULT_ASSETS = {
        "dem": {
            "out_layer": "elevation",
            "config_def": "opentopo_dem"
        },
        "contours": {
            "in_layer": "elevation",
            "out_layer": "contours",
            "config_def": "contours"
        },
        "hillshade": {
            "in_layer": "elevation",
            "out_layer": "basemap",
            "config_def": "hillshade"
        },
        "public_roads" : {
        "out_layer": "roads",
        "config_def": "overture_roads"
        },
        "public_creeks" : {
            "out_layer": "creeks",
            "config_def": "nhd_creeks"
        },
        "local_hillshade" : {
            "out_layer": "basemap",
            "config_def": "local_hillshade"
        },
        "opentopo_dem" : {
            "out_layer": "basemap",
            "config_def": "opentopo_dem"
        },    
        "webmap" : {
            "in_layers": ["basemap", "roads", "creeks"],
            "config_def": "webmap"
        }
    }

DEFAULT_DATA_ROOT = "/root/swales"

DEFAULT_BBOX = {"north": 0, "south": 0, "east": 0, "west": 0}
def create(config: Dict[str, Any] = DEFAULT_CONFIG, 
           layers: List[Dict[str, Any]] = DEFAULT_LAYERS, 
           assets: Dict[str, Any] = DEFAULT_ASSETS, 
           data_root: str = DEFAULT_DATA_ROOT, 
           name: str = "Nameless",
           bbox: Dict[str, Any] = DEFAULT_BBOX,
           feature_collection: Dict[str, Any] = None) -> None:
    """
    Create a new version of the stewardship atlas and a config file.
    """

    # create core config in /staging, built from args here and metadata.json
    if feature_collection:
        feature = feature_collection['features'][0]
        name = feature['properties']['name']
        bbox = utils.geojson_to_bbox(feature['geometry']['coordinates'][0])

    
    p = Path(data_root) / name
    p.mkdir(parents=True, exist_ok=True)
    
    (p / 'staging').mkdir(parents=True, exist_ok=True)
    (p / 'staging' / 'outlets').mkdir(parents=True, exist_ok=True)
    (p / 'staging' / 'layers' ).mkdir(parents=True, exist_ok=True)


    config['data_root'] = data_root
    config['name'] = name
    config['dataswale']['bbox'] = bbox
    config['dataswale']['layers'] = layers
    config['assets'] = assets
    
    inlets_config = json.load(open("../configuration/inlets_config.json"))
    for asset_name, asset in config['assets'].items():
        asset['config'] = inlets_config[asset['config_def']]
    for layer in layers:
        (p / 'staging' / 'layers' / layer['name']).mkdir(parents=True, exist_ok=True)
        (p / 'staging' / 'deltas' / layer['name'] / 'work').mkdir(parents=True, exist_ok=True)
         
    logger.info(f"built a config: {config}")
    json.dump(config, open(p / 'staging' / 'atlas_config.json', 'w'), indent=2)
    return config

    # populate

    
def delete():
    pass

def new_version():
    pass

