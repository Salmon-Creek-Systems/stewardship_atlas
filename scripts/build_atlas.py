#!/usr/bin/env python3
"""
Build a stewardship atlas from its single-file GeoJSON configuration.

Usage:
    python build_atlas.py <atlas>                     # full atlas build
    python build_atlas.py config_only <atlas>         # regenerate config only
    python build_atlas.py config_only <geojson_path>  # ...from a specific file

The work happens inside a session (issue #159): the atlas is locked, hydrated
from S3 into the workspace, built there, and written back. `ATLAS_WORKSPACE_ROOT`
must point at a local directory to hydrate into — the same one the webapp uses,
so the two share a warm cache.

The source GeoJSON is resolved in this order:
  1. an explicit path argument, if it names a file;
  2. `staging/atlas.geojson` inside the atlas — the form that travels with it;
  3. `configuration/{atlas}.geojson` in this checkout.

To call from Python or a notebook:
    import atlas
    atlas.build_atlas_from_geojson('/path/to/scvfd.geojson', config_only=True)
"""

import json
import sys
from pathlib import Path

script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir.parent / 'python'))

import atlas
import atlas_session


def resolve_target(target: str):
    """(atlas_name, explicit source path or None) from one argument.

    A path is accepted because that is how this has always been called, and
    because a brand-new atlas's source can only be a file in the checkout.
    """
    path = Path(target)
    if path.is_file():
        with open(path) as handle:
            data = json.load(handle)
        try:
            name = data['features'][0]['properties']['name']
        except (KeyError, IndexError, TypeError):
            raise ValueError(f"{path} has no features[0].properties.name")
        return name, path
    if '/' in target or target.endswith('.geojson'):
        raise FileNotFoundError(f"GeoJSON file not found: {target}")
    return target, None


def main():
    if len(sys.argv) == 3 and sys.argv[1] == 'config_only':
        target, config_only = sys.argv[2], True
    elif len(sys.argv) == 2:
        target, config_only = sys.argv[1], False
    else:
        print(f"Usage: {sys.argv[0]} [config_only] <atlas|geojson_path>",
              file=sys.stderr)
        sys.exit(1)

    try:
        atlas_name, source = resolve_target(target)
    except (ValueError, FileNotFoundError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    purpose = f"build_atlas{' config_only' if config_only else ''}"
    try:
        # require_config=False: a first build has nothing in S3 to load, and a
        # rebuild is about to replace the config anyway.
        with atlas_session.open_session(atlas_name, purpose=purpose,
                                        require_config=False) as session:
            geojson_path = source
            if geojson_path is None:
                geojson_path = atlas.source_geojson_path(
                    {'name': atlas_name, 'data_root': str(session.workspace_root)})
            if geojson_path is None:
                print(f"Error: no source GeoJSON for '{atlas_name}' in "
                      f"staging/atlas.geojson or configuration/{atlas_name}.geojson",
                      file=sys.stderr)
                sys.exit(1)

            config, config_path = atlas.build_atlas_from_geojson(
                geojson_path, config_only=config_only,
                data_root=session.workspace_root)
    except (ValueError, FileNotFoundError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(config_path)


if __name__ == '__main__':
    main()
