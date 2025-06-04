import json
import os
import sys
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
    InvalidDelta
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

if __name__ == '__main__':
    unittest.main() 