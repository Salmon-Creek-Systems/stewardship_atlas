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

from unittest.mock import patch

from raster_inlets import resample_raster_gdal, set_crs_raster, vsi_path, cog_source

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


class TestVsiPath(unittest.TestCase):
    """A source URL becomes the GDAL virtual filesystem path for it."""

    def test_https_becomes_vsicurl(self):
        self.assertEqual(vsi_path('https://x.s3.amazonaws.com/a/b.cog.tif'),
                         '/vsicurl/https://x.s3.amazonaws.com/a/b.cog.tif')

    def test_http_becomes_vsicurl(self):
        self.assertEqual(vsi_path('http://example.org/b.tif'),
                         '/vsicurl/http://example.org/b.tif')

    def test_s3_becomes_vsis3(self):
        self.assertEqual(vsi_path('s3://bucket/key/b.cog.tif'), '/vsis3/bucket/key/b.cog.tif')

    def test_existing_vsi_path_passes_through(self):
        self.assertEqual(vsi_path('/vsicurl/https://x/y.tif'), '/vsicurl/https://x/y.tif')

    def test_local_path_passes_through(self):
        self.assertEqual(vsi_path('/root/data/x.tif'), '/root/data/x.tif')


class TestCogSource(unittest.TestCase):
    """The inlet windows a remote COG rather than downloading it."""

    URL = 'https://scs-atlas-data.s3.us-east-1.amazonaws.com/sources/rasters/hs/hs.cog.tif'

    def setUp(self):
        self.config = {
            'name': 'test_atlas',
            'dataswale': {
                'crs': 'EPSG:4269',
                'bbox': {'west': -123.7, 'east': -123.4, 'south': 39.5, 'north': 39.8},
            },
            'assets': {
                'hillshade_source': {
                    'out_layer': 'basemap',
                    'config': {'url': self.URL},
                },
            },
        }

        class FakeQueue:
            def delta_path(self, config, name, action):
                return Path('/tmp/deltas/basemap/hillshade_source__x__create.tiff')

        self.queue = FakeQueue()

    def test_windows_remote_cog_without_downloading(self):
        with patch('utils.canonicalize_raster') as warp:
            out = cog_source(self.config, 'hillshade_source', delta_queue=self.queue)

        args, kwargs = warp.call_args
        self.assertEqual(args[0], '/vsicurl/' + self.URL)
        self.assertEqual(args[2], 'EPSG:4269')
        self.assertEqual(args[3], self.config['dataswale']['bbox'])
        # Without this GDAL lists the whole containing prefix before reading a byte.
        self.assertEqual(kwargs['gdal_config']['GDAL_DISABLE_READDIR_ON_OPEN'], 'EMPTY_DIR')
        # canonicalize_raster returns its *input*; the inlet must return the delta.
        self.assertEqual(out, self.queue.delta_path(None, None, None))

    def test_resample_width_passed_through(self):
        self.config['assets']['hillshade_source']['config']['resample_width'] = 2048
        with patch('utils.canonicalize_raster') as warp:
            cog_source(self.config, 'hillshade_source', delta_queue=self.queue)
        self.assertEqual(warp.call_args[0][4], 2048)

    def test_resample_width_defaults_to_none(self):
        with patch('utils.canonicalize_raster') as warp:
            cog_source(self.config, 'hillshade_source', delta_queue=self.queue)
        self.assertIsNone(warp.call_args[0][4])

    def test_s3_url_uses_vsis3(self):
        self.config['assets']['hillshade_source']['config']['url'] = 's3://b/k/hs.cog.tif'
        with patch('utils.canonicalize_raster') as warp:
            cog_source(self.config, 'hillshade_source', delta_queue=self.queue)
        self.assertEqual(warp.call_args[0][0], '/vsis3/b/k/hs.cog.tif')


if __name__ == '__main__':
    unittest.main() 