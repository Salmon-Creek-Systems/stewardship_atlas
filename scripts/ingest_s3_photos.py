#!/usr/bin/env python3
"""
Batch-ingest geotagged photos from an S3 directory or zip file into an atlas layer.

Photos must have GPS EXIF data. Each valid photo becomes a point feature
in the target layer, identical to what the email photo inlet produces.
Photos without GPS are skipped and reported on stdout.

Usage:
    python3 scripts/ingest_s3_photos.py <atlas_name> s3://<bucket>/<prefix> [options]
    python3 scripts/ingest_s3_photos.py <atlas_name> s3://<bucket>/<path/to/batch.zip> [options]

    SWALES_ROOT env var must be set (same as running the webapp).

Options:
    --layer LAYER       target layer name (default: config's email_photo_default_layer, then "poi")
    --sender TEXT       provenance string in feature properties (default: "s3_import")
    --dry-run           print what would be ingested without writing anything
    --profile PROFILE   AWS profile name (default: atlas)

Example:
    SWALES_ROOT=/root/swales_dev \\
    python3 scripts/ingest_s3_photos.py scvfd s3://my-bucket/field-photos/2026-04/ --dry-run
"""
import argparse
import io
import json
import mimetypes
import os
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import boto3

# Allow importing from python/
sys.path.insert(0, str(Path(__file__).parent.parent / 'python'))

import dataswale_geojson
import deltas_geojson
import email_inlet

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.tiff', '.tif'}
MEDIA_BUCKET = 'scs-atlas-data'


def parse_s3_url(s3_url: str):
    """Parse 's3://bucket/prefix' into (bucket, prefix)."""
    if not s3_url.startswith('s3://'):
        raise ValueError(f"S3 URL must start with s3://: {s3_url}")
    parts = s3_url[5:].split('/', 1)
    bucket = parts[0]
    prefix = parts[1] if len(parts) > 1 else ''
    return bucket, prefix


def _ingest_directory(s3, bucket, prefix, args):
    """List and process image objects under an S3 prefix."""
    paginator = s3.get_paginator('list_objects_v2')
    features = []
    n_skip = 0

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get('Contents', []):
            key = obj['Key']
            if Path(key).suffix.lower() not in IMAGE_EXTENSIONS:
                continue

            try:
                resp = s3.get_object(Bucket=bucket, Key=key)
                image_bytes = resp['Body'].read()
            except Exception as e:
                print(f"SKIP {key}: download error — {e}")
                n_skip += 1
                continue

            gps = email_inlet.extract_gps(image_bytes)
            if not gps:
                print(f"SKIP {key}: no GPS data")
                n_skip += 1
                continue
            if 'lat' not in gps or 'lon' not in gps:
                print(f"SKIP {key}: incomplete GPS ({gps})")
                n_skip += 1
                continue

            image_url = f"https://{bucket}.s3.amazonaws.com/{key}"
            extra_props = {k: v for k, v in gps.items() if k not in ('lat', 'lon')}
            feature = email_inlet.build_feature(
                lat=gps['lat'], lon=gps['lon'],
                title=Path(key).stem, sender=args.sender,
                timestamp=obj['LastModified'].isoformat(),
                image_url=image_url, extra_props=extra_props,
            )
            features.append(feature)
            print(f"OK   {key}  lat={gps['lat']:.5f} lon={gps['lon']:.5f}")

    return features, n_skip


def _ingest_zip(s3, bucket, prefix, args):
    """Download a zip from S3, upload each image to MEDIA_BUCKET, and process."""
    try:
        resp = s3.get_object(Bucket=bucket, Key=prefix)
        zip_bytes = resp['Body'].read()
    except Exception as e:
        print(f"ERROR: could not download zip s3://{bucket}/{prefix} — {e}", file=sys.stderr)
        sys.exit(1)

    zip_stem = Path(prefix).stem
    features = []
    n_skip = 0

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        entries = [n for n in zf.namelist()
                   if Path(n).suffix.lower() in IMAGE_EXTENSIONS]
        print(f"Zip contains {len(entries)} image(s)")

        for name in entries:
            try:
                image_bytes = zf.read(name)
            except Exception as e:
                print(f"SKIP {name}: zip read error — {e}")
                n_skip += 1
                continue

            filename = Path(name).name
            dest_key = f"{args.atlas_name}/media/email_photos/{zip_stem}/{filename}"
            content_type = mimetypes.guess_type(filename)[0] or 'image/jpeg'
            try:
                s3.put_object(
                    Bucket=MEDIA_BUCKET, Key=dest_key, Body=image_bytes,
                    ContentType=content_type, ContentDisposition='inline',
                )
            except Exception as e:
                print(f"SKIP {name}: upload error — {e}")
                n_skip += 1
                continue

            image_url = f"https://{MEDIA_BUCKET}.s3.amazonaws.com/{dest_key}"

            gps = email_inlet.extract_gps(image_bytes)
            if not gps:
                print(f"SKIP {name}: no GPS data")
                n_skip += 1
                continue
            if 'lat' not in gps or 'lon' not in gps:
                print(f"SKIP {name}: incomplete GPS ({gps})")
                n_skip += 1
                continue

            zinfo = zf.getinfo(name)
            ts = datetime(*zinfo.date_time).isoformat()
            extra_props = {k: v for k, v in gps.items() if k not in ('lat', 'lon')}
            feature = email_inlet.build_feature(
                lat=gps['lat'], lon=gps['lon'],
                title=Path(name).stem, sender=args.sender,
                timestamp=ts, image_url=image_url,
                extra_props=extra_props,
            )
            features.append(feature)
            print(f"OK   {name}  lat={gps['lat']:.5f} lon={gps['lon']:.5f}")

    return features, n_skip


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('atlas_name', help='Atlas slug (e.g. scvfd)')
    parser.add_argument('s3_url', help='S3 location to scan, e.g. s3://bucket/prefix/ or s3://bucket/batch.zip')
    parser.add_argument('--layer', help='Target layer name (default: from atlas config)')
    parser.add_argument('--sender', default='s3_import', help='Provenance string (default: s3_import)')
    parser.add_argument('--dry-run', action='store_true', help='Preview only, no writes')
    parser.add_argument('--profile', default='atlas', help='AWS profile name (default: atlas)')
    args = parser.parse_args()

    # Load atlas config
    swales_root = os.environ.get('SWALES_ROOT')
    if not swales_root:
        print('ERROR: SWALES_ROOT environment variable is not set', file=sys.stderr)
        sys.exit(1)

    config_path = Path(swales_root) / args.atlas_name / 'staging' / 'atlas_config.json'
    if not config_path.exists():
        print(f'ERROR: Atlas config not found at {config_path}', file=sys.stderr)
        sys.exit(1)

    config = json.load(open(config_path))

    # Resolve layer name
    layer_name = args.layer or config.get('email_photo_default_layer', 'poi')
    layer_names = {l['name'] for l in config['dataswale'].get('layers', [])}
    if layer_name not in layer_names:
        print(f"ERROR: Layer '{layer_name}' not found in atlas. Available: {sorted(layer_names)}", file=sys.stderr)
        sys.exit(1)

    # Parse S3 URL
    try:
        bucket, prefix = parse_s3_url(args.s3_url)
    except ValueError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)

    print(f"Atlas:   {args.atlas_name}")
    print(f"Layer:   {layer_name}")
    print(f"Source:  s3://{bucket}/{prefix}")
    print(f"Sender:  {args.sender}")
    if args.dry_run:
        print("Mode:    dry-run (no writes)")
    print()

    session = boto3.Session(profile_name=args.profile)
    s3 = session.client('s3')

    if prefix.endswith('.zip'):
        features, n_skip = _ingest_zip(s3, bucket, prefix, args)
    else:
        features, n_skip = _ingest_directory(s3, bucket, prefix, args)

    print()
    print(f"Result: {len(features)} OK, {n_skip} skipped")

    if not features:
        print("Nothing to ingest.")
        return

    if args.dry_run:
        print("Dry-run: no delta written.")
        return

    # Write single delta for the whole batch
    fc = {"type": "FeatureCollection", "features": features}
    deltas_geojson.add_deltas_from_features(config, "s3_import", fc, "create", layer_name=layer_name)

    # Refresh layer
    dataswale_geojson.refresh_vector_layer(config, layer_name)

    print(f"Wrote {len(features)} features to layer '{layer_name}'.")


if __name__ == '__main__':
    main()
