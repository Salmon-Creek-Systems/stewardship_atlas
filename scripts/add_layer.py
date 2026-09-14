#!/usr/bin/env python3
"""Add a new S3-backed vector layer to an existing atlas.

The GeoJSON file must ALREADY be uploaded to S3. By default the tool expects it
at:

    s3://scs-internal/{atlas}/imports/{layer}.geojson

Override the location with --s3-key (and --s3-bucket). The tool registers the
layer plus a same-named s3_geojson inlet, wires it into consumer outlets
(webmap, webedit, sqldb by default), rebuilds the config, and materializes.

The layer is styled dark green with thick lines / big dots by default. The
inlet clips to the atlas bounding box, so features outside the atlas area are
dropped.

Runs inside a session (issue #159): the atlas is locked, hydrated from S3 into
the workspace, edited there, and written back. `ATLAS_WORKSPACE_ROOT` must name
a local directory to hydrate into.

    python scripts/add_layer.py scvfd trailheads
    python scripts/add_layer.py scvfd fireline --geometry linestring
    python scripts/add_layer.py scvfd parcels --geometry polygon \\
        --s3-key incoming/parcels_2026.geojson --consumers webmap,sqldb

Follow-up: the layer shows in the webmap/console immediately. To show feature
attributes in the map popup, add `show_attributes` + `editable_columns` to the
layer in the source geojson once you know its fields.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "python"))
import atlas
import atlas_session


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("atlas", help="atlas slug (e.g. scvfd)")
    ap.add_argument("layer", help="new layer name")
    ap.add_argument("--s3-key",
                    help="S3 key of the geojson (default {atlas}/imports/{layer}.geojson)")
    ap.add_argument("--s3-bucket", default="scs-internal")
    ap.add_argument("--geometry", default="point",
                    choices=["point", "linestring", "polygon"])
    ap.add_argument("--color", default=None,
                    help="hex color like '#FFAA33' (default: dark green)")
    ap.add_argument("--consumers",
                    help="comma-separated consumer asset names "
                         "(default: webmap,webedit,sqldb)")
    ap.add_argument("--no-rebuild", action="store_true",
                    help="skip build_atlas config_only (for inspection)")
    ap.add_argument("--no-materialize", action="store_true",
                    help="edit config + rebuild but don't materialize")
    args = ap.parse_args()

    consumers = [c.strip() for c in args.consumers.split(",")] if args.consumers else None
    with atlas_session.open_session(args.atlas,
                                    purpose=f"add_layer {args.layer}") as session:
        atlas.add_layer(
            session.config, args.layer,
            s3_key=args.s3_key, s3_bucket=args.s3_bucket,
            geometry_type=args.geometry, color=args.color, consumers=consumers,
            rebuild=not args.no_rebuild, run_materialize=not args.no_materialize)


if __name__ == "__main__":
    main()
