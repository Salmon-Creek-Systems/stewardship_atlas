import os
import sys
import unittest
from pathlib import Path
import shutil
from unittest.mock import patch, MagicMock, mock_open
import json
import numpy as np

# Add the python directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from eddies import contours_gdal, hillshade_gdal, asset_methods

class TestEddies(unittest.TestCase):
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
            },
            "assets": {
                "test_contours": {
                    "in_layer": "dem",
                    "out_layer": "contours",
                    "config": {
                        "interval": 10.0
                    }
                },
                "test_hillshade": {
                    "in_layer": "dem",
                    "out_layer": "hillshade",
                    "config": {
                        "intensity": 0.7
                    }
                }
            }
        }
        
        # Create test directory
        os.makedirs(self.test_config["data_root"], exist_ok=True)
        
        # Create test fixture directory if it doesn't exist
        self.fixture_dir = Path(os.path.join(os.path.dirname(__file__), "fixtures"))
        os.makedirs(self.fixture_dir, exist_ok=True)
        
        # Create a test DEM file
        self.test_dem = Path(self.test_config["data_root"]) / "dem.tiff"
        with open(self.test_dem, 'w') as f:
            f.write("dummy dem content")

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

    @patch('gdal.Open')
    @patch('ogr.GetDriverByName')
    @patch('gdal.ContourGenerate')
    def test_contours_gdal(self, mock_contour_generate, mock_driver, mock_gdal_open):
        """Test contour generation from DEM"""
        # Mock GDAL dataset
        mock_ds = MagicMock()
        mock_band = MagicMock()
        mock_ds.GetRasterBand.return_value = mock_band
        mock_gdal_open.return_value = mock_ds
        
        # Mock memory driver and dataset
        mock_mem_driver = MagicMock()
        mock_mem_ds = MagicMock()
        mock_mem_layer = MagicMock()
        mock_mem_driver.CreateDataSource.return_value = mock_mem_ds
        mock_mem_ds.CreateLayer.return_value = mock_mem_layer
        mock_driver.return_value = mock_mem_driver
        
        # Mock GeoJSON driver
        mock_geojson_driver = MagicMock()
        mock_geojson_ds = MagicMock()
        mock_geojson_layer = MagicMock()
        mock_driver.side_effect = [mock_mem_driver, mock_geojson_driver]
        mock_geojson_driver.CreateDataSource.return_value = mock_geojson_ds
        mock_geojson_ds.CopyLayer.return_value = mock_geojson_layer
        
        # Call the function
        result = contours_gdal(self.test_config, 'test_contours')
        
        # Verify GDAL operations
        mock_gdal_open.assert_called_once()
        mock_ds.GetRasterBand.assert_called_once_with(1)
        mock_mem_ds.CreateLayer.assert_called_once()
        mock_contour_generate.assert_called_once()
        mock_geojson_driver.CreateDataSource.assert_called_once()
        mock_geojson_ds.CopyLayer.assert_called_once()

    @patch('gdal.Open')
    @patch('gdal.GetDriverByName')
    def test_hillshade_gdal(self, mock_driver, mock_gdal_open):
        """Test hillshade generation from DEM"""
        # Mock GDAL dataset
        mock_ds = MagicMock()
        mock_ds.ReadAsArray.return_value = np.array([[0, 1], [1, 0]])
        mock_ds.RasterXSize = 2
        mock_ds.RasterYSize = 2
        mock_ds.GetGeoTransform.return_value = (0, 1, 0, 0, 0, 1)
        mock_ds.GetProjection.return_value = "EPSG:4326"
        mock_gdal_open.return_value = mock_ds
        
        # Mock output dataset
        mock_out_ds = MagicMock()
        mock_out_band = MagicMock()
        mock_out_ds.GetRasterBand.return_value = mock_out_band
        mock_driver.return_value.Create.return_value = mock_out_ds
        
        # Call the function
        result = hillshade_gdal(self.test_config, 'test_hillshade')
        
        # Verify GDAL operations
        mock_gdal_open.assert_called_once()
        mock_ds.ReadAsArray.assert_called_once()
        mock_driver.assert_called_once_with('GTiff')
        mock_out_ds.SetGeoTransform.assert_called_once()
        mock_out_ds.SetProjection.assert_called_once()
        mock_out_band.WriteArray.assert_called_once()

    def test_asset_methods(self):
        """Test that asset methods are correctly registered"""
        self.assertIn('generate_contours', asset_methods)
        self.assertIn('derived_hillshade', asset_methods)
        self.assertEqual(asset_methods['generate_contours'], contours_gdal)
        self.assertEqual(asset_methods['derived_hillshade'], hillshade_gdal)

if __name__ == '__main__':
    unittest.main() 