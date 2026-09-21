import io
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from email_inlet import parse_subject, extract_gps, build_feature, exif_timestamp


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

    def test_no_colon_uses_default_layer(self):
        layer, title = parse_subject("Locked gate on Miller Road", default_layer="private_notes")
        self.assertEqual(layer, "private_notes")
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

        self.assertAlmostEqual(result["altitude"], 142.0, places=1)
        self.assertIn("raw_exif", result)
        self.assertAlmostEqual(result["raw_exif"]["GPSAltitude"], 142.0, places=1)

    def test_raw_exif_present(self):
        exif = self._make_fake_exif(
            lat_dms=(38, 7, 0.0), lat_ref="N",
            lon_dms=(122, 27, 0.0), lon_ref="W",
        )
        with patch("PIL.Image.open") as mock_open:
            mock_img = MagicMock()
            mock_img._getexif.return_value = exif
            mock_open.return_value = mock_img

            result = extract_gps(b"fake-image-bytes")

        self.assertIn("raw_exif", result)
        self.assertIn("GPSLatitude", result["raw_exif"])
        self.assertIn("GPSLongitude", result["raw_exif"])


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


class TestExifTimestamp(unittest.TestCase):
    """Capture date beats arrival date — see the Hargus batch, uploaded to S3
    in 2026 from photos taken in March 2025."""

    FALLBACK = "2026-04-08T13:04:57+00:00"

    def test_prefers_exif_datetime(self):
        ts = exif_timestamp({"datetime": "2025:03:29 17:33:28"}, self.FALLBACK)
        self.assertEqual(ts, "2025-03-29T17:33:28")

    def test_falls_back_when_absent(self):
        self.assertEqual(exif_timestamp({"lat": 39.7}, self.FALLBACK), self.FALLBACK)

    def test_falls_back_on_empty_gps(self):
        self.assertEqual(exif_timestamp({}, self.FALLBACK), self.FALLBACK)
        self.assertEqual(exif_timestamp(None, self.FALLBACK), self.FALLBACK)

    def test_falls_back_on_unparseable(self):
        self.assertEqual(exif_timestamp({"datetime": "not a date"}, self.FALLBACK),
                         self.FALLBACK)

    def test_tolerates_surrounding_whitespace(self):
        ts = exif_timestamp({"datetime": "  2024:12:08 03:02:51  "}, self.FALLBACK)
        self.assertEqual(ts, "2024-12-08T03:02:51")



class TestOutsideBbox(unittest.TestCase):
    """A fix outside the atlas would import a feature off the edge of its map."""

    BBOX = {'west': -123.726259, 'east': -123.428583,
            'south': 39.591268, 'north': 39.873943}

    def _outside(self, lat, lon):
        sys.path.insert(0, os.path.abspath(
            os.path.join(os.path.dirname(__file__), '..', '..', 'scripts')))
        from ingest_s3_photos import outside_bbox
        return outside_bbox(self.BBOX, lat, lon)

    def test_inside_is_kept(self):
        # A Hargus 2025 fix.
        self.assertFalse(self._outside(39.776600, -123.548344))

    def test_west_of_the_atlas_is_dropped(self):
        # The GoPro shots in Hargus_biochar, ~6km west, inside westport.
        self.assertTrue(self._outside(39.870032, -123.801585))

    def test_corners_are_inside(self):
        self.assertFalse(self._outside(39.591268, -123.726259))
        self.assertFalse(self._outside(39.873943, -123.428583))

    def test_just_past_each_edge_is_outside(self):
        self.assertTrue(self._outside(39.591267, -123.5))   # south
        self.assertTrue(self._outside(39.873944, -123.5))   # north
        self.assertTrue(self._outside(39.7, -123.726260))   # west
        self.assertTrue(self._outside(39.7, -123.428582))   # east

    def test_null_island_is_dropped(self):
        # What fhe's photos layer holds for photos whose fix never resolved.
        self.assertTrue(self._outside(0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
