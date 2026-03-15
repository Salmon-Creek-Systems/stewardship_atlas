import io
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from email_inlet import parse_subject, extract_gps, build_feature


class TestParseSubject(unittest.TestCase):

    def test_layer_and_title(self):
        layer, title = parse_subject("poi: Locked gate on Miller Road")
        self.assertEqual(layer, "poi")
        self.assertEqual(title, "Locked gate on Miller Road")

    def test_no_colon_defaults_to_poi(self):
        # No colon — whole subject is title, layer defaults to poi
        layer, title = parse_subject("Locked gate on Miller Road")
        self.assertEqual(layer, "poi")
        self.assertEqual(title, "Locked gate on Miller Road")

    def test_empty_title_after_colon(self):
        layer, title = parse_subject("poi:")
        self.assertEqual(layer, "poi")
        self.assertEqual(title, "Photo submission")

    def test_layer_lowercased(self):
        layer, title = parse_subject("POI: Something")
        self.assertEqual(layer, "poi")

    def test_extra_whitespace(self):
        layer, title = parse_subject("  hydrants  :  New hydrant  ")
        self.assertEqual(layer, "hydrants")
        self.assertEqual(title, "New hydrant")

    def test_colon_in_title(self):
        # Only split on first colon
        layer, title = parse_subject("poi: Title with: extra colon")
        self.assertEqual(layer, "poi")
        self.assertEqual(title, "Title with: extra colon")


class TestExtractGps(unittest.TestCase):

    def _make_fake_exif(self, lat_dms, lat_ref, lon_dms, lon_ref, altitude=None):
        """Build a minimal fake _getexif() return value."""
        from PIL.ExifTags import TAGS, GPSTAGS

        # Invert the TAGS and GPSTAGS dicts to look up by name
        tag_by_name = {v: k for k, v in TAGS.items()}
        gps_tag_by_name = {v: k for k, v in GPSTAGS.items()}

        gps_info = {
            gps_tag_by_name["GPSLatitude"]: lat_dms,
            gps_tag_by_name["GPSLatitudeRef"]: lat_ref,
            gps_tag_by_name["GPSLongitude"]: lon_dms,
            gps_tag_by_name["GPSLongitudeRef"]: lon_ref,
        }
        if altitude is not None:
            gps_info[gps_tag_by_name["GPSAltitude"]] = altitude

        return {tag_by_name["GPSInfo"]: gps_info}

    def test_northern_western(self):
        exif = self._make_fake_exif(
            lat_dms=(38, 7, 21.6), lat_ref="N",
            lon_dms=(122, 27, 21.6), lon_ref="W",
        )
        with patch("PIL.Image.open") as mock_open:
            mock_img = MagicMock()
            mock_img._getexif.return_value = exif
            mock_open.return_value = mock_img

            result = extract_gps(b"fake-image-bytes")

        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["lat"], 38.1226667, places=4)
        self.assertAlmostEqual(result["lon"], -122.4560000, places=4)

    def test_southern_eastern(self):
        exif = self._make_fake_exif(
            lat_dms=(33, 52, 0.0), lat_ref="S",
            lon_dms=(151, 12, 0.0), lon_ref="E",
        )
        with patch("PIL.Image.open") as mock_open:
            mock_img = MagicMock()
            mock_img._getexif.return_value = exif
            mock_open.return_value = mock_img

            result = extract_gps(b"fake-image-bytes")

        self.assertIsNotNone(result)
        self.assertLess(result["lat"], 0)
        self.assertGreater(result["lon"], 0)

    def test_no_exif_returns_none(self):
        with patch("PIL.Image.open") as mock_open:
            mock_img = MagicMock()
            mock_img._getexif.return_value = None
            mock_open.return_value = mock_img

            result = extract_gps(b"fake-image-bytes")

        self.assertIsNone(result)

    def test_no_gps_tag_returns_none(self):
        with patch("PIL.Image.open") as mock_open:
            mock_img = MagicMock()
            mock_img._getexif.return_value = {271: "Apple"}  # Make tag, no GPS
            mock_open.return_value = mock_img

            result = extract_gps(b"fake-image-bytes")

        self.assertIsNone(result)

    def test_altitude_included(self):
        from fractions import Fraction
        exif = self._make_fake_exif(
            lat_dms=(38, 7, 0.0), lat_ref="N",
            lon_dms=(122, 27, 0.0), lon_ref="W",
            altitude=Fraction(142, 1),
        )
        with patch("PIL.Image.open") as mock_open:
            mock_img = MagicMock()
            mock_img._getexif.return_value = exif
            mock_open.return_value = mock_img

            result = extract_gps(b"fake-image-bytes")

        self.assertIn("altitude", result)
        self.assertAlmostEqual(result["altitude"], 142.0, places=1)


class TestBuildFeature(unittest.TestCase):

    def test_basic_structure(self):
        feature = build_feature(
            lat=38.123, lon=-122.456,
            title="Test gate",
            sender="user@example.com",
            timestamp="2024-03-09T14:32:00Z",
            image_url="https://bucket.s3.amazonaws.com/scvfd/media/email_photos/test.jpg",
        )
        self.assertEqual(feature["type"], "Feature")
        self.assertEqual(feature["geometry"]["type"], "Point")

    def test_lon_lat_order(self):
        # GeoJSON is [lon, lat]
        feature = build_feature(38.0, -122.0, "T", "a@b.com", "2024-01-01", "http://x")
        coords = feature["geometry"]["coordinates"]
        self.assertEqual(coords[0], -122.0)  # lon first
        self.assertEqual(coords[1], 38.0)    # lat second

    def test_properties(self):
        feature = build_feature(38.0, -122.0, "My title", "sender@x.com", "2024-01-01", "http://img")
        props = feature["properties"]
        self.assertEqual(props["name"], "My title")
        self.assertEqual(props["source"], "email")
        self.assertEqual(props["sender"], "sender@x.com")
        self.assertEqual(props["image_url"], "http://img")

    def test_extra_props_merged(self):
        feature = build_feature(38.0, -122.0, "T", "a@b.com", "2024-01-01", "http://x",
                                extra_props={"make": "Apple", "altitude": 142.0})
        self.assertEqual(feature["properties"]["make"], "Apple")
        self.assertEqual(feature["properties"]["altitude"], 142.0)


if __name__ == "__main__":
    unittest.main()
