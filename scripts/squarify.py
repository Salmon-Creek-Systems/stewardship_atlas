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

sys.path.insert(0, str(Path(__file__).parent.parent / "python"))

import utils





# The transform lives in utils so the s3_geojson inlet and this CLI stay in
# step; a region squared on import and one squared by hand must not differ.
get_bbox = utils.geojson_bbox
create_square_from_bbox = utils.square_from_bbox
squarify_feature = utils.squarify_feature


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
        
        squared_features = []
        for i, feature in enumerate(features):
            geom_type = feature.get('geometry', {}).get('type', '')
            name = feature.get('properties', {}).get('name', f"feature_{i}")
            
            if geom_type in ['Polygon', 'MultiPolygon']:
                original_bbox = get_bbox(feature['geometry']['coordinates'])
                if original_bbox:
                    min_x, min_y, max_x, max_y = original_bbox
                    orig_width = max_x - min_x
                    orig_height = max_y - min_y
                    
                    squared_feature = squarify_feature(feature)
                    squared_features.append(squared_feature)
                    
                    size = max(orig_width, orig_height)
                    # Verify the square was created
                    new_bbox = get_bbox(squared_feature['geometry']['coordinates'])
                    if new_bbox:
                        new_width = new_bbox[2] - new_bbox[0]
                        new_height = new_bbox[3] - new_bbox[1]
                        print(f"  {name}: {orig_width:.1f} x {orig_height:.1f} -> {new_width:.1f} x {new_height:.1f} ✓")
                    else:
                        print(f"  {name}: {orig_width:.1f} x {orig_height:.1f} -> ERROR")
                else:
                    # Invalid bbox, keep original
                    squared_features.append(feature)
            else:
                # Not a polygon, keep original
                print(f"  {name}: {geom_type} (skipped)")
                squared_features.append(feature)
        
        geojson_data['features'] = squared_features
    else:
        raise ValueError(f"Unexpected GeoJSON type: {geojson_data.get('type')}")
    
    # Create output filename
    output_path = input_path.parent / f"squared_{input_path.name}"
    
    # Write output GeoJSON
    print(f"\nWriting: {output_path}")
    with open(output_path, 'w') as f:
        json.dump(geojson_data, f, indent=2)
    
    output_feature_count = len(geojson_data['features'])
    print(f"✓ Done! Created {output_feature_count} squared features")
    
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

