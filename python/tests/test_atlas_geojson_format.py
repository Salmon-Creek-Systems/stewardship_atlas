"""Tests for the single-file GeoJSON atlas config format.

Two groups:
  TestKennedyGeoJSON  — pure JSON structure checks, no server deps, runs locally.
  TestInlineFormat    — tests build_atlas_from_geojson logic; requires server deps
                        (gspread, geojson, etc.) and must be run on the server.
"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import atlas
    ATLAS_AVAILABLE = True
except ImportError:
    ATLAS_AVAILABLE = False

REQUIRED_ATLAS_PROPERTIES = [
    'name', 'data_root', 'shared_dir',
    'admin_emails', 'base_url', 'port', 'versioned_outlets', 'logo_url',
]


GEOMETRY = {
    "type": "Polygon",
    "coordinates": [[
        [-123.975, 40.242], [-123.975, 40.233],
        [-123.953, 40.233], [-123.953, 40.242],
        [-123.975, 40.242],
    ]],
}

COMMON_PROPS = {
    "name": "test",
    "data_root": "/root/swales_dev",
    "shared_dir": "/root/data/",
    "admin_emails": ["test@example.com"],
    "base_url": "https://fireatlas.org/test",
    "app_url": "https://fireatlas.org:9000",
    "port": 9000,
    "logo_url": "https://example.com/logo.png",
    "versioned_outlets": ["html", "webmap"],
}

SAMPLE_ASSETS = {
    "dem": {"type": "inlet", "out_layer": "elevation", "config_def": "opentopo_dem"},
    "webmap": {"type": "outlet", "name": "webmap", "in_layers": ["basemap"], "config_def": "webmap", "access": ["public"]},
}

SAMPLE_LAYERS_DICT = {
    "regions": {"name": "regions", "geometry_type": "polygon", "color": [50, 50, 50]},
    "elevation": {"name": "elevation", "geometry_type": "raster"},
    "basemap": {"name": "basemap", "geometry_type": "raster"},
}


def _make_geojson(extra_props):
    return {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": GEOMETRY, "properties": {**COMMON_PROPS, **extra_props}}],
    }


@unittest.skipUnless(ATLAS_AVAILABLE, "server deps not installed")
class TestInlineFormat(unittest.TestCase):

    def _call_from_geojson(self, tmp_path, geojson_dict):
        gj_file = tmp_path / "test.geojson"
        gj_file.write_text(json.dumps(geojson_dict))
        with patch("atlas.build_atlas") as mock_build:
            mock_build.return_value = ({}, Path("/fake/config.json"))
            atlas.build_atlas_from_geojson(str(gj_file), config_only=True)
        return mock_build

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_inline_format_detected(self):
        gj = _make_geojson({"assets": SAMPLE_ASSETS, "layers": SAMPLE_LAYERS_DICT})
        mock = self._call_from_geojson(self.tmp, gj)
        call_kwargs = mock.call_args.kwargs
        self.assertIn("assets", call_kwargs)
        self.assertIn("layers", call_kwargs)
        self.assertNotIn("assets_path", call_kwargs)
        self.assertNotIn("layers_path", call_kwargs)

    def test_layers_dict_converted_to_list(self):
        gj = _make_geojson({"assets": SAMPLE_ASSETS, "layers": SAMPLE_LAYERS_DICT})
        mock = self._call_from_geojson(self.tmp, gj)
        layers_arg = mock.call_args.kwargs["layers"]
        self.assertIsInstance(layers_arg, list)
        self.assertEqual(len(layers_arg), len(SAMPLE_LAYERS_DICT))
        layer_names = [l["name"] for l in layers_arg]
        self.assertEqual(layer_names, list(SAMPLE_LAYERS_DICT.keys()))

    def test_layers_dict_order_preserved(self):
        ordered_layers = {
            "c_layer": {"name": "c_layer", "geometry_type": "raster"},
            "a_layer": {"name": "a_layer", "geometry_type": "polygon"},
            "b_layer": {"name": "b_layer", "geometry_type": "point"},
        }
        gj = _make_geojson({"assets": SAMPLE_ASSETS, "layers": ordered_layers})
        mock = self._call_from_geojson(self.tmp, gj)
        names = [l["name"] for l in mock.call_args.kwargs["layers"]]
        self.assertEqual(names, ["c_layer", "a_layer", "b_layer"])

    def test_assets_passed_as_dict(self):
        gj = _make_geojson({"assets": SAMPLE_ASSETS, "layers": SAMPLE_LAYERS_DICT})
        mock = self._call_from_geojson(self.tmp, gj)
        self.assertEqual(mock.call_args.kwargs["assets"], SAMPLE_ASSETS)

    def test_file_path_format_still_accepted(self):
        gj = _make_geojson({
            "assets_path": "/some/path/assets.json",
            "layers_path": "/some/path/layers.json",
        })
        gj_file = self.tmp / "test.geojson"
        gj_file.write_text(json.dumps(gj))
        with patch("atlas.build_atlas") as mock_build:
            mock_build.return_value = ({}, Path("/fake/config.json"))
            atlas.build_atlas_from_geojson(str(gj_file), config_only=True)
        call_kwargs = mock_build.call_args.kwargs
        self.assertEqual(call_kwargs["assets_path"], "/some/path/assets.json")
        self.assertEqual(call_kwargs["layers_path"], "/some/path/layers.json")
        self.assertNotIn("assets", call_kwargs)
        self.assertNotIn("layers", call_kwargs)

    def test_neither_format_raises(self):
        gj = _make_geojson({})
        gj_file = self.tmp / "test.geojson"
        gj_file.write_text(json.dumps(gj))
        with self.assertRaises(ValueError, msg="Should raise when neither format present"):
            atlas.build_atlas_from_geojson(str(gj_file))

    def test_missing_required_property_raises(self):
        props = {k: v for k, v in COMMON_PROPS.items() if k != "name"}
        gj = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": GEOMETRY, "properties": {**props, "assets": SAMPLE_ASSETS, "layers": SAMPLE_LAYERS_DICT}}]}
        gj_file = self.tmp / "test.geojson"
        gj_file.write_text(json.dumps(gj))
        with self.assertRaises(ValueError):
            atlas.build_atlas_from_geojson(str(gj_file))

    def test_assets_and_layers_not_in_extra_props(self):
        gj = _make_geojson({"assets": SAMPLE_ASSETS, "layers": SAMPLE_LAYERS_DICT, "custom_field": "hello"})
        mock = self._call_from_geojson(self.tmp, gj)
        extra = mock.call_args.kwargs.get("extra_props", {})
        self.assertNotIn("assets", extra)
        self.assertNotIn("layers", extra)
        self.assertEqual(extra.get("custom_field"), "hello")


class TestKennedyGeoJSON(unittest.TestCase):
    """Smoke test: kennedy.geojson parses without errors."""

    KENNEDY_PATH = Path(__file__).parent.parent.parent / "configuration" / "kennedy.geojson"

    def test_kennedy_geojson_valid_json(self):
        self.assertTrue(self.KENNEDY_PATH.exists(), "kennedy.geojson not found")
        with open(self.KENNEDY_PATH) as f:
            data = json.load(f)
        self.assertEqual(data["type"], "FeatureCollection")

    def test_kennedy_has_inline_layers_and_assets(self):
        with open(self.KENNEDY_PATH) as f:
            data = json.load(f)
        props = data["features"][0]["properties"]
        self.assertIsInstance(props.get("layers"), dict)
        self.assertIsInstance(props.get("assets"), dict)

    def test_kennedy_layers_no_name_in_values(self):
        """Dict key is the name; values must not redundantly repeat it."""
        with open(self.KENNEDY_PATH) as f:
            data = json.load(f)
        layers = data["features"][0]["properties"]["layers"]
        for key, layer in layers.items():
            self.assertNotIn("name", layer, f"Layer '{key}' has redundant 'name' field in value")

    def test_kennedy_has_required_properties(self):
        with open(self.KENNEDY_PATH) as f:
            data = json.load(f)
        props = data["features"][0]["properties"]
        for prop in REQUIRED_ATLAS_PROPERTIES:
            self.assertIn(prop, props, f"Missing required property: {prop}")

    def test_kennedy_no_path_properties(self):
        with open(self.KENNEDY_PATH) as f:
            data = json.load(f)
        props = data["features"][0]["properties"]
        self.assertNotIn("assets_path", props, "kennedy.geojson should not have assets_path")
        self.assertNotIn("layers_path", props, "kennedy.geojson should not have layers_path")


if __name__ == "__main__":
    unittest.main()
