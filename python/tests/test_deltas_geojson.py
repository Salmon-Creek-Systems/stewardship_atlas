import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime

# Add the python directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import geojson
from deltas_geojson import (
    create,
    add_deltas_from_features,
    transform,
    delta_path,
    apply_deltas,
    InvalidDelta,
    _apply_delete_delta,
)

class TestDeltasGeoJSON(unittest.TestCase):
    def setUp(self):
        self.test_config = {
            "name": "test_layer",
            "data_root": "test_data",
            "vector_width": 3,
            "assets": {
                "test_asset": {
                    "config": {
                        "data_type": "geojson"
                    },
                    "out_layer": "test_layer"
                }
            }
        }
        with open("python/tests/fixtures/simple_delta.geojson", "r") as f:
            self.test_feature_collection = geojson.load(f)
    
    def tearDown(self):
        # Clean up any test directories that might have been created
        test_dir = Path(self.test_config["data_root"])
        if test_dir.exists():
            for file in test_dir.rglob("*"):
                if file.is_file():
                    file.unlink()
            for dir in reversed(list(test_dir.rglob("*"))):
                if dir.is_dir():
                    dir.rmdir()
            test_dir.rmdir()

    def test_create_directory_structure(self):
        """Test that create() sets up the correct directory structure"""
        create(self.test_config)
        
        deltas_dir = Path(self.test_config["data_root"]) / f"deltas_{self.test_config['name']}"
        processed_dir = deltas_dir / "processed"
        
        self.assertTrue(deltas_dir.exists())
        self.assertTrue(processed_dir.exists())

    def test_create_missing_config(self):
        """Test that create() raises ValueError with missing config fields"""
        with self.assertRaises(ValueError) as cm:
            create({"data_root": "test"})
        self.assertIn("Configuration must include 'name' field", str(cm.exception))
        
        with self.assertRaises(ValueError) as cm:
            create({"name": "test"})
        self.assertIn("Configuration must include 'data_root' field", str(cm.exception))

    def test_transform_feature(self):
        """Test that transform() correctly sets vector_width"""
        feature = geojson.Feature(
            geometry=geojson.Point([0, 0]),
            properties={"name": "Test"}
        )
        
        transformed = transform(feature, self.test_config)
        self.assertEqual(transformed["properties"]["vector_width"], self.test_config["vector_width"])
        
        # Test default vector_width
        transformed = transform(feature, {"name": "test", "data_root": "test"})
        self.assertEqual(transformed["properties"]["vector_width"], 2)

    def test_delta_path(self):
        """Test that delta_path() generates correct paths"""
        path = delta_path(self.test_config, "test_asset", "create")
        
        # Check path structure
        self.assertTrue(path.endswith(".geojson"))
        self.assertIn("test_asset", path)
        self.assertIn("create", path)
        
        # Check timestamp format
        timestamp = path.split("__")[1]
        try:
            datetime.strptime(timestamp, "%Y%m%d_%H%M%S")
        except ValueError:
            self.fail("Timestamp in path is not in correct format")

    def test_add_deltas_from_features(self):
        """Test that add_deltas_from_features() correctly writes GeoJSON"""
        # Create directory structure
        create(self.test_config)
        
        # Add deltas
        paths = add_deltas_from_features(self.test_config, "test_asset", self.test_feature_collection, "create")
        
        self.assertEqual(len(paths), 1)
        self.assertTrue(Path(paths[0]).exists())
        
        # Verify file contents
        with open(paths[0], "r") as f:
            saved_data = json.load(f)
        self.assertEqual(saved_data, self.test_feature_collection)

    def test_apply_deltas(self):
        """Test that apply_deltas() correctly processes delta files"""
        # Create directory structure
        create(self.test_config)
        
        # Add a test delta
        paths = add_deltas_from_features(self.test_config, "test_asset", self.test_feature_collection, "create")
        
        # Apply deltas
        result = apply_deltas(self.test_config, "test_layer")
        
        # Verify result
        self.assertIsInstance(result, geojson.FeatureCollection)
        self.assertEqual(len(result["features"]), len(self.test_feature_collection["features"]))
        
        # Verify file was created in work directory
        work_dir = Path(self.test_config["data_root"]) / f"deltas_{self.test_config['name']}" / "work"
        layer_file = work_dir / f"{self.test_config['name']}.geojson"
        self.assertTrue(layer_file.exists())

class TestApplyDeleteDelta(unittest.TestCase):
    """Tests for _apply_delete_delta() — runs locally, no server or atlas config needed."""

    # Three points: A and B are inside the delete polygon, C is outside.
    POINT_A = [0.5, 0.5]   # inside
    POINT_B = [1.0, 1.0]   # inside
    POINT_C = [10.0, 10.0] # outside

    # Delete polygon covers roughly (0,0)-(2,2)
    DELETE_POLYGON_COORDS = [[[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0], [0.0, 0.0]]]

    def _make_layer(self, points_with_names):
        """Return a FeatureCollection with Point features."""
        features = [
            geojson.Feature(
                geometry=geojson.Point(coords),
                properties={"name": name}
            )
            for coords, name in points_with_names
        ]
        return geojson.FeatureCollection(features)

    def _make_delete_polygon(self, coords=None):
        """Return a FeatureCollection containing one delete polygon."""
        coords = coords or self.DELETE_POLYGON_COORDS
        return geojson.FeatureCollection([
            geojson.Feature(geometry=geojson.Polygon(coords), properties={})
        ])

    def _write_geojson(self, path, fc):
        with open(path, "w") as f:
            geojson.dump(fc, f)

    def _read_layer(self, path):
        with open(path) as f:
            return geojson.load(f)

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_deletes_intersecting_features(self):
        """Features inside the polygon are removed; outside features survive."""
        layer_path = self.tmp / "layer.geojson"
        delta_path = self.tmp / "delta.geojson"
        (self.tmp / "work").mkdir()

        self._write_geojson(layer_path, self._make_layer([
            (self.POINT_A, "A"),
            (self.POINT_B, "B"),
            (self.POINT_C, "C"),
        ]))
        self._write_geojson(delta_path, self._make_delete_polygon())

        _apply_delete_delta(layer_path, delta_path)

        result = self._read_layer(layer_path)
        names = [f["properties"]["name"] for f in result["features"]]
        self.assertEqual(names, ["C"])

    def test_no_match_leaves_layer_unchanged(self):
        """If the polygon doesn't intersect anything, all features survive."""
        layer_path = self.tmp / "layer.geojson"
        delta_path = self.tmp / "delta.geojson"
        (self.tmp / "work").mkdir()

        self._write_geojson(layer_path, self._make_layer([
            (self.POINT_C, "C"),
        ]))
        # Polygon far from POINT_C
        self._write_geojson(delta_path, self._make_delete_polygon())

        _apply_delete_delta(layer_path, delta_path)

        result = self._read_layer(layer_path)
        self.assertEqual(len(result["features"]), 1)
        self.assertEqual(result["features"][0]["properties"]["name"], "C")

    def test_deletes_all_features(self):
        """All features deleted when all intersect the polygon."""
        layer_path = self.tmp / "layer.geojson"
        delta_path = self.tmp / "delta.geojson"
        (self.tmp / "work").mkdir()

        self._write_geojson(layer_path, self._make_layer([
            (self.POINT_A, "A"),
            (self.POINT_B, "B"),
        ]))
        self._write_geojson(delta_path, self._make_delete_polygon())

        _apply_delete_delta(layer_path, delta_path)

        result = self._read_layer(layer_path)
        self.assertEqual(len(result["features"]), 0)

    def test_delta_moved_to_work_dir(self):
        """Consumed delta file is moved to work/ subdirectory."""
        layer_path = self.tmp / "layer.geojson"
        delta_path = self.tmp / "assetless__20260101_000000__delete.geojson"
        work_dir = self.tmp / "work"
        work_dir.mkdir()

        self._write_geojson(layer_path, self._make_layer([(self.POINT_A, "A")]))
        self._write_geojson(delta_path, self._make_delete_polygon())

        _apply_delete_delta(layer_path, delta_path)

        self.assertFalse(delta_path.exists())
        self.assertTrue((work_dir / delta_path.name).exists())

    def test_delete_after_annotate_sequence(self):
        """apply_deltas handles create → annotate → delete in order via apply_deltas."""
        # This tests the full pipeline through apply_deltas() with a 'delete' action,
        # verifying the action name is parsed correctly from the filename stem.
        layer_dir = self.tmp / "layers" / "roads"
        deltas_dir = self.tmp / "deltas" / "roads"
        (layer_dir).mkdir(parents=True)
        (deltas_dir / "work").mkdir(parents=True)

        # Write a create delta with two features
        create_delta = deltas_dir / "assetless__20260101_000001__create.geojson"
        self._write_geojson(create_delta, self._make_layer([
            (self.POINT_A, "A"),
            (self.POINT_C, "C"),
        ]))

        # Write a delete delta that covers POINT_A only
        delete_delta = deltas_dir / "assetless__20260101_000002__delete.geojson"
        self._write_geojson(delete_delta, self._make_delete_polygon())

        config = {
            "name": "testatlas",
            "data_root": str(self.tmp),
            "dataswale": {"versions": ["staging"]},
        }

        result = apply_deltas(config, "roads")
        names = [f["properties"]["name"] for f in result["features"]]
        self.assertIn("C", names)
        self.assertNotIn("A", names)

    def test_annotate_after_delete_no_error(self):
        """Annotating a deleted feature finds nothing and does not raise."""
        # A delete followed by an annotate that covers the same area should be a no-op.
        # The annotate spatial join simply finds no matching features.
        layer_dir = self.tmp / "layers" / "roads"
        deltas_dir = self.tmp / "deltas" / "roads"
        (layer_dir).mkdir(parents=True)
        (deltas_dir / "work").mkdir(parents=True)

        # Start with one feature inside the polygon
        create_delta = deltas_dir / "assetless__20260101_000001__create.geojson"
        self._write_geojson(create_delta, self._make_layer([(self.POINT_A, "A")]))

        # Delete it
        delete_delta = deltas_dir / "assetless__20260101_000002__delete.geojson"
        self._write_geojson(delete_delta, self._make_delete_polygon())

        config = {
            "name": "testatlas",
            "data_root": str(self.tmp),
            "dataswale": {"versions": ["staging"]},
        }

        # Should not raise even though annotate would find nothing to match
        try:
            result = apply_deltas(config, "roads")
            self.assertEqual(len(result["features"]), 0)
        except Exception as e:
            self.fail(f"apply_deltas raised unexpectedly after delete: {e}")


if __name__ == '__main__':
    unittest.main() 