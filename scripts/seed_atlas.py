#!/usr/bin/env python3
"""Seed S3 from the box's on-disk atlases — the one-time move of the source of
truth (Phase 3 Step 3 task 8, issue #159).

Run on the box, where /root/swales_dev and /root/data are.

    # what would be carried, per atlas, by directory
    python scripts/seed_atlas.py --all --dry-run

    # one atlas
    python scripts/seed_atlas.py kennedy

    # all fifteen
    python scripts/seed_atlas.py --all

Staging only: a version used to be a full copy of staging, and the box holds
ten for westport alone. The first publish after seeding creates the version
that matters; the older ones stay on the EBS snapshot.

Shared `/root/data` files are seeded per atlas, from the files its config
actually names — only ~20 of them are referenced anywhere, and one
*unreferenced* file there is 4.9 GB.

Env:
    SWALES_ROOT           default /root/swales_dev
    ATLAS_SHARED_DIR      default /root/data
    ATLAS_PRIVATE_BUCKET  default from atlas_store (prod)

Nothing here needs a session: an atlas that is not in S3 yet cannot be locked
by anyone, and a re-run skips objects whose bytes are already there. Seed
before anything starts writing through the API.
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'python'))

import atlas_seed
import atlas_shared
import atlas_store
import atlas_workspace as ws


def load_config(staging_dir: Path):
    path = staging_dir / 'atlas_config.json'
    if not path.is_file():
        return None
    try:
        with open(path) as handle:
            return json.load(handle)
    except ValueError as exc:
        print(f"    WARNING: {path} is not readable JSON ({exc}); "
              f"no shared files will be seeded", file=sys.stderr)
        return None


def seed_one(client, bucket, atlas_name, args) -> dict:
    staging_dir = Path(args.swales_root) / atlas_name / 'staging'
    if not staging_dir.is_dir():
        print(f"  SKIP {atlas_name}: no staging tree at {staging_dir}")
        return {'atlas': atlas_name, 'status': 'missing'}

    if not args.force and not args.dry_run and ws.is_seeded(client, bucket, atlas_name):
        print(f"  SKIP {atlas_name}: already seeded (use --force to re-run)")
        return {'atlas': atlas_name, 'status': 'already-seeded'}

    plan = atlas_seed.plan_staging_upload(
        staging_dir,
        include_delta_archive=not args.skip_delta_archive,
        skip_outlets=args.skip_outlet)

    config = load_config(staging_dir)
    shared_plan, missing, bundled = ([], [], [])
    if config is not None:
        shared_plan, missing, bundled = atlas_seed.plan_shared_upload(
            config, args.shared_dir)

    totals = atlas_seed.summarize(plan)
    total_bytes = sum(v['bytes'] for v in totals.values())
    print(f"  {atlas_name}: {len(plan)} file(s), {atlas_seed.human(total_bytes)}"
          f"  +{len(shared_plan)} shared")
    if args.verbose or args.dry_run:
        for name in sorted(totals, key=lambda k: -totals[k]['bytes'])[:args.top]:
            entry = totals[name]
            print(f"      {atlas_seed.human(entry['bytes']):>9}  "
                  f"{entry['files']:>5} files  {name}")
    if missing:
        print(f"      MISSING shared input(s) with no fallback: "
              f"{', '.join(missing)}")
    if bundled and args.verbose:
        print(f"      not in {args.shared_dir}, using the repo's bundled copy: "
              f"{', '.join(bundled)}")

    result = {'atlas': atlas_name, 'status': 'ok', 'files': len(plan),
              'bytes': total_bytes, 'shared': len(shared_plan),
              'missing_shared': missing, 'bundled_shared': bundled}

    moved = atlas_seed.upload(
        client, bucket, plan, lambda rel: ws._key(atlas_name, rel),
        dry_run=args.dry_run)
    shared_moved = atlas_seed.upload(
        client, bucket, shared_plan, atlas_shared.shared_key,
        dry_run=args.dry_run)

    result['uploaded'] = moved['uploaded']
    result['skipped'] = moved['skipped'] + shared_moved['skipped']
    if not args.dry_run:
        print(f"      uploaded {moved['uploaded']} ({atlas_seed.human(moved['bytes'])}), "
              f"skipped {moved['skipped']} already present; "
              f"shared {shared_moved['uploaded']} uploaded, "
              f"{shared_moved['skipped']} present")
    return result


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('atlases', nargs='*', help='atlas names (default: --all)')
    ap.add_argument('--all', action='store_true',
                    help=f'seed all {len(atlas_seed.MIGRATE)} atlases on the migration list')
    ap.add_argument('--list', action='store_true', help='print the migration list and exit')
    ap.add_argument('--dry-run', action='store_true',
                    help='plan and report, upload nothing')
    ap.add_argument('--force', action='store_true',
                    help='seed even if the atlas already has a staging config in S3')
    ap.add_argument('--skip-delta-archive', action='store_true',
                    help="leave deltas/*/work out — it is the only record of applied "
                         "edits, so this loses history; roughly 2/3 of staging by size")
    ap.add_argument('--skip-outlet', action='append', default=[],
                    help='outlet directory to leave out (repeatable)')
    ap.add_argument('--swales-root', default=os.environ.get('SWALES_ROOT', '/root/swales_dev'))
    ap.add_argument('--shared-dir', default=os.environ.get('ATLAS_SHARED_DIR', '/root/data'))
    ap.add_argument('--bucket', default=None, help='private bucket (default: from env/config)')
    ap.add_argument('--top', type=int, default=12, help='directories to list per atlas')
    ap.add_argument('-v', '--verbose', action='store_true')
    args = ap.parse_args()

    if args.list:
        for name in atlas_seed.MIGRATE:
            print(name)
        return

    names = atlas_seed.MIGRATE if args.all else args.atlases
    if not names:
        ap.error('name at least one atlas, or pass --all')
    unknown = [n for n in names if n not in atlas_seed.MIGRATE]
    if unknown and not args.force:
        ap.error(f"not on the migration list: {', '.join(unknown)} "
                 f"(--force to seed anyway, --list to see it)")

    bucket = args.bucket or atlas_store.cloud_settings({})['private_bucket']
    client = atlas_store._s3()
    print(f"{'DRY RUN: ' if args.dry_run else ''}seeding {len(names)} atlas(es) "
          f"from {args.swales_root} to s3://{bucket}/")

    results = [seed_one(client, bucket, name, args) for name in names]

    total = sum(r.get('bytes', 0) for r in results)
    seeded = [r for r in results if r['status'] == 'ok']
    print(f"\n{'would seed' if args.dry_run else 'seeded'}: {len(seeded)} atlas(es), "
          f"{atlas_seed.human(total)}")
    for r in results:
        if r['status'] != 'ok':
            print(f"  {r['atlas']}: {r['status']}")
        elif r.get('missing_shared'):
            print(f"  {r['atlas']}: missing shared inputs "
                  f"{', '.join(r['missing_shared'])}")


if __name__ == '__main__':
    main()
