"""asset_inputs: the staging prefixes a job reads, or None for a full hydrate."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
import asset_inputs as ai  # noqa: E402

CONFIG = {'assets': {
    'webmap': {'type': 'outlet', 'in_layers': ['roads', 'creeks'],
               'config': {'fetch_type': 'webmap'}},
    'console': {'type': 'outlet', 'config_def': 'console',
                'config': {'fetch_type': 'console'}},
    'sqldb': {'type': 'outlet', 'config_def': 'sqldb', 'config': {'fetch_type': 'sqldb'}},
    'contours': {'type': 'eddy', 'config': {'in_layer': 'dem', 'out_layer': 'contours'}},
    'overture_roads': {'type': 'inlet', 'out_layer': 'roads'},
    'game_kit': {'type': 'outlet', 'in_layers': ['lidar_basemap'],
                 'roles': {'roads': ['roads'], 'water': ['watertanks']}},
}}


class TestAssetInputs(unittest.TestCase):
    def test_webmap_reads_its_layers_regions_and_its_own_dir(self):
        self.assertEqual(ai.asset_inputs(CONFIG, 'webmap'), [
            'atlas.geojson', 'atlas_config.json', 'layers/creeks/', 'layers/regions/',
            'layers/roads/', 'outlets/webmap/'])

    def test_console_reads_the_webmap_check_and_the_db(self):
        got = ai.asset_inputs(CONFIG, 'console')
        self.assertIn('outlets/webmap/index.html', got)
        self.assertIn('outlets/sqldb/atlas.db', got)

    def test_eddy_reads_in_and_out_layer_from_its_template_config(self):
        got = ai.asset_inputs(CONFIG, 'contours')
        self.assertIn('layers/dem/', got)
        self.assertIn('layers/contours/', got)

    def test_inlet_reads_its_pending_deltas(self):
        self.assertIn('deltas/roads/', ai.asset_inputs(CONFIG, 'overture_roads'))

    def test_game_kit_roles_are_inputs(self):
        got = ai.asset_inputs(CONFIG, 'game_kit')
        self.assertIn('layers/watertanks/', got)
        self.assertIn('layers/roads/', got)

    def test_full_and_unknown_mean_everything(self):
        self.assertIsNone(ai.asset_inputs(CONFIG, 'sqldb'))
        self.assertIsNone(ai.asset_inputs(CONFIG, 'no_such_asset'))

    def test_layer_refresh(self):
        self.assertEqual(ai.layer_refresh_inputs('hydrants'), [
            'atlas.geojson', 'atlas_config.json', 'deltas/hydrants/', 'layers/hydrants/'])


    def test_layer_change_adds_the_outlets_it_rematerializes(self):
        got = ai.layer_change_inputs(CONFIG, 'hydrants', ['webmap', 'console', 'webedit'])
        self.assertIn('deltas/hydrants/', got)
        self.assertIn('layers/roads/', got)                 # the webmap's
        self.assertIn('outlets/sqldb/atlas.db', got)        # the console's
        # an outlet the atlas does not have (webedit) is skipped

    def test_layer_change_through_a_full_outlet_is_everything(self):
        self.assertIsNone(ai.layer_change_inputs(CONFIG, 'hydrants', ai.ADD_LAYER_OUTLETS))

    def test_layer_rebuild_adds_its_inlets(self):
        self.assertEqual(ai.layer_inlets(CONFIG, 'roads'), ['overture_roads'])
        self.assertEqual(ai.layer_rebuild_inputs(CONFIG, 'roads'),
                         ai.layer_refresh_inputs('roads'))   # the inlet reads nothing more
        self.assertEqual(ai.layer_rebuild_inputs(CONFIG, 'hydrants'),
                         ai.layer_refresh_inputs('hydrants'))


class TestWanted(unittest.TestCase):
    def test_exact_files_and_dir_prefixes(self):
        w = ai.wanted(['atlas_config.json', 'layers/roads/'])
        self.assertTrue(w('atlas_config.json'))
        self.assertTrue(w('layers/roads/roads.geojson'))
        self.assertFalse(w('layers/roads_index/roads_index.geojson'))   # not a prefix match
        self.assertFalse(w('atlas_config.json.bak'))

    def test_none_stays_none(self):
        self.assertIsNone(ai.wanted(None))


class TestRealConfigs(unittest.TestCase):
    """Every allowlisted outlet of every active atlas gets an answer without raising."""

    def test_active_atlases(self):
        from test_json_configs import ACTIVE_ATLASES, CONFIG_DIR
        for atlas in ACTIVE_ATLASES:
            props = json.load(open(CONFIG_DIR / f'{atlas}.geojson'))['features'][0]['properties']
            for name in props['assets']:
                with self.subTest(atlas=atlas, asset=name):
                    got = ai.asset_inputs(props, name)
                    self.assertTrue(got is None or 'atlas_config.json' in got)


if __name__ == '__main__':
    unittest.main()
