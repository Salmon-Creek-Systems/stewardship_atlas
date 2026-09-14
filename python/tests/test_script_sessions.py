"""Entry points outside the webapp, moved onto sessions (#159 task 7).

Scripts used to read `{SWALES_ROOT}/{atlas}/staging/atlas_config.json` and edit
that tree in place. Under the session model the atlas lives in S3 and is
hydrated into a workspace, so what changes is where each entry point *looks*:
for the config, for an atlas's source GeoJSON, for the shared templates, and
for the answer to "does this atlas exist".

    cd python && python -m pytest tests/test_script_sessions.py -v
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..',
                                                'scripts')))

from fake_s3 import FakeS3, FakeClientError
import atlas_store
import atlas_workspace

# Stub only atlas's genuinely-heavy direct imports, and remove them again so
# this file cannot make a later suite pass vacuously (#153).
_STUBBED = []
for _mod in ('outlets', 'outlets_qgis_atlas', 'vector_inlets', 'raster_inlets',
             'eddies', 'deltas_geojson'):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()
        _STUBBED.append(_mod)

# utils needs gspread (absent locally) — same treatment as test_add_layer.
_prior_utils = sys.modules.get('utils')
if _prior_utils is None or isinstance(_prior_utils, MagicMock):
    _utils = MagicMock()
    _utils._detect_indent = lambda p: 2
    sys.modules['utils'] = _utils
    _STUBBED.append('utils')

import atlas
import build_atlas as build_atlas_script


def tearDownModule():
    for _mod in _STUBBED:
        sys.modules.pop(_mod, None)


PRIV = 'PRIV'


class TestIsSeeded(unittest.TestCase):
    """`/create_atlas` asks S3 whether a name is taken, not the local disk.

    A leftover workspace directory would have refused a name that is genuinely
    free, and an atlas created from another machine would not have been seen.
    """

    def setUp(self):
        self.client = FakeS3()

    def test_an_atlas_with_a_staging_config_is_seeded(self):
        self.client.put_object(Bucket=PRIV, Key='scvfd/staging/atlas_config.json',
                               Body=b'{}')
        self.assertTrue(atlas_workspace.is_seeded(self.client, PRIV, 'scvfd'))

    def test_an_unknown_atlas_is_not_seeded(self):
        self.assertFalse(atlas_workspace.is_seeded(self.client, PRIV, 'nobody'))

    def test_other_objects_under_the_prefix_do_not_count(self):
        """A half-created atlas that never got a config is not an atlas."""
        self.client.put_object(Bucket=PRIV, Key='scvfd/staging/layers/roads/x.geojson',
                               Body=b'{}')
        self.assertFalse(atlas_workspace.is_seeded(self.client, PRIV, 'scvfd'))

    def test_a_real_error_is_not_swallowed(self):
        """Access denied must not read as 'free to create'."""
        class Denied:
            def head_object(self, **_):
                raise FakeClientError('AccessDenied', 403, 'HeadObject')

        with self.assertRaises(Exception):
            atlas_workspace.is_seeded(Denied(), PRIV, 'scvfd')


class TestSourceGeojsonResolution(unittest.TestCase):
    """Where an atlas's single-file source lives.

    Staging first: that copy travels with the atlas, so a session hydrates it
    and an edit to it is written back. A file in the checkout can do neither,
    and editing one from the server was always the awkward half of `add_layer`.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.config = {'name': 'scvfd', 'data_root': str(self.root)}

    def _write_staging_source(self, text='{}'):
        path = self.root / 'scvfd' / 'staging' / 'atlas.geojson'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_staging_source_is_preferred(self):
        expected = self._write_staging_source()
        self.assertEqual(atlas.source_geojson_path(self.config), expected)

    def test_falls_back_to_the_checkout(self):
        """kennedy's source really is in configuration/ — until the seed moves
        it — so the fallback has to work against the live repo."""
        found = atlas.source_geojson_path({'name': 'kennedy',
                                           'data_root': str(self.root)})
        self.assertIsNotNone(found)
        self.assertEqual(found.name, 'kennedy.geojson')
        self.assertEqual(found.parent, atlas.shared_config_directory())

    def test_an_atlas_with_no_source_anywhere_is_none(self):
        self.assertIsNone(atlas.source_geojson_path(
            {'name': 'no_such_atlas_anywhere', 'data_root': str(self.root)}))

    def test_shared_templates_come_from_the_running_code(self):
        """Not from a per-atlas `app/` symlink: a workspace has none, and a
        stale per-atlas checkout used to be able to supply a different template
        from the one the process was running."""
        shared = atlas.shared_config_directory()
        self.assertTrue((shared / 'shared_layers_config.json').is_file())
        self.assertTrue((shared / 'shared_outlets_config.json').is_file())
        self.assertEqual(shared, Path(atlas.__file__).parent.parent / 'configuration')


class TestBuildAtlasTargetResolution(unittest.TestCase):
    """`build_atlas.py` takes an atlas name or a path to a source GeoJSON.

    The path form is how it has always been called — and it is the only form
    available for an atlas that does not exist yet.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _source(self, name='scvfd'):
        path = self.root / f'{name}.geojson'
        path.write_text(json.dumps({
            'type': 'FeatureCollection',
            'features': [{'type': 'Feature', 'geometry': None,
                          'properties': {'name': name}}]}))
        return path

    def test_a_path_yields_the_atlas_name_from_the_file(self):
        """The name comes out of the file rather than off the filename: a
        session on the wrong atlas would lock and write the wrong tree."""
        path = self._source()
        self.assertEqual(build_atlas_script.resolve_target(str(path)),
                         ('scvfd', path))

    def test_a_bare_name_defers_to_the_session(self):
        self.assertEqual(build_atlas_script.resolve_target('scvfd'),
                         ('scvfd', None))

    def test_a_missing_path_is_an_error_not_an_atlas_name(self):
        with self.assertRaises(FileNotFoundError):
            build_atlas_script.resolve_target(str(self.root / 'nope.geojson'))
        with self.assertRaises(FileNotFoundError):
            build_atlas_script.resolve_target('configuration/nope.geojson')

    def test_a_source_without_a_name_is_refused(self):
        path = self.root / 'broken.geojson'
        path.write_text(json.dumps({'type': 'FeatureCollection', 'features': []}))
        with self.assertRaises(ValueError):
            build_atlas_script.resolve_target(str(path))


if __name__ == '__main__':
    unittest.main()
