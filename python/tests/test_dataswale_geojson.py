import os
import sys
import unittest
from pathlib import Path

# Add the python directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dataswale_geojson import alter_geojson, enrich_ogr_featureset

class TestDataswaleGeoJSON(unittest.TestCase):
    def setUp(self):
        self.test_config = {
            "name": "test_layer",
            "data_root": "test_data"
        }
        # Create a simple test GeoJSON file
        self.test_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [0, 0]
                    },
                    "properties": {
                        "name": "Test Point",
                        "value": 100,
                        "prefix_test": "prefix_value"
                    }
                }
            ]
        }
        self.test_file = os.path.join(self.test_config["data_root"], "test.geojson")
        os.makedirs(self.test_config["data_root"], exist_ok=True)

    def tearDown(self):
        # Clean up any test files that might have been created
        test_dir = Path(self.test_config["data_root"])
        if test_dir.exists():
            for file in test_dir.rglob("*"):
                if file.is_file():
                    file.unlink()
            for dir in reversed(list(test_dir.rglob("*"))):
                if dir.is_dir():
                    dir.rmdir()
            test_dir.rmdir()

    def test_alter_geojson_canonicalize(self):
        """Test that alter_geojson correctly handles property canonicalization"""
        # Write test file
        with open(self.test_file, 'w') as f:
            json.dump(self.test_geojson, f)
        
        # Define alterations
        alterations = {
            "canonicalize": [
                {
                    "from": ["name"],
                    "to": "display_name"
                },
                {
                    "from": ["prefix_test"],
                    "to": "REMOVE",
                    "remove_prefix": ["prefix_"]
                }
            ]
        }
        
        # Apply alterations
        alter_geojson(self.test_file, alterations)
        
        # Read and verify changes
        with open(self.test_file, 'r') as f:
            result = json.load(f)
        
        feature = result['features'][0]
        self.assertIn('display_name', feature['properties'])
        self.assertEqual(feature['properties']['display_name'], 'Test Point')
        self.assertNotIn('prefix_test', feature['properties'])

    def test_alter_geojson_vector_width(self):
        """Test that alter_geojson correctly sets vector width"""
        # Write test file
        with open(self.test_file, 'w') as f:
            json.dump(self.test_geojson, f)
        
        # Define alterations
        alterations = {
            "vector_width": {
                "default": 2,
                "attribute": "value",
                "map": {
                    "100": 3
                }
            }
        }
        
        # Apply alterations
        alter_geojson(self.test_file, alterations)
        
        # Read and verify changes
        with open(self.test_file, 'r') as f:
            result = json.load(f)
        
        feature = result['features'][0]
        self.assertIn('vector_width', feature['properties'])
        self.assertEqual(feature['properties']['vector_width'], 3)

    def test_enrich_ogr_featureset(self):
        """Test that enrich_ogr_featureset correctly adds properties"""
        # Test with missing properties
        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [0, 0]
            }
        }
        enriched = enrich_ogr_featureset({"features": [feature]})
        self.assertIn('properties', enriched['features'][0])
        self.assertEqual(enriched['features'][0]['properties'], {})
        
        # Test with existing properties
        feature_with_props = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [0, 0]
            },
            "properties": {"test": "value"}
        }
        enriched = enrich_ogr_featureset({"features": [feature_with_props]})
        self.assertEqual(enriched['features'][0]['properties'], {"test": "value"})

if __name__ == '__main__':
    unittest.main() 