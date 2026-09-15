#!/usr/bin/env python3
"""Publish the site-wide web assets — everything the consoles fetch from
/local/ (issue #181).

    # what would be written, and what would be pruned
    python scripts/publish_site_assets.py --dry-run

    # publish to the outlets bucket and invalidate /local/*
    python scripts/publish_site_assets.py

    # build the tree locally and look at it, without touching S3
    python scripts/publish_site_assets.py --out-dir /tmp/site

The stylesheets, help pages, manuals and about/contact pages are built from
this repo by python/site_assets.py. They belong to no atlas, so this needs no
session and no lock: it writes the root of the outlets bucket, which the
distribution's default behaviour already serves at /, so key
`local/css/console.css` is `https://<host>/local/css/console.css` with no
CloudFront change at all.

Run it after changing templates/css, templates/help.html, documents/help/*.md,
the manuals, or documents/site/*.md. Nothing else regenerates these — an atlas
materialize deliberately no longer does.

Env:
    ATLAS_OUTLETS_BUCKET   default from atlas_store (prod)
    ATLAS_DISTRIBUTION_ID  default from atlas_store (prod); '' skips invalidation

Note: the `local/` prefix sits at the bucket root alongside the per-atlas
prefixes, so an atlas literally named `local` would collide with it. Renaming
the prefix to something honest is #182.
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'python'))

import atlas_store
import site_assets

REPO_ROOT = Path(__file__).parent.parent


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--bucket', default=os.environ.get('ATLAS_OUTLETS_BUCKET',
                                                       atlas_store.DEFAULT_OUTLETS_BUCKET))
    ap.add_argument('--distribution-id',
                    default=os.environ.get('ATLAS_DISTRIBUTION_ID',
                                           atlas_store.DEFAULT_DISTRIBUTION_ID))
    ap.add_argument('--out-dir', help='build here and stop; nothing is uploaded')
    ap.add_argument('--dry-run', action='store_true',
                    help='build and report the upload plan without writing to S3')
    args = ap.parse_args(argv)

    if args.out_dir:
        written = site_assets.build_site_assets(REPO_ROOT, args.out_dir)
        print(f"built {len(written)} files into {args.out_dir}")
        return 0

    with tempfile.TemporaryDirectory(prefix='atlas-site-') as tmp:
        written = site_assets.build_site_assets(REPO_ROOT, tmp)
        plan = atlas_store.plan_upload(tmp, site_assets.SITE_PREFIX)
        print(f"built {len(written)} files -> s3://{args.bucket}/{site_assets.SITE_PREFIX}/")

        if args.dry_run:
            existing = atlas_store.list_keys(args.bucket, f"{site_assets.SITE_PREFIX}/")
            stale = atlas_store.stale_keys(existing, [key for _, key, _ in plan])
            for _, key, _ in plan:
                print(f"  upload {key}")
            for key in stale:
                print(f"  prune  {key}")
            print(f"dry run: {len(plan)} to upload, {len(stale)} to prune")
            return 0

        client = atlas_store._s3()
        existing = atlas_store.list_keys(args.bucket, f"{site_assets.SITE_PREFIX}/",
                                         client=client)
        moved = atlas_store.upload_plan(args.bucket, plan, client=client)
        stale = atlas_store.stale_keys(existing, [key for _, key, _ in plan])
        pruned = atlas_store.delete_keys(args.bucket, stale, client=client)
        print(f"uploaded {len(plan)} files ({moved} bytes), pruned {pruned}")

        invalidation = atlas_store.invalidate_paths(
            args.distribution_id, [f"/{site_assets.SITE_PREFIX}/*"],
            caller_prefix='site-assets')
        if invalidation:
            print(f"invalidation {invalidation}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
