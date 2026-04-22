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


def parse_subject(subject: str, default_layer: str = "poi") -> tuple[str, str]:
    """Parse email subject into (layer_name, title).

    Format: "<layer_name>: <title>"
    If no colon is present, the entire subject is used as the title
    and the layer defaults to default_layer.
    """
    if ":" in subject:
        layer_name, _, title = subject.partition(":")
        layer_name = layer_name.strip().lower()
        title = title.strip() or "Photo submission"
    else:
        layer_name = default_layer
        title = subject.strip() or "Photo submission"
    return layer_name, title


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
    return result


def build_feature(lat: float, lon: float, title: str, sender: str,
                  timestamp: str, image_url: str,
                  extra_props: dict = None) -> geojson.Feature:
    """Build a GeoJSON Point feature from extracted data.

    GeoJSON coordinate order is [lon, lat].
    """
    properties = {
        "name": title,
        "timestamp": timestamp,
        "source": "email",
        "sender": sender,
        "image_url": image_url,
        "URL": image_url,
    }
    if extra_props:
        properties.update(extra_props)

    return geojson.Feature(
        geometry=geojson.Point((lon, lat)),
        properties=properties,
    )
