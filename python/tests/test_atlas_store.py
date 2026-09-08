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


class TestRoleVariantPruning(unittest.TestCase):
    """`html` and `console` generate all four role variants into one directory.

    The outlet's own access field reads as public because its public/ variant
    is, so outlet-level tiering alone published the admin console. Regression
    test for that leak.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _write(self, relative):
        path = self.tmp / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('x')

    def test_is_protected_path(self):
        self.assertTrue(atlas_store.is_protected_path('admin/index.html'))
        self.assertTrue(atlas_store.is_protected_path('internal/index.html'))
        self.assertTrue(atlas_store.is_protected_path('technical/index.html'))
        self.assertFalse(atlas_store.is_protected_path('public/index.html'))
        self.assertFalse(atlas_store.is_protected_path('index.html'))

    def test_only_first_segment_is_pruned(self):
        # A layer named "admin" must keep its html/{layer}/attribution.html.
        self.assertFalse(atlas_store.is_protected_path('data/admin.geojson'))
        self.assertFalse(atlas_store.is_protected_path('admin'))

    def test_role_variants_are_not_uploaded(self):
        self._write('public/index.html')
        self._write('admin/index.html')
        self._write('internal/index.html')
        self._write('technical/index.html')
        self._write('roads/attribution.html')

        keys = sorted(key for _, key, _ in atlas_store.plan_upload(self.tmp, 'p'))
        self.assertEqual(keys, ['p/public/index.html', 'p/roads/attribution.html'])

    def test_leaked_keys_are_pruned_on_republish(self):
        # The mirror design closes the leak by itself: keys already in the
        # bucket that the new plan does not write get deleted.
        self._write('public/index.html')
        self._write('admin/index.html')
        planned = [key for _, key, _ in atlas_store.plan_upload(self.tmp, 'p')]
        already_in_bucket = ['p/public/index.html', 'p/admin/index.html']
        self.assertEqual(atlas_store.stale_keys(already_in_bucket, planned),
                         ['p/admin/index.html'])


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


# ---------------------------------------------------------------------------
# Phase 3 (#159): source layer data in S3
# ---------------------------------------------------------------------------

class TestLayerKeysAndBuckets(unittest.TestCase):

    SETTINGS = {'outlets_bucket': 'scs-atlas-outlets-prod',
                'private_bucket': 'scs-atlas-private-prod'}

    def test_layer_key_is_version_stamped(self):
        self.assertEqual(
            atlas_store.layer_key('kennedy', 'roads', '2026-09-08', 'roads.geojson'),
            'kennedy/layers/roads/2026-09-08/roads.geojson')

    def test_public_layer_goes_to_the_outlets_bucket(self):
        self.assertEqual(atlas_store.layer_bucket(['public'], self.SETTINGS),
                         'scs-atlas-outlets-prod')
        self.assertEqual(atlas_store.layer_bucket(['public', 'admin'], self.SETTINGS),
                         'scs-atlas-outlets-prod')
        self.assertEqual(atlas_store.layer_bucket('public', self.SETTINGS),
                         'scs-atlas-outlets-prod')

    def test_protected_layer_goes_to_the_private_bucket(self):
        for access in (['internal'], ['admin'], ['technical'], ['internal', 'admin']):
            self.assertEqual(atlas_store.layer_bucket(access, self.SETTINGS),
                             'scs-atlas-private-prod', access)

    def test_absent_tier_never_reaches_the_public_bucket(self):
        """is_public(None) is True — that default must not decide a bucket.

        normalize_access() defaults a missing tier to public, matching
        atlas.py. It is the same fail-open default that made `sqldb`, whose
        atlas.db holds every layer, read as public. A bucket decision has to
        require an explicit tier.
        """
        for access in (None, [], ''):
            self.assertEqual(atlas_store.layer_bucket(access, self.SETTINGS),
                             'scs-atlas-private-prod', repr(access))

    def test_cloud_settings_exposes_the_layer_opt_in(self):
        config = {'name': 'kennedy', 'cloud': {
            'enabled': True, 'outlets_bucket': 'out', 'private_bucket': 'priv',
            'layers': True}}
        settings = atlas_store.cloud_settings(config)
        self.assertTrue(settings['layers'])
        self.assertEqual(settings['outlets_bucket'], 'out')
        self.assertEqual(settings['private_bucket'], 'priv')

    def test_outlet_mirroring_alone_does_not_ship_layers(self):
        """An atlas already on the Phase 2 read path must opt in separately."""
        config = {'name': 'kennedy',
                  'cloud': {'enabled': True, 'outlets_bucket': 'out'}}
        self.assertFalse(atlas_store.cloud_settings(config)['layers'])
        self.assertEqual(
            atlas_store.publish_layer_data(config, '/tmp', 'v1', {})['status'],
            'skipped')


class TestLayerUploadPlan(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.config = {'name': 'kennedy', 'cloud': {
            'enabled': True, 'outlets_bucket': 'OUT', 'private_bucket': 'PRIV',
            'layers': True}}
        for layer, files in (('roads', ['roads.geojson']),
                             ('basemap', ['basemap.tiff', 'basemap.tiff.jpg']),
                             ('notes', ['notes.geojson'])):
            d = self.tmp / 'layers' / layer
            d.mkdir(parents=True)
            for f in files:
                (d / f).write_text('x')
        self.layer_assets = {
            'roads': {'href': 'h/roads.geojson', 'files': ['roads.geojson']},
            'basemap': {'href': 'h/basemap.tiff',
                        'files': ['basemap.tiff', 'basemap.tiff.jpg']},
            'notes': {'href': 'h/notes.geojson', 'files': ['notes.geojson']},
        }
        self.access = {'roads': ['public'], 'basemap': ['public'],
                       'notes': ['internal']}

    def _plan(self, written):
        return atlas_store.plan_layer_uploads(
            self.config, self.tmp, 'V1', self.layer_assets, written, self.access)

    def test_reused_layers_are_not_uploaded(self):
        """A publish with no edits ships nothing — the point of stamped keys."""
        self.assertEqual(self._plan([]), [])

    def test_every_servable_file_is_uploaded_not_just_the_primary(self):
        keys = [k for _, _, k, _ in self._plan(['basemap'])]
        self.assertIn('kennedy/layers/basemap/V1/basemap.tiff', keys)
        self.assertIn('kennedy/layers/basemap/V1/basemap.tiff.jpg', keys,
                      'a webmap asks a raster for the rendered image')

    def test_plan_routes_each_layer_by_tier(self):
        buckets = {k.split('/')[2]: b for _, b, k, _ in
                   self._plan(['roads', 'notes'])}
        self.assertEqual(buckets['roads'], 'OUT')
        self.assertEqual(buckets['notes'], 'PRIV')

    def test_content_types_are_set(self):
        types = {Path(k).name: c for _, _, k, c in self._plan(['roads', 'basemap'])}
        self.assertEqual(types['roads.geojson'], 'application/geo+json')
        self.assertEqual(types['basemap.tiff'], 'image/tiff')
        self.assertEqual(types['basemap.tiff.jpg'], 'image/jpeg')

    def test_a_file_named_by_the_catalog_but_missing_on_disk_is_skipped(self):
        self.layer_assets['roads']['files'].append('ghost.geojson')
        keys = [Path(k).name for _, _, k, _ in self._plan(['roads'])]
        self.assertEqual(keys, ['roads.geojson'])

    def test_catalog_documents_are_planned_into_the_outlets_bucket(self):
        stac = self.tmp / 'stac' / 'roads'
        stac.mkdir(parents=True)
        (stac / 'collection.json').write_text('{}')
        (self.tmp / 'stac' / 'catalog.json').write_text('{}')

        plan = atlas_store.plan_catalog_upload(self.config, self.tmp, 'V1')
        keys = sorted(k for _, _, k, _ in plan)
        self.assertEqual(keys, ['kennedy/catalog/V1/catalog.json',
                                'kennedy/catalog/V1/roads/collection.json'])
        self.assertTrue(all(b == 'OUT' for _, b, _, _ in plan))
