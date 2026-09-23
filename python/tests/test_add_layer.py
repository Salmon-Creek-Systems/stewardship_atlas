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

    def test_hex_color_override(self):
        plan = atlas.plan_add_layer(RESOLVED_ASSETS, 'x', color='#FFAA33')
        self.assertEqual(plan['layer_def']['color'], [255, 170, 51])
        self.assertEqual(plan['layer_def']['paint']['circle-color'], '#ffaa33')

    def test_rgb_list_color_still_accepted(self):
        plan = atlas.plan_add_layer(RESOLVED_ASSETS, 'x', color=[10, 20, 30])
        self.assertEqual(plan['layer_def']['color'], [10, 20, 30])

    def test_bad_hex_color_rejected(self):
        with self.assertRaises(ValueError):
            atlas.plan_add_layer(RESOLVED_ASSETS, 'x', color='not-a-color')

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


class TestPlanAddLayerOptions(unittest.TestCase):
    """The console Add Layer form's options: name validation, empty source,
    label property, width, colour map."""

    def test_unsafe_names_rejected(self):
        for bad in ('../etc', 'Trailheads', 'trail heads', '1roads', '', 'a/b', 'x' * 50):
            with self.assertRaises(ValueError, msg=bad):
                atlas.plan_add_layer(RESOLVED_ASSETS, bad)

    def test_good_name_accepted(self):
        atlas.plan_add_layer(RESOLVED_ASSETS, 'road_2026_v2')

    def test_unknown_geometry_and_source_rejected(self):
        with self.assertRaises(ValueError):
            atlas.plan_add_layer(RESOLVED_ASSETS, 'x', geometry_type='raster')
        with self.assertRaises(ValueError):
            atlas.plan_add_layer(RESOLVED_ASSETS, 'x', source='ftp')

    def test_empty_source_has_no_inlet_and_a_name_column(self):
        plan = atlas.plan_add_layer(RESOLVED_ASSETS, 'culverts', source='empty')
        self.assertIsNone(plan['inlet_key'])
        self.assertIsNone(plan['inlet_asset'])
        self.assertEqual(plan['layer_def']['editable_columns'][0]['name'], 'name')
        # Still wired into consumers so it can be drawn in webedit and seen.
        self.assertIn('webedit', [k for k, _ in plan['consumer_edits']])

    def test_label_property_canonicalizes_to_name(self):
        plan = atlas.plan_add_layer(RESOLVED_ASSETS, 'roads2', label_property='street')
        self.assertTrue(plan['layer_def']['add_labels'])
        self.assertEqual(plan['inlet_asset']['alterations'],
                         {'canonicalize': [{'to': 'name', 'from': ['street']}]})

    def test_label_property_name_needs_no_alteration(self):
        plan = atlas.plan_add_layer(RESOLVED_ASSETS, 'roads2', label_property='name')
        self.assertTrue(plan['layer_def']['add_labels'])
        self.assertNotIn('alterations', plan['inlet_asset'])

    def test_no_label_property_turns_labels_off(self):
        plan = atlas.plan_add_layer(RESOLVED_ASSETS, 'roads2')
        self.assertFalse(plan['layer_def']['add_labels'])
        self.assertNotIn('alterations', plan['inlet_asset'])

    def test_width_maps_per_geometry(self):
        line = atlas.plan_add_layer(RESOLVED_ASSETS, 'a', geometry_type='linestring', width=7)
        point = atlas.plan_add_layer(RESOLVED_ASSETS, 'b', geometry_type='point', width=5)
        poly = atlas.plan_add_layer(RESOLVED_ASSETS, 'c', geometry_type='polygon', width=5)
        self.assertEqual(line['layer_def']['paint']['line-width'], 7)
        self.assertEqual(point['layer_def']['paint']['circle-radius'], 5)
        self.assertNotIn('line-width', poly['layer_def']['paint'])
        self.assertNotIn('circle-radius', poly['layer_def']['paint'])

    def test_colormap_builds_ramp_and_pdf_stops(self):
        cm = {'palette': 'YlGn', 'property': 'depth', 'min': 0, 'max': 8}
        plan = atlas.plan_add_layer(RESOLVED_ASSETS, 'wells', geometry_type='point', colormap=cm)
        ld = plan['layer_def']
        expr = ld['paint']['circle-color']
        self.assertEqual(expr[0], 'interpolate')
        self.assertEqual(expr[2], ['to-number', ['get', 'depth'], 0])
        self.assertEqual(len(expr), 3 + 2 * 9)                 # 9 stops, value+colour each
        self.assertEqual(expr[3:5], [0.0, '#ffffe5'])
        self.assertEqual(expr[-2:], [8.0, '#004529'])
        self.assertEqual(len(ld['qgis_color_stops']['stops']), 9)
        # The coloured property is readable in a popup.
        self.assertTrue(ld['show_attributes'])
        self.assertIn({'name': 'depth', 'type': 'number'}, ld['editable_columns'])

    def test_colormap_paint_key_per_geometry(self):
        cm = {'palette': 'Reds', 'property': 'v', 'min': 0, 'max': 1}
        for geom, key in (('linestring', 'line-color'), ('polygon', 'fill-color')):
            plan = atlas.plan_add_layer(RESOLVED_ASSETS, 'x', geometry_type=geom, colormap=cm)
            self.assertEqual(plan['layer_def']['paint'][key][0], 'interpolate')

    def test_colormap_bad_palette_or_range_rejected(self):
        with self.assertRaises(ValueError):
            atlas.plan_add_layer(RESOLVED_ASSETS, 'x', colormap={
                'palette': 'Nope', 'property': 'v', 'min': 0, 'max': 1})
        with self.assertRaises(ValueError):
            atlas.plan_add_layer(RESOLVED_ASSETS, 'x', colormap={
                'palette': 'Reds', 'property': 'v', 'min': 5, 'max': 5})


class TestValidateLayerUpload(unittest.TestCase):

    def _fc(self, *types):
        return {'type': 'FeatureCollection', 'features': [
            {'type': 'Feature', 'properties': {},
             'geometry': None if t is None else {'type': t, 'coordinates': []}}
            for t in types]}

    def test_matching_geometries_accepted(self):
        self.assertEqual(atlas.validate_layer_upload(self._fc('Point', 'MultiPoint', None), 'point'), 3)

    def test_mismatched_geometry_named_in_error(self):
        with self.assertRaisesRegex(ValueError, 'LineString'):
            atlas.validate_layer_upload(self._fc('Point', 'LineString'), 'point')

    def test_not_a_feature_collection_rejected(self):
        for bad in ({'type': 'Feature'}, [], {'type': 'FeatureCollection'}, self._fc()):
            with self.assertRaises(ValueError):
                atlas.validate_layer_upload(bad, 'point')


class TestPlanEditLayer(unittest.TestCase):
    """The console's Edit Layer form: restyle an existing layer in place."""

    POINT = {'name': 'hydrants', 'geometry_type': 'point', 'color': [12, 94, 46],
             'paint': {'circle-radius': 9, 'circle-color': '#0c5e2e',
                       'circle-stroke-width': 1.5, 'circle-stroke-color': '#063c1c'}}
    LINE = {'name': 'trails', 'geometry_type': 'linestring', 'color': [12, 94, 46],
            'paint': {'line-color': '#0c5e2e', 'line-width': 4}}
    POLY = {'name': 'parcels', 'geometry_type': 'polygon', 'color': [12, 94, 46],
            'fill_color': [12, 94, 46], 'paint': {'fill-color': '#0c5e2e', 'fill-opacity': 0.5}}
    ICONS = ('camera', 'culvert', 'flower')

    def test_absent_keys_are_left_alone(self):
        out = atlas.plan_edit_layer(self.POINT, {'width': 12}, icons=self.ICONS)
        self.assertEqual(out['paint']['circle-radius'], 12)
        self.assertEqual(out['paint']['circle-color'], '#0c5e2e')       # untouched
        self.assertEqual(out['paint']['circle-stroke-width'], 1.5)      # untouched
        self.assertEqual(out['color'], [12, 94, 46])

    def test_renaming_is_refused(self):
        with self.assertRaises(ValueError):
            atlas.plan_edit_layer(self.POINT, {'name': 'other'}, icons=self.ICONS)

    def test_width_means_radius_width_and_nothing_for_polygons(self):
        self.assertEqual(atlas.plan_edit_layer(self.POINT, {'width': 5})['paint']['circle-radius'], 5)
        self.assertEqual(atlas.plan_edit_layer(self.LINE, {'width': 5})['paint']['line-width'], 5)
        poly = atlas.plan_edit_layer(self.POLY, {'width': 5})
        self.assertNotIn('line-width', poly['paint'])
        self.assertNotIn('circle-radius', poly['paint'])

    def test_colour_updates_both_color_and_paint(self):
        out = atlas.plan_edit_layer(self.LINE, {'color': '#FFAA33'})
        self.assertEqual(out['color'], [255, 170, 51])
        self.assertEqual(out['paint']['line-color'], '#ffaa33')

    def test_polygon_colour_also_sets_fill_color_and_outline(self):
        out = atlas.plan_edit_layer(self.POLY, {'color': '#FFAA33'})
        self.assertEqual(out['fill_color'], [255, 170, 51])
        self.assertEqual(out['paint']['fill-color'], '#ffaa33')
        self.assertEqual(out['paint']['fill-outline-color'], '#ffaa33')

    def test_opacity_per_geometry(self):
        self.assertEqual(atlas.plan_edit_layer(self.POINT, {'opacity': 0.5})['paint']['circle-opacity'], 0.5)
        self.assertEqual(atlas.plan_edit_layer(self.LINE, {'opacity': 0.5})['paint']['line-opacity'], 0.5)
        poly = atlas.plan_edit_layer(self.POLY, {'opacity': 0.25})
        self.assertEqual(poly['paint']['fill-opacity'], 0.25)
        self.assertEqual(poly['fill_opacity'], 0.25)        # QGIS reads this one

    def test_opacity_out_of_range_rejected(self):
        for bad in (-0.1, 1.5):
            with self.assertRaises(ValueError):
                atlas.plan_edit_layer(self.POINT, {'opacity': bad})

    def test_icon_sets_symbol_and_forces_labels(self):
        out = atlas.plan_edit_layer(self.POINT, {'icon': 'camera'}, icons=self.ICONS)
        self.assertEqual(out['symbol'], {'png': 'camera.png', 'icon': 'camera'})
        # The icon rides on the label layer, so labels have to be on.
        self.assertTrue(out['add_labels'])

    def test_icon_size_follows_width_and_the_dot_goes_away(self):
        out = atlas.plan_edit_layer(self.POINT, {'icon': 'camera', 'width': 18}, icons=self.ICONS)
        self.assertEqual(out['icon-size'], 2.0)             # 9 (the default dot) -> 1.0
        # Otherwise a coloured dot sits behind the icon.
        self.assertEqual(out['paint']['circle-radius'], 0)
        self.assertEqual(atlas.plan_edit_layer(self.POINT, {'icon': 'camera', 'width': 9},
                                               icons=self.ICONS)['icon-size'], 1.0)

    def test_clearing_icon_goes_back_to_a_dot(self):
        withicon = atlas.plan_edit_layer(self.POINT, {'icon': 'camera', 'width': 18}, icons=self.ICONS)
        out = atlas.plan_edit_layer(withicon, {'icon': ''}, icons=self.ICONS)
        self.assertNotIn('symbol', out)
        self.assertNotIn('icon-size', out)

    def test_unknown_icon_and_icon_on_a_line_rejected(self):
        with self.assertRaises(ValueError):
            atlas.plan_edit_layer(self.POINT, {'icon': 'rocket'}, icons=self.ICONS)
        with self.assertRaises(ValueError):
            atlas.plan_edit_layer(self.LINE, {'icon': 'camera'}, icons=self.ICONS)

    def test_label_field_is_made_editable(self):
        out = atlas.plan_edit_layer(self.POINT, {'label_field': 'street'})
        self.assertEqual(out['label_field'], 'street')
        # Otherwise a hand-drawn feature could never be given a label.
        self.assertIn({'name': 'street', 'type': 'string', 'default': ''}, out['editable_columns'])

    def test_label_field_name_is_the_default_and_stored_as_absent(self):
        out = atlas.plan_edit_layer(dict(self.POINT, label_field='street'), {'label_field': 'name'})
        self.assertNotIn('label_field', out)

    def test_colormap_and_back_to_flat(self):
        cm = {'palette': 'Greens', 'property': 'depth', 'min': 0, 'max': 10}
        ramped = atlas.plan_edit_layer(self.POINT, {'colormap': cm})
        self.assertEqual(ramped['paint']['circle-color'][0], 'interpolate')
        self.assertEqual(len(ramped['qgis_color_stops']['stops']), 9)

        flat = atlas.plan_edit_layer(ramped, {'colormap': None, 'color': '#FFAA33'})
        self.assertEqual(flat['paint']['circle-color'], '#ffaa33')
        self.assertNotIn('qgis_color_stops', flat)

    def test_colour_does_not_clobber_an_existing_ramp(self):
        cm = {'palette': 'Greens', 'property': 'depth', 'min': 0, 'max': 10}
        ramped = atlas.plan_edit_layer(self.POINT, {'colormap': cm})
        still = atlas.plan_edit_layer(ramped, {'color': '#FFAA33'})
        self.assertEqual(still['paint']['circle-color'][0], 'interpolate')
        self.assertEqual(still['color'], [255, 170, 51])     # legend/QGIS colour still updates


class TestStylingOutlets(unittest.TestCase):
    """Which outlets a restyle re-materializes: webmaps, not the slow ones."""

    CONFIG = {'assets': {
        'webmap':     {'type': 'outlet', 'config': {'fetch_type': 'webmap', 'in_layers': ['roads', 'hydrants']}},
        'experimental_webmap': {'type': 'outlet', 'config': {'fetch_type': 'webmap', 'in_layers': ['hydrants']}},
        'webedit':    {'type': 'outlet', 'config': {'fetch_type': 'webedit', 'in_layers': ['hydrants']}},
        'runbook':    {'type': 'outlet', 'config': {'fetch_type': 'qgis_atlas', 'in_layers': ['hydrants']}},
        'html':       {'type': 'outlet', 'config': {'fetch_type': 'html'}},
    }}

    def test_every_webmap_showing_the_layer(self):
        self.assertEqual(sorted(atlas.styling_outlets(self.CONFIG, 'hydrants')),
                         ['experimental_webmap', 'webedit', 'webmap'])

    def test_skips_maps_without_the_layer_and_slow_outlets(self):
        self.assertEqual(atlas.styling_outlets(self.CONFIG, 'roads'), ['webmap'])
        self.assertNotIn('runbook', atlas.styling_outlets(self.CONFIG, 'hydrants'))


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


    def test_empty_layer_initialised_not_imported(self):
        import dataswale_geojson
        orig_clear = dataswale_geojson.clear_vector_layer
        dataswale_geojson.clear_vector_layer = MagicMock()
        self.addCleanup(setattr, dataswale_geojson, 'clear_vector_layer', orig_clear)

        atlas.add_layer(self.config, 'culverts', geometry_type='point', source='empty')

        props = json.load(open(self.seed))['features'][0]['properties']
        self.assertIn('culverts', props['layers'])
        self.assertNotIn('culverts', props['assets'])              # no inlet
        self.assertIn('culverts', props['assets']['webmap']['in_layers'])
        dataswale_geojson.clear_vector_layer.assert_called_once()  # file exists, so no 404
        self._refresh_mock.assert_not_called()
        self.assertNotIn('culverts', self.materialized)            # nothing to materialize

    def test_empty_layer_refuses_existing_name(self):
        with self.assertRaises(ValueError):
            atlas.add_layer(self.config, 'roads', source='empty')

if __name__ == '__main__':
    unittest.main()
