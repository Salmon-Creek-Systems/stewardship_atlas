import os
import sys
import unittest
from pathlib import Path
import shutil
from unittest.mock import patch, MagicMock, mock_open
import json

# Add the python directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from outlets import (
    webmap_json, generate_map_page, outlet_webmap,
    grass_init, extract_region_layer_ogr_grass,
    outlet_sql_duckdb
)

class TestOutlets(unittest.TestCase):
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
                },
                "layers": [
                    {
                        "name": "test_raster",
                        "geometry_type": "raster",
                        "fill_color": [150, 150, 150],
                        "color": [100, 100, 100]
                    },
                    {
                        "name": "test_polygon",
                        "geometry_type": "polygon",
                        "fill_color": [200, 200, 200],
                        "color": [150, 150, 150]
                    },
                    {
                        "name": "test_line",
                        "geometry_type": "linestring",
                        "color": [100, 100, 100]
                    },
                    {
                        "name": "test_point",
                        "geometry_type": "point",
                        "color": [50, 50, 50],
                        "add_labels": True
                    }
                ]
            },
            "assets": {
                "test_webmap": {
                    "in_layers": ["test_raster", "test_polygon", "test_line", "test_point"]
                },
                "test_region": {
                    "in_layers": ["test_polygon"],
                    "config": {
                        "query": "SELECT * FROM test_table"
                    }
                }
            }
        }
        
        # Create test directory
        os.makedirs(self.test_config["data_root"], exist_ok=True)
        
        # Create test fixture directory if it doesn't exist
        self.fixture_dir = Path(os.path.join(os.path.dirname(__file__), "fixtures"))
        os.makedirs(self.fixture_dir, exist_ok=True)
        
        # Create a test template file
        self.test_template = Path(self.test_config["data_root"]) / "templates" / "map.html"
        os.makedirs(self.test_template.parent, exist_ok=True)
        with open(self.test_template, 'w') as f:
            f.write("""
            <!DOCTYPE html>
            <html>
            <head>
                <title>{title}</title>
            </head>
            <body>
                <div id="map"></div>
                <script>
                    var map_config = {map_config};
                    {dynamic_layers}
                </script>
            </body>
            </html>
            """)

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

    def test_webmap_json(self):
        """Test webmap JSON generation"""
        result = webmap_json(self.test_config, 'test_webmap', None)
        
        # Verify map config structure
        self.assertIn('map_config', result)
        self.assertIn('dynamic_layers', result)
        
        # Verify map style
        map_config = result['map_config']
        self.assertIn('style', map_config)
        self.assertIn('sources', map_config['style'])
        self.assertIn('layers', map_config['style'])
        
        # Verify sources
        sources = map_config['style']['sources']
        self.assertIn('test_raster', sources)
        self.assertIn('test_polygon', sources)
        self.assertIn('test_line', sources)
        self.assertIn('test_point', sources)
        
        # Verify layers
        layers = map_config['style']['layers']
        self.assertEqual(len(layers), 5)  # 4 base layers + 1 label layer
        
        # Verify layer types
        layer_types = {layer['type'] for layer in layers}
        self.assertIn('raster', layer_types)
        self.assertIn('fill', layer_types)
        self.assertIn('line', layer_types)
        self.assertIn('circle', layer_types)
        self.assertIn('symbol', layer_types)

    @patch('builtins.open', new_callable=mock_open, read_data="""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{title}</title>
    </head>
    <body>
        <div id="map"></div>
        <script>
            var map_config = {map_config};
            {dynamic_layers}
        </script>
    </body>
    </html>
    """)
    def test_generate_map_page(self, mock_file):
        """Test map page generation"""
        map_config_data = {
            'map_config': {
                'style': {
                    'sources': {},
                    'layers': []
                }
            },
            'dynamic_layers': [
                {
                    'symbol': 'test.png',
                    'name': 'test_layer',
                    'layout': {
                        'icon-image': 'test_layer'
                    }
                }
            ]
        }
        
        output_path = Path(self.test_config["data_root"]) / "test.html"
        result = generate_map_page("Test Map", map_config_data, output_path, None)
        
        # Verify file was written
        mock_file.assert_called_with(output_path, 'w')
        
        # Verify template was formatted
        mock_file().write.assert_called_once()
        written_content = mock_file().write.call_args[0][0]
        self.assertIn('Test Map', written_content)
        self.assertIn('test_layer', written_content)

    @patch('outlets.webmap_json')
    @patch('outlets.generate_map_page')
    @patch('subprocess.run')
    def test_outlet_webmap(self, mock_run, mock_generate, mock_webmap):
        """Test webmap outlet generation"""
        # Mock webmap_json response
        mock_webmap.return_value = {
            'map_config': {'style': {'sources': {}, 'layers': []}},
            'dynamic_layers': []
        }
        
        # Mock generate_map_page
        mock_generate.return_value = Path(self.test_config["data_root"]) / "outlets" / "test_webmap" / "index.html"
        
        # Call the function
        result = outlet_webmap(self.test_config, 'test_webmap')
        
        # Verify function calls
        mock_webmap.assert_called_once_with(self.test_config, 'test_webmap')
        mock_generate.assert_called_once()
        mock_run.assert_called_once()  # For copying CSS files

    @patch('subprocess.check_output')
    @patch('subprocess.run')
    def test_grass_init(self, mock_run, mock_check_output):
        """Test GRASS initialization"""
        # Mock GRASS path and Python path
        mock_check_output.return_value = "/usr/lib/grass83/python"
        
        # Mock grass.jupyter.init
        mock_grass_jupyter = MagicMock()
        mock_grass_jupyter.init.return_value = "GRASS_SESSION"
        
        with patch.dict('sys.modules', {'grass.jupyter': mock_grass_jupyter}):
            result = grass_init("test_atlas")
            
            # Verify GRASS initialization
            mock_check_output.assert_called_once()
            mock_run.assert_called_once()
            mock_grass_jupyter.init.assert_called_once_with(
                "~/grassdata",
                "test_atlas",
                "PERMANENT"
            )
            self.assertEqual(result, "GRASS_SESSION")

    @patch('outlets.grass_init')
    @patch('grass.script.read_command')
    def test_extract_region_layer_ogr_grass(self, mock_read_command, mock_grass_init):
        """Test region extraction using GRASS"""
        # Mock GRASS session
        mock_grass_init.return_value = "GRASS_SESSION"
        
        # Mock GRASS commands
        mock_read_command.side_effect = [
            "region_set",  # g.region
            "clip_created",  # v.clip
            "export_complete"  # v.out.ogr
        ]
        
        # Test region
        region = {
            "name": "test_region",
            "bbox": {
                "north": 41.5,
                "south": 41.0,
                "east": -73.0,
                "west": -73.5
            }
        }
        
        # Call the function
        result = extract_region_layer_ogr_grass(
            self.test_config,
            "test_region",
            "test_layer",
            region
        )
        
        # Verify GRASS operations
        mock_grass_init.assert_called_once_with(self.test_config["name"])
        self.assertEqual(mock_read_command.call_count, 3)
        
        # Verify region setting
        region_call = mock_read_command.call_args_list[0]
        self.assertEqual(region_call[0][0], 'g.region')
        self.assertEqual(region_call[0][1]['n'], region['bbox']['north'])
        self.assertEqual(region_call[0][1]['s'], region['bbox']['south'])
        self.assertEqual(region_call[0][1]['e'], region['bbox']['east'])
        self.assertEqual(region_call[0][1]['w'], region['bbox']['west'])

    @patch('duckdb.sql')
    def test_outlet_sql_duckdb(self, mock_sql):
        """Test DuckDB SQL outlet"""
        # Mock DuckDB response
        mock_response = MagicMock()
        mock_response.columns = ['id', 'name', 'geom']
        mock_response.fetchall.return_value = [
            (1, 'Test Point', '{"type": "Point", "coordinates": [-73.2, 41.2]}'),
            (2, 'Test Line', '{"type": "LineString", "coordinates": [[-73.3, 41.3], [-73.1, 41.1]]}')
        ]
        mock_sql.return_value = mock_response
        
        # Call the function
        result = outlet_sql_duckdb(self.test_config, 'test_region')
        
        # Verify DuckDB operations
        mock_sql.assert_called_once()
        sql_call = mock_sql.call_args[0][0]
        self.assertIn("SELECT * FROM test_table", sql_call)
        
        # Verify output path
        expected_path = Path(self.test_config["data_root"]) / "outlets" / "test_region" / "test_region.geojson"
        self.assertEqual(result, expected_path)

if __name__ == '__main__':
    unittest.main() 