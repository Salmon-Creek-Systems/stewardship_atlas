"""Console layer actions write the source config through write_source_geojson,
which refuses a change that introduces a config_checks problem."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

REPO_KENNEDY = Path(__file__).parent.parent.parent / 'configuration' / 'kennedy.geojson'


class TestWriteSourceGeojson(unittest.TestCase):
    def setUp(self):
        try:
            import atlas
        except Exception as e:  # pragma: no cover - needs the box's dependencies
            self.skipTest(f"atlas not importable here: {e}")
        self.atlas = atlas
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'atlas.geojson'
        self.text = REPO_KENNEDY.read_text()
        self.path.write_text(self.text)

    def tearDown(self):
        self.tmp.cleanup()

    def doc(self):
        return json.loads(self.text)

    def test_a_valid_change_is_written(self):
        gj = self.doc()
        gj['features'][0]['properties']['description'] = 'edited'
        self.atlas.write_source_geojson(self.path, gj)
        self.assertEqual(json.loads(self.path.read_text())['features'][0]['properties']['description'],
                         'edited')

    def test_undeclared_layer_in_public_webmap_is_refused(self):
        gj = self.doc()
        props = gj['features'][0]['properties']
        props['layers']['newthing'] = {'geometry_type': 'point'}
        props['assets']['webmap'].setdefault('in_layers', []).append('newthing')
        with self.assertRaisesRegex(ValueError, 'webmap:newthing=None'):
            self.atlas.write_source_geojson(self.path, gj)
        self.assertEqual(self.path.read_text(), self.text)   # nothing written

    def test_a_preexisting_problem_does_not_block_other_edits(self):
        gj = self.doc()
        gj['features'][0]['properties']['versioned_outlets'] = ['webmap']
        self.path.write_text(json.dumps(gj, indent=4))      # already broken on disk
        gj['features'][0]['properties']['description'] = 'still editable'
        self.atlas.write_source_geojson(self.path, gj)

    def test_add_layer_plan_declares_public_by_default(self):
        plan = self.atlas.plan_add_layer({'webmap': {'in_layers': []}}, 'culverts2')
        self.assertEqual(plan['layer_def']['access'], ['public'])


if __name__ == '__main__':
    unittest.main()
