"""
Lambda handler for email photo inlet.

Triggered by S3 ObjectCreated events on the atlas-ingress bucket.
SES writes raw emails there; this handler unpacks them and POSTs to the webapp.

Environment variables:
  WEBAPP_URL            - base URL of the atlas API (the box's :9000, or the
                          distribution's /api after cutover)
  SERVICE_TOKEN_SECRET  - Secrets Manager name of the token sent as
                          x-atlas-service-token (the cloud API requires it;
                          the box ignores it). Unset: no header.
  IMAGE_BY_REFERENCE    - "1": stage the image in this bucket under images/
                          and send its key, not the bytes. A function URL takes
                          at most 6 MB, and a base64 phone photo is often more.
                          The box's code only reads image_data, so this stays
                          off until cutover.
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

SERVICE_TOKEN_HEADER = "x-atlas-service-token"
# Staged images go here. Not under incoming/, which triggers this Lambda.
STAGED_IMAGE_PREFIX = "images/"

_service_token = None


def service_token():
    """The service token, read once per container. None when not configured."""
    global _service_token
    name = os.environ.get("SERVICE_TOKEN_SECRET")
    if not name:
        return None
    if _service_token is None:
        secrets = boto3.client("secretsmanager")
        _service_token = secrets.get_secret_value(SecretId=name)["SecretString"].strip()
    return _service_token


def image_by_reference():
    return os.environ.get("IMAGE_BY_REFERENCE") == "1"


def staged_image_key(email_key, filename):
    """incoming/abc + IMG_1.jpg -> images/abc/IMG_1.jpg"""
    email_id = email_key.split("/", 1)[-1]
    return f"{STAGED_IMAGE_PREFIX}{email_id}/{os.path.basename(filename)}"


def extract_body_text(msg) -> str:
    """Extract plain text body from a parsed email message.

    Prefers text/plain; falls back to text/html. Returns empty string if none found.
    """
    for content_type in ("text/plain", "text/html"):
        for part in msg.walk():
            if part.get_content_type() == content_type:
                try:
                    return part.get_payload(decode=True).decode("utf-8", errors="replace").strip()
                except Exception:
                    pass
    return ""


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
    image_bytes = None
    filename = None
    for part in msg.walk():
        if part.get_content_maintype() == "image":
            image_bytes = part.get_payload(decode=True)
            filename = os.path.basename(part.get_filename() or "photo.jpg")
            break

    if not image_bytes:
        logger.info("No image attachment found — skipping")
        return

    logger.info(f"Subject: {subject}")
    logger.info(f"From: {sender}")

    body_text = extract_body_text(msg)

    payload = {
        "subject": subject,
        "sender": sender,
        "atlas_name": atlas_name,
        "filename": filename,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "body_text": body_text,
    }
    staged_key = None
    if image_by_reference():
        staged_key = staged_image_key(key, filename)
        s3.put_object(Bucket=bucket, Key=staged_key, Body=image_bytes)
        payload["image_key"] = staged_key
    else:
        payload["image_data"] = base64.b64encode(image_bytes).decode()

    headers = {}
    token = service_token()
    if token:
        headers[SERVICE_TOKEN_HEADER] = token

    try:
        resp = requests.post(f"{WEBAPP_URL}/ingest/email_photo", json=payload,
                             headers=headers, timeout=30)
    finally:
        # The API has copied it (or refused it); the raw email is what is kept
        # for a retry or for inspection.
        if staged_key:
            s3.delete_object(Bucket=bucket, Key=staged_key)
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
