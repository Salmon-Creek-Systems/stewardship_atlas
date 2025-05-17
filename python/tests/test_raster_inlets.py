import os
import sys
import unittest
from pathlib import Path
import shutil

# Add the python directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Configure GDAL to only show errors, not warnings
os.environ['CPL_LOG'] = '/dev/null'
os.environ['GDAL_ERROR_LEVEL'] = '1'  # Only show errors, not warnings

from raster_inlets import resample_raster_gdal, set_crs_raster

class TestRasterInlets(unittest.TestCase):
    def setUp(self):
        self.test_config = {
            "name": "test_atlas",
            "data_root": "test_data",
            "dataswale": {
                "name": "test_layer",
                "crs": "EPSG:4326"
            }
        }
        self.fixture_path = Path(os.path.join(os.path.dirname(__file__), "fixtures", "atlas3.tiff"))
        self.assertTrue(self.fixture_path.exists(), "Test fixture atlas3.tiff not found")
        
        # Create test directory in setUp
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

    def test_set_crs_raster(self):
        """Test that set_crs_raster correctly sets the CRS"""
        # Copy fixture to test directory
        test_tiff = Path(self.test_config["data_root"]) / "test.tiff"
        shutil.copy(self.fixture_path, test_tiff)
        
        # Set CRS
        outpath = set_crs_raster(self.test_config, test_tiff)
        
        # Verify output file exists
        self.assertTrue(Path(outpath).exists())
        self.assertGreater(Path(outpath).stat().st_size, 0)

    def test_resample_raster_gdal(self):
        """Test that resample_raster_gdal correctly resamples the raster"""
        # Copy fixture to test directory
        test_tiff = Path(self.test_config["data_root"]) / "test.tiff"
        shutil.copy(self.fixture_path, test_tiff)
        
        # Resample raster
        resample_width = 100
        outpath = resample_raster_gdal(self.test_config, test_tiff, resample_width)
        
        # Verify output file exists
        self.assertTrue(Path(outpath).exists())
        self.assertGreater(Path(outpath).stat().st_size, 0)

if __name__ == '__main__':
    unittest.main() 