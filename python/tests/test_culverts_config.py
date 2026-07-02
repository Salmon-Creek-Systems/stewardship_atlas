"""Static wiring checks for the scvfd culverts layer.

Guards the JSON edits that add the hand-drawn `culverts` point layer: layer
definition, editable columns, inlet asset, shared inlet config_def, and
presence in the same outlets as watertanks. Pure JSON — no server deps.
"""
import json
import unittest
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configuration"

EXPECTED_COLUMNS = [
    "diameter", "condition", "slope", "upstream", "width",
    "downstream", "drop", "previous_work", "comments",
]


class TestCulvertsConfig(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gj = json.loads((CONFIG_DIR / "scvfd.geojson").read_text())
        cls.props = cls.gj["features"][0]["properties"]
        cls.layers = cls.props["layers"]
        cls.assets = cls.props["assets"]
        cls.shared = json.loads((CONFIG_DIR / "shared_inlets_config.json").read_text())

    def test_layer_defined_as_point(self):
        self.assertIn("culverts", self.layers)
        self.assertEqual(self.layers["culverts"]["geometry_type"], "point")

    def test_editable_columns(self):
        cols = self.layers["culverts"]["editable_columns"]
        self.assertEqual([c["name"] for c in cols], EXPECTED_COLUMNS)
        self.assertTrue(all(c["type"] == "string" for c in cols))

    def test_show_attributes_and_icon(self):
        cul = self.layers["culverts"]
        self.assertTrue(cul.get("show_attributes"))
        self.assertEqual(cul["symbol"], {"png": "culvert.png"})

    def test_inlet_asset(self):
        asset = self.assets["local_culverts"]
        self.assertEqual(asset["type"], "inlet")
        self.assertEqual(asset["out_layer"], "culverts")
        self.assertEqual(asset["config_def"], "local_culverts")

    def test_shared_inlet_config_def(self):
        cfg = self.shared["local_culverts"]
        self.assertEqual(cfg["fetch_type"], "local_ogr")
        self.assertEqual(cfg["feature_type"], "point")
        self.assertEqual(cfg["inpath_template"], "scvfd_culverts.geojson")

    def test_present_in_same_outlets_as_watertanks(self):
        def outlets_with(layer):
            return {
                a.get("name", key)
                for key, a in self.assets.items()
                if a.get("type") == "outlet" and layer in a.get("in_layers", [])
            }
        self.assertEqual(outlets_with("culverts"), outlets_with("watertanks"))
        self.assertIn("webmap", outlets_with("culverts"))
        self.assertIn("webedit", outlets_with("culverts"))

    def test_icon_png_exists(self):
        png = Path(__file__).resolve().parents[2] / "templates" / "icons" / "culvert.png"
        self.assertTrue(png.exists())


if __name__ == "__main__":
    unittest.main()
