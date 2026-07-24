"""
Tests for atlas.plan_add_layer (pure) and atlas.add_layer (executor).

Heavy server-side deps are stubbed so atlas imports cleanly, matching
test_copy_layer.py. The planner tests run fully; the executor test mocks the
build + materialize + refresh calls and runs against a temp single-file
geojson.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Stub only atlas's genuinely-heavy direct imports so `import atlas` succeeds.
# Use the REAL versioning/utils/dataswale_geojson (they import fine locally and
# other suites depend on the real ones). Track and remove our stubs in
# tearDownModule so this file stays hermetic and doesn't leak into later suites.
# deltas_geojson pulls in duckdb (absent locally); real dataswale_geojson
# imports it, so stub it (test_copy_layer stubs it too — no conflict). Keep
# dataswale_geojson itself REAL so test_copy_layer's use of it isn't clobbered.
_STUBBED = []
for _mod in ('outlets', 'outlets_qgis_atlas', 'vector_inlets', 'raster_inlets',
             'eddies', 'deltas_geojson'):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()
        _STUBBED.append(_mod)

# utils needs gspread (absent locally); stub it with a real _detect_indent.
_prior_utils = sys.modules.get('utils')
if _prior_utils is None or isinstance(_prior_utils, MagicMock):
    _utils = MagicMock()
    _utils._detect_indent = lambda p: 2
    sys.modules['utils'] = _utils
    _STUBBED.append('utils')

import atlas


def tearDownModule():
    for _mod in _STUBBED:
        sys.modules.pop(_mod, None)


# Resolved runtime assets (config['assets']) — webmap/webedit carry in_layers,
# sqldb carries layers, an eddy carries neither.
RESOLVED_ASSETS = {
    'webmap':  {'type': 'outlet', 'in_layers': ['roads', 'creeks']},
    'webedit': {'type': 'outlet', 'in_layers': ['roads']},
    'sqldb':   {'type': 'outlet', 'layers': ['roads', 'creeks']},
    'gdal_contours': {'type': 'eddy', 'config': {'in_layer': 'dem', 'out_layer': 'contours'}},
}


class TestPlanAddLayer(unittest.TestCase):

    def test_layer_def_styling_defaults_point(self):
        plan = atlas.plan_add_layer(RESOLVED_ASSETS, 'trailheads')
        ld = plan['layer_def']
        self.assertEqual(ld['name'], 'trailheads')
        self.assertEqual(ld['geometry_type'], 'point')
        self.assertEqual(ld['interaction'], 'interface')     # console visibility
        self.assertEqual(ld['color'], [12, 94, 46])          # dark green
        self.assertEqual(ld['paint']['circle-radius'], 9)    # big dots
        self.assertEqual(ld['paint']['circle-color'], '#0c5e2e')

    def test_layer_def_styling_linestring_is_thick(self):
        plan = atlas.plan_add_layer(RESOLVED_ASSETS, 'fireline', geometry_type='linestring')
        self.assertEqual(plan['layer_def']['paint']['line-width'], 4)   # thick
        self.assertEqual(plan['layer_def']['paint']['line-color'], '#0c5e2e')

    def test_inlet_carries_out_layer(self):
        # delta_path routes deltas to deltas/{out_layer}/, so the inlet must
        # carry out_layer == layer_name (data lands in layers/{out_layer}/).
        plan = atlas.plan_add_layer(RESOLVED_ASSETS, 'trailheads', s3_key='scvfd/imports/th.geojson')
        self.assertEqual(plan['inlet_key'], 'trailheads')
        inlet = plan['inlet_asset']
        self.assertEqual(inlet['name'], 'trailheads')
        self.assertEqual(inlet['out_layer'], 'trailheads')
        self.assertEqual(inlet['config_def'], 's3_geojson_inlet')
        self.assertEqual(inlet['s3_bucket'], 'scs-internal')
        self.assertEqual(inlet['s3_key'], 'scvfd/imports/th.geojson')

    def test_consumer_edits_by_field(self):
        plan = atlas.plan_add_layer(RESOLVED_ASSETS, 'trailheads')
        edits = dict(plan['consumer_edits'])
        self.assertEqual(edits['webmap'], 'in_layers')
        self.assertEqual(edits['webedit'], 'in_layers')
        self.assertEqual(edits['sqldb'], 'layers')

    def test_consumers_overridable(self):
        plan = atlas.plan_add_layer(RESOLVED_ASSETS, 'trailheads', consumers=['webmap'])
        self.assertEqual(plan['consumer_edits'], [('webmap', 'in_layers')])

    def test_missing_consumer_ignored(self):
        plan = atlas.plan_add_layer(RESOLVED_ASSETS, 'trailheads',
                                    consumers=['webmap', 'nonexistent'])
        self.assertEqual([k for k, _ in plan['consumer_edits']], ['webmap'])


class TestAddLayerExecutor(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.name = 'testatlas'
        app_cfg = root / self.name / 'app' / 'configuration'
        app_cfg.mkdir(parents=True)
        (root / self.name / 'staging').mkdir(parents=True)

        # Single-file seed geojson with inline layers/assets.
        self.seed = app_cfg / f'{self.name}.geojson'
        self.seed.write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "geometry": None, "properties": {
                "layers": {"roads": {"name": "roads", "geometry_type": "linestring"}},
                "assets": {
                    "webmap": {"type": "outlet", "config_def": "webmap", "in_layers": ["roads"]},
                    "sqldb":  {"type": "outlet", "config_def": "sqldb", "layers": ["roads"]},
                },
            }}],
        }, indent=2))

        # Resolved config passed to add_layer.
        self.config = {
            'name': self.name,
            'data_root': str(root),
            'dataswale': {'layers': [{'name': 'roads', 'geometry_type': 'linestring'}]},
            'assets': RESOLVED_ASSETS,
        }

        # Patch build + materialize + refresh on the shared modules; restore
        # after each test so we don't pollute other suites.
        import dataswale_geojson
        self.materialized = []
        self._orig_build = atlas.build_atlas_from_geojson
        self._orig_materialize = atlas.materialize
        self._orig_refresh = dataswale_geojson.refresh_vector_layer
        atlas.build_atlas_from_geojson = MagicMock(side_effect=self._fake_build)
        atlas.materialize = MagicMock(side_effect=lambda cfg, a, *x, **k: self.materialized.append(a))
        dataswale_geojson.refresh_vector_layer = MagicMock()
        self._refresh_mock = dataswale_geojson.refresh_vector_layer

        def _restore():
            atlas.build_atlas_from_geojson = self._orig_build
            atlas.materialize = self._orig_materialize
            dataswale_geojson.refresh_vector_layer = self._orig_refresh
        self.addCleanup(_restore)

    def _fake_build(self, geojson_path, config_only=False):
        # Emulate config_only: write a staging atlas_config.json with the new asset.
        gj = json.load(open(geojson_path))
        props = gj['features'][0]['properties']
        out = {'name': self.name, 'data_root': self.config['data_root'],
               'assets': {k: {} for k in props['assets']},
               'dataswale': {'layers': list(props['layers'].values())}}
        (Path(self.config['data_root']) / self.name / 'staging' / 'atlas_config.json').write_text(json.dumps(out))

    def tearDown(self):
        self.tmp.cleanup()

    def test_edits_seed_and_materializes_in_order(self):
        atlas.add_layer(self.config, 'trailheads', geometry_type='point')

        gj = json.load(open(self.seed))
        props = gj['features'][0]['properties']
        # Layer + same-named inlet added.
        self.assertIn('trailheads', props['layers'])
        self.assertEqual(props['assets']['trailheads']['config_def'], 's3_geojson_inlet')
        self.assertEqual(props['assets']['trailheads']['s3_key'], 'testatlas/imports/trailheads.geojson')
        # Consumers wired.
        self.assertIn('trailheads', props['assets']['webmap']['in_layers'])
        self.assertIn('trailheads', props['assets']['sqldb']['layers'])
        # Inlet materialized before consumers; refresh applied.
        self.assertEqual(self.materialized[0], 'trailheads')
        self.assertIn('webmap', self.materialized)
        self._refresh_mock.assert_called_once()

    def test_collision_with_unmanaged_layer_rejected(self):
        # 'roads' exists and is not an add_layer import → refuse to overwrite.
        with self.assertRaises(ValueError):
            atlas.add_layer(self.config, 'roads')

    def test_rerun_repairs_partial_import(self):
        # Simulate a partial add: import registered in the seed but with a
        # broken inlet (wrong bucket, missing out_layer). Re-running upserts.
        gj = json.loads(self.seed.read_text())
        props = gj['features'][0]['properties']
        props['layers']['derelicts'] = {'name': 'derelicts', 'geometry_type': 'point'}
        props['assets']['derelicts'] = {'type': 'inlet', 'config_def': 's3_geojson_inlet',
                                        's3_bucket': 'scs-atlas-data', 's3_key': 'x'}
        self.seed.write_text(json.dumps(gj, indent=2))

        atlas.add_layer(self.config, 'derelicts', geometry_type='point')

        fixed = json.loads(self.seed.read_text())['features'][0]['properties']['assets']['derelicts']
        self.assertEqual(fixed['s3_bucket'], 'scs-internal')      # bucket repaired
        self.assertEqual(fixed['out_layer'], 'derelicts')         # out_layer added
        self.assertEqual(self.materialized[0], 'derelicts')       # (re)materialized


if __name__ == '__main__':
    unittest.main()
