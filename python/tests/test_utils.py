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
    geojson_bbox,
    square_from_bbox,
    squarify_feature,
    bbox_to_corners,
    bbox_to_polygon,
    geojson_to_bbox,
    tiff2jpg,
    canonicalize_raster,
    resample_raster_gdal,
    set_crs_raster,
    alter_geojson,
    alter_features,
    shape_feature,
    page_square_from_bbox,
    POLYGON_SHAPES
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

    def test_alter_features_canonicalize_to_name_in_memory(self):
        """The Add Layer label property: copy a chosen property into `name`,
        which the webmap and PDF labels read. The source property is kept."""
        features = [{'type': 'Feature', 'geometry': None,
                     'properties': {'street': 'Miller Rd', 'name': ''}}]
        kept = alter_features(features, {'canonicalize': [{'to': 'name', 'from': ['street']}]})
        self.assertEqual(kept[0]['properties']['name'], 'Miller Rd')
        self.assertEqual(kept[0]['properties']['street'], 'Miller Rd')

    def test_alter_features_filter_returns_kept_only(self):
        features = [{'type': 'Feature', 'geometry': None, 'properties': {'class': c}}
                    for c in ('primary', 'track')]
        kept = alter_features(features, {'filter': [['require', 'class', ['primary']]]})
        self.assertEqual([f['properties']['class'] for f in kept], ['primary'])

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


class TestCanonicalizeRasterGdalConfig(unittest.TestCase):
    """gdal_config becomes --config pairs, and existing callers are unaffected."""

    BBOX = {'west': -123.7, 'east': -123.4, 'south': 39.5, 'north': 39.8}

    def _warp_args(self, **kwargs):
        with patch('utils.subprocess.check_output') as run:
            canonicalize_raster('in.tif', 'out.tif', 'EPSG:4269', self.BBOX, **kwargs)
        return run.call_args[0][0]

    def test_no_config_is_unchanged(self):
        args = self._warp_args()
        self.assertEqual(args[0], 'gdalwarp')
        self.assertNotIn('--config', args)
        self.assertEqual(args[1:3], ['-t_srs', 'EPSG:4269'])

    def test_config_pairs_precede_other_options(self):
        args = self._warp_args(gdal_config={'GDAL_DISABLE_READDIR_ON_OPEN': 'EMPTY_DIR'})
        self.assertEqual(args[:4],
                         ['gdalwarp', '--config', 'GDAL_DISABLE_READDIR_ON_OPEN', 'EMPTY_DIR'])
        self.assertEqual(args[4], '-t_srs')

    def test_extent_and_paths_still_present(self):
        args = self._warp_args(gdal_config={'A': 'B'})
        self.assertEqual(args[-2:], ['in.tif', 'out.tif'])
        self.assertIn('-te', args)
        te = args.index('-te')
        self.assertEqual(args[te + 1:te + 5], ['-123.7', '39.5', '-123.4', '39.8'])

    def test_resample_width_still_applies(self):
        args = self._warp_args(resample_width=2048, gdal_config={'A': 'B'})
        self.assertIn('-ts', args)
        self.assertEqual(args[args.index('-ts') + 1], '2048')



class TestSquarify(unittest.TestCase):
    """Regions are stored already squared, because each one prints as a page."""

    def _ring(self, feature):
        return feature['geometry']['coordinates'][0]

    def _wh(self, feature):
        r = self._ring(feature)
        xs = [p[0] for p in r]
        ys = [p[1] for p in r]
        return max(xs) - min(xs), max(ys) - min(ys)

    RECT = {'type': 'Feature', 'properties': {'name': 'x'},
            'geometry': {'type': 'Polygon',
                         'coordinates': [[[0, 0], [4, 0], [4, 1], [0, 1], [0, 0]]]}}

    def test_bbox_over_nested_rings(self):
        self.assertEqual(geojson_bbox([[[0, 0], [4, 0], [4, 1], [0, 1], [0, 0]]]),
                         (0, 0, 4, 1))

    def test_bbox_of_nothing_is_none(self):
        self.assertIsNone(geojson_bbox([]))

    def test_square_takes_the_longer_side(self):
        ring = square_from_bbox((0, 0, 4, 1))
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        self.assertEqual(max(xs) - min(xs), 4)
        self.assertEqual(max(ys) - min(ys), 4)

    def test_square_is_concentric_with_the_original(self):
        ring = square_from_bbox((0, 0, 4, 1))
        self.assertEqual((sum(p[0] for p in ring[:4]) / 4,
                          sum(p[1] for p in ring[:4]) / 4), (2.0, 0.5))

    def test_square_ring_is_closed(self):
        ring = square_from_bbox((0, 0, 4, 1))
        self.assertEqual(ring[0], ring[-1])

    def test_feature_geometry_becomes_square(self):
        w, h = self._wh(squarify_feature(self.RECT))
        self.assertEqual(w, h)

    def test_transform_metadata_is_recorded(self):
        props = squarify_feature(self.RECT)['properties']
        self.assertTrue(props['_squarified'])
        self.assertEqual(props['_original_width'], 4)
        self.assertEqual(props['_original_height'], 1)
        self.assertEqual(props['_square_size'], 4)

    def test_input_is_not_mutated(self):
        squarify_feature(self.RECT)
        self.assertEqual(self.RECT['geometry']['coordinates'][0][1], [4, 0])

    def test_non_polygons_pass_through(self):
        pt = {'type': 'Feature', 'properties': {},
              'geometry': {'type': 'Point', 'coordinates': [1, 2]}}
        self.assertEqual(squarify_feature(pt)['geometry'], pt['geometry'])

    def test_null_geometry_passes_through(self):
        f = {'type': 'Feature', 'properties': {}, 'geometry': None}
        self.assertIsNone(squarify_feature(f)['geometry'])

    def test_squaring_a_square_is_a_no_op(self):
        # Re-importing regions must not drift them.
        once = squarify_feature(self.RECT)
        self.assertEqual(self._ring(squarify_feature(once)), self._ring(once))


if __name__ == '__main__':
    unittest.main() 

class TestPolygonShapes(unittest.TestCase):
    """polygon_shape: what a polygon becomes when it lands in a layer.

    'square' has to be square ON THE PAGE — outlets_qgis renders in EPSG:3857,
    where a degree of latitude takes 1/cos(lat) the space of a degree of
    longitude — while 'square_degrees' reproduces the older inlet squarify.
    """

    # A tall, narrow corridor at ~39.65N, like a drawn driving route.
    FEATURE = {'type': 'Feature', 'properties': {},
               'geometry': {'type': 'Polygon', 'coordinates': [[
                   [-123.60, 39.60], [-123.58, 39.60], [-123.58, 39.70],
                   [-123.60, 39.70], [-123.60, 39.60]]]}}

    def _extent(self, feature):
        ring = feature['geometry']['coordinates'][0]
        xs = [c[0] for c in ring]
        ys = [c[1] for c in ring]
        return max(xs) - min(xs), max(ys) - min(ys), (max(ys) + min(ys)) / 2

    def _page_ratio(self, feature):
        """Height/width as the printed page sees it (1.0 = square on paper)."""
        import math
        dlon, dlat, lat = self._extent(feature)
        return (dlat / math.cos(math.radians(lat))) / dlon

    def test_raw_passes_through(self):
        self.assertEqual(shape_feature(self.FEATURE, 'raw'), self.FEATURE)
        self.assertEqual(shape_feature(self.FEATURE, None), self.FEATURE)

    def test_bbox_becomes_a_rectangle_covering_the_original(self):
        out = shape_feature(self.FEATURE, 'bbox')
        dlon, dlat, _ = self._extent(out)
        self.assertAlmostEqual(dlon, 0.02, places=6)
        self.assertAlmostEqual(dlat, 0.10, places=6)
        self.assertEqual(len(out['geometry']['coordinates'][0]), 5)   # closed ring

    def test_square_is_square_on_the_page(self):
        out = shape_feature(self.FEATURE, 'square')
        self.assertAlmostEqual(self._page_ratio(out), 1.0, places=3)

    def test_square_degrees_is_the_legacy_shape(self):
        out = shape_feature(self.FEATURE, 'square_degrees')
        dlon, dlat, _ = self._extent(out)
        self.assertAlmostEqual(dlon, dlat, places=6)          # square in degrees
        # ...which reaches the page ~1.3x taller than wide at this latitude.
        self.assertAlmostEqual(self._page_ratio(out), 1.30, places=2)

    def test_square_covers_the_original_and_stays_centred(self):
        out = shape_feature(self.FEATURE, 'square')
        dlon, dlat, lat = self._extent(out)
        self.assertGreaterEqual(dlon + 1e-9, 0.02)
        self.assertGreaterEqual(dlat + 1e-9, 0.10)
        self.assertAlmostEqual(lat, 39.65, places=6)

    def test_every_shape_is_idempotent(self):
        # refresh_vector_layer reapplies the layer's shape to every feature on
        # every refresh, so shaping a shaped polygon must change nothing.
        for mode in POLYGON_SHAPES:
            once = shape_feature(self.FEATURE, mode)
            twice = shape_feature(once, mode)
            for a, b in zip(once['geometry']['coordinates'][0], twice['geometry']['coordinates'][0]):
                self.assertAlmostEqual(a[0], b[0], places=9, msg=mode)
                self.assertAlmostEqual(a[1], b[1], places=9, msg=mode)

    def test_points_and_lines_pass_through_every_mode(self):
        point = {'type': 'Feature', 'properties': {},
                 'geometry': {'type': 'Point', 'coordinates': [-123.6, 39.6]}}
        for mode in POLYGON_SHAPES:
            self.assertEqual(shape_feature(point, mode)['geometry'], point['geometry'])

    def test_page_square_from_bbox_at_the_equator_is_a_degree_square(self):
        # cos(0) = 1, so the two definitions agree there.
        ring = page_square_from_bbox((0.0, 0.0, 0.2, 0.1))
        xs = [c[0] for c in ring]
        ys = [c[1] for c in ring]
        self.assertAlmostEqual(max(xs) - min(xs), max(ys) - min(ys), places=6)

