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

from vector_inlets import overture_duckdb, local_ogr, gazetteer_grid

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

class TestGazetteerGrid(unittest.TestCase):
    """Tests for gazetteer_grid() — projected-space grid generation."""

    def _make_config(self, bbox, num_cols):
        return {
            "name": "test_atlas",
            "dataswale": {"bbox": bbox},
            "assets": {
                "gazetteer_grid": {
                    "config": {"num_cols": num_cols},
                    "out_layer": "gazetteer_regions",
                }
            }
        }

    def _run(self, bbox, num_cols):
        mock_dq = MagicMock()
        captured = {}
        def capture(config, name, fc, action, layer_name=None):
            captured['fc'] = fc
        mock_dq.add_deltas_from_features.side_effect = capture
        config = self._make_config(bbox, num_cols)
        gazetteer_grid(config=config, name="gazetteer_grid", delta_queue=mock_dq)
        return captured['fc']

    def test_num_columns(self):
        bbox = {"west": -122.5, "east": -122.0, "south": 37.5, "north": 38.0}
        fc = self._run(bbox, num_cols=4)
        cols = {f['properties']['name'].split('_')[0] for f in fc['features']}
        self.assertEqual(cols, {'1', '2', '3', '4'})

    def test_cells_are_square_in_3857(self):
        from pyproj import Transformer
        to_3857 = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
        bbox = {"west": -122.5, "east": -122.0, "south": 37.5, "north": 38.0}
        fc = self._run(bbox, num_cols=3)
        for feat in fc['features']:
            coords = feat['geometry']['coordinates'][0]
            # Project the four corners back to 3857
            pts = [to_3857.transform(lon, lat) for lon, lat in coords[:4]]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            width = max(xs) - min(xs)
            height = max(ys) - min(ys)
            self.assertAlmostEqual(width, height, places=0,
                msg=f"Cell {feat['properties']['name']} not square: {width:.1f} x {height:.1f} m")

    def test_no_gaps_between_adjacent_cells(self):
        """East edge of cell (col, row) must equal west edge of cell (col+1, row)."""
        from pyproj import Transformer
        to_3857 = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
        bbox = {"west": -122.5, "east": -122.0, "south": 37.5, "north": 38.0}
        fc = self._run(bbox, num_cols=3)
        by_name = {f['properties']['name']: f for f in fc['features']}
        for feat in fc['features']:
            east_nbr = feat['properties']['east_neighbor']
            if east_nbr is None:
                continue
            coords_a = feat['geometry']['coordinates'][0]
            coords_b = by_name[east_nbr]['geometry']['coordinates'][0]
            # East edge of A: the two corners with max longitude in 3857
            pts_a = [to_3857.transform(lon, lat) for lon, lat in coords_a[:4]]
            pts_b = [to_3857.transform(lon, lat) for lon, lat in coords_b[:4]]
            east_a = max(p[0] for p in pts_a)
            west_b = min(p[0] for p in pts_b)
            self.assertAlmostEqual(east_a, west_b, places=0,
                msg=f"Gap between {feat['properties']['name']} and {east_nbr}: {abs(east_a - west_b):.2f} m")

    def test_covers_full_bbox(self):
        """Grid must cover the entire atlas bbox (may extend slightly beyond south)."""
        from pyproj import Transformer
        to_3857 = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
        bbox = {"west": -122.5, "east": -122.0, "south": 37.5, "north": 38.0}
        fc = self._run(bbox, num_cols=3)
        west_m, south_m = to_3857.transform(bbox['west'], bbox['south'])
        east_m, north_m = to_3857.transform(bbox['east'], bbox['north'])
        all_pts = [
            to_3857.transform(lon, lat)
            for f in fc['features']
            for lon, lat in f['geometry']['coordinates'][0]
        ]
        grid_west = min(p[0] for p in all_pts)
        grid_east = max(p[0] for p in all_pts)
        grid_north = max(p[1] for p in all_pts)
        grid_south = min(p[1] for p in all_pts)
        self.assertLessEqual(grid_west, west_m + 1)
        self.assertGreaterEqual(grid_east, east_m - 1)
        self.assertGreaterEqual(grid_north, north_m - 1)
        self.assertLessEqual(grid_south, south_m + 1)

    def test_neighbor_properties(self):
        bbox = {"west": -122.5, "east": -122.0, "south": 37.5, "north": 38.0}
        fc = self._run(bbox, num_cols=3)
        by_name = {f['properties']['name']: f for f in fc['features']}
        # 1_A: NW corner — no north or west neighbor
        nw = by_name['1_A']['properties']
        self.assertIsNone(nw['north_neighbor'])
        self.assertIsNone(nw['west_neighbor'])
        self.assertEqual(nw['east_neighbor'], '2_A')
        self.assertEqual(nw['south_neighbor'], '1_B')
        # 2_A: top row middle — no north neighbor
        mid = by_name['2_A']['properties']
        self.assertIsNone(mid['north_neighbor'])
        self.assertEqual(mid['west_neighbor'], '1_A')
        self.assertEqual(mid['east_neighbor'], '3_A')

    def test_nw_convention(self):
        """1_A must be the northernmost, westernmost cell."""
        from pyproj import Transformer
        to_3857 = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
        bbox = {"west": -122.5, "east": -122.0, "south": 37.5, "north": 38.0}
        fc = self._run(bbox, num_cols=3)
        by_name = {f['properties']['name']: f for f in fc['features']}
        nw_pts = [to_3857.transform(lon, lat)
                  for lon, lat in by_name['1_A']['geometry']['coordinates'][0]]
        grid_west = min(
            to_3857.transform(lon, lat)[0]
            for f in fc['features']
            for lon, lat in f['geometry']['coordinates'][0]
        )
        grid_north = max(
            to_3857.transform(lon, lat)[1]
            for f in fc['features']
            for lon, lat in f['geometry']['coordinates'][0]
        )
        self.assertAlmostEqual(min(p[0] for p in nw_pts), grid_west, places=0)
        self.assertAlmostEqual(max(p[1] for p in nw_pts), grid_north, places=0)


if __name__ == '__main__':
    unittest.main()