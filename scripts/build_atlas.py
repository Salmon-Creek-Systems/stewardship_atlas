#!/usr/bin/env python3
"""
Build a stewardship atlas from a GeoJSON configuration file.

Usage:
    python build_atlas.py <geojson_path>          # full atlas creation
    python build_atlas.py config_only <geojson_path>  # regenerate config only

To call from Python or a notebook:
    import atlas
    atlas.build_atlas_from_geojson('/path/to/scvfd.geojson', config_only=True)
"""

import sys
from pathlib import Path

script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir.parent / 'python'))

import atlas


def main():
    if len(sys.argv) == 3 and sys.argv[1] == 'config_only':
        geojson_path = Path(sys.argv[2])
        config_only = True
    elif len(sys.argv) == 2:
        geojson_path = Path(sys.argv[1])
        config_only = False
    else:
        print(f"Usage: {sys.argv[0]} <geojson_path>", file=sys.stderr)
        print(f"       {sys.argv[0]} config_only <geojson_path>", file=sys.stderr)
        sys.exit(1)

    if not geojson_path.exists():
        print(f"Error: GeoJSON file not found: {geojson_path}", file=sys.stderr)
        sys.exit(1)

    try:
        config, config_path = atlas.build_atlas_from_geojson(geojson_path, config_only=config_only)
    except (ValueError, FileNotFoundError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(config_path)


if __name__ == '__main__':
    main()
