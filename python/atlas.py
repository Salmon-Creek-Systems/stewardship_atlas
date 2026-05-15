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
        props = feature['properties']

        # Pass all GeoJSON properties into config; special cases below override as needed
        config.update(props)

        # bbox comes from geometry, not properties
        bbox = utils.geojson_to_bbox(feature['geometry']['coordinates'][0])

        # versioned_outlets belongs under dataswale, not at top level
        config['dataswale']['versioned_outlets'] = props.get('versioned_outlets', [])
        config.pop('versioned_outlets', None)

        # Defaults for optional fields that may be absent from the GeoJSON
        config.setdefault('base_url', f"https://fireatlas.org/{props['name']}")
        config.setdefault('app_url', "https://fireatlas.org")
        config.setdefault('atlasappport', 443)
        config.setdefault('logo', "/local/scs-smallgrass1.png")
        config.setdefault('admin_emails', [])

        name = props['name']
        admin_emails = config['admin_emails']
    
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
        # name defaults to dict key; type comes from shared template after merge
        if 'name' not in asset:
            asset['name'] = asset_name

        if 'config_def' in asset:
            # Start with the base config from appropriate config file
            asset['config'] = copy.deepcopy(all_configs[asset['config_def']])

            # Apply overrides from asset config (except config_def)
            for key, value in asset.items():
                if key != 'config':
                    asset['config'][key] = value

            # Inject type from shared template if not specified in per-atlas asset.
            # Shared configs use 'asset_type'; some older entries use 'type'.
            if 'type' not in asset:
                asset['type'] = (asset['config'].get('asset_type')
                                 or asset['config'].get('type', ''))

    # Process layers: layer_def resolves shared template; overrides applied on top
    processed_layers = []
    for layer in layers:
        if 'layer_def' in layer:
            base = default_layers_config.get(layer['layer_def'])
            if base is None:
                raise KeyError(f"layer_def '{layer['layer_def']}' not found in shared_layers_config.json")
            layer_config = copy.deepcopy(base)
        else:
            layer_config = {}

        for key, value in layer.items():
            if key not in ('layer_def', 'config'):
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
            regions_dir = p / 'staging' / 'layers' / 'regions'
            geojson.dump(feature_collection, open(regions_dir / 'default_atlas_regions.geojson', "w"))
            geojson.dump(feature_collection, open(regions_dir / 'regions.geojson', "w"))
    
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

_BUILD_DEFAULT_ROLES = {"internal": "internal", "admin": "admin"}

REQUIRED_ATLAS_PROPERTIES = [
    'name', 'data_root', 'shared_dir',
    'admin_emails', 'base_url', 'port', 'versioned_outlets', 'logo_url',
]


def setup_role_htpasswds(data_root: str, atlas_name: str):
    """Create per-role htpasswd files used by nginx for auth.

    Reads passwords from {data_root}/roles/{atlas_name}_roles.json, falling
    back to infrastructure/htpasswd/roles/{atlas_name}_roles.json in the repo,
    then to built-in defaults.
    """
    data_root = Path(data_root)
    roles_dir = data_root / "roles" / atlas_name
    roles_dir.mkdir(parents=True, exist_ok=True)

    data_root_roles_json = data_root / "roles" / f"{atlas_name}_roles.json"
    repo_root = Path(__file__).parent.parent
    infra_roles_json = repo_root / "infrastructure" / "htpasswd" / "roles" / f"{atlas_name}_roles.json"

    if data_root_roles_json.exists():
        roles_json = data_root_roles_json
    elif infra_roles_json.exists():
        roles_json = infra_roles_json
        logger.warning(f"Using roles from repo: {infra_roles_json}")
    else:
        roles_json = None
        logger.warning(f"No roles file found for {atlas_name}, using defaults")

    roles = dict(_BUILD_DEFAULT_ROLES)
    if roles_json:
        with open(roles_json) as f:
            roles = json.load(f)

    if not data_root_roles_json.exists():
        data_root_roles_json.parent.mkdir(parents=True, exist_ok=True)
        with open(data_root_roles_json, 'w') as f:
            json.dump(roles, f, indent=2)

    admin_password = roles.get("admin")
    for role, password in roles.items():
        htpasswd_file = roles_dir / f"{role}.htpasswd"
        flag = "bc" if not htpasswd_file.exists() else "b"
        os.system(f"htpasswd -{flag} {htpasswd_file} {role} {password}")
        if role != "admin" and admin_password:
            os.system(f"htpasswd -b {htpasswd_file} admin {admin_password}")
        logger.info(f"Wrote {htpasswd_file}")


def build_atlas(
    name: str,
    data_root: str,
    shared_dir: str,
    admin_emails: List[str],
    base_url: str,
    app_url: str,
    port: int,
    versioned_outlets: List[str],
    logo_url: str,
    geometry: Dict[str, Any],
    assets_path: str = None,
    layers_path: str = None,
    assets: Dict[str, Any] = None,
    layers: List[Dict[str, Any]] = None,
    config_only: bool = False,
    extra_props: Dict[str, Any] = None,
):
    """Build a new atlas or regenerate config for an existing one.

    Returns (atlas_config dict, Path to written atlas_config.json).
    Equivalent to running: python scripts/build_atlas.py [config_only] <geojson>

    Accepts either file paths (assets_path/layers_path) or inline dicts
    (assets/layers). At least one pair must be provided.
    """
    from datetime import datetime as _dt

    if assets_path is not None and not Path(assets_path).exists():
        raise FileNotFoundError(f"Assets file not found: {assets_path}")
    if layers_path is not None and not Path(layers_path).exists():
        raise FileNotFoundError(f"Layers file not found: {layers_path}")
    if not Path(data_root).exists():
        raise FileNotFoundError(f"Data root not found: {data_root}")
    if not Path(shared_dir).exists():
        raise FileNotFoundError(f"Shared dir not found: {shared_dir}")

    feature_collection = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                **(extra_props or {}),
                "name": name,
                "admin_emails": admin_emails or [],
                "base_url": base_url,
                "app_url": app_url,
                "atlasappport": port,
                "logo": logo_url,
                "versioned_outlets": versioned_outlets or [],
            },
        }],
    }

    config_path = Path(data_root) / name / 'staging' / 'atlas_config.json'
    setup_role_htpasswds(data_root, name)

    if config_only:
        if config_path.exists():
            timestamp = _dt.now().strftime("%Y%m%d_%H%M%S")
            backup_path = config_path.parent / f"atlas_config-BACKUP-{timestamp}.json"
            config_path.rename(backup_path)
            logger.info(f"Backed up existing config to: {backup_path}")

        config = create_config(
            layers_path=layers_path,
            layers=layers,
            assets_path=assets_path,
            assets=assets,
            data_root=data_root,
            feature_collection=feature_collection,
        )
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
    else:
        config = create(
            layers_path=layers_path,
            layers=layers,
            assets_path=assets_path,
            assets=assets,
            data_root=data_root,
            shared_dir=Path(shared_dir),
            feature_collection=feature_collection,
        )
        materialize(config, 'notebook')
        materialize(config, 'html')

    return config, config_path


def build_atlas_from_geojson(geojson_path, config_only: bool = False):
    """Build or regenerate an atlas from its GeoJSON config file.

    Equivalent to: python scripts/build_atlas.py [config_only] <geojson_path>

    Returns (atlas_config dict, Path to written atlas_config.json).

    Accepts two source formats:
    - Inline: 'assets' (dict) and 'layers' (dict keyed by name) embedded as
      GeoJSON feature properties. Single-file format — no external JSON files.
    - File-path: 'assets_path' and 'layers_path' strings pointing to separate
      JSON files. Legacy format, still fully supported.
    """
    geojson_path = Path(geojson_path)
    with open(geojson_path) as f:
        geojson_data = json.load(f)

    if geojson_data.get('type') != 'FeatureCollection':
        raise ValueError("GeoJSON must be a FeatureCollection")
    features = geojson_data.get('features', [])
    if not features:
        raise ValueError("GeoJSON must have at least one feature")

    feature = features[0]
    geometry = feature.get('geometry')
    if not geometry:
        raise ValueError("First feature must have a geometry")

    props = feature.get('properties', {})
    missing = [p for p in REQUIRED_ATLAS_PROPERTIES if p not in props]
    if missing:
        raise ValueError(f"Missing required properties: {', '.join(missing)}")

    has_inline = isinstance(props.get('assets'), dict) and isinstance(props.get('layers'), dict)
    has_paths = 'assets_path' in props and 'layers_path' in props
    if not has_inline and not has_paths:
        raise ValueError(
            "GeoJSON must have either inline 'assets' and 'layers' dicts "
            "or 'assets_path' and 'layers_path' strings"
        )

    known = set(REQUIRED_ATLAS_PROPERTIES) | {'app_url', 'config_only', 'assets_path', 'layers_path', 'assets', 'layers'}
    extra_props = {k: v for k, v in props.items() if k not in known}

    mode = "config only" if config_only else "full atlas"
    logger.info(f"Building {mode} for '{props['name']}' from {geojson_path.name}")

    common = dict(
        name=props['name'],
        data_root=props['data_root'],
        shared_dir=props['shared_dir'],
        admin_emails=props['admin_emails'],
        base_url=props['base_url'],
        app_url=props.get('app_url', props['base_url']),
        port=props['port'],
        versioned_outlets=props['versioned_outlets'],
        logo_url=props['logo_url'],
        geometry=geometry,
        config_only=config_only,
        extra_props=extra_props,
    )

    if has_inline:
        # layers dict is keyed by name; inject name into each layer for downstream compatibility
        layers_list = [{'name': k, **v} for k, v in props['layers'].items()]
        return build_atlas(**common, assets=props['assets'], layers=layers_list)
    else:
        return build_atlas(**common, assets_path=props['assets_path'], layers_path=props['layers_path'])


def rename_layer(config, old_name, new_name, dry_run=False):
    """Rename a layer across all dataswale files and source config files.

    Updates: layer data directory, deltas directory, {atlas}_layers.json,
    and all in_layer/in_layers/out_layer/layers/regions_layer references in
    {atlas}_assets.json and all shared_*.json config files.

    After running (non-dry-run):
      1. python scripts/build_atlas.py config_only
      2. Rematerialize 'webmap' and 'html'
      3. grep -r '<old_name>' configuration/  (sanity check on built config)

    Note: shared_*.json updates affect all atlases using those templates.
    """
    import dataswale_geojson

    name = config['name']
    staging_path = Path(config['data_root']) / name / 'staging'
    config_dir = Path(config['data_root']) / name / 'app' / 'configuration'

    layer_names = [l['name'] for l in config['dataswale']['layers']]
    if old_name not in layer_names:
        raise ValueError(f"Layer '{old_name}' not found. Available: {layer_names}")
    if new_name in layer_names:
        raise ValueError(f"Layer '{new_name}' already exists in config.")

    prefix = "[DRY RUN] " if dry_run else ""
    print(f"{prefix}Renaming layer '{old_name}' → '{new_name}' in atlas '{name}'\n")

    print("Filesystem:")
    dataswale_geojson.rename_layer_file(staging_path, old_name, new_name, dry_run=dry_run)
    dataswale_geojson.rename_deltas_dir(staging_path, old_name, new_name, dry_run=dry_run)

    print("\nConfig files:")
    layers_json = config_dir / f'{name}_layers.json'
    utils.rename_layer_key(layers_json, old_name, new_name, dry_run=dry_run)

    asset_configs = ([config_dir / f'{name}_assets.json']
                     + list(config_dir.glob('shared_*.json')))
    utils.replace_layer_references(asset_configs, old_name, new_name, dry_run=dry_run)

    if config.get('email_photo_default_layer') == old_name:
        print(f"\nWARNING: email_photo_default_layer='{old_name}' in {name}.geojson"
              f" — update manually to '{new_name}'.")

    if not dry_run:
        print(f"\nNext steps:")
        print(f"  1. python scripts/build_atlas.py config_only")
        print(f"  2. Rematerialize 'webmap' and 'html'")
        print(f"  3. grep -r '{old_name}' {config_dir}")


def materialize(config: Dict[str, Any], asset_name: str, materializers: Dict[str, Any]=DEFAULT_MATERIALIZERS):
    materializer_name = config['assets'][asset_name]['config']['fetch_type']
    return materializers[materializer_name](config, asset_name)

