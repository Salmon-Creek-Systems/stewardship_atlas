"""
Email photo inlet: parse subject lines, extract GPS EXIF from images,
and build GeoJSON point features for ingestion into atlas layers.
"""
import io
import logging

import geojson
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS

logger = logging.getLogger(__name__)


def parse_subject(subject: str) -> tuple[str, str]:
    """Parse email subject into (layer_name, title).

    Format: "<layer_name> | <title>"
    Title defaults to "Photo submission" if absent.
    """
    parts = subject.split("|", 1)
    layer_name = parts[0].strip().lower()
    title = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "Photo submission"
    return layer_name, title


def extract_gps(image_bytes: bytes) -> dict | None:
    """Extract GPS and supplementary EXIF data from image bytes.

    Returns a dict with keys: lat, lon, and optionally altitude,
    datetime, make, model. Returns None if no GPS data found.
    """
    img = Image.open(io.BytesIO(image_bytes))
    exif_data = img._getexif()
    if not exif_data:
        return None

    gps_info = {}
    extra = {}
    for tag_id, value in exif_data.items():
        tag = TAGS.get(tag_id, tag_id)
        if tag == "GPSInfo":
            for gps_tag_id, gps_value in value.items():
                gps_tag = GPSTAGS.get(gps_tag_id, gps_tag_id)
                gps_info[gps_tag] = gps_value
        elif tag == "DateTime":
            extra["datetime"] = value
        elif tag == "Make":
            extra["make"] = value
        elif tag == "Model":
            extra["model"] = value

    if not gps_info.get("GPSLatitude") or not gps_info.get("GPSLongitude"):
        return None

    def to_decimal(dms, ref):
        d, m, s = dms
        decimal = float(d) + float(m) / 60 + float(s) / 3600
        if ref in ("S", "W"):
            decimal = -decimal
        return round(decimal, 7)

    result = {
        "lat": to_decimal(gps_info["GPSLatitude"], gps_info["GPSLatitudeRef"]),
        "lon": to_decimal(gps_info["GPSLongitude"], gps_info["GPSLongitudeRef"]),
    }

    if "GPSAltitude" in gps_info:
        alt = gps_info["GPSAltitude"]
        result["altitude"] = round(float(alt), 2)

    result.update(extra)
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
