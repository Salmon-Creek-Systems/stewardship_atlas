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
    {"name": "regions", "geometry_type": "polygon", "color": [50, 50, 50]},
    {"name": "tsunami", "geometry_type": "polygon", "color": [0,0,255], "fill_color": 'none', "fill_opacity": 0.3},
    # {"name": "tsunami", "geometry_type": "polygon", "fill_color": [0, 0, 250], "fill_opacity": 0.3},
    {"name": "elevation", "geometry_type": "raster"},
    {"name": "contours", "geometry_type": "linestring", "color": [100, 255, 80]},
    {"name": "basemap", "geometry_type": "raster"},
    {"name": "lidar_basemap", "geometry_type": "raster"},
    {"name": "roads", "geometry_type": "linestring", "color": [100, 55, 50], "add_labels": True, "interaction": "interface", "vector_width":True},
    {"name": "internal_roads", "geometry_type": "linestring", "color": [100, 155, 50], "add_labels": True, "access": [ "admin"], "vector_width":True},
    {"name": "turnouts", "geometry_type": "point", "color": [50, 255, 100], "add_labels": False, "access": [ "admin"]},
    {"name": "creeks", "geometry_type": "linestring", "add_labels": True, "color": [50, 50, 200], "interaction": "interface", "vector_width":True},
    {"name": "campgrounds", "geometry_type": "point", "color": [0, 0, 255], "add_labels": True,"add_labels": True,
     "symbol": {"png": "tent.png", "icon": "basic/triangle"},
     "icon-size": 0.05,
     "interaction": "interface"},
    {"name": "milemarkers", "geometry_type": "point", "color": [0, 200, 0], "add_labels": True,
     "symbol" : {"png": "milemarker.png", "icon": "basic/marker"},
     "icon-size": 0.12, "icon-anchor": "bottom"},
    {"name": "helilandings", "geometry_type": "point", "color": [0, 255, 0], "add_labels": True,
     "symbol": {"png": "helipad.png", "icon": "extra/target"}, 'icon-size': 0.15, "access": [ "internal", "admin"], "interaction": "interface"},
    {"name": "hydrants", "geometry_type": "point", "color": [0, 0, 255], "add_labels": True,
     "symbol": {"png": "hydrant.png", "icon": "extra/half-circle"},
     "icon-size": 0.05,
     "interaction": "interface"},
    {"name": "buildings", "geometry_type": "polygon", "color": [0, 0, 0], "fill_color": [100,100,100], "add_labels": True, "interaction": "interface"},
    {"name": "addresses", "geometry_type": "polygon", "color": [255, 0, 0]},
    {"name": "parcels", "geometry_type": "polygon", "color": [255, 0, 0, 0.3], "fill_color": [0,0,0,0]}
]



DEFAULT_CONFIG = {
    "data_root": "/root/swales",
    "name": "Nameless",
    "description": "Underscripted",
    "logo": "/local/scs-smallgrass1.png",
    "dataswale": {
        "crs": "EPSG:4269",     
        "bbox": {"north": 0, "south": 0, "east": 0, "west": 0},
        "versions": ["staging"],
        "layers": []   
    }
}
DEFAULT_ASSETS = {
        "dem": {
            "type": "inlet",
            "out_layer": "elevation",
            "config_def": "opentopo_dem"
        },
        "gdal_contours": {
            "type": "eddy",            
            "in_layer": "elevation",
            "out_layer": "contours",
            "config_def": "gdal_contours"
        },
        "derived_hillshade": {
            "type": "eddy",
            "in_layer": "elevation",
            "out_layer": "basemap",
            "config_def": "derived_hillshade"
        },
    "public_buildings" : {
        "type": "inlet",
        "out_layer": "buildings",
        "config_def": "overture_buildings"
        },
    "public_addresses" : {
        "type": "inlet",
        "out_layer": "parcels",
        "config_def": "overture_addresses"
    },
    "oa_parcels" : {
        "type": "inlet",
        "out_layer": "parcels",
        "config_def": "local_parcels"
    },
    "local_tsunami" : {
        "type": "inlet",
        "out_layer": "tsunami",
        "config_def": "local_tsunami"
    },

        "public_roads" : {
            "type": "inlet",
            "out_layer": "roads",
            "config_def": "overture_roads"
        },
        "internal_roads" : {
            "type": "inlet",
            "out_layer": "internal_roads",
            "config_def": "internal_roads"
        },

    "public_creeks" : {
            "type": "inlet",
            "out_layer": "creeks",
            "config_def": "nhd_creeks"
        },
        "local_hillshade" : {
            "type": "inlet",
            "out_layer": "lidar_basemap",
            "config_def": "local_hillshade"
        },
        "opentopo_dem" : {
            "type": "inlet",
            "out_layer": "basemap",
            "config_def": "opentopo_dem"
        },
    "local_helilandings" : {
        "type": "inlet",
        "out_layer": "helilandings",
        "config_def": "local_helilandings"
    },
        "local_hydrants" : {
            "type": "inlet",
            "out_layer": "hydrants",
            "config_def": "local_hydrants"
        },
    "local_campgrounds" : {
        "type": "inlet",
        "out_layer": "campgrounds",
        "config_def": "local_campgrounds"
        },

        "local_turnouts" : {
            "type": "inlet",
            "out_layer": "turnouts",
            "config_def": "local_turnouts"
        },
    "local_milemarkers" : {
        "type": "inlet",
        "out_layer": "milemarkers",
        "config_def": "local_milemarkers"
    },
    "webmap" : {
        "type": "outlet",
        "name": "webmap",
        "in_layers": ["basemap", "tsunami", "parcels", "roads", "milemarkers", "creeks", "buildings","campgrounds",  "helilandings", "hydrants"],
        "config_def": "webmap",
        "access": ["internal", "admin"]
    },
    "internal_webmap" : {
            "type": "outlet",
            "in_layers": ["basemap", "parcels", "roads", "internal_roads", "turnouts", "milemarkers", "creeks", "buildings", "helilandings", "hydrants"],
            "config_def": "webmap",
            "access": ["internal", "admin"]
        },
    "runbook": {
            "type": "outlet",
            "name": "runbook",
            "in_layers": ["lidar_basemap", "tsunami", "roads", "creeks", "buildings",  "campgrounds", "helilandings", "milemarkers", "hydrants"],
            "access": ["internal", "admin"],
             "config_def": "runbook",
             "regions" : [
                {"bbox": {
                    "east": -121.19978063408502,
                    "west": -121.20391109005016,
                    "south": 39.23863749098538,
                    "north": 39.24416744687048},
                "name": "RockLoop",
                "caption": "Double loop with Ginger and big rocks.",
                "vectors": [],
                "raster": "",
                "text": "Hwy20 to the County Lnie"},
                {"bbox": {
                    "east": -121.19978063408502,
                    "west": -121.20391109005016,
                    "south": 39.23863749098538,
                    "north": 39.24416744687048},
                "name": "GolfCourses",
                "caption": "Golf Courses",
                "vectors": [],
                "raster": "",
                "text": "Golf Courses"}]

    },
    "html": {
            "type": "outlet",
            "name": "html",
             "config_def": "static_html"},
    "sqldb": {
        "type": "outlet",
        "name": "sqldb",
        "layers": ["basemap", "roads", "creeks"]
    },
    "sqlquery": {
        "type": "outlet",
        "name": "sqlquery",
        "config_def": "sqlquery",
        "access": ["admin"]
    }
}

DEFAULT_DATA_ROOT = "/root/swales"

DEFAULT_BBOX = {"north": 0, "south": 0, "east": 0, "west": 0}


def add_htpasswds(config, path, access):
    """Add htpasswd entries for users with access to a directory.
    
    Args:
        path: Path to the directory to protect
        access: List of access levels (e.g., ['admin', 'internal'])
    """
    htpasswd_file = path / '.htpasswd'
    roles_path = Path(config['data_root']) / "roles" / f"{config['name']}_roles.json"

    
    # Read roles from roles.json file
    with open(roles_path, 'r') as f:
        roles = json.load(f)
  
    # Add users based on access levels
    for role, role_passwd in roles.items():
        logger.info(f"checking for {role} in {access}")
        if role in access:
            if not htpasswd_file.exists():
                cli_str = f"htpasswd -bc {htpasswd_file} {role} {role_passwd}"
            else:
                cli_str = f"htpasswd -b {htpasswd_file} {role} {role_passwd}"

            logger.info(cli_str)
            os.system(cli_str)
            logger.info(f"Added {role} user  to {htpasswd_file}")


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
        if 'config_def' in asset:
            asset['config'] = inlets_config[asset['config_def']]
        if asset['type'] == 'inlet':
            (p / 'staging' / 'deltas' / asset['out_layer'] / 'work').mkdir(parents=True, exist_ok=True)
            if asset.get('access',['public']).count('public') == 0:
                # add htpasswrd to new directory
                add_htpasswds(config, p / 'staging' / 'deltas' / asset['out_layer'], asset['accsss'])
        elif asset['type'] == 'outlet':
            (p / 'staging' / 'outlets' / asset_name / 'work' ).mkdir(parents=True, exist_ok=True)
            if asset.get('access',['public']).count('public') == 0:
                # add htpasswrd to new directory
                add_htpasswds(config,p / 'staging' / 'outlets' / asset_name, asset['access'])
    for layer in layers:
        (p / 'staging' / 'layers' / layer['name']).mkdir(parents=True, exist_ok=True)
        if layer.get('access',['public']).count('public') == 0:
            # add htpasswrd to new directory
            add_htpasswds(config, p / 'staging' / 'layers' / layer['name'], layer['access'])

         
    logger.info(f"built a config: {config}")
    json.dump(config, open(p / 'staging' / 'atlas_config.json', 'w'), indent=2)
    return config

    # populate

    
def delete():
    pass

def new_version():
    pass

