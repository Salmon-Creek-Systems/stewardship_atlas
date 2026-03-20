"""
This module provides functionality for creating and processing atlas data.
This implementation  depends on GeoJSON andDuckDB for spatial joins and data processing.
The module includes the following main functions:

- create: Create the deltas directory structure for a stewart atlas and a config file.
- new_version: Create a new version of the stewart atlas and a config file.
- asset: Create an asset for a stewart atlas and a config file.

"""
# Boring Imports
import json
import os
import logging
import shutil
import copy
from datetime import datetime
from pathlib import Path


# Interesting imports
import geojson

# Our imports
import utils
import outlets
import outlets_qgis_atlas
import vector_inlets
import raster_inlets
import eddies

# Configure logging
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)
logger.setLevel(logging.INFO)
from typing import List, Dict, Tuple, Any

# These are instance specific and shouldn't have defaults
# DEFAULT_DATA_ROOT = "/root/swales"
# DEFAULT_BBOX = {"north": 0, "south": 0, "east": 0, "west": 0}
# DEFAULT_SHARED_DIR = Path('/root/data')

# Load general defaults and building blocks
#DEFAULT_CONFIG = json.load(open("../configuration/default_atlas_config.json"))
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
DEFAULT_ROLES = {"internal": "internal","admin": "admin"}
DEFAULT_MATERIALIZERS =  outlets.asset_methods | eddies.asset_methods | vector_inlets.asset_methods | raster_inlets.asset_methods  |  outlets_qgis_atlas.asset_methods 


def discover_versions(swale_path: Path) -> List[str]:
    """Discover all existing versions in a swale directory.
    
    Versions are subdirectories that contain an atlas_config.json file.
    
    Args:
        swale_path: Path to the swale directory
    
    Returns:
        List of version names (directory names)
    """
    versions = []
    if not swale_path.exists():
        return versions
    
    for item in swale_path.iterdir():
        if item.is_dir() and (item / 'atlas_config.json').exists():
            versions.append(item.name)
    
    logger.debug(f"Discovered versions in {swale_path}: {versions}")
    return sorted(versions)


def add_htpasswds(config, path, access):
    """Add htpasswd entries for users with access to a directory.
    
    Args:
        path: Path to the directory to protect
        access: List of access levels (e.g., ['admin', 'internal'])
    """
    htpasswd_file = path / '.htpasswd'
    roles_path = Path(config['data_root']) / "roles" / f"{config['name']}_roles.json"

    if not roles_path.exists():
        logger.info(f"Creating new roles file in {roles_path}...")
        with open(roles_path, 'w') as fo:
            json.dump(DEFAULT_ROLES, fo)
    
    # Read roles from roles.json file
    with open(roles_path, 'r') as f:
        roles = json.load(f)
  
    # Add users based on access levels
    for role, role_passwd in roles.items():
        logger.debug(f"checking for {role} in {access}")
        if role in access:
            if not htpasswd_file.exists():
                cli_str = f"htpasswd -bc {htpasswd_file} {role} {role_passwd}"
            else:
                cli_str = f"htpasswd -b {htpasswd_file} {role} {role_passwd}"

            logger.debug(cli_str)
            os.system(cli_str)
            logger.debug(f"Added {role} user  to {htpasswd_file}")


def create_config(config: Dict[str, Any] = None, 
                  layers: List[Dict[str, Any]] = None, 
                  layers_path: str = None,
                  assets: Dict[str, Any] = None, 
                  assets_path: str = None,
                  data_root: str = None,
                  name: str = "Nameless",
                  admin_emails: List[str] = None,
                  bbox: Dict[str, Any] = None,
                  feature_collection: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Build an atlas configuration dict without creating directories or files.
    
    This function processes layers and assets definitions, expands config_def references,
    and returns a complete atlas configuration dictionary.
    
    Args:
        config: Base config dict to start from (defaults to DEFAULT_CONFIG copy)
        layers: List of layer definitions (alternative to layers_path)
        layers_path: Path to layers JSON file
        assets: Dict of asset definitions (alternative to assets_path)
        assets_path: Path to assets JSON file
        data_root: Root directory for atlas data
        name: Atlas name
        admin_emails: List of admin email addresses
        bbox: Bounding box dict (alternative to feature_collection)
        feature_collection: GeoJSON FeatureCollection with extent and properties
        
    Returns:
        Complete atlas configuration dictionary
    """
    if config is None:
        config = copy.deepcopy(DEFAULT_CONFIG)
    
    if admin_emails is None:
        admin_emails = []
    
    # Extract properties from feature_collection if provided
    if feature_collection:
        feature = feature_collection['features'][0]
        name = feature['properties']['name']
        admin_emails = feature['properties']['admin_emails']
        config['base_url'] = feature['properties'].get('base_url', f"https://fireatlas.org/{name}")
        config['app_url'] = feature['properties'].get('app_url', "https://fireatlas.org")
        config['atlasappport'] = feature['properties'].get('atlasappport', 443)
        bbox = utils.geojson_to_bbox(feature['geometry']['coordinates'][0])
        config['logo'] = feature['properties'].get('logo', "/local/scs-smallgrass1.png")
        config['dataswale']['versioned_outlets'] = feature['properties'].get('versioned_outlets', [])
    
    p = Path(data_root) / name
    
    config['data_root'] = data_root
    config['name'] = name
    config['dataswale']['bbox'] = bbox
    config['admin_emails'] = admin_emails
    
    # Discover existing versions in the swale directory
    config['dataswale']['versions'] = discover_versions(p)

    # Load layers and assets definitions
    if layers_path is not None:
        layers = json.load(open(layers_path))
    else:
        layers = layers or []
    
    if assets_path is not None:
        assets = json.load(open(assets_path))
    else:
        assets = assets or {}
    
    config['spreadsheets'] = {}

    # In unified mode there is one shared app/ checkout at {data_root}/app/.
    # Create the symlink now so shared config JSONs can be found below.
    p.mkdir(parents=True, exist_ok=True)
    if not (p / 'app').exists() and (Path(data_root) / 'app').exists():
        (p / 'app').symlink_to(Path(data_root) / 'app', target_is_directory=True)

    # Load asset and layer core/shared definitions
    shared_config_dir = p / 'app' / 'configuration'
    inlets_config = json.load(open(shared_config_dir / "shared_inlets_config.json"))
    eddies_config = json.load(open(shared_config_dir / "shared_eddies_config.json"))
    outlets_config = json.load(open(shared_config_dir / "shared_outlets_config.json"))
    default_layers_config = json.load(open(shared_config_dir / "shared_layers_config.json"))
    
    # Combine all asset configs into one lookup
    all_configs = {**inlets_config, **eddies_config, **outlets_config}
    
    # Process assets with config_def support
    for asset_name, asset in assets.items():
        if 'config_def' in asset:
            # Start with the base config from appropriate config file
            asset['config'] = copy.deepcopy(all_configs[asset['config_def']])
            
            # Apply overrides from asset config (except config_def)
            for key, value in asset.items():
                if key != 'config':
                    asset['config'][key] = value
    
    # Process layers with config_def support
    processed_layers = []
    for layer in layers:
        if 'config_def' in layer:
            # Start with the base config from default_layers.json
            layer_config = copy.deepcopy(default_layers_config[layer['config_def']])
        else:
            # Backward compatibility: start with empty config
            layer_config = {}
        
        # Apply overrides from layer config (except config_def)
        for key, value in layer.items():
            if key != 'config':
                layer_config[key] = value
        
        processed_layers.append(layer_config)
    
    # Set processed layers and assets in config
    config['dataswale']['layers'] = processed_layers
    config['assets'] = assets
    
    logger.info(f"Built config for {config['name']}.")
    logger.debug(f"Config: {config}")
    
    return config


def create(config: Dict[str, Any] = None, 
           layers: List[Dict[str, Any]] = None, 
           layers_path: str = None,
           assets: Dict[str, Any] = None, 
           assets_path: str = None,
           data_root: str = None,
           shared_dir: Path = None,
           name: str = "Nameless",
           admin_emails: List[str] = None,
           bbox: Dict[str, Any] = None,
           feature_collection: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Create a new stewardship atlas with directory structure and config file.

    This function:
    1. Builds the config using create_config()
    2. Creates directory structure (staging/, layers/, outlets/, deltas/)
    3. Creates symlinks to shared_dir
    4. Adds htpasswd files for protected directories
    5. Stores initial feature collection in layers/regions
    6. Writes atlas_config.json
    
    Args:
        config: Base config dict to start from (defaults to DEFAULT_CONFIG copy)
        layers: List of layer definitions (alternative to layers_path)
        layers_path: Path to layers JSON file
        assets: Dict of asset definitions (alternative to assets_path)
        assets_path: Path to assets JSON file
        data_root: Root directory for atlas data
        shared_dir: Path to shared resources directory
        name: Atlas name
        admin_emails: List of admin email addresses
        bbox: Bounding box dict (alternative to feature_collection)
        feature_collection: GeoJSON FeatureCollection with extent and properties
        
    Returns:
        Complete atlas configuration dictionary
    """
    if admin_emails is None:
        admin_emails = []
    
    # Build the configuration
    config = create_config(
        config=config,
        layers=layers,
        layers_path=layers_path,
        assets=assets,
        assets_path=assets_path,
        data_root=data_root,
        name=name,
        admin_emails=admin_emails,
        bbox=bbox,
        feature_collection=feature_collection
    )
    
    # Now create directory structure
    p = Path(data_root) / config['name']
    p.mkdir(parents=True, exist_ok=True)

    if not (p / 'local').is_symlink():
        (p / 'local').symlink_to(shared_dir, target_is_directory=True)

    if not (p / 'CURRENT').is_symlink():
        (p / 'CURRENT').symlink_to(p / 'staging', target_is_directory=True)


    (p / 'staging').mkdir(parents=True, exist_ok=True)
    (p / 'staging' / 'outlets').mkdir(parents=True, exist_ok=True)
    (p / 'staging' / 'layers').mkdir(parents=True, exist_ok=True)
    
    if not (p / 'staging' / 'local').is_symlink():
        (p / 'staging' / 'local').symlink_to(shared_dir, target_is_directory=True)
    
    # Create directories and htpasswds for assets
    for asset_name, asset in config['assets'].items():
        if asset['type'] == 'inlet':
            (p / 'staging' / 'deltas' / asset['out_layer'] / 'work').mkdir(parents=True, exist_ok=True)
            if asset.get('access', ['public']).count('public') == 0:
                add_htpasswds(config, p / 'staging' / 'deltas' / asset['out_layer'], asset.get('access', []))
        elif asset['type'] == 'outlet':
            (p / 'staging' / 'outlets' / asset_name / 'work').mkdir(parents=True, exist_ok=True)
            if asset.get('access', ['public']).count('public') == 0:
                add_htpasswds(config, p / 'staging' / 'outlets' / asset_name, asset['access'])
    
    # Create directories and htpasswds for layers
    for layer_config in config['dataswale']['layers']:
        (p / 'staging' / 'layers' / layer_config['name']).mkdir(parents=True, exist_ok=True)
        if layer_config.get('access', ['public']).count('public') == 0:
            add_htpasswds(config, p / 'staging' / 'layers' / layer_config['name'], layer_config['access'])
    
    # Store initial feature collection in layers/regions
    if (p / 'staging' / 'layers' / 'regions').exists():
        if feature_collection:
            logger.info(f"Storing initial feature collection in {p / 'staging' / 'layers' / 'regions'}...")
            geojson.dump(feature_collection, open(p / 'staging' / 'layers' / 'regions' / 'default_atlas_regions.geojson', "w"))
    
    # Write config file
    config_path = p / 'staging' / 'atlas_config.json'
    logger.info(f"Writing config to {config_path}")
    json.dump(config, open(config_path, 'w'), indent=2)
    
    return config

    # populate

    
def delete():
    pass

def new_version():
    pass

def materialize(config: Dict[str, Any], asset_name: str, materializers: Dict[str, Any]=DEFAULT_MATERIALIZERS):
    materializer_name = config['assets'][asset_name]['config']['fetch_type']
    return materializers[materializer_name](config, asset_name)

