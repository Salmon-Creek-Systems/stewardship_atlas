import os
import sys
import unittest
from pathlib import Path
import json
import geojson
import shutil

# Add the python directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dataswale_geojson import (
    create,
    delete,
    new_version,
    asset,
    refresh_vector_layer,
    refresh_raster_layer,
    eddy
)

class TestDataswaleGeoJSON(unittest.TestCase):
    def setUp(self):
        self.test_config = {
            "name": "test_layer",
            "data_root": "test_data",
            "assets": {
                "test_asset": {
                    "config": {
                        "data_type": "geojson"
                    },
                    "out_layer": "test_layer"
                },
                "test_eddy": {
                    "config": {
                        "data_type": "geojson"
                    },
                    "out_layer": "test_layer"
                }
            }
        }
        # Create a simple test GeoJSON file
        self.test_feature_collection = geojson.FeatureCollection([
            geojson.Feature(
                geometry=geojson.Point([0, 0]),
                properties={"name": "Test Point", "value": 100}
            )
        ])
        
        # Create test directories
        os.makedirs(self.test_config["data_root"], exist_ok=True)
        
        # Create a mock delta queue builder function
        def mock_delta_queue_builder(config, name):
            return self.test_feature_collection
        self.mock_delta_queue_builder = mock_delta_queue_builder

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

    def test_refresh_vector_layer(self):
        """Test that refresh_vector_layer correctly rebuilds a vector layer"""
        # Create the layer directory
        layer_dir = Path(self.test_config["data_root"]) / "layers" / "test_layer"
        layer_dir.mkdir(parents=True, exist_ok=True)
        
        # Refresh the layer
        layer_path = refresh_vector_layer(self.test_config, "test_layer", self.mock_delta_queue_builder)
        
        # Verify the layer file was created
        self.assertTrue(Path(layer_path).exists())
        
        # Verify the contents
        with open(layer_path, 'r') as f:
            result = json.load(f)
        self.assertEqual(result, self.test_feature_collection)

    def test_refresh_raster_layer(self):
        """Test that refresh_raster_layer correctly rebuilds a raster layer"""
        # Create test directories
        deltas_dir = Path(self.test_config["data_root"]) / "deltas" / "test_layer"
        deltas_dir.mkdir(parents=True, exist_ok=True)
        
        # Create a test raster file
        test_raster = deltas_dir / "test.tiff"
        with open(test_raster, 'w') as f:
            f.write("test raster data")
        
        # Refresh the layer
        layer_path = refresh_raster_layer(self.test_config, "test_layer", None)
        
        # Verify the layer file was created
        self.assertTrue(Path(layer_path).exists())
        
        # Verify the contents match
        with open(layer_path, 'r') as f1, open(test_raster, 'r') as f2:
            self.assertEqual(f1.read(), f2.read())

    def test_eddy(self):
        """Test that eddy correctly applies transformations"""
        # Create the layer directory
        layer_dir = Path(self.test_config["data_root"]) / "layers" / "test_layer"
        layer_dir.mkdir(parents=True, exist_ok=True)
        
        # Mock the eddy function
        def mock_eddy(config, eddy_name):
            return layer_dir / "transformed.geojson"
        
        # Apply the eddy
        result = eddy(self.test_config, "test_eddy")
        
        # Verify the result
        self.assertIsInstance(result, Path)
        self.assertTrue(result.exists())

if __name__ == '__main__':
    unittest.main() 

class TestFeatureWebmapUrls(unittest.TestCase):
    """add_webmap_urls picks a builder by layer name; an override always wins."""

    CONFIG = {'base_url': 'https://fireatlas.org/sfe',
              'dataswale': {'layers': [{'name': 'regions'}, {'name': 'roads_primary'}]}}

    def _fc(self, **props):
        return {'type': 'FeatureCollection', 'features': [{
            'type': 'Feature', 'properties': dict(props),
            'geometry': {'type': 'Polygon', 'coordinates': [[
                [-123.70, 39.70], [-123.69, 39.70], [-123.69, 39.71],
                [-123.70, 39.71], [-123.70, 39.70]]]}}]}

    def test_the_module_under_test_is_real(self):
        import dataswale_geojson
        self.assertFalse(hasattr(dataswale_geojson.add_webmap_urls, 'return_value'))

    def test_regions_get_a_share_view_link(self):
        from dataswale_geojson import add_webmap_urls
        url = add_webmap_urls(self.CONFIG, 'regions', self._fc())['features'][0]['properties']['webmap_url']
        self.assertIn('/staging/outlets/webmap/?s=', url)

    def test_other_layers_keep_the_centroid_link(self):
        from dataswale_geojson import add_webmap_urls
        url = add_webmap_urls(self.CONFIG, 'roads_primary', self._fc())['features'][0]['properties']['webmap_url']
        self.assertIn('?lat=', url)
        self.assertIn('&zoom=17', url)

    def test_override_wins(self):
        from dataswale_geojson import add_webmap_urls
        fc = add_webmap_urls(self.CONFIG, 'regions', self._fc(webmap_url_override='https://x/y'))
        self.assertEqual(fc['features'][0]['properties']['webmap_url'], 'https://x/y')
