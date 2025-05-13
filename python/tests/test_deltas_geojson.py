import json
import os
import sys
import unittest
from pathlib import Path

# Add the python directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import geojson
from deltas_geojson import create, add_deltas, transform, queue, InvalidDelta

class TestDeltasGeoJSON(unittest.TestCase):
    def setUp(self):
        self.test_config = {
            "name": "test_layer",
            "data_root": "test_data",
            "vector_width": 3
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

    def test_add_deltas(self):
        """Test that add_deltas() correctly writes and validates GeoJSON"""
        # Create directory structure
        create(self.test_config)
        
        # Add deltas
        count, path = add_deltas(self.test_feature_collection, self.test_config)
        
        self.assertEqual(count, len(self.test_feature_collection["features"]))
        self.assertTrue(Path(path).exists())
        
        # Verify file contents
        with open(path, "r") as f:
            saved_data = json.load(f)
        self.assertEqual(saved_data, self.test_feature_collection)
        
        # Test invalid input
        with self.assertRaises(InvalidDelta) as cm:
            add_deltas({"type": "Feature"}, self.test_config)
        self.assertIn("Input must be a GeoJSON FeatureCollection", str(cm.exception))

    def test_queue(self):
        """Test that queue() correctly processes and moves files"""
        # Create directory structure
        create(self.test_config)
        
        # Add a test file
        count, path = add_deltas(self.test_feature_collection, self.test_config)
        
        # Process queue
        features = list(queue(self.test_config))
        
        self.assertEqual(len(features), len(self.test_feature_collection["features"]))
        self.assertTrue(all(f["properties"]["vector_width"] == self.test_config["vector_width"] for f in features))
        
        # Verify file was moved to processed
        self.assertFalse(Path(path).exists())
        processed_path = Path(self.test_config["data_root"]) / f"deltas_{self.test_config['name']}" / "processed" / Path(path).name
        self.assertTrue(processed_path.exists())

    def test_queue_invalid_file(self):
        """Test that queue() handles invalid files correctly"""
        # Create directory structure
        create(self.test_config)
        
        # Create an invalid file
        invalid_path = Path(self.test_config["data_root"]) / f"deltas_{self.test_config['name']}" / "invalid.geojson"
        with open(invalid_path, "w") as f:
            f.write("invalid json")
        
        # Test queue processing
        with self.assertRaises(InvalidDelta) as cm:
            list(queue(self.test_config))
        self.assertIn("Invalid JSON in", str(cm.exception))

if __name__ == '__main__':
    unittest.main() 