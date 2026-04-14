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

# Stub out heavy server-side dependencies before importing eddies
for _mod in ('duckdb', 'geojson', 'osgeo', 'osgeo.gdal', 'osgeo.ogr',
             'versioning', 'utils', 'outlets', 'dataswale_geojson',
             'matplotlib', 'matplotlib.colors', 'matplotlib.pyplot',
             'rasterio', 'geopandas', 'mercantile', 'pmtiles',
             'pmtiles.writer', 'shapely', 'shapely.geometry'):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

import h3
from eddies import (contours_gdal, hillshade_gdal, h3_for_point, asset_methods,
                    _h3_grid_distance_safe, _get_cell_path_distances, _cell_yield)

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
        self.assertIn('gdal_contours', asset_methods)
        self.assertIn('contours', asset_methods)  # alias
        self.assertIn('derived_hillshade', asset_methods)
        self.assertIn('h3_cells', asset_methods)
        self.assertIn('fuel_mass_landfire', asset_methods)
        self.assertIn('biochar_simulation', asset_methods)
        self.assertEqual(asset_methods['gdal_contours'], contours_gdal)
        self.assertEqual(asset_methods['derived_hillshade'], hillshade_gdal)


class TestH3ForPoint(unittest.TestCase):
    """Tests for h3_for_point — Point geometry H3 indexing."""

    def _make_point(self, lng, lat):
        return {'type': 'Point', 'coordinates': [lng, lat]}

    def test_returns_single_cell(self):
        """h3_for_point should return exactly one H3 cell."""
        geometry = self._make_point(-122.4, 37.8)
        result = h3_for_point(geometry)
        self.assertEqual(result['cell_count'], 1)
        self.assertEqual(len(result['cells']), 1)

    def test_representative_index_matches_cell(self):
        """representative_index should be the same as the single cell."""
        geometry = self._make_point(-122.4, 37.8)
        result = h3_for_point(geometry)
        self.assertEqual(result['representative_index'], result['cells'][0])

    def test_resolution_matches_starting_res(self):
        """Returned resolution should match the requested starting resolution."""
        geometry = self._make_point(-118.2, 34.0)
        result = h3_for_point(geometry, starting_res=9)
        self.assertEqual(result['resolution'], 9)

    def test_cell_is_valid_h3_string(self):
        """The returned cell should be a non-empty string (valid H3 index)."""
        geometry = self._make_point(-122.4, 37.8)
        result = h3_for_point(geometry)
        cell = result['cells'][0]
        self.assertIsInstance(cell, str)
        self.assertTrue(len(cell) > 0)

    def test_wrong_geometry_type_raises(self):
        """Passing a non-Point geometry should raise an exception."""
        geometry = {'type': 'LineString', 'coordinates': [[-122.4, 37.8], [-122.5, 37.9]]}
        with self.assertRaises(Exception):
            h3_for_point(geometry)

    def test_missing_coordinates_raises(self):
        """Geometry without coordinates should raise an exception."""
        with self.assertRaises(Exception):
            h3_for_point({'type': 'Point'})

    def test_accepts_max_num_cells_kwarg(self):
        """h3_for_point should silently accept max_num_cells (used by h3_cells dispatcher)."""
        geometry = self._make_point(-122.4, 37.8)
        result = h3_for_point(geometry, starting_res=8, max_num_cells=10)
        self.assertEqual(result['cell_count'], 1)


class TestBiocharSimulation(unittest.TestCase):
    """Tests for biochar simulation helper functions (pure functions, no I/O)."""

    def setUp(self):
        # Two adjacent H3 cells at resolution 8 — use latlng_to_cell to get valid cells
        self.cell_a = h3.latlng_to_cell(37.5, -122.0, 8)
        self.cell_b = h3.latlng_to_cell(37.51, -122.01, 8)
        # A cell at a different resolution
        self.cell_low_res = h3.latlng_to_cell(37.5, -122.0, 6)

        self.sample_treatment = {
            "production_rate": 8,
            "biomass_rate": 0.90,
            "biochar_rate": 0.30,
            "risk_reduction_rate": 0.90,
            "cost_rate": 1000,
            "water_distance_cost": 0.01,
            "drag_distance_cost": 0.03,
            "air_pollution": 2,
        }

    # --- _h3_grid_distance_safe ---

    def test_same_resolution_returns_nonneg_int(self):
        dist = _h3_grid_distance_safe(self.cell_a, self.cell_b)
        self.assertIsInstance(dist, int)
        self.assertGreaterEqual(dist, 0)

    def test_same_cell_distance_is_zero(self):
        dist = _h3_grid_distance_safe(self.cell_a, self.cell_a)
        self.assertEqual(dist, 0)

    def test_mismatched_resolution_does_not_raise(self):
        # Should not raise even though resolutions differ
        try:
            dist = _h3_grid_distance_safe(self.cell_a, self.cell_low_res)
            self.assertGreaterEqual(dist, 0)
        except Exception as e:
            self.fail(f"_h3_grid_distance_safe raised unexpectedly: {e}")

    # --- _get_cell_path_distances ---

    def test_empty_target_sets_distance_to_zero(self):
        burns = [{'properties': {'h3_index': self.cell_a}}]
        result = _get_cell_path_distances(burns, [], 'roads_distance')
        self.assertEqual(result[0]['properties']['roads_distance'], 0)

    def test_nonempty_target_sets_nonneg_distance(self):
        burns = [{'properties': {'h3_index': self.cell_a}}]
        targets = [{'properties': {'h3_index': self.cell_b}}]
        result = _get_cell_path_distances(burns, targets, 'roads_distance')
        self.assertGreaterEqual(result[0]['properties']['roads_distance'], 0)

    def test_missing_burn_h3_index_defaults_to_zero(self):
        burns = [{'properties': {}}]
        targets = [{'properties': {'h3_index': self.cell_b}}]
        result = _get_cell_path_distances(burns, targets, 'roads_distance')
        self.assertEqual(result[0]['properties']['roads_distance'], 0)

    # --- _cell_yield ---

    def test_cell_yield_all_keys_present(self):
        burn_props = {'fuel_mass': 2.0, 'creeks_distance': 1, 'roads_distance': 3}
        result = _cell_yield(burn_props, 'trailer_kiln', self.sample_treatment, 0.001, 0.01)
        expected_keys = {'budget_cost', 'biomass_extracted', 'biochar_extracted',
                         'air_pollution', 'risk_reduced', 'biomass_sale', 'biochar_sale'}
        self.assertEqual(set(result.keys()), expected_keys)

    def test_cell_yield_budget_cost_positive(self):
        burn_props = {'fuel_mass': 1.0, 'creeks_distance': 0, 'roads_distance': 0}
        result = _cell_yield(burn_props, 'trailer_kiln', self.sample_treatment, 0.001, 0.01)
        self.assertGreater(result['budget_cost'], 0)

    def test_cell_yield_biochar_nonneg(self):
        burn_props = {'fuel_mass': 1.0, 'creeks_distance': 0, 'roads_distance': 0}
        result = _cell_yield(burn_props, 'trailer_kiln', self.sample_treatment, 0.001, 0.01)
        self.assertGreaterEqual(result['biochar_extracted'], 0)

    def test_cell_yield_missing_fuel_mass_defaults_to_one(self):
        burn_props = {'creeks_distance': 0, 'roads_distance': 0}
        result_default = _cell_yield(burn_props, 'trailer_kiln', self.sample_treatment, 0.001, 0.01)
        burn_props_explicit = {'fuel_mass': 1.0, 'creeks_distance': 0, 'roads_distance': 0}
        result_explicit = _cell_yield(burn_props_explicit, 'trailer_kiln', self.sample_treatment, 0.001, 0.01)
        self.assertAlmostEqual(result_default['budget_cost'], result_explicit['budget_cost'])

    def test_cell_yield_zero_biochar_rate(self):
        treatment_no_biochar = dict(self.sample_treatment, biochar_rate=0.0)
        burn_props = {'fuel_mass': 1.0, 'creeks_distance': 0, 'roads_distance': 0}
        result = _cell_yield(burn_props, 'chop_and_leave', treatment_no_biochar, 0.001, 0.01)
        self.assertEqual(result['biochar_extracted'], 0.0)
        self.assertEqual(result['biochar_sale'], 0.0)


if __name__ == '__main__':
    unittest.main()