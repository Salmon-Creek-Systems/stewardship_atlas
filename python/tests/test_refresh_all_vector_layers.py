"""
Tests for dataswale_geojson.refresh_all_vector_layers.

Covers the two gaps that left a newly-created atlas with layers that were
defined but empty: inlet-written deltas never applied to the layer, and
inlet-less layers never given a file at all.

Heavy deps are stubbed the same way as test_add_layer.py, but the delta
application itself is exercised for real via a fake delta_queue_builder that
reads the delta files off disk.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# This suite needs the REAL versioning/geojson/dataswale_geojson. Other suites
# (test_copy_layer, test_color_expression) stub those into sys.modules and never
# remove them, so depending on collection order we can inherit their MagicMocks —
# a mocked versioning.atlas_path() silently makes every layer look like it
# already exists, and the tests then pass vacuously. Purge and re-import the real
# ones, then hand the mocks back in tearDownModule so those suites still work if
# they run after us.
_REPLACED = {}
for _mod in ('versioning', 'geojson'):
    if isinstance(sys.modules.get(_mod), MagicMock):
        _REPLACED[_mod] = sys.modules.pop(_mod)
if _REPLACED and 'dataswale_geojson' in sys.modules:
    # Already imported against the mocks — its module-level `import versioning`
    # is bound to the mock, so it has to be re-imported too, mock or not.
    _REPLACED['dataswale_geojson'] = sys.modules.pop('dataswale_geojson')

# eddies pulls in matplotlib, deltas_geojson pulls in duckdb, and add_webmap_urls
# lazily imports shapely — none are installed locally, none are under test.
_STUBBED = []
for _mod in ('eddies', 'deltas_geojson', 'shapely', 'shapely.geometry'):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()
        _STUBBED.append(_mod)

# utils needs gspread (absent locally); json_serial is all the refresh path uses.
_prior_utils = sys.modules.get('utils')
if _prior_utils is None or isinstance(_prior_utils, MagicMock):
    _utils = MagicMock()
    _utils.json_serial = str
    sys.modules['utils'] = _utils
    _STUBBED.append('utils')

import dataswale_geojson

# Guard the guard: if we still ended up with mocks, every assertion below would
# pass vacuously, so fail loudly instead.
assert not isinstance(dataswale_geojson, MagicMock), "dataswale_geojson is mocked"
assert not isinstance(dataswale_geojson.versioning, MagicMock), "versioning is mocked"


def tearDownModule():
    for _mod in _STUBBED:
        sys.modules.pop(_mod, None)
    sys.modules.pop('dataswale_geojson', None)
    sys.modules.update(_REPLACED)


def point(lon, lat):
    return {'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
            'properties': {}}


def collect_deltas(config, layer_name):
    """Stand-in for deltas.apply_deltas: concatenate the layer's delta files."""
    deltas_dir = Path(config['data_root']) / config['name'] / 'staging' / 'deltas' / layer_name
    features = []
    for path in sorted(deltas_dir.glob('*.geojson')):
        features.extend(json.load(open(path))['features'])
    return {'type': 'FeatureCollection', 'features': features}


class TestRefreshAllVectorLayers(unittest.TestCase):

    # roads/creeks are inlet-backed (deltas on disk), photos has no inlet,
    # terrain_dem is raster and notes is a document layer — neither is ours.
    LAYERS = [
        {'name': 'roads', 'geometry_type': 'linestring'},
        {'name': 'creeks', 'geometry_type': 'linestring'},
        {'name': 'photos', 'geometry_type': 'point'},
        {'name': 'regions', 'geometry_type': 'polygon'},
        {'name': 'terrain_dem', 'geometry_type': 'raster'},
        {'name': 'notes', 'geometry_type': 'document'},
    ]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = {
            'name': 'testatlas',
            'data_root': self.tmp.name,
            'dataswale': {'layers': self.LAYERS},
        }
        self.staging = Path(self.tmp.name) / 'testatlas' / 'staging'

        for layer in self.LAYERS:
            (self.staging / 'layers' / layer['name']).mkdir(parents=True)

        # Two inlets ran and left deltas behind.
        self.write_delta('roads', 'public_roads__20260826_155155__create.geojson',
                         [point(-119.85, 37.88)])
        self.write_delta('creeks', 'public_creeks__20260826_155155__create.geojson',
                         [point(-119.86, 37.89), point(-119.87, 37.90)])
        # A raster inlet's delta dir exists too, and must be left alone.
        (self.staging / 'deltas' / 'terrain_dem').mkdir(parents=True)

        # regions is written directly by atlas.create, so it already has a file.
        self.write_layer('regions', [point(-119.85, 37.88)])

    def write_delta(self, layer, filename, features):
        d = self.staging / 'deltas' / layer
        d.mkdir(parents=True, exist_ok=True)
        json.dump({'type': 'FeatureCollection', 'features': features},
                  open(d / filename, 'w'))

    def write_layer(self, layer, features):
        d = self.staging / 'layers' / layer
        d.mkdir(parents=True, exist_ok=True)
        json.dump({'type': 'FeatureCollection', 'features': features},
                  open(d / f'{layer}.geojson', 'w'))

    def layer_features(self, layer):
        path = self.staging / 'layers' / layer / f'{layer}.geojson'
        return json.load(open(path))['features']

    def run_refresh(self):
        return dict(dataswale_geojson.refresh_all_vector_layers(
            self.config, delta_queue_builder=collect_deltas))

    def test_inlet_deltas_are_applied_to_the_layer(self):
        """The reported bug: deltas on disk, layer file never written."""
        results = self.run_refresh()
        self.assertEqual(results['roads'], 'refreshed')
        self.assertEqual(results['creeks'], 'refreshed')
        self.assertEqual(len(self.layer_features('roads')), 1)
        self.assertEqual(len(self.layer_features('creeks')), 2)

    def test_inlet_less_layer_gets_empty_feature_collection(self):
        """photos has no inlet, so it must still end up with a valid file."""
        results = self.run_refresh()
        self.assertEqual(results['photos'], 'initialized')
        self.assertEqual(self.layer_features('photos'), [])

    def test_non_vector_layers_are_untouched(self):
        results = self.run_refresh()
        self.assertNotIn('terrain_dem', results)
        self.assertNotIn('notes', results)
        for layer in ('terrain_dem', 'notes'):
            self.assertFalse((self.staging / 'layers' / layer / f'{layer}.geojson').exists())

    def test_existing_layer_file_is_not_clobbered(self):
        """regions already has content and no deltas — leave it alone."""
        results = self.run_refresh()
        self.assertNotIn('regions', results)
        self.assertEqual(len(self.layer_features('regions')), 1)

    def test_one_failing_layer_does_not_stop_the_others(self):
        def explode(config, layer_name):
            if layer_name == 'roads':
                raise RuntimeError('boom')
            return collect_deltas(config, layer_name)

        results = dict(dataswale_geojson.refresh_all_vector_layers(
            self.config, delta_queue_builder=explode))
        self.assertTrue(results['roads'].startswith('failed: '))
        self.assertEqual(results['creeks'], 'refreshed')
        self.assertEqual(results['photos'], 'initialized')
        # A failed refresh must not then be blanked to an empty file — that
        # would report success over a layer whose deltas are still pending.
        self.assertFalse((self.staging / 'layers' / 'roads' / 'roads.geojson').exists())

    def test_atlas_with_no_deltas_dir_still_initializes_layers(self):
        """A starter whose inlets all failed: no deltas tree at all."""
        import shutil
        shutil.rmtree(self.staging / 'deltas')
        results = self.run_refresh()
        self.assertEqual(results['roads'], 'initialized')
        self.assertEqual(results['photos'], 'initialized')
        self.assertEqual(self.layer_features('roads'), [])


if __name__ == '__main__':
    unittest.main()
