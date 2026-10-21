#!/usr/bin/env python3
"""
Convert a GeoTIFF to a Cloud-Optimized GeoTIFF and publish it as a shared source.

This is deliberately *not* an inlet. An inlet imports data into one atlas at that
atlas's bbox, and gets re-run whenever the layer is rebuilt. This is a one-time
conversion of a third-party file into a format many atlases can read remotely:
it has no atlas, no version, and no business being re-triggered by a layer
refresh. See `cog_source` in raster_inlets.py for the consuming half, which
windows the published COG into an atlas with gdalwarp over /vsicurl/.

Published layout, under a prefix that is deliberately not an atlas name:

    s3://{bucket}/sources/rasters/{name}/{name}.cog.tif
    s3://{bucket}/sources/rasters/{name}/{name}.provenance.json

The `sources/rasters/` shape is not arbitrary. scs-atlas-data's bucket policy
already grants public s3:GetObject on `arn:aws:s3:::scs-atlas-data/*/rasters/*`,
and S3 ARN wildcards span `/`, so this key is world-readable under the existing
policy with no IAM change — the same grant the per-atlas `{atlas}/rasters/...`
keys from the s3_upload outlet rely on. It occupies the atlas-name slot with
`sources`, which is why no atlas may be named `sources`.

Usage:
    python3 scripts/publish_source_cog.py <input.tif> <source_name> [options]

Example:
    python3 scripts/publish_source_cog.py \\
        /root/data/westport_dem_2m_hillshade.tiff mendocino_coast_hillshade_2m \\
        --dry-run
"""
import argparse
import json
import hashlib
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_BUCKET = 'scs-atlas-data'
DEFAULT_PREFIX = 'sources/rasters'
DEFAULT_REGION = 'us-east-1'


def run(cmd, dry_run=False, capture=False):
    """Run a subprocess, echoing it so the transcript records what was done."""
    print('  $ ' + ' '.join(str(c) for c in cmd))
    if dry_run:
        return None
    if capture:
        return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    subprocess.run(cmd, check=True)
    return None


def probe(path):
    """gdalinfo -json for the input raster."""
    try:
        out = subprocess.run(['gdalinfo', '-json', str(path)],
                             check=True, capture_output=True, text=True).stdout
    except FileNotFoundError:
        sys.exit("ERROR: gdalinfo not found — this runs on the server, not the laptop")
    except subprocess.CalledProcessError as e:
        sys.exit(f"ERROR: gdalinfo failed on {path}:\n{e.stderr}")
    return json.loads(out)


def describe(info):
    """The parts of gdalinfo we record and make decisions from."""
    bands = info.get('bands', [])
    return {
        'size': info.get('size'),
        'band_count': len(bands),
        'band_types': [b.get('type') for b in bands],
        'nodata': bands[0].get('noDataValue') if bands else None,
        'crs': (info.get('coordinateSystem', {}).get('wkt', '') or '')[:120],
        'corner_coordinates': info.get('cornerCoordinates'),
        'wgs84_extent': info.get('wgs84Extent'),
    }


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def build_cog(src, out_path, meta, args):
    """Warp to EPSG:3857 and write a COG, optionally expanding grey to RGB.

    maplibre-cog-protocol renders a 3- or 4-band COG as an image with no colour
    directive at all, which is what a hillshade wants — a Brewer ramp over
    grey terrain is both wrong and an extra thing to configure. A single-band
    source is therefore expanded through a VRT, which costs no pixel copy.
    """
    warp_input = src
    tmp_vrt = None

    if args.rgb:
        tmp_vrt = out_path.parent / f'{out_path.stem}_rgb.vrt'
        run(['gdal_translate', '-of', 'VRT', '-b', '1', '-b', '1', '-b', '1',
             str(src), str(tmp_vrt)], args.dry_run)
        warp_input = tmp_vrt

    creation = {'COMPRESS': 'DEFLATE', 'BLOCKSIZE': '512',
                'OVERVIEW_RESAMPLING': args.resampling.upper()}
    for opt in args.co:
        key, _, value = opt.partition('=')
        creation[key.upper()] = value

    cmd = ['gdalwarp', '-t_srs', 'EPSG:3857', '-r', args.resampling, '-of', 'COG']
    for key, value in creation.items():
        cmd += ['-co', f'{key}={value}']

    # Source nodata of 0 is why this matters: without an alpha band the
    # no-coverage area renders as opaque black rather than as nothing.
    if args.alpha:
        cmd += ['-dstalpha']
    if meta['nodata'] is not None:
        cmd += ['-srcnodata', str(meta['nodata'])]

    cmd += [str(warp_input), str(out_path)]
    run(cmd, args.dry_run)

    if tmp_vrt is not None and not args.dry_run and tmp_vrt.exists():
        tmp_vrt.unlink()

    return creation


def main():
    ap = argparse.ArgumentParser(
        description='Convert a GeoTIFF to a COG and publish it as a shared source.')
    ap.add_argument('input', help='path to the source GeoTIFF')
    ap.add_argument('source_name',
                    help='stable slug for this source, e.g. mendocino_coast_hillshade_2m')
    ap.add_argument('--bucket', default=DEFAULT_BUCKET)
    ap.add_argument('--prefix', default=DEFAULT_PREFIX)
    ap.add_argument('--region', default=DEFAULT_REGION)
    ap.add_argument('--profile', default='atlas', help='AWS profile (default: atlas)')
    ap.add_argument('--resampling', default='bilinear',
                    help='gdalwarp -r value (default: bilinear)')
    ap.add_argument('--co', action='append', default=[], metavar='KEY=VALUE',
                    help='extra COG creation option, repeatable; overrides the '
                         'defaults (COMPRESS=DEFLATE, BLOCKSIZE=512, '
                         'OVERVIEW_RESAMPLING). PREDICTOR=2 is worth trying on '
                         'continuous-tone imagery. Stay with a compression '
                         'geotiff.js can decode — DEFLATE, LZW or JPEG — since '
                         'WEBP would render server-side but not in the browser.')
    ap.add_argument('--rgb', dest='rgb', action='store_true', default=None,
                    help='expand a single band to 3-band RGB (default: auto)')
    ap.add_argument('--no-rgb', dest='rgb', action='store_false')
    ap.add_argument('--no-alpha', dest='alpha', action='store_false', default=True,
                    help='do not add an alpha band from nodata')
    ap.add_argument('--workdir', help='where to write the COG (default: a temp dir)')
    ap.add_argument('--keep', action='store_true', help='keep the local COG after upload')
    ap.add_argument('--checksum', action='store_true',
                    help='record a sha256 of the source (slow on large files)')
    ap.add_argument('--note', default='', help='free text recorded in the provenance file')
    ap.add_argument('--dry-run', action='store_true',
                    help='print what would happen, convert nothing, upload nothing')
    args = ap.parse_args()

    src = Path(args.input)
    if not src.exists() and not args.dry_run:
        sys.exit(f"ERROR: no such file: {src}")

    print(f"Source: {src}")
    info = probe(src) if src.exists() else {}
    meta = describe(info) if info else {'band_count': 1, 'nodata': None}
    print(f"  {meta.get('size')} px, {meta['band_count']} band(s) "
          f"{meta.get('band_types')}, nodata={meta['nodata']}")

    # Auto: a single-band source becomes RGB so it needs no cog_color to render.
    if args.rgb is None:
        args.rgb = meta['band_count'] == 1
        print(f"  --rgb not given; {'expanding to RGB' if args.rgb else 'keeping bands as-is'}")

    workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix='source_cog_'))
    workdir.mkdir(parents=True, exist_ok=True)
    out_path = workdir / f'{args.source_name}.cog.tif'

    print(f"Converting -> {out_path}")
    creation_used = build_cog(src, out_path, meta, args)

    key_base = f"{args.prefix}/{args.source_name}/{args.source_name}"
    cog_key = f"{key_base}.cog.tif"
    prov_key = f"{key_base}.provenance.json"
    url = f"https://{args.bucket}.s3.{args.region}.amazonaws.com/{cog_key}"

    out_info = probe(out_path) if out_path.exists() else {}
    provenance = {
        'source_name': args.source_name,
        'published_utc': datetime.now(timezone.utc).isoformat(),
        'published_by': os.environ.get('USER', 'unknown'),
        'note': args.note,
        'input': {
            'path': str(src),
            'bytes': src.stat().st_size if src.exists() else None,
            'modified_utc': (datetime.fromtimestamp(src.stat().st_mtime, timezone.utc).isoformat()
                             if src.exists() else None),
            'sha256': sha256(src) if (args.checksum and src.exists() and not args.dry_run) else None,
            **meta,
        },
        'output': {
            'url': url,
            'bytes': out_path.stat().st_size if out_path.exists() else None,
            **(describe(out_info) if out_info else {}),
        },
        'conversion': {
            'target_crs': 'EPSG:3857',
            'resampling': args.resampling,
            'creation_options': creation_used,
            'expanded_to_rgb': args.rgb,
            'alpha_from_nodata': args.alpha,
            'gdal_version': (subprocess.run(['gdalinfo', '--version'], capture_output=True,
                                            text=True).stdout.strip()
                             if not args.dry_run else None),
        },
    }

    prov_path = workdir / f'{args.source_name}.provenance.json'
    print(f"Provenance -> {prov_path}")
    if not args.dry_run:
        prov_path.write_text(json.dumps(provenance, indent=2) + '\n')

    print(f"Uploading -> s3://{args.bucket}/{cog_key}")
    if not args.dry_run:
        import boto3
        s3 = boto3.Session(profile_name=args.profile).client('s3')
        s3.upload_file(str(out_path), args.bucket, cog_key,
                       ExtraArgs={'ContentType': 'image/tiff'})
        s3.upload_file(str(prov_path), args.bucket, prov_key,
                       ExtraArgs={'ContentType': 'application/json'})

    if not args.keep and not args.dry_run and out_path.exists():
        out_path.unlink()
        print(f"Removed local {out_path} (--keep to retain)")

    print()
    print("Published. Point a cog_source inlet at:")
    print(f"  {url}")
    if args.dry_run:
        print("\n(dry run — nothing was converted or uploaded)")


if __name__ == '__main__':
    main()
