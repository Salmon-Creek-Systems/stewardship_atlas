"""Tests for the pure half of atlas_store — no boto3, no network.

The module splits deliberately: access-tier logic, key construction, content
types and upload planning have no AWS dependency, so they are testable in the
bare local env. Everything below runs without credentials.
"""

import json
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
    """Settings resolve config -> environment -> code default, and never gate.

    The `enabled` / `layers` booleans are gone. S3 is the storage backend, not
    a feature an atlas opts into, so the only question these settings answer is
    *which* buckets — which is what makes the staging rehearsal a set of env
    vars rather than eleven config edits.
    """

    ENV_KEYS = ('ATLAS_OUTLETS_BUCKET', 'ATLAS_PRIVATE_BUCKET',
                'ATLAS_DISTRIBUTION_ID', 'ATLAS_PUBLIC_BASE_URL')

    def setUp(self):
        self._saved = {k: os.environ.pop(k, None) for k in self.ENV_KEYS}

    def tearDown(self):
        for key, value in self._saved.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value

    def test_defaults_need_no_config_at_all(self):
        settings = atlas_store.cloud_settings({'name': 'kennedy'})
        self.assertEqual(settings['bucket'], atlas_store.DEFAULT_OUTLETS_BUCKET)
        self.assertEqual(settings['private_bucket'], atlas_store.DEFAULT_PRIVATE_BUCKET)
        self.assertEqual(settings['public_base_url'], atlas_store.DEFAULT_PUBLIC_BASE_URL)

    def test_there_is_no_enabled_flag_any_more(self):
        # Guards the collapse: a stray `enabled` key must not resurrect a gate.
        settings = atlas_store.cloud_settings(
            {'name': 'k', 'cloud': {'enabled': False, 'layers': False}})
        self.assertNotIn('enabled', settings)
        self.assertNotIn('layers', settings)
        self.assertEqual(settings['bucket'], atlas_store.DEFAULT_OUTLETS_BUCKET)

    def test_env_overrides_the_default(self):
        os.environ['ATLAS_OUTLETS_BUCKET'] = 'staging-outlets'
        os.environ['ATLAS_PRIVATE_BUCKET'] = 'staging-private'
        os.environ['ATLAS_PUBLIC_BASE_URL'] = 'https://d123.cloudfront.net'
        settings = atlas_store.cloud_settings({'name': 'k'})
        self.assertEqual(settings['bucket'], 'staging-outlets')
        self.assertEqual(settings['private_bucket'], 'staging-private')
        self.assertEqual(settings['public_base_url'], 'https://d123.cloudfront.net')

    def test_config_overrides_env(self):
        os.environ['ATLAS_OUTLETS_BUCKET'] = 'from-env'
        settings = atlas_store.cloud_settings(
            {'name': 'k', 'cloud': {'outlets_bucket': 'from-config'}})
        self.assertEqual(settings['bucket'], 'from-config')

    def test_public_base_url_loses_its_trailing_slash(self):
        # It is concatenated with a key that already starts with the atlas name;
        # a trailing slash here yields '//kennedy/...' in every emitted URL.
        os.environ['ATLAS_PUBLIC_BASE_URL'] = 'https://next.fireatlas.org/'
        settings = atlas_store.cloud_settings({'name': 'k'})
        self.assertEqual(settings['public_base_url'], 'https://next.fireatlas.org')


def _config(assets, versioned=None, cloud=None):
    dataswale = {'layers': []}
    if versioned is not None:
        dataswale['versioned_outlets'] = versioned
    config = {'name': 'testatlas', 'assets': assets, 'dataswale': dataswale}
    # `cloud.outlets` is mandatory, so tests aimed at the *other* filters get a
    # permissive allowlist by default. Tests about the allowlist itself pass
    # `cloud` explicitly, and TestMandatoryAllowlist omits it on purpose.
    config['cloud'] = cloud if cloud is not None else {'outlets': sorted(assets)}
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

    def test_allowlisted_but_untiered_outlet_is_warned(self):
        # It passes the tier check only by atlas.py's fail-open default (#175),
        # so it publishes, but never silently.
        config = _config({'webmap': {'type': 'outlet'}}, cloud={'outlets': ['webmap']})
        with self.assertLogs('atlas_store', level='WARNING') as captured:
            names = atlas_store.publishable_outlets(config)
        self.assertEqual(names, ['webmap'])
        self.assertIn('fail-open default', ''.join(captured.output))

    def test_explicit_public_is_not_warned(self):
        config = _config({'webmap': {'type': 'outlet', 'access': ['public']}},
                         cloud={'outlets': ['webmap']})
        with self.assertLogs('atlas_store', level='WARNING') as captured:
            logging.getLogger('atlas_store').warning('sentinel')
            names = atlas_store.publishable_outlets(config)
        self.assertEqual(names, ['webmap'])
        self.assertEqual(len(captured.output), 1)  # only the sentinel


class TestMandatoryAllowlist(unittest.TestCase):
    """No `cloud.outlets` means no outlets published, not "publish the defaults".

    While `cloud.enabled` existed, an un-migrated atlas was protected by being
    switched off. S3 is now unconditional, so the allowlist is the only thing
    between `access`-defaults-to-public and a world-readable bucket. This is
    the failure that put 17 protected layers on CloudFront (#177); it is not
    being left to a default a second time.
    """

    def test_no_allowlist_publishes_nothing(self):
        config = {'name': 'unmigrated',
                  'assets': {'webmap': {'type': 'outlet', 'access': ['public']},
                             'sqldb': {'type': 'outlet'}},
                  'dataswale': {'layers': []}}
        with self.assertLogs('atlas_store', level='WARNING') as captured:
            self.assertEqual(atlas_store.publishable_outlets(config), [])
        self.assertIn('no `cloud.outlets` allowlist', ''.join(captured.output))

    def test_empty_allowlist_publishes_nothing_without_warning(self):
        # An explicit [] is a decision, not an omission.
        config = _config({'webmap': {'type': 'outlet', 'access': ['public']}},
                         cloud={'outlets': []})
        self.assertEqual(atlas_store.publishable_outlets(config), [])

    def test_a_cloud_block_without_outlets_is_still_an_omission(self):
        config = _config({'webmap': {'type': 'outlet', 'access': ['public']}},
                         cloud={'private_bucket': 'p'})
        with self.assertLogs('atlas_store', level='WARNING'):
            self.assertEqual(atlas_store.publishable_outlets(config), [])


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


class TestPublishWithNothingAllowlisted(unittest.TestCase):
    def test_no_allowlist_still_reaches_no_network(self):
        # An atlas with no `cloud.outlets` produces an empty plan, so publish
        # returns before importing boto3 or needing credentials. That is what
        # keeps this callable in publish_new_version for every atlas now that
        # there is no `enabled` flag to switch it off.
        result = atlas_store.publish_public_outlets(
            {'name': 'kennedy', 'assets': {}, 'dataswale': {}}, '/nonexistent', 'v1')
        self.assertEqual(result['status'], 'empty')


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

    def test_cloud_settings_names_both_buckets(self):
        config = {'name': 'kennedy', 'cloud': {
            'outlets_bucket': 'out', 'private_bucket': 'priv'}}
        settings = atlas_store.cloud_settings(config)
        self.assertEqual(settings['outlets_bucket'], 'out')
        self.assertEqual(settings['private_bucket'], 'priv')

    def test_layer_push_no_longer_has_an_opt_out(self):
        """There is no `cloud.layers` flag: source data always goes to S3.

        An empty catalog summary means nothing to upload, so this still makes
        no network call — but the reason is "no layers", not "switched off".
        """
        config = {'name': 'kennedy',
                  'cloud': {'outlets_bucket': 'out', 'private_bucket': 'priv'}}
        result = atlas_store.publish_layer_data(config, '/tmp', 'v1', {})
        self.assertNotEqual(result.get('status'), 'skipped')
        self.assertEqual(result['objects'], 0)


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

    def _plan(self, names, versions=None):
        layer_versions = versions or {n: 'V1' for n in names}
        return atlas_store.plan_layer_uploads(
            self.config, self.tmp, 'V1', self.layer_assets, layer_versions,
            self.access)

    def test_plan_describes_every_layer_not_just_the_written_ones(self):
        """Reuse is computed against the LOCAL catalog, which knows nothing
        about what S3 holds. Planning only written layers meant kennedy's first
        push uploaded no layer data at all — every layer was 'unchanged since
        the last local version'. The plan is the desired end state; the caller
        reconciles it against the bucket."""
        keys = [k for _, _, k, _ in self._plan(['roads', 'notes'])]
        self.assertEqual(len(keys), 2)

    def test_a_reused_layer_is_keyed_at_the_version_that_holds_it(self):
        keys = [k for _, _, k, _ in
                self._plan(['roads'], versions={'roads': 'V0-OLDER'})]
        self.assertEqual(keys, ['kennedy/layers/roads/V0-OLDER/roads.geojson'])

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


# ---------------------------------------------------------------------------
# Phase 3 (#159): the served layer mirror under {atlas}/current/layers/
#
# This replaces `bake_data`. Fixtures here deliberately carry the things that
# broke earlier slices: more than one outlet (so the union can be wrong), a
# protected layer referenced by a public outlet (the #177 shape), a stray file
# in a layer directory, and a layer with no data at all.
# ---------------------------------------------------------------------------

class TestCurrentLayerMirror(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.version = self.tmp / 'v1'
        for layer, files in {
            'roads':     ['roads.geojson'],
            'hydrants':  ['hydrants.geojson'],
            'basemap':   ['basemap.tiff', 'basemap.tiff.jpg',
                          'basemap.tiff.aux.xml', 'stats.json'],
            'secret':    ['secret.geojson'],
            'unused':    ['unused.geojson'],
            'empty':     [],
        }.items():
            d = self.version / 'layers' / layer
            d.mkdir(parents=True)
            for name in files:
                (d / name).write_text('x')
        (self.version / 'layers' / 'roads' / '.htpasswd').write_text('creds')

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _config(self, **overrides):
        config = {
            'name': 'testatlas',
            'assets': {
                'webmap':  {'type': 'outlet', 'access': ['public'],
                            'in_layers': ['roads', 'basemap', 'empty']},
                'gazetteer': {'type': 'outlet', 'access': ['public'],
                              'in_layers': ['roads', 'hydrants']},
                'private_webmap': {'type': 'outlet', 'access': ['admin'],
                                   'in_layers': ['secret']},
            },
            'dataswale': {'layers': [
                {'name': 'roads', 'access': ['public']},
                {'name': 'hydrants', 'access': ['public']},
                {'name': 'basemap', 'access': ['public']},
                {'name': 'secret', 'access': ['admin']},
                {'name': 'unused', 'access': ['public']},
                {'name': 'empty', 'access': ['public']},
            ]},
            'cloud': {'outlets': ['webmap', 'gazetteer', 'private_webmap']},
        }
        config.update(overrides)
        return config

    def _keys(self, **kw):
        plan = atlas_store.plan_current_layers(self._config(), self.version, **kw)
        return sorted(key for _, key, _ in plan)

    def test_union_across_published_outlets(self):
        names = atlas_store.outlet_layer_names(self._config())
        # private_webmap is not publishable (admin), so `secret` is not in the
        # union at all — the allowlist and the tier filter agree.
        self.assertEqual(names, ['basemap', 'empty', 'hydrants', 'roads'])

    def test_mirrors_referenced_public_layers(self):
        keys = self._keys()
        self.assertIn('testatlas/current/layers/roads/roads.geojson', keys)
        self.assertIn('testatlas/current/layers/hydrants/hydrants.geojson', keys)

    def test_a_raster_brings_its_rendered_image(self):
        # The webmap requests basemap.tiff.jpg, not the tiff. Mirroring only the
        # primary file would leave the map with a broken source.
        keys = self._keys()
        self.assertIn('testatlas/current/layers/basemap/basemap.tiff', keys)
        self.assertIn('testatlas/current/layers/basemap/basemap.tiff.jpg', keys)

    def test_strays_and_sidecars_never_mirror(self):
        blob = '\n'.join(self._keys())
        self.assertNotIn('.htpasswd', blob)
        self.assertNotIn('aux.xml', blob)
        self.assertNotIn('stats.json', blob)

    def test_unreferenced_layer_is_not_published(self):
        # `unused` is public and has data, but no published outlet names it.
        # Being public must not be sufficient to leave the box.
        self.assertNotIn('testatlas/current/layers/unused/unused.geojson', self._keys())

    def test_protected_layer_is_never_mirrored(self):
        """The #177 shape: a protected layer must not reach the public bucket.

        Here it cannot even be reached, because the outlet referencing it is
        not publishable — but the layer-level tier check is asserted directly
        so the guarantee does not rest on the outlet filter alone.
        """
        config = self._config()
        # Force the protected layer into a *public* outlet's in_layers.
        config['assets']['webmap']['in_layers'].append('secret')
        with self.assertLogs('atlas_store', level='WARNING') as captured:
            plan = atlas_store.plan_current_layers(config, self.version)
        keys = [key for _, key, _ in plan]
        self.assertNotIn('testatlas/current/layers/secret/secret.geojson', keys)
        self.assertIn("declared access=['admin']", ''.join(captured.output))

    def test_layer_with_no_data_gets_an_empty_collection(self):
        stub_dir = self.tmp / 'stubs'
        stub_dir.mkdir()
        plan = atlas_store.plan_current_layers(
            self._config(), self.version, stub_dir)
        entry = [e for e in plan if e[1].endswith('empty/empty.geojson')]
        self.assertEqual(len(entry), 1, 'referenced empty layer needs a stub (#135)')
        self.assertEqual(json.loads(entry[0][0].read_text()),
                         {'type': 'FeatureCollection', 'features': []})

    def test_without_a_stub_dir_an_empty_layer_is_simply_absent(self):
        self.assertFalse([k for k in self._keys() if 'empty/' in k])

    def test_keys_sit_under_the_pruned_prefix(self):
        """Layer keys must live under current/, or stale pruning cannot reach them.

        publish_public_outlets lists `{atlas}/current/` and deletes whatever the
        new plan omits. A layer key outside that prefix would linger and keep
        being served after the layer stopped being referenced.
        """
        prefix = atlas_store.current_prefix('testatlas') + '/'
        for key in self._keys():
            self.assertTrue(key.startswith(prefix), key)

    def test_the_relative_url_a_webmap_emits_resolves_to_a_mirrored_key(self):
        """The join that makes one artifact work on both hosts.

        `{atlas}/current/outlets/webmap/` + `../../layers/roads/roads.geojson`
        must land exactly on a key this plan writes. Asserting the two halves
        separately is what let earlier slices ship a URL pointing at nothing.
        """
        import posixpath
        import atlas_catalog
        outlet_dir = f"{atlas_store.current_prefix('testatlas')}/outlets/webmap/"
        url = atlas_catalog.layer_data_url('roads', 'roads.geojson')
        resolved = posixpath.normpath(posixpath.join(outlet_dir, url))
        self.assertIn(resolved, self._keys())


class TestShippedConfigsPinNoSubstrate(unittest.TestCase):
    """No atlas config may name a bucket, distribution, or on/off flag.

    Those describe the deployed substrate, not an atlas, and `cloud_settings`
    resolves config *before* environment. So a config that pins
    `outlets_bucket: scs-atlas-outlets-prod` silently wins over
    ATLAS_OUTLETS_BUCKET — which would point a staging rehearsal at the
    production buckets while appearing to work. The whole reason the staging
    substrate is a set of env vars rather than eleven config edits is that this
    stays empty.

    `outlets` is the exception and belongs here: which outlets an atlas
    publishes is a property of the atlas.
    """

    FORBIDDEN = {'enabled', 'layers', 'outlets_bucket', 'private_bucket',
                 'distribution_id', 'public_base_url'}

    def test_no_config_pins_substrate(self):
        import glob
        root = Path(__file__).resolve().parent.parent.parent / 'configuration'
        checked = 0
        for path in sorted(glob.glob(str(root / '*.geojson'))):
            with open(path) as handle:
                doc = json.load(handle)
            props = (doc['features'][0]['properties']
                     if 'features' in doc else doc)
            cloud = props.get('cloud') or {}
            if not cloud:
                continue
            checked += 1
            offending = self.FORBIDDEN & set(cloud)
            self.assertEqual(
                offending, set(),
                f"{Path(path).name} pins {sorted(offending)} in its cloud block; "
                f"those belong in the environment, not the atlas")
        self.assertGreater(checked, 0, 'no cloud blocks found — test is vacuous')
