#!/usr/bin/env python3
"""
Backfill ContentType and ContentDisposition on existing email photos in S3.

S3 doesn't support in-place metadata updates — each object is copied to itself
with the new metadata. Run this once after deploying the public-read CDK change.

Usage:
    python3 scripts/backfill_photo_metadata.py [--dry-run] [--profile PROFILE]

Options:
    --dry-run   Print what would be updated without making changes
    --profile   AWS profile name (default: atlas)
"""
import argparse
import mimetypes
import sys
import boto3

BUCKET = "scs-atlas-data"
PHOTO_PATH_SEGMENT = "/media/email_photos/"


def backfill(dry_run: bool, profile: str):
    session = boto3.Session(profile_name=profile)
    s3 = session.client("s3")

    paginator = s3.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=BUCKET)

    updated = 0
    skipped = 0

    for page in pages:
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if PHOTO_PATH_SEGMENT not in key:
                continue

            # Check current metadata
            head = s3.head_object(Bucket=BUCKET, Key=key)
            current_ct = head.get("ContentType", "")
            current_cd = head.get("ContentDisposition", "")

            content_type = mimetypes.guess_type(key)[0] or "application/octet-stream"

            if current_ct == content_type and current_cd == "inline":
                skipped += 1
                continue

            print(f"{'[dry-run] ' if dry_run else ''}Updating {key}")
            print(f"  ContentType: {current_ct!r} -> {content_type!r}")
            print(f"  ContentDisposition: {current_cd!r} -> 'inline'")

            if not dry_run:
                s3.copy_object(
                    Bucket=BUCKET,
                    CopySource={"Bucket": BUCKET, "Key": key},
                    Key=key,
                    ContentType=content_type,
                    ContentDisposition="inline",
                    MetadataDirective="REPLACE",
                )
            updated += 1

    print(f"\nDone. {updated} updated, {skipped} already correct.")
    if dry_run and updated:
        print("Re-run without --dry-run to apply changes.")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--profile", default="atlas")
    args = parser.parse_args()

    backfill(dry_run=args.dry_run, profile=args.profile)


if __name__ == "__main__":
    main()
