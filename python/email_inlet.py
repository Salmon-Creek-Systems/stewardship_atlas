"""
Email photo inlet: parse subject lines, extract GPS EXIF from images,
and build GeoJSON point features for ingestion into atlas layers.
"""
import io
import logging

import geojson
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS

from utils import safe_float

logger = logging.getLogger(__name__)


def parse_subject(subject: str) -> str:
    """Return email subject as the feature title.

    Layer is no longer extracted from the subject — use the email body instead.
    """
    return subject.strip() or "Photo submission"


def parse_body(body_text: str, layer_names: list, editable_columns: list) -> dict:
    """Parse email body for layer selection and editable field values.

    Returns {"layer": str|None, "props": dict, "raw": str}.

    Layer detection (first match wins):
    - Any line with "layer: <value>" or "layer=<value>" (case-insensitive key)
    - First non-empty line with no separator that matches a known layer name

    All other lines are scanned for "key: value" or "key=value" pairs whose
    keys match editable column names (case-insensitive). Unknown keys ignored.

    Never raises — returns {"layer": None, "props": {}, "raw": body_text or ""}
    on any error.
    """
    raw = body_text or ""
    try:
        col_names = {c["name"].lower(): c["name"] for c in (editable_columns or [])}
        layer_set = {n.lower() for n in (layer_names or [])}

        layer = None
        props = {}
        first_line_checked = False

        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue

            sep_pos = -1
            for sep in (":", "="):
                pos = line.find(sep)
                if pos != -1 and (sep_pos == -1 or pos < sep_pos):
                    sep_pos = pos

            if sep_pos == -1:
                # No separator — check if first non-empty line matches a layer name
                if not first_line_checked and line.lower() in layer_set:
                    layer = line.lower()
                first_line_checked = True
                continue

            first_line_checked = True
            key = line[:sep_pos].strip().lower()
            value = line[sep_pos + 1:].strip()

            if key == "layer":
                if value.lower() in layer_set:
                    layer = value.lower()
            elif key in col_names:
                props[col_names[key]] = value

        return {"layer": layer, "props": props, "raw": raw}
    except Exception:
        return {"layer": None, "props": {}, "raw": raw}


def _exif_to_json(value):
    """Convert an EXIF value to a JSON-safe type."""
    if isinstance(value, tuple):
        return [_exif_to_json(v) for v in value]
    try:
        return safe_float(value)
    except (TypeError, ValueError):
        return str(value)


def extract_gps(image_bytes: bytes) -> dict | None:
    """Extract GPS and supplementary EXIF data from image bytes.

    Returns a dict with keys: lat, lon, and raw_exif (all extracted EXIF fields).
    Returns None if no GPS data found.
    """
    img = Image.open(io.BytesIO(image_bytes))
    exif_data = img._getexif()
    if not exif_data:
        return None

    gps_info = {}
    raw_exif = {}
    for tag_id, value in exif_data.items():
        tag = TAGS.get(tag_id, tag_id)
        if tag == "GPSInfo":
            for gps_tag_id, gps_value in value.items():
                gps_tag = GPSTAGS.get(gps_tag_id, gps_tag_id)
                gps_info[gps_tag] = gps_value
                raw_exif[gps_tag] = _exif_to_json(gps_value)
        elif tag in ("DateTime", "Make", "Model", "DateTimeOriginal",
                     "DateTimeDigitized", "Software", "ImageDescription"):
            raw_exif[tag] = str(value)

    if not gps_info.get("GPSLatitude") or not gps_info.get("GPSLongitude"):
        return None

    def to_decimal(dms, ref):
        d, m, s = dms
        decimal = safe_float(d) + safe_float(m) / 60 + safe_float(s) / 3600
        if ref in ("S", "W"):
            decimal = -decimal
        return round(decimal, 7)

    result = {
        "lat": to_decimal(gps_info["GPSLatitude"], gps_info["GPSLatitudeRef"]),
        "lon": to_decimal(gps_info["GPSLongitude"], gps_info["GPSLongitudeRef"]),
        "raw_exif": raw_exif,
    }

    if "GPSAltitude" in gps_info:
        result["altitude"] = round(safe_float(gps_info["GPSAltitude"]), 2)

    for key, exif_tag in (("datetime", "DateTime"), ("make", "Make"), ("model", "Model")):
        if exif_tag in raw_exif:
            result[key] = raw_exif[exif_tag]

    return result


def build_feature(lat: float, lon: float, title: str, sender: str,
                  timestamp: str, image_url: str,
                  body_props: dict = None,
                  extra_props: dict = None) -> geojson.Feature:
    """Build a GeoJSON Point feature from extracted data.

    GeoJSON coordinate order is [lon, lat].
    body_props (parsed from email body) are applied first; extra_props (EXIF/GPS)
    follow so GPS metadata wins over body values. Hardcoded fields always take
    precedence over both.
    """
    properties = {}
    if body_props:
        properties.update(body_props)
    if extra_props:
        properties.update(extra_props)
    properties.update({
        "name": title,
        "timestamp": timestamp,
        "source": "email",
        "sender": sender,
        "image_url": image_url,
        "URL": image_url,
    })

    return geojson.Feature(
        geometry=geojson.Point((lon, lat)),
        properties=properties,
    )
