import os
import sys
import unittest
from pathlib import Path
import shutil
from unittest.mock import patch, MagicMock
import json

# Add the python directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils import (
    rgb_to_css,
    bbox_to_corners,
    bbox_to_polygon,
    geojson_to_bbox,
    tiff2jpg,
    canonicalize_raster,
    resample_raster_gdal,
    set_crs_raster,
    alter_geojson
)

class TestUtils(unittest.TestCase):
    def setUp(self):
        self.test_config = {
            "name": "test_atlas",
            "data_root": "test_data",
            "dataswale": {
                "name": "test_layer",
                "crs": "EPSG:4326",
                "bbox": {
                    "west": -73.5,
                    "south": 41.0,
                    "east": -73.0,
                    "north": 41.5
                }
            }
        }
        
        # Create test directory
        os.makedirs(self.test_config["data_root"], exist_ok=True)
        
        # Create test fixture directory if it doesn't exist
        self.fixture_dir = Path(os.path.join(os.path.dirname(__file__), "fixtures"))
        os.makedirs(self.fixture_dir, exist_ok=True)
        
        # Create a test TIFF file
        self.test_tiff = Path(self.test_config["data_root"]) / "test.tiff"
        with open(self.test_tiff, 'w') as f:
            f.write("dummy tiff content")
            
        # Create a test GeoJSON file
        self.test_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [-73.2, 41.2]
                    },
                    "properties": {
                        "id": 1,
                        "name": "Test Point",
                        "width": "wide"
                    }
                }
            ]
        }
        self.test_geojson_path = Path(self.test_config["data_root"]) / "test.geojson"
        with open(self.test_geojson_path, 'w') as f:
            json.dump(self.test_geojson, f)

    def tearDown(self):
        # Clean up test directory
        test_dir = Path(self.test_config["data_root"])
        if test_dir.exists():
            for file in test_dir.rglob("*"):
                if file.is_file():
                    file.unlink()
            for dir in reversed(list(test_dir.rglob("*"))):
                if dir.is_dir():
                    dir.rmdir()
            test_dir.rmdir()

    def test_rgb_to_css(self):
        """Test RGB to CSS color conversion"""
        test_cases = [
            ((255, 0, 0), 'rgb(255, 0, 0)'),
            ((0, 255, 0), 'rgb(0, 255, 0)'),
            ((0, 0, 255), 'rgb(0, 0, 255)'),
            ((128, 128, 128), 'rgb(128, 128, 128)')
        ]
        for rgb, expected in test_cases:
            self.assertEqual(rgb_to_css(rgb), expected)

    def test_bbox_to_corners(self):
        """Test bbox to corners conversion"""
        bbox = {
            "west": -73.5,
            "south": 41.0,
            "east": -73.0,
            "north": 41.5
        }
        expected = [
            [-73.5, 41.5],  # northwest
            [-73.0, 41.5],  # northeast
            [-73.0, 41.0],  # southeast
            [-73.5, 41.0]   # southwest
        ]
        self.assertEqual(bbox_to_corners(bbox), expected)

    def test_bbox_to_polygon(self):
        """Test bbox to polygon conversion"""
        bbox = {
            "west": -73.5,
            "south": 41.0,
            "east": -73.0,
            "north": 41.5
        }
        expected = [
            [-73.5, 41.5],  # northwest
            [-73.0, 41.5],  # northeast
            [-73.0, 41.0],  # southeast
            [-73.5, 41.0],  # southwest
            [-73.5, 41.5]   # back to northwest to close polygon
        ]
        self.assertEqual(bbox_to_polygon(bbox), expected)

    def test_geojson_to_bbox(self):
        """Test GeoJSON to bbox conversion"""
        geojson = [
            [-73.5, 41.0],  # southwest
            [-73.5, 41.5],  # northwest
            [-73.0, 41.5],  # northeast
            [-73.0, 41.0]   # southeast
        ]
        expected = {
            "west": -73.5,
            "south": 41.0,
            "east": -73.0,
            "north": 41.5
        }
        self.assertEqual(geojson_to_bbox(geojson), expected)

    @patch('subprocess.check_output')
    def test_tiff2jpg(self, mock_check_output):
        """Test TIFF to JPG conversion"""
        mock_check_output.return_value = b''
        result = tiff2jpg(str(self.test_tiff))
        self.assertEqual(result, str(self.test_tiff) + ".jpg")
        mock_check_output.assert_called_once_with(['gdal_translate', '-b', '1', '-scale', str(self.test_tiff), str(self.test_tiff) + ".jpg"])

    @patch('subprocess.check_output')
    def test_canonicalize_raster(self, mock_check_output):
        """Test raster canonicalization"""
        mock_check_output.return_value = b''
        result = canonicalize_raster(
            str(self.test_tiff),
            str(self.test_tiff),
            self.test_config['dataswale']['crs'],
            self.test_config['dataswale']['bbox']
        )
        self.assertEqual(result, str(self.test_tiff))
        mock_check_output.assert_called_once()

    @patch('subprocess.check_output')
    def test_resample_raster_gdal(self, mock_check_output):
        """Test raster resampling"""
        mock_check_output.return_value = b''
        result = resample_raster_gdal(self.test_config, str(self.test_tiff), 400)
        self.assertEqual(result, str(self.test_tiff))
        mock_check_output.assert_called_once()

    @patch('subprocess.check_output')
    def test_set_crs_raster(self, mock_check_output):
        """Test setting raster CRS"""
        mock_check_output.return_value = b''
        result = set_crs_raster(self.test_config, str(self.test_tiff))
        self.assertEqual(result, str(self.test_tiff))
        mock_check_output.assert_called_once()

    def test_alter_geojson_canonicalize(self):
        """Test GeoJSON property canonicalization"""
        alt_conf = {
            "canonicalize": [
                {
                    "from": ["name"],
                    "to": "display_name",
                    "default": "Unknown"
                }
            ]
        }
        alter_geojson(str(self.test_geojson_path), alt_conf)
        
        with open(self.test_geojson_path, 'r') as f:
            result = json.load(f)
        
        self.assertEqual(result['features'][0]['properties']['display_name'], "Test Point")
        self.assertNotIn('name', result['features'][0]['properties'])

    def test_alter_geojson_vector_width(self):
        """Test GeoJSON vector width alteration"""
        alt_conf = {
            "vector_width": {
                "attribute": "width",
                "map": {
                    "wide": 5,
                    "narrow": 2
                },
                "default": 3
            }
        }
        alter_geojson(str(self.test_geojson_path), alt_conf)
        
        with open(self.test_geojson_path, 'r') as f:
            result = json.load(f)
        
        self.assertEqual(result['features'][0]['properties']['vector_width'], 5)

    def test_alter_geojson_concat(self):
        """Test GeoJSON property concatenation"""
        alt_conf = {
            "canonicalize": [
                {
                    "from": ["id", "name"],
                    "to": "full_name",
                    "concat": " - "
                }
            ]
        }
        alter_geojson(str(self.test_geojson_path), alt_conf)
        
        with open(self.test_geojson_path, 'r') as f:
            result = json.load(f)
        
        self.assertEqual(result['features'][0]['properties']['full_name'], "1 - Test Point")

    def test_alter_geojson_remove_prefix(self):
        """Test GeoJSON property prefix removal"""
        alt_conf = {
            "canonicalize": [
                {
                    "from": ["name"],
                    "to": "clean_name",
                    "remove_prefix": ["Test "]
                }
            ]
        }
        alter_geojson(str(self.test_geojson_path), alt_conf)
        
        with open(self.test_geojson_path, 'r') as f:
            result = json.load(f)
        
        self.assertEqual(result['features'][0]['properties']['clean_name'], "Point")

if __name__ == '__main__':
    unittest.main() 