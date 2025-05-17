import os
import sys
import unittest
from pathlib import Path
import shutil
import tempfile
import json
from datetime import datetime
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

# Add the python directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from webapp import app, extract_coordinates_from_url

class TestWebApp(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory for testing
        self.test_dir = tempfile.mkdtemp()
        self.storage_dir = os.path.join(self.test_dir, "uploads")
        os.makedirs(self.storage_dir, exist_ok=True)
        
        # Create test client
        self.client = TestClient(app)
        
        # Patch the storage directory
        self.storage_patcher = patch('webapp.STORAGE_DIR', self.storage_dir)
        self.storage_patcher.start()

    def tearDown(self):
        # Clean up the temporary directory
        shutil.rmtree(self.test_dir)
        self.storage_patcher.stop()

    def test_extract_coordinates_from_url(self):
        """Test coordinate extraction from various Google Maps URL formats"""
        # Test @ format
        lat, lon = extract_coordinates_from_url("https://www.google.com/maps/@37.7749,-122.4194,15z")
        self.assertEqual(lat, 37.7749)
        self.assertEqual(lon, -122.4194)

        # Test q parameter format
        lat, lon = extract_coordinates_from_url("https://www.google.com/maps?q=37.7749,-122.4194")
        self.assertEqual(lat, 37.7749)
        self.assertEqual(lon, -122.4194)

        # Test invalid URL
        with self.assertRaises(Exception):
            extract_coordinates_from_url("https://example.com")

    @patch('webapp.requests.get')
    def test_extract_coordinates_from_short_url(self, mock_get):
        """Test coordinate extraction from shortened URLs"""
        # Mock the redirect response
        mock_response = MagicMock()
        mock_response.url = "https://www.google.com/maps/@37.7749,-122.4194,15z"
        mock_get.return_value = mock_response

        lat, lon = extract_coordinates_from_url("https://goo.gl/maps/abc123")
        self.assertEqual(lat, 37.7749)
        self.assertEqual(lon, -122.4194)

    def test_json_upload_poi(self):
        """Test JSON upload for POI data"""
        # Create test payload
        payload = {
            "data": {
                "asset": "pin_to_poi",
                "url": "https://www.google.com/maps/@37.7749,-122.4194,15z",
                "poi_type": "test_poi"
            }
        }

        # Make request
        response = self.client.post("/json_upload", json=payload)
        
        # Check response
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")
        
        # Check file was created
        poi_dir = os.path.join(self.storage_dir, "poi_deltas")
        self.assertTrue(os.path.exists(poi_dir))
        files = os.listdir(poi_dir)
        self.assertEqual(len(files), 1)
        
        # Check file contents
        with open(os.path.join(poi_dir, files[0]), 'r') as f:
            data = json.load(f)
            self.assertEqual(data["type"], "FeatureCollection")
            self.assertEqual(len(data["features"]), 1)
            feature = data["features"][0]
            self.assertEqual(feature["geometry"]["type"], "Point")
            self.assertEqual(feature["geometry"]["coordinates"], [-122.4194, 37.7749])
            self.assertEqual(feature["properties"]["poi_type"], "test_poi")

    def test_store_json(self):
        """Test storing JSON data with versioning"""
        # Create test payload
        payload = {
            "data": {
                "layer": "test_layer",
                "content": "test content"
            }
        }

        # Make request
        response = self.client.post("/store/test_swale", json=payload)
        
        # Check response
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")
        
        # Check file was created
        swale_dir = os.path.join(self.storage_dir, "test_swale", "test_layer")
        self.assertTrue(os.path.exists(swale_dir))
        files = os.listdir(swale_dir)
        self.assertEqual(len(files), 1)
        
        # Check file contents
        with open(os.path.join(swale_dir, files[0]), 'r') as f:
            data = json.load(f)
            self.assertEqual(data["layer"], "test_layer")
            self.assertEqual(data["content"], "test content")

    @patch('webapp.atlas.asset_materialize')
    def test_refresh(self, mock_materialize):
        """Test refreshing an asset"""
        # Mock the materialize function
        mock_materialize.return_value = "success"
        
        # Mock config files
        with patch('builtins.open', MagicMock()) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = '{"assets": {"test_asset": {}}}'
            
            # Make request
            response = self.client.get("/refresh?swale=test_swale&asset=test_asset")
            
            # Check response
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "success")
            self.assertEqual(response.json()["asset"], "test_asset")
            
            # Check materialize was called
            mock_materialize.assert_called_once()

    def test_publish_status(self):
        """Test publish status endpoint"""
        # Make request
        response = self.client.get("/publish-status?swale=test_swale")
        
        # Check response
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("publishing", data)
        self.assertIn("started_at", data)
        self.assertIn("finished_at", data)
        self.assertIn("log", data)

if __name__ == '__main__':
    unittest.main() 