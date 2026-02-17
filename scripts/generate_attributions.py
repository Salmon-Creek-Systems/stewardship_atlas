#!/usr/bin/env python3
"""
Generate an Attributions layer GeoJSON from an existing atlas_config.json.

This script migrates attribution data from layer configs to a centralized
Attributions layer. Layers with identical attribution data are grouped
into single features.

Usage:
    python generate_attributions.py /path/to/atlas_config.json [output_path]

If output_path is not specified, outputs to the layers/attributions directory
in the atlas staging area.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Any, List, Tuple

# Add python directory to path for imports
script_dir = Path(__file__).parent
python_dir = script_dir.parent / 'python'
sys.path.insert(0, str(python_dir))

import utils


def bbox_to_polygon_coords(bbox: Dict[str, float]) -> List[List[List[float]]]:
    """Convert a bbox dict to GeoJSON Polygon coordinates."""
    return [[
        [bbox['west'], bbox['north']],
        [bbox['east'], bbox['north']],
        [bbox['east'], bbox['south']],
        [bbox['west'], bbox['south']],
        [bbox['west'], bbox['north']]  # Close the polygon
    ]]


def attribution_key(attr: Dict[str, Any]) -> Tuple:
    """
    Create a hashable key from attribution data for grouping.
    Returns a tuple of (title, description, url, license, citation, logo_url).
    """
    return (
        attr.get('title', ''),
        attr.get('description', ''),
        attr.get('url', ''),
        attr.get('license', ''),
        attr.get('citation', ''),
        attr.get('logo_url', '')
    )


def generate_attributions(config_path: str, output_path: str = None) -> Path:
    """
    Generate an Attributions layer GeoJSON from atlas config.
    
    Args:
        config_path: Path to atlas_config.json
        output_path: Optional output path for the GeoJSON. If not provided,
                     outputs to layers/attributions/attributions.geojson
                     
    Returns:
        Path to the generated GeoJSON file
    """
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    
    with open(config_path) as f:
        config = json.load(f)
    
    # Get bbox for geometry
    bbox = config.get('dataswale', {}).get('bbox')
    if not bbox:
        raise ValueError("Config missing dataswale.bbox")
    
    polygon_coords = bbox_to_polygon_coords(bbox)
    
    # Get layers
    layers = config.get('dataswale', {}).get('layers', [])
    if not layers:
        print("Warning: No layers found in config", file=sys.stderr)
    
    # Get atlas-level defaults for placeholders
    atlas_name = config.get('name', 'atlas')
    atlas_logo = config.get('logo', '')
    
    # Group layers by their attribution data
    # Key: attribution tuple, Value: list of layer names
    attribution_groups: Dict[Tuple, List[str]] = defaultdict(list)
    
    for layer in layers:
        layer_name = layer.get('name', 'unknown')
        attr = layer.get('attribution', {})
        
        # Check if layer has any real attribution data
        has_attribution = any([attr.get('title'), attr.get('description'), attr.get('url'), 
                               attr.get('license'), attr.get('citation')])
        
        if has_attribution:
            # Build attribution dict from existing data with reasonable defaults
            attribution = {
                'title': attr.get('title', attr.get('description', f'Source for {layer_name}')[:50]),
                'description': attr.get('description', 'Derived from atlas data using QGIS, GRASS, and Python.'),
                'url': attr.get('url', ''),
                'license': attr.get('license', 'Not Specified'),
                'citation': attr.get('citation', 'Not Specified'),
                'logo_url': attr.get('logo_url', atlas_logo)
            }
        else:
            # No attribution data - create placeholder with helpful defaults
            attribution = {
                'title': f'Atlas Derived Source: {layer_name}',
                'description': 'Derived from atlas data using QGIS, GRASS, and Python.',
                'url': '',
                'license': 'Not Specified',
                'citation': 'Not Specified',
                'logo_url': atlas_logo
            }
        
        key = attribution_key(attribution)
        attribution_groups[key].append(layer_name)
    
    # Build GeoJSON features
    features = []
    for attr_tuple, layer_names in attribution_groups.items():
        title, description, url, license_url, citation, logo_url = attr_tuple
        
        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": polygon_coords
            },
            "properties": {
                "title": title,
                "layers": sorted(layer_names)  # Sort for consistency
            }
        }
        
        # Only include optional fields if they have values
        if description:
            feature["properties"]["description"] = description
        if url:
            feature["properties"]["url"] = url
        if license_url:
            feature["properties"]["license"] = license_url
        if citation:
            feature["properties"]["citation"] = citation
        if logo_url:
            feature["properties"]["logo_url"] = logo_url
        
        features.append(feature)
    
    # Sort features by title for consistent output
    features.sort(key=lambda f: f["properties"]["title"])
    
    # Build FeatureCollection
    feature_collection = {
        "type": "FeatureCollection",
        "features": features
    }
    
    # Determine output path
    if output_path:
        out_path = Path(output_path)
    else:
        # Default to layers/attributions/attributions.geojson relative to config
        staging_dir = config_path.parent
        out_path = staging_dir / 'layers' / 'attributions' / 'attributions.geojson'
    
    # Ensure output directory exists
    out_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Write GeoJSON
    with open(out_path, 'w') as f:
        json.dump(feature_collection, f, indent=2)
    
    print(f"Generated {len(features)} attribution features for {sum(len(f['properties']['layers']) for f in features)} layers", file=sys.stderr)
    
    return out_path


def main():
    """CLI entry point."""
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <atlas_config.json> [output_path]", file=sys.stderr)
        sys.exit(1)
    
    config_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    try:
        result_path = generate_attributions(config_path, output_path)
        # Print only the path on success
        print(result_path)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
