"""
Lambda handler for email photo inlet.

Triggered by S3 ObjectCreated events on the atlas-ingress bucket.
SES writes raw emails there; this handler unpacks them and POSTs to the webapp.

Environment variables:
  WEBAPP_URL  - base URL of the atlas webapp (e.g. https://atlas.fireatlas.org)
"""
import base64
import email
import json
import logging
import os
from datetime import datetime, timezone

import boto3
import requests

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")
WEBAPP_URL = os.environ["WEBAPP_URL"]


def extract_atlas_name(msg) -> str | None:
    """Derive atlas name from the To: header.

    Expects addresses like scvfd@fireatlas.org or scvfd@atlas.yourdomain.org.
    Returns the local part (before @) as the atlas name.
    """
    to_header = msg.get("To", "")
    # Handle "Display Name <addr@domain>" and plain "addr@domain"
    addr = to_header.strip()
    if "<" in addr:
        addr = addr.split("<")[1].rstrip(">")
    local = addr.split("@")[0].strip().lower()
    return local if local else None


def handler(event, context):
    record = event["Records"][0]
    bucket = record["s3"]["bucket"]["name"]
    key = record["s3"]["object"]["key"]

    logger.info(f"Processing s3://{bucket}/{key}")

    # Retrieve raw email
    obj = s3.get_object(Bucket=bucket, Key=key)
    raw_email = obj["Body"].read()

    # Parse email
    msg = email.message_from_bytes(raw_email)
    subject = msg.get("Subject", "")
    sender = msg.get("From", "")
    atlas_name = extract_atlas_name(msg)

    if not atlas_name:
        logger.error("Could not determine atlas name from To: header — skipping")
        return

    # Find first image attachment
    image_data = None
    filename = None
    for part in msg.walk():
        if part.get_content_maintype() == "image":
            image_data = base64.b64encode(part.get_payload(decode=True)).decode()
            filename = part.get_filename() or "photo.jpg"
            break

    if not image_data:
        logger.info("No image attachment found — skipping")
        return

    logger.info(f"Subject: {subject}")
    logger.info(f"From: {sender}")

    payload = {
        "subject": subject,
        "sender": sender,
        "atlas_name": atlas_name,
        "image_data": image_data,
        "filename": filename,
        "received_at": datetime.now(timezone.utc).isoformat(),
    }

    resp = requests.post(f"{WEBAPP_URL}/ingest/email_photo", json=payload, timeout=30)
    logger.info(f"Webapp response: {resp.status_code} {resp.text}")

    if resp.status_code == 200:
        s3.delete_object(Bucket=bucket, Key=key)
        logger.info(f"Deleted {key} from {bucket}")
    elif resp.status_code == 400:
        try:
            detail = resp.json().get("detail", "")
        except Exception:
            detail = resp.text
        if "No GPS" in detail:
            no_gps_key = "quarantine/no-gps/" + key.split("/", 1)[-1]
            s3.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": key}, Key=no_gps_key)
            s3.delete_object(Bucket=bucket, Key=key)
            logger.info(f"Moved {key} to {no_gps_key} (no GPS data)")
        else:
            logger.error(f"Webapp returned 400 — leaving in bucket for inspection: {detail}")
    else:
        logger.error(f"Webapp returned {resp.status_code} — leaving in bucket for inspection")
