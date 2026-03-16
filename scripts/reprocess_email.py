#!/usr/bin/env python3
"""
Inspect and retrigger emails stuck in the S3 ingress bucket.

Usage:
    python scripts/reprocess_email.py --list                  # show stuck emails
    python scripts/reprocess_email.py --inspect <key>         # show sender, subject, GPS status
    python scripts/reprocess_email.py <key>                   # retrigger Lambda processing
    python scripts/reprocess_email.py --profile atlas         # AWS profile (default: atlas)

Examples:
    python scripts/reprocess_email.py --list
    python scripts/reprocess_email.py --inspect incoming/63hfbll9b84j2...
    python scripts/reprocess_email.py incoming/63hfbll9b84j2...
"""

import argparse
import email
import io
import sys
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

BUCKET = "atlas-ingress"
PREFIX = "incoming/"


def list_stuck(s3):
    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=PREFIX)
    objects = sorted(resp.get("Contents", []), key=lambda o: o["LastModified"], reverse=True)
    if not objects:
        print("No emails currently in ingress bucket.")
        return
    print(f"{len(objects)} email(s) in bucket (not yet successfully processed):\n")
    for obj in objects:
        age = datetime.now(timezone.utc) - obj["LastModified"]
        print(f"  {obj['Key']}  ({obj['Size']:,} bytes, {int(age.total_seconds() // 60)}m ago)")


def inspect(s3, key):
    from PIL import Image
    from PIL.ExifTags import TAGS, GPSTAGS

    full_key = key if key.startswith(PREFIX) else PREFIX + key
    print(f"Inspecting: {full_key}\n")

    obj = s3.get_object(Bucket=BUCKET, Key=full_key)
    raw = obj["Body"].read()
    msg = email.message_from_bytes(raw)

    print(f"  From:    {msg.get('From', '(none)')}")
    print(f"  Subject: {msg.get('Subject', '(empty)')}")
    print(f"  To:      {msg.get('To', '(none)')}")
    print()

    image_part = None
    for part in msg.walk():
        if part.get_content_maintype() == "image":
            image_part = part
            break

    if not image_part:
        print("  No image attachment found.")
        return

    image_bytes = image_part.get_payload(decode=True)
    filename = image_part.get_filename() or "unknown"
    print(f"  Image: {filename} ({len(image_bytes):,} bytes)")

    try:
        img = Image.open(io.BytesIO(image_bytes))
        exif = img._getexif()
    except Exception as e:
        print(f"  Could not read EXIF: {e}")
        return

    if not exif:
        print("  EXIF: none")
        return

    tags = {TAGS.get(k, k): v for k, v in exif.items()}

    make = tags.get("Make", "")
    model = tags.get("Model", "")
    dt = tags.get("DateTime", "")
    if make or model:
        print(f"  Camera: {make} {model}".strip())
    if dt:
        print(f"  Taken:  {dt}")

    if "GPSInfo" in tags:
        gps_raw = {GPSTAGS.get(k, k): v for k, v in tags["GPSInfo"].items()}

        def to_decimal(dms, ref):
            d, m, s = dms
            dec = float(d) + float(m) / 60 + float(s) / 3600
            return -dec if ref in ("S", "W") else dec

        try:
            lat = to_decimal(gps_raw["GPSLatitude"], gps_raw["GPSLatitudeRef"])
            lon = to_decimal(gps_raw["GPSLongitude"], gps_raw["GPSLongitudeRef"])
            print(f"  GPS:    ✓  lat={lat:.6f}, lon={lon:.6f}")
        except KeyError:
            print(f"  GPS:    partial data — {list(gps_raw.keys())}")
    else:
        print("  GPS:    ✗  No GPS data in EXIF — submission will fail.")
        print("           Check iOS Settings → Privacy → Location Services → Camera.")


def reprocess(s3, key):
    full_key = key if key.startswith(PREFIX) else PREFIX + key
    print(f"Retriggering: {full_key}")
    try:
        s3.copy_object(
            Bucket=BUCKET,
            CopySource={"Bucket": BUCKET, "Key": full_key},
            Key=full_key,
            MetadataDirective="REPLACE",
            Metadata={"reprocessed": "true"},
        )
        print("Done — Lambda should trigger within a few seconds.")
    except ClientError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Inspect and retrigger stuck email submissions.")
    parser.add_argument("key", nargs="?", help="S3 object key to reprocess")
    parser.add_argument("--list", action="store_true", help="List stuck emails")
    parser.add_argument("--inspect", metavar="KEY", help="Show sender, subject, and GPS status")
    parser.add_argument("--profile", default="atlas", help="AWS profile (default: atlas)")
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    s3 = session.client("s3")

    if args.inspect:
        inspect(s3, args.inspect)
    elif args.list or not args.key:
        list_stuck(s3)
    else:
        reprocess(s3, args.key)


if __name__ == "__main__":
    main()
