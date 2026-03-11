"""
Tests for _apply_delete_delta() and the 'delete' action in apply_deltas().

Runs locally without QGIS, GDAL, or server data paths.
Heavy dependencies (eddies/osgeo) are mocked at the module level before import.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# Mock heavy server-side dependencies before importing deltas_geojson
for mod in ('eddies', 'osgeo', 'osgeo.gdal', 'osgeo.ogr', 'osgeo.osr',
            'osgeo.gdal_array', 'matplotlib', 'matplotlib.colors',
            'outlets', 'outlets_qgis_atlas', 'vector_inlets', 'raster_inlets'):
    sys.modules.setdefault(mod, MagicMock())

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import geojson
from deltas_geojson import _apply_delete_delta, apply_deltas


class TestApplyDeleteDelta(unittest.TestCase):
    """Tests for _apply_delete_delta() — pure DuckDB spatial logic."""

    # Three points: A and B are inside the delete polygon, C is outside.
    POINT_A = [0.5, 0.5]    # inside
    POINT_B = [1.0, 1.0]    # inside
    POINT_C = [10.0, 10.0]  # outside

    # Delete polygon covers roughly (0,0)-(2,2)
    DELETE_POLY = [[[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0], [0.0, 0.0]]]

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmpdir.name)
        (self.tmp / "work").mkdir()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _write(self, path, fc):
        with open(path, "w") as f:
            geojson.dump(fc, f)

    def _read(self, path):
        with open(path) as f:
            return geojson.load(f)

    def _layer(self, points_with_names):
        return geojson.FeatureCollection([
            geojson.Feature(geometry=geojson.Point(coords), properties={"name": name})
            for coords, name in points_with_names
        ])

    def _delete_poly(self, coords=None):
        return geojson.FeatureCollection([
            geojson.Feature(geometry=geojson.Polygon(coords or self.DELETE_POLY), properties={})
        ])

    def test_deletes_intersecting_features(self):
        """Features inside the polygon are removed; outside features survive."""
        layer = self.tmp / "layer.geojson"
        delta = self.tmp / "delta.geojson"
        self._write(layer, self._layer([(self.POINT_A, "A"), (self.POINT_B, "B"), (self.POINT_C, "C")]))
        self._write(delta, self._delete_poly())

        _apply_delete_delta(layer, delta)

        names = [f["properties"]["name"] for f in self._read(layer)["features"]]
        self.assertEqual(names, ["C"])

    def test_no_match_leaves_layer_unchanged(self):
        """Polygon that doesn't intersect anything — all features survive."""
        layer = self.tmp / "layer.geojson"
        delta = self.tmp / "delta.geojson"
        self._write(layer, self._layer([(self.POINT_C, "C")]))
        self._write(delta, self._delete_poly())  # polygon is far from POINT_C

        _apply_delete_delta(layer, delta)

        result = self._read(layer)
        self.assertEqual(len(result["features"]), 1)
        self.assertEqual(result["features"][0]["properties"]["name"], "C")

    def test_deletes_all_features(self):
        """All features deleted when all intersect the polygon."""
        layer = self.tmp / "layer.geojson"
        delta = self.tmp / "delta.geojson"
        self._write(layer, self._layer([(self.POINT_A, "A"), (self.POINT_B, "B")]))
        self._write(delta, self._delete_poly())

        _apply_delete_delta(layer, delta)

        self.assertEqual(len(self._read(layer)["features"]), 0)

    def test_delta_moved_to_work_dir(self):
        """Consumed delta file is moved to work/ subdirectory."""
        layer = self.tmp / "layer.geojson"
        delta = self.tmp / "assetless__20260101_000000__delete.geojson"
        self._write(layer, self._layer([(self.POINT_A, "A")]))
        self._write(delta, self._delete_poly())

        _apply_delete_delta(layer, delta)

        self.assertFalse(delta.exists())
        self.assertTrue((self.tmp / "work" / delta.name).exists())

    def _make_atlas_dirs(self, atlas_name="testatlas", layer="roads"):
        """Create directory structure matching versioning.atlas_path conventions.

        versioning.atlas_path returns: {data_root}/{atlas_name}/staging/{local_path}
        """
        base = self.tmp / atlas_name / "staging"
        layer_dir = base / "layers" / layer
        deltas_dir = base / "deltas" / layer
        layer_dir.mkdir(parents=True)
        (deltas_dir / "work").mkdir(parents=True)
        return deltas_dir

    def test_apply_deltas_create_then_delete(self):
        """apply_deltas() handles create → delete sequence correctly."""
        deltas_dir = self._make_atlas_dirs()

        self._write(
            deltas_dir / "assetless__20260101_000001__create.geojson",
            self._layer([(self.POINT_A, "A"), (self.POINT_C, "C")])
        )
        self._write(
            deltas_dir / "assetless__20260101_000002__delete.geojson",
            self._delete_poly()  # covers POINT_A, not POINT_C
        )

        config = {"name": "testatlas", "data_root": str(self.tmp),
                  "dataswale": {"versions": ["staging"]}}

        result = apply_deltas(config, "roads")
        names = [f["properties"]["name"] for f in result["features"]]
        self.assertIn("C", names)
        self.assertNotIn("A", names)

    def test_delete_then_apply_deltas_no_error(self):
        """Deleting all features leaves an empty layer without error."""
        deltas_dir = self._make_atlas_dirs()

        self._write(
            deltas_dir / "assetless__20260101_000001__create.geojson",
            self._layer([(self.POINT_A, "A")])
        )
        self._write(
            deltas_dir / "assetless__20260101_000002__delete.geojson",
            self._delete_poly()
        )

        config = {"name": "testatlas", "data_root": str(self.tmp),
                  "dataswale": {"versions": ["staging"]}}

        result = apply_deltas(config, "roads")
        self.assertEqual(len(result["features"]), 0)


if __name__ == '__main__':
    unittest.main()
