# Email Photo Inlet — Design Document

## Overview

This document captures the design for a new feature: **ingesting geotagged photos via email into the Stewardship Atlas as vector features**. It is intended as a handoff for implementation in Claude Code.

---

## Feature Summary

A user emails a geotagged photo (e.g. from a smartphone) to a dedicated Atlas email address. The system extracts the GPS coordinates from the image EXIF data and creates a GeoJSON Point feature in a specified Atlas layer. The target layer and feature title are parsed from the email subject line.

---

## Architecture

### Data Flow

```
Email (with photo attachment)
    → AWS SES (receives mail, writes raw email to S3)
    → S3 Ingress Bucket (s3://atlas-ingress/) — triggers event
    → AWS Lambda (thin handler: unpacks email, calls webapp)
    → Webapp API endpoint (POST /ingest/email_photo)
    → Python: parse subject, extract EXIF, build GeoJSON feature
    → deltas_geojson.add_deltas_from_features() → delta written to layer
```

### Key Design Decisions

1. **S3 as the universal intake point.** SES writes the raw email to a dedicated S3 ingress bucket. The Lambda is triggered by the S3 event, not directly by SES. This decouples the pipeline from the mail provider — anything that writes to the ingress bucket (future: different mail provider, mobile upload, SMS-with-photo) will be processed identically from S3 onward.

2. **Separate ingress bucket.** Use a dedicated `atlas-ingress` bucket distinct from the main Atlas data bucket. Cleaner IAM permissions (Lambda only needs read on ingress bucket) and clearer separation of raw inbound vs. processed data.

3. **Lambda is thin.** Lambda's only job is to: retrieve the raw email from S3, extract the attachment and subject line, and POST to the webapp. All real logic lives in the webapp.

4. **No API Gateway (for now).** Lambda calls the webapp directly via HTTP. API Gateway can be added later when broader API exposure is needed.

5. **SES for both send and receive.** SES is the right long-term choice as it supports sending too, which will be useful for notifications later. For now we only need receiving. Receiving does **not** require leaving the SES sandbox — sandbox restrictions only apply to sending.

---

## AWS Setup Required

### SES

- Verify the Atlas domain(s) in SES (add DNS records: DKIM, SPF, MX)
- MX record points to SES inbound endpoint (e.g. `inbound-smtp.us-east-1.amazonaws.com`)
- Create a **receipt rule** for the target address (e.g. `atlas@yourdomain.org`):
  - Action: **Deliver to S3 bucket** → `atlas-ingress`
  - (No sandbox approval needed for receiving)

### S3

- Create bucket: `atlas-ingress` (separate from main data bucket)
- Configure **S3 Event Notification**: trigger Lambda on `s3:ObjectCreated:*`

### Lambda

- Runtime: Python 3.x
- Trigger: S3 event from `atlas-ingress` bucket
- IAM: read access to `atlas-ingress`, HTTP access to webapp
- Thin handler only — see pseudocode below

### Region Note

SES email receiving is only supported in certain AWS regions. Verify your target region supports it before setup. See [AWS SES endpoints](https://docs.aws.amazon.com/general/latest/gr/ses.html).

---

## Subject Line Format

The subject line encodes the target layer and an optional title:

```
<layer_name> | <title>
```

Examples:
```
poi | Locked gate on Miller Road
notes | Suspicious structure near creek
hydrants | New hydrant installed 2024
```

Parsing rules:
- Split on `|` (strip whitespace)
- First token = layer name (lowercased, stripped)
- Second token (optional) = feature title; defaults to `"Photo submission"` if absent
- If subject doesn't match any known layer → reject or route to a default fallback layer (TBD)

---

## EXIF Extraction

Use **Pillow** (`PIL`) for EXIF extraction. It's already likely available; if not, add to `requirements.txt`.

```python
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS

def extract_gps(image_bytes):
    img = Image.open(io.BytesIO(image_bytes))
    exif_data = img._getexif()
    if not exif_data:
        return None
    
    gps_info = {}
    for tag_id, value in exif_data.items():
        tag = TAGS.get(tag_id, tag_id)
        if tag == "GPSInfo":
            for gps_tag_id, gps_value in value.items():
                gps_tag = GPSTAGS.get(gps_tag_id, gps_tag_id)
                gps_info[gps_tag] = gps_value

    if not gps_info:
        return None

    def to_decimal(dms, ref):
        d, m, s = dms
        decimal = float(d) + float(m)/60 + float(s)/3600
        if ref in ['S', 'W']:
            decimal = -decimal
        return decimal

    lat = to_decimal(gps_info['GPSLatitude'], gps_info['GPSLatitudeRef'])
    lon = to_decimal(gps_info['GPSLongitude'], gps_info['GPSLongitudeRef'])
    return lat, lon
```

Additional EXIF fields to grab while we're there (store as feature properties even if not used immediately):
- `GPSAltitude`
- `DateTime` (capture timestamp)
- `Make`, `Model` (device info, useful for debugging)

---

## GeoJSON Feature

```python
import geojson

feature = geojson.Feature(
    geometry=geojson.Point((lon, lat)),  # GeoJSON is lon, lat order
    properties={
        "name": title,           # from subject line
        "timestamp": datetime_str,
        "source": "email",
        "sender": sender_email,
        "image_url": s3_image_url,  # store image back to S3, reference here
    }
)
fc = geojson.FeatureCollection([feature])
```

---

## Image Storage

After processing, store the original image in the **main Atlas S3 data bucket** (not the ingress bucket) under a path like:

```
s3://atlas-data/{atlas_name}/media/email_photos/{timestamp}_{filename}
```

Store the public (or pre-signed) URL as the `image_url` property on the feature. This lets the webmap display or link the photo when a user clicks the feature.

---

## Webapp API Endpoint

Add a new endpoint to `webapp.py`:

```
POST /ingest/email_photo
```

Request body (posted by Lambda):
```json
{
  "subject": "poi | Locked gate on Miller Road",
  "sender": "user@example.com",
  "atlas_name": "wvfd",
  "image_data": "<base64-encoded image bytes>",
  "filename": "IMG_1234.jpg",
  "received_at": "2024-03-09T14:32:00Z"
}
```

Response:
```json
{
  "status": "ok",
  "layer": "poi",
  "feature_id": "...",
  "lat": 38.123,
  "lon": -122.456
}
```

Error responses:
- `400` — no EXIF GPS data found
- `400` — layer name not recognized
- `422` — no image attachment

---

## Lambda Handler (Pseudocode)

```python
import boto3
import email
import base64
import json
import requests

s3 = boto3.client('s3')
WEBAPP_URL = "https://your-atlas-server.com"

def handler(event, context):
    # Get S3 object info from event
    bucket = event['Records'][0]['s3']['bucket']['name']
    key = event['Records'][0]['s3']['object']['key']
    
    # Retrieve raw email from S3
    obj = s3.get_object(Bucket=bucket, Key=key)
    raw_email = obj['Body'].read()
    
    # Parse the email
    msg = email.message_from_bytes(raw_email)
    subject = msg.get('Subject', '')
    sender = msg.get('From', '')
    
    # Extract atlas name from recipient address or key path (TBD)
    atlas_name = extract_atlas_name(msg)
    
    # Find image attachment
    image_data = None
    filename = None
    for part in msg.walk():
        if part.get_content_maintype() == 'image':
            image_data = base64.b64encode(part.get_payload(decode=True)).decode()
            filename = part.get_filename()
            break
    
    if not image_data:
        print("No image attachment found, skipping.")
        return
    
    # POST to webapp
    payload = {
        "subject": subject,
        "sender": sender,
        "atlas_name": atlas_name,
        "image_data": image_data,
        "filename": filename,
        "received_at": datetime.utcnow().isoformat()
    }
    
    resp = requests.post(f"{WEBAPP_URL}/ingest/email_photo", json=payload)
    print(f"Webapp response: {resp.status_code} {resp.text}")
    
    # Optionally delete the raw email from ingress bucket after processing
    s3.delete_object(Bucket=bucket, Key=key)
```

---

## Atlas Layer Configuration

The target layer must already exist in the Atlas config. For example, to support email photo ingestion into a `poi` layer, it should be defined in `{atlas}_layers.json` as a point layer. No new layer config format is needed — email photos just create features in existing layers via the delta mechanism.

Suggested: document in the atlas config which layers accept email ingestion (optional, for validation purposes).

---

## Files to Create / Modify

| File | Change |
|------|--------|
| `python/webapp.py` | Add `POST /ingest/email_photo` endpoint |
| `python/email_inlet.py` | New module: EXIF extraction, subject parsing, feature building |
| `infrastructure/lambda/email_photo_handler.py` | New Lambda function |
| `infrastructure/lambda/requirements.txt` | `requests`, `boto3` |
| `python/requirements.txt` | Add `Pillow` if not present |
| `configuration/shared_inlets_config.json` | Optionally add `email_photo` inlet type |

---

## Open Questions / Future Work

- **Atlas routing**: How does Lambda know which atlas to route to? Options: (a) one email address per atlas, (b) atlas name encoded in the recipient address (e.g. `wvfd@atlas.org`), (c) encoded in subject. Option (b) is cleanest.
- **Sender allowlist**: Should we validate that `sender` is in `admin_emails` for the atlas? Probably yes for v1.
- **Default layer fallback**: What happens if subject doesn't match a known layer? Route to a `field_notes` layer, or bounce with a reply?
- **Review/approval step**: For now features go straight into staging. Later could add a pending queue.
- **Message body**: Future version could parse the email body for a longer description or tags.
- **Multiple attachments**: Future version could handle multiple images in one email, creating one feature per image.
- **Sending**: SES sending (for notifications, confirmations) is a separate future step requiring production access approval.
