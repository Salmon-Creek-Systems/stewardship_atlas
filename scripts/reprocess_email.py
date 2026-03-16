#!/usr/bin/env python3
"""
Retrigger Lambda processing for an email stuck in the S3 ingress bucket.

Copies the object with a metadata change, which fires a new ObjectCreated
event and causes the Lambda to reprocess it.

Usage:
    python scripts/reprocess_email.py <s3-object-key>
    python scripts/reprocess_email.py --list                  # show stuck emails
    python scripts/reprocess_email.py --profile atlas         # AWS profile (default: atlas)

Examples:
    python scripts/reprocess_email.py incoming/63hfbll9b84j2...
    python scripts/reprocess_email.py --list
"""

import argparse
import sys
import boto3
from botocore.exceptions import ClientError
from datetime import datetime, timezone

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
    parser = argparse.ArgumentParser(description="Retrigger stuck email Lambda processing.")
    parser.add_argument("key", nargs="?", help="S3 object key to reprocess")
    parser.add_argument("--list", action="store_true", help="List stuck emails")
    parser.add_argument("--profile", default="atlas", help="AWS profile (default: atlas)")
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    s3 = session.client("s3")

    if args.list or not args.key:
        list_stuck(s3)
    else:
        reprocess(s3, args.key)


if __name__ == "__main__":
    main()
