import os
import sys
import unittest
from pathlib import Path
import shutil
from unittest.mock import patch, MagicMock
import json
import geojson

# Add the python directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from vector_inlets import overture_duckdb, local_ogr

class TestVectorInlets(unittest.TestCase):
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
                "test_asset": {
                    "config": {
                        "inpath_template": "SELECT * FROM test_table WHERE ST_Within(geom, ST_GeomFromText('POLYGON(({west} {south}, {east} {south}, {east} {north}, {west} {north}, {west} {south}))'))",
                        "layer": "test_layer",
                        "data_type": "geojson"
                    },
                    "out_layer": "test_layer"
                }
            }
        }
        
        # Create test directory
        os.makedirs(self.test_config["data_root"], exist_ok=True)
        
        # Create a mock delta queue
        self.mock_delta_queue = MagicMock()
        self.mock_delta_queue.delta_path.return_value = Path(self.test_config["data_root"]) / "test_output.geojson"
        self.mock_delta_queue.add_deltas_from_features.return_value = [str(self.mock_delta_queue.delta_path.return_value)]

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

    @patch('duckdb.sql')
    def test_overture_duckdb(self, mock_sql):
        # Mock the duckdb response
        mock_response = MagicMock()
        mock_response.columns = ['geom', 'id', 'name']
        mock_response.fetchall.return_value = [
            ('{"type": "Point", "coordinates": [-73.2, 41.2]}', 1, 'Test Point'),
            ('{"type": "LineString", "coordinates": [[-73.3, 41.3], [-73.1, 41.1]]}', 2, 'Test Line')
        ]
        mock_sql.return_value = mock_response

        # Call the function
        result = overture_duckdb(self.test_config, 'test_asset', self.mock_delta_queue, quick=False)

        # Verify results
        self.assertEqual(result, 2)  # Two features were processed
        self.mock_delta_queue.add_deltas_from_features.assert_called_once()
        
        # Verify the feature collection passed to add_deltas_from_features
        call_args = self.mock_delta_queue.add_deltas_from_features.call_args[0]
        self.assertEqual(call_args[0], self.test_config)
        self.assertEqual(call_args[1], 'test_asset')
        self.assertEqual(call_args[3], 'create')
        
        # Verify the feature collection structure
        feature_collection = call_args[2]
        self.assertEqual(len(feature_collection['features']), 2)
        self.assertEqual(feature_collection['features'][0]['properties']['id'], 1)
        self.assertEqual(feature_collection['features'][1]['properties']['id'], 2)

    @patch('duckdb.sql')
    def test_overture_duckdb_quick(self, mock_sql):
        # Mock the duckdb response
        mock_response = MagicMock()
        mock_response.columns = ['geom', 'id', 'name']
        mock_response.fetchall.return_value = [
            ('{"type": "Point", "coordinates": [-73.2, 41.2]}', 1, 'Test Point')
        ]
        mock_sql.return_value = mock_response

        # Call the function with quick=True
        result = overture_duckdb(self.test_config, 'test_asset', self.mock_delta_queue, quick=True)

        # Verify results
        self.assertEqual(result, 1)  # One feature was processed
        self.mock_delta_queue.add_deltas_from_features.assert_called_once()

    def test_local_ogr(self):
        # Create a test GeoJSON file
        test_geojson = {
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
                        "name": "Test Point"
                    }
                }
            ]
        }
        
        # Write test file
        test_input = Path(self.test_config["data_root"]) / "test_input.geojson"
        with open(test_input, 'w') as f:
            json.dump(test_geojson, f)

        # Update config to point to test file
        self.test_config['assets']['test_asset']['config']['inpath_template'] = "test_input.geojson"

        # Call the function
        with patch('subprocess.check_output') as mock_check_output, \
             patch('versioning.atlas_path', return_value=Path(self.test_config["data_root"])):
            mock_check_output.return_value = b''  # Empty output is fine
            result = local_ogr(self.test_config, 'test_asset', self.mock_delta_queue)

        # Verify results
        self.assertEqual(result, self.mock_delta_queue.delta_path.return_value)
        mock_check_output.assert_called_once()
        
        # Verify ogr2ogr command arguments
        args = mock_check_output.call_args[0][0]
        self.assertEqual(args[0], 'ogr2ogr')
        self.assertEqual(args[1], '-f')
        self.assertEqual(args[2], 'GeoJSON')
        self.assertEqual(args[3], '-t_srs')
        self.assertEqual(args[4], self.test_config['dataswale']['crs'])
        self.assertEqual(str(args[-3]), str(self.mock_delta_queue.delta_path.return_value))  # Output path
        self.assertEqual(str(args[-2]), str(test_input))  # Input path
        self.assertEqual(args[-1], "test_layer")  # Layer name

    def test_local_ogr_with_geometry(self):
        # Add geometry filter to config
        self.test_config['assets']['test_asset']['config']['geometry'] = True
        
        # Create a test GeoJSON file
        test_geojson = {
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
                        "name": "Test Point"
                    }
                }
            ]
        }
        
        # Write test file
        test_input = Path(self.test_config["data_root"]) / "test_input.geojson"
        with open(test_input, 'w') as f:
            json.dump(test_geojson, f)

        # Update config to point to test file
        self.test_config['assets']['test_asset']['config']['inpath_template'] = "test_input.geojson"

        # Call the function
        with patch('subprocess.check_output') as mock_check_output, \
             patch('versioning.atlas_path', return_value=Path(self.test_config["data_root"])):
            mock_check_output.return_value = b''  # Empty output is fine
            result = local_ogr(self.test_config, 'test_asset', self.mock_delta_queue)

        # Verify results
        self.assertEqual(result, self.mock_delta_queue.delta_path.return_value)
        mock_check_output.assert_called_once()
        
        # Verify ogr2ogr command arguments include spatial filter
        args = mock_check_output.call_args[0][0]
        self.assertIn('-spat', args)
        self.assertIn('-spat_srs', args)
        self.assertEqual(args[args.index('-spat_srs') + 1], self.test_config['dataswale']['crs'])

if __name__ == '__main__':
    unittest.main() 