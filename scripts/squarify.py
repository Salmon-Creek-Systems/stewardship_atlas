#!/usr/bin/env python3
"""
Convert all polygon features in a GeoJSON file to squares.

Each polygon is replaced with a square whose side length equals the longest
dimension of the original polygon's bounding box, centered on the original polygon.

Usage:
    python squarify.py input.geojson
    
Output:
    Creates squared_input.geojson in the same directory
"""

import json
import sys
from pathlib import Path


def get_bbox(coordinates):
    """
    Get bounding box (min_x, min_y, max_x, max_y) from polygon coordinates.
    Handles both Polygon and MultiPolygon types.
    """
    # Flatten all coordinate pairs
    all_coords = []
    
    def flatten(coords):
        """Recursively flatten coordinate structure"""
        for item in coords:
            if isinstance(item, list) and len(item) > 0:
                if isinstance(item[0], (int, float)):
                    # This is a coordinate pair [lon, lat]
                    all_coords.append(item)
                else:
                    # This is a nested list, recurse
                    flatten(item)
    
    flatten(coordinates)
    
    if not all_coords:
        return None
    
    lons = [c[0] for c in all_coords]
    lats = [c[1] for c in all_coords]
    
    return (min(lons), min(lats), max(lons), max(lats))


def create_square_from_bbox(bbox):
    """
    Create a square polygon from a bounding box.
    Uses the larger dimension and centers the square on the bbox center.
    
    Args:
        bbox: tuple of (min_x, min_y, max_x, max_y)
        
    Returns:
        List of coordinates forming a square polygon
    """
    min_x, min_y, max_x, max_y = bbox
    
    # Calculate dimensions
    width = max_x - min_x
    height = max_y - min_y
    
    # Use the larger dimension
    size = max(width, height)
    
    # Calculate center
    center_x = (min_x + max_x) / 2
    center_y = (min_y + max_y) / 2
    
    # Create square coordinates (closed polygon)
    half_size = size / 2
    square_coords = [
        [center_x - half_size, center_y - half_size],  # Bottom-left
        [center_x + half_size, center_y - half_size],  # Bottom-right
        [center_x + half_size, center_y + half_size],  # Top-right
        [center_x - half_size, center_y + half_size],  # Top-left
        [center_x - half_size, center_y - half_size]   # Close the polygon
    ]
    
    return square_coords


def squarify_feature(feature):
    """
    Convert a feature's geometry to a square.
    
    Args:
        feature: GeoJSON feature dict
        
    Returns:
        Modified feature with square geometry
    """
    geometry = feature.get('geometry', {})
    geom_type = geometry.get('type', '')
    
    if geom_type not in ['Polygon', 'MultiPolygon']:
        # Not a polygon, return as-is
        return feature
    
    coordinates = geometry.get('coordinates', [])
    bbox = get_bbox(coordinates)
    
    if bbox is None:
        # Invalid geometry, return as-is
        return feature
    
    # Create square
    square_coords = create_square_from_bbox(bbox)
    
    # Update geometry to a simple Polygon (square)
    feature['geometry'] = {
        'type': 'Polygon',
        'coordinates': [square_coords]  # Single ring
    }
    
    # Add metadata about the transformation
    if 'properties' not in feature:
        feature['properties'] = {}
    
    min_x, min_y, max_x, max_y = bbox
    width = max_x - min_x
    height = max_y - min_y
    size = max(width, height)
    
    feature['properties']['_squarified'] = True
    feature['properties']['_original_width'] = width
    feature['properties']['_original_height'] = height
    feature['properties']['_square_size'] = size
    
    return feature


def squarify_geojson(input_path):
    """
    Read a GeoJSON file, convert all polygons to squares, and save to a new file.
    
    Args:
        input_path: Path to input GeoJSON file
        
    Returns:
        Path to output file
    """
    input_path = Path(input_path)
    
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")
    
    # Read input GeoJSON
    print(f"Reading: {input_path}")
    with open(input_path, 'r') as f:
        geojson_data = json.load(f)
    
    # Process features
    if geojson_data.get('type') == 'FeatureCollection':
        features = geojson_data.get('features', [])
        print(f"Processing {len(features)} features...")
        
        for i, feature in enumerate(features):
            geom_type = feature.get('geometry', {}).get('type', '')
            name = feature.get('properties', {}).get('name', f"feature_{i}")
            
            if geom_type in ['Polygon', 'MultiPolygon']:
                original_bbox = get_bbox(feature['geometry']['coordinates'])
                if original_bbox:
                    min_x, min_y, max_x, max_y = original_bbox
                    orig_width = max_x - min_x
                    orig_height = max_y - min_y
                    
                    features[i] = squarify_feature(feature)
                    
                    size = max(orig_width, orig_height)
                    print(f"  {name}: {orig_width:.1f} x {orig_height:.1f} -> {size:.1f} x {size:.1f}")
            else:
                print(f"  {name}: {geom_type} (skipped)")
        
        geojson_data['features'] = features
    else:
        raise ValueError(f"Unexpected GeoJSON type: {geojson_data.get('type')}")
    
    # Create output filename
    output_path = input_path.parent / f"squared_{input_path.name}"
    
    # Write output GeoJSON
    print(f"\nWriting: {output_path}")
    with open(output_path, 'w') as f:
        json.dump(geojson_data, f, indent=2)
    
    print(f"✓ Done! Created {len(features)} squared features")
    
    return output_path


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("\nError: No input file specified")
        sys.exit(1)
    
    input_file = sys.argv[1]
    
    try:
        output_file = squarify_geojson(input_file)
        print(f"\n✓ Successfully created: {output_file}")
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

