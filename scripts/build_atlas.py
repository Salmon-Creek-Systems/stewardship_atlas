#!/usr/bin/env python3
"""
Build a stewardship atlas from a GeoJSON configuration file.

This script creates a new atlas or regenerates the config for an existing one.
All configuration parameters are read from the first feature's properties in
the input GeoJSON file.

Usage:
    python build_atlas.py /path/to/extent.geojson

The GeoJSON file should have this structure:
{
  "type": "FeatureCollection",
  "features": [{
    "type": "Feature",
    "geometry": { "type": "Polygon", "coordinates": [[...]] },
    "properties": {
      "name": "myatlas",
      "assets_path": "/path/to/assets.json",
      "layers_path": "/path/to/layers.json",
      "data_root": "/root/swales",
      "shared_dir": "/root/data",
      "admin_emails": ["admin@example.com"],
      "base_url": "https://myatlas.fireatlas.org",
      "port": 9997,
      "versioned_outlets": ["html", "webmap", "runbook"],
      "logo_url": "https://example.com/logo.png",
      "config_only": false
    }
  }]
}
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Tuple

# Add python directory to path for imports
script_dir = Path(__file__).parent
python_dir = script_dir.parent / 'python'
sys.path.insert(0, str(python_dir))

import atlas
import utils


# Required properties that must be in the GeoJSON or passed to build_atlas()
REQUIRED_PROPERTIES = [
    'name',
    'assets_path',
    'layers_path', 
    'data_root',
    'shared_dir',
    'admin_emails',
    'base_url',
    'port',
    'versioned_outlets',
    'logo_url',
]


def build_atlas(
    name: str,
    assets_path: str,
    layers_path: str,
    data_root: str,
    shared_dir: str,
    admin_emails: List[str],
    base_url: str,
    app_url: str,
    port: int,
    versioned_outlets: List[str],
    logo_url: str,
    geometry: Dict[str, Any],
    config_only: bool = False
) -> Tuple[Dict[str, Any], Path]:
    """
    Build a new atlas or regenerate config for an existing one.
    
    Args:
        name: Atlas/swale name
        assets_path: Path to assets definition JSON file
        layers_path: Path to layers definition JSON file
        data_root: Root directory for atlas data
        shared_dir: Directory for shared/external resources
        admin_emails: List of admin email addresses
        base_url: Base URL for the atlas (e.g., https://myatlas.fireatlas.org)
        port: Webapp port number
        versioned_outlets: List of outlet names to version
        logo_url: URL to logo image
        geometry: Polygon geometry dict defining the atlas extent
        config_only: If True, only regenerate config (backup existing)
        
    Returns:
        Tuple of (atlas_config dict, Path to written config file)
        
    Raises:
        ValueError: If required parameters are missing or invalid
        FileNotFoundError: If required files don't exist
    """
    # Validate inputs
    if not name:
        raise ValueError("name is required")
    if not assets_path:
        raise ValueError("assets_path is required")
    if not layers_path:
        raise ValueError("layers_path is required")
    if not data_root:
        raise ValueError("data_root is required")
    if not shared_dir:
        raise ValueError("shared_dir is required")
    if not base_url:
        raise ValueError("base_url is required")
    if not geometry:
        raise ValueError("geometry is required")
    
    # Validate file paths exist
    if not Path(assets_path).exists():
        raise FileNotFoundError(f"Assets file not found: {assets_path}")
    if not Path(layers_path).exists():
        raise FileNotFoundError(f"Layers file not found: {layers_path}")
    if not Path(data_root).exists():
        raise FileNotFoundError(f"Data root directory not found: {data_root}")
    if not Path(shared_dir).exists():
        raise FileNotFoundError(f"Shared directory not found: {shared_dir}")
    
    # Build the feature collection structure expected by atlas.create/create_config
    feature_collection = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "name": name,
                "admin_emails": admin_emails or [],
                "base_url": base_url,
                "app_url": app_url,
                "atlasappport": port,
                "logo": logo_url,
                "versioned_outlets": versioned_outlets or []
            }
        }]
    }
    
    # Determine config path
    config_path = Path(data_root) / name / 'staging' / 'atlas_config.json'
    
    if config_only:
        # Only regenerate config, backup existing
        if config_path.exists():
            # Create backup with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = config_path.parent / f"atlas_config-BACKUP-{timestamp}.json"
            config_path.rename(backup_path)
            print(f"Backed up existing config to: {backup_path}", file=sys.stderr)
        
        # Generate config only (no directory creation)
        config = atlas.create_config(
            layers_path=layers_path,
            assets_path=assets_path,
            data_root=data_root,
            feature_collection=feature_collection
        )
        
        # Ensure staging directory exists for writing config
        config_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write the config
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
    else:
        # Full atlas creation
        config = atlas.create(
            layers_path=layers_path,
            assets_path=assets_path,
            data_root=data_root,
            shared_dir=Path(shared_dir),
            feature_collection=feature_collection
        )
    
    return config, config_path


def main():
    """CLI entry point - reads GeoJSON and builds atlas."""
    if len(sys.argv) == 3:
        print(f"Generating new config (only)")
        geojson_path = Path(sys.argv[2])
        config_only = True
    
    elif len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <geojson_path>", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"Generating new entire atlas)")
        geojson_path = Path(sys.argv[1])
        config_only = False
        
    if not geojson_path.exists():
        print(f"Error: GeoJSON file not found: {geojson_path}", file=sys.stderr)
        sys.exit(1)
    
    # Load GeoJSON
    with open(geojson_path) as f:
        geojson_data = json.load(f)
    
    # Validate structure
    if geojson_data.get('type') != 'FeatureCollection':
        print("Error: GeoJSON must be a FeatureCollection", file=sys.stderr)
        sys.exit(1)
    
    features = geojson_data.get('features', [])
    if not features:
        print("Error: GeoJSON must have at least one feature", file=sys.stderr)
        sys.exit(1)
    
    feature = features[0]
    geometry = feature.get('geometry')
    properties = feature.get('properties', {})
    
    if not geometry:
        print("Error: First feature must have a geometry", file=sys.stderr)
        sys.exit(1)
    
    # Check for required properties
    missing = [prop for prop in REQUIRED_PROPERTIES if prop not in properties]
    if missing:
        print(f"Error: Missing required properties: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    
    # Extract all parameters from properties
    try:
        config, config_path = build_atlas(
            name=properties['name'],
            assets_path=properties['assets_path'],
            layers_path=properties['layers_path'],
            data_root=properties['data_root'],
            shared_dir=properties['shared_dir'],
            admin_emails=properties['admin_emails'],
            base_url=properties['base_url'],
            app_url=properties['app_url'],
            port=properties['port'],
            versioned_outlets=properties['versioned_outlets'],
            logo_url=properties['logo_url'],
            geometry=geometry,
            config_only=properties.get('config_only', True)
        )
    except (ValueError, FileNotFoundError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    
    # Print only the path on success
    print(config_path)


if __name__ == '__main__':
    main()
