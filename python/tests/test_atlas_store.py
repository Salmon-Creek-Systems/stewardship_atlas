"""Tests for the pure half of atlas_store — no boto3, no network.

The module splits deliberately: access-tier logic, key construction, content
types and upload planning have no AWS dependency, so they are testable in the
bare local env. Everything below runs without credentials.
"""

import logging
import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import atlas_store


class TestContentTypes(unittest.TestCase):
    def test_geo_formats_are_explicit(self):
        # mimetypes does not know these, and getting them wrong is a
        # browser-visible bug (pmtiles in particular needs range requests).
        self.assertEqual(atlas_store.content_type_for('roads.geojson'),
                         'application/geo+json')
        self.assertEqual(atlas_store.content_type_for('terrain.pmtiles'),
                         'application/octet-stream')
        self.assertEqual(atlas_store.content_type_for('hillshade.tif'), 'image/tiff')

    def test_web_formats(self):
        self.assertEqual(atlas_store.content_type_for('index.html'), 'text/html')
        self.assertEqual(atlas_store.content_type_for('webmap.js'),
                         'application/javascript')
        self.assertEqual(atlas_store.content_type_for('style.css'), 'text/css')

    def test_case_insensitive_and_pathlike(self):
        self.assertEqual(atlas_store.content_type_for(Path('a/b/PAGE.HTML')), 'text/html')

    def test_unknown_falls_back(self):
        self.assertEqual(atlas_store.content_type_for('mystery.zzz'),
                         atlas_store.DEFAULT_CONTENT_TYPE)


class TestAccessTiers(unittest.TestCase):
    def test_missing_access_is_public(self):
        # Matches the existing default in atlas.py: .get('access', ['public'])
        self.assertEqual(atlas_store.normalize_access(None), ['public'])
        self.assertTrue(atlas_store.is_public(None))

    def test_bare_string_access(self):
        # shared_outlets_config.json -> sqlquery has "access": "internal"
        self.assertEqual(atlas_store.normalize_access('internal'), ['internal'])
        self.assertFalse(atlas_store.is_public('internal'))

    def test_list_access(self):
        self.assertTrue(atlas_store.is_public(['public']))
        self.assertFalse(atlas_store.is_public(['admin']))
        self.assertFalse(atlas_store.is_public(['internal', 'technical']))

    def test_mixed_tier_counts_as_public(self):
        self.assertTrue(atlas_store.is_public(['public', 'admin']))


class TestCloudSettings(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.pop(k, None)
                       for k in ('ATLAS_OUTLETS_BUCKET', 'ATLAS_DISTRIBUTION_ID')}

    def tearDown(self):
        for key, value in self._saved.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value

    def test_disabled_by_default(self):
        # Un-migrated atlases must be untouched during the strangler window.
        self.assertFalse(atlas_store.cloud_settings({'name': 'kennedy'})['enabled'])

    def test_enabled_needs_a_bucket(self):
        settings = atlas_store.cloud_settings({'name': 'k', 'cloud': {'enabled': True}})
        self.assertFalse(settings['enabled'])

    def test_enabled_with_bucket(self):
        settings = atlas_store.cloud_settings({
            'name': 'k',
            'cloud': {'enabled': True, 'outlets_bucket': 'scs-atlas-outlets-prod'},
        })
        self.assertTrue(settings['enabled'])
        self.assertEqual(settings['bucket'], 'scs-atlas-outlets-prod')

    def test_env_supplies_bucket(self):
        os.environ['ATLAS_OUTLETS_BUCKET'] = 'from-env'
        settings = atlas_store.cloud_settings({'name': 'k', 'cloud': {'enabled': True}})
        self.assertTrue(settings['enabled'])
        self.assertEqual(settings['bucket'], 'from-env')

    def test_flag_still_gates_env_bucket(self):
        os.environ['ATLAS_OUTLETS_BUCKET'] = 'from-env'
        self.assertFalse(atlas_store.cloud_settings({'name': 'k'})['enabled'])


def _config(assets, versioned=None, cloud=None):
    dataswale = {'layers': []}
    if versioned is not None:
        dataswale['versioned_outlets'] = versioned
    config = {'name': 'testatlas', 'assets': assets, 'dataswale': dataswale}
    if cloud is not None:
        config['cloud'] = cloud
    return config


class TestPublishableOutlets(unittest.TestCase):
    def test_only_outlets(self):
        config = _config({
            'webmap':  {'type': 'outlet'},
            'roads':   {'type': 'inlet'},
            'h3':      {'type': 'eddy'},
        })
        self.assertEqual(atlas_store.publishable_outlets(config), ['webmap'])

    def test_protected_outlets_excluded(self):
        config = _config({
            'webmap':      {'type': 'outlet'},
            'webedit':     {'type': 'outlet', 'access': ['admin']},
            'config_edit': {'type': 'outlet', 'access': ['technical']},
            'sqlquery':    {'type': 'outlet', 'access': 'internal'},
        })
        self.assertEqual(atlas_store.publishable_outlets(config), ['webmap'])

    def test_versioned_outlets_filter(self):
        # versioned_outlets is already the publish snapshot filter (#131 C9);
        # an outlet outside it has no directory in the version to upload.
        config = _config({
            'webmap':  {'type': 'outlet'},
            'runbook': {'type': 'outlet'},
        }, versioned=['webmap'])
        self.assertEqual(atlas_store.publishable_outlets(config), ['webmap'])

    def test_empty_versioned_outlets_means_all(self):
        config = _config({
            'webmap':  {'type': 'outlet'},
            'runbook': {'type': 'outlet'},
        }, versioned=[])
        self.assertEqual(atlas_store.publishable_outlets(config), ['runbook', 'webmap'])


class TestOutletAllowlist(unittest.TestCase):
    """cloud.outlets is the safe way to cut an atlas over.

    The public-by-default access field is the hazard: kennedy's `sqldb` outlet
    has no access level, so it reads as public, but atlas.db contains every
    layer including ones only an admin webmap references.
    """

    def test_allowlist_restricts(self):
        config = _config({
            'webmap': {'type': 'outlet'},
            'sqldb':  {'type': 'outlet'},
            'stac':   {'type': 'outlet'},
        }, cloud={'outlets': ['webmap']})
        self.assertEqual(atlas_store.publishable_outlets(config), ['webmap'])

    def test_allowlist_cannot_promote_a_protected_outlet(self):
        # A typo in the allowlist must not push an admin outlet public.
        config = _config({
            'webmap':  {'type': 'outlet'},
            'webedit': {'type': 'outlet', 'access': ['admin']},
        }, cloud={'outlets': ['webmap', 'webedit']})
        self.assertEqual(atlas_store.publishable_outlets(config), ['webmap'])

    def test_allowlist_cannot_promote_a_non_outlet(self):
        config = _config({
            'roads': {'type': 'inlet'},
        }, cloud={'outlets': ['roads']})
        self.assertEqual(atlas_store.publishable_outlets(config), [])

    def test_without_allowlist_default_public_is_warned(self):
        config = _config({'sqldb': {'type': 'outlet'}})
        with self.assertLogs('atlas_store', level='WARNING') as captured:
            names = atlas_store.publishable_outlets(config)
        self.assertEqual(names, ['sqldb'])
        self.assertIn('no explicit access level', ''.join(captured.output))

    def test_explicit_public_is_not_warned(self):
        config = _config({'webmap': {'type': 'outlet', 'access': ['public']}})
        with self.assertLogs('atlas_store', level='WARNING') as captured:
            logging.getLogger('atlas_store').warning('sentinel')
            names = atlas_store.publishable_outlets(config)
        self.assertEqual(names, ['webmap'])
        self.assertEqual(len(captured.output), 1)  # only the sentinel


class TestKeys(unittest.TestCase):
    def test_current_prefix(self):
        self.assertEqual(atlas_store.current_prefix('kennedy'), 'kennedy/current')

    def test_pointer_sits_outside_current_prefix(self):
        # Otherwise pruning stale keys under current/ would delete the pointer.
        pointer = atlas_store.pointer_key('kennedy')
        self.assertEqual(pointer, 'kennedy/current.json')
        self.assertFalse(pointer.startswith(atlas_store.current_prefix('kennedy') + '/'))

    def test_stale_keys(self):
        existing = ['a/x.html', 'a/old.geojson', 'a/y.js']
        planned = ['a/x.html', 'a/y.js']
        self.assertEqual(atlas_store.stale_keys(existing, planned), ['a/old.geojson'])

    def test_stale_keys_ignores_new_keys(self):
        self.assertEqual(atlas_store.stale_keys([], ['a/new.html']), [])


class TestPlanUpload(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _write(self, relative, content='x'):
        path = self.tmp / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def test_walks_nested_files(self):
        self._write('index.html')
        self._write('js/webmap.js')
        self._write('data/roads.geojson')

        plan = atlas_store.plan_upload(self.tmp, 'kennedy/current/outlets/webmap')
        keys = sorted(key for _, key, _ in plan)
        self.assertEqual(keys, [
            'kennedy/current/outlets/webmap/data/roads.geojson',
            'kennedy/current/outlets/webmap/index.html',
            'kennedy/current/outlets/webmap/js/webmap.js',
        ])

    def test_content_types_attached(self):
        self._write('data/roads.geojson')
        (_, _, content_type), = atlas_store.plan_upload(self.tmp, 'p')
        self.assertEqual(content_type, 'application/geo+json')

    def test_trailing_slash_in_prefix_does_not_double(self):
        self._write('index.html')
        (_, key, _), = atlas_store.plan_upload(self.tmp, 'p/')
        self.assertEqual(key, 'p/index.html')

    def test_missing_directory_is_empty_plan(self):
        self.assertEqual(atlas_store.plan_upload(self.tmp / 'nope', 'p'), [])

    def test_directories_are_not_uploaded(self):
        (self.tmp / 'empty_dir').mkdir()
        self._write('a.html')
        self.assertEqual(len(atlas_store.plan_upload(self.tmp, 'p')), 1)


class TestPlanPublish(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _outlet(self, name, filename='index.html'):
        path = self.tmp / 'outlets' / name / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('x')

    def test_public_outlets_only(self):
        self._outlet('webmap')
        self._outlet('webedit')
        config = _config({
            'webmap':  {'type': 'outlet'},
            'webedit': {'type': 'outlet', 'access': ['admin']},
        })
        keys = [key for _, key, _ in atlas_store.plan_publish(config, self.tmp)]
        self.assertEqual(keys, ['testatlas/current/outlets/webmap/index.html'])

    def test_missing_outlet_dir_is_skipped_not_fatal(self):
        # A never-materialized outlet should not take the whole push down.
        self._outlet('webmap')
        config = _config({
            'webmap':  {'type': 'outlet'},
            'runbook': {'type': 'outlet'},
        })
        keys = [key for _, key, _ in atlas_store.plan_publish(config, self.tmp)]
        self.assertEqual(keys, ['testatlas/current/outlets/webmap/index.html'])

    def test_explicit_outlet_names_override(self):
        self._outlet('webmap')
        self._outlet('runbook')
        config = _config({'webmap': {'type': 'outlet'}, 'runbook': {'type': 'outlet'}})
        keys = [key for _, key, _ in
                atlas_store.plan_publish(config, self.tmp, ['runbook'])]
        self.assertEqual(keys, ['testatlas/current/outlets/runbook/index.html'])


class TestPublishIsSafeWhenDisabled(unittest.TestCase):
    def test_disabled_atlas_is_a_noop(self):
        # No boto3 import, no credentials, no exception — this is what makes it
        # safe to leave in publish_new_version for every atlas.
        result = atlas_store.publish_public_outlets(
            {'name': 'kennedy', 'assets': {}, 'dataswale': {}}, '/nonexistent', 'v1')
        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['reason'], 'disabled')


if __name__ == '__main__':
    unittest.main()
