import json
import os
import sys
import unittest
from unittest import mock
from pathlib import Path
import shutil
import tempfile

# Add the python directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import atlas_store
import versioning
from versioning import atlas_path, atlas_file, publish_new_version
from fake_s3 import FakeS3

class TestVersioning(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory for testing
        self.test_dir = tempfile.mkdtemp()
        self.test_config = {
            "name": "test_atlas",
            "data_root": self.test_dir
        }

    def tearDown(self):
        # Clean up the temporary directory
        shutil.rmtree(self.test_dir)

    def test_atlas_path(self):
        """Test that atlas_path correctly constructs paths"""
        # Test with default version
        path = atlas_path(self.test_config)
        expected_path = Path(self.test_dir) / "test_atlas" / "staging"
        self.assertEqual(path, expected_path)

        # Test with custom version
        path = atlas_path(self.test_config, version="prod")
        expected_path = Path(self.test_dir) / "test_atlas" / "prod"
        self.assertEqual(path, expected_path)

        # Test with local path
        path = atlas_path(self.test_config, local_path="data/input")
        expected_path = Path(self.test_dir) / "test_atlas" / "staging" / "data" / "input"
        self.assertEqual(path, expected_path)

        # Test with both custom version and local path
        path = atlas_path(self.test_config, local_path="data/output", version="prod")
        expected_path = Path(self.test_dir) / "test_atlas" / "prod" / "data" / "output"
        self.assertEqual(path, expected_path)

    def test_atlas_file(self):
        """Test that atlas_file correctly creates directories and files"""
        # Test creating a new file
        test_path = Path(self.test_dir) / "test_atlas" / "staging" / "test.txt"
        with atlas_file(test_path, 'w') as f:
            f.write("test content")
        
        # Verify file exists and has correct content
        self.assertTrue(test_path.exists())
        with open(test_path, 'r') as f:
            self.assertEqual(f.read(), "test content")

        # Test reading an existing file
        with atlas_file(test_path, 'r') as f:
            content = f.read()
            self.assertEqual(content, "test content")

        # Test creating a file in a deep directory structure
        deep_path = Path(self.test_dir) / "test_atlas" / "staging" / "deep" / "nested" / "file.txt"
        with atlas_file(deep_path, 'w') as f:
            f.write("deep content")
        
        # Verify deep directory structure was created
        self.assertTrue(deep_path.exists())
        with open(deep_path, 'r') as f:
            self.assertEqual(f.read(), "deep content")

OUT = 'OUT'
PRIV = 'PRIV'


def make_atlas(root: Path, versioned_outlets=None):
    """A minimal staging tree: one layer, a deltas tree with its work/ archive,
    two built outlets, atlas_config.json, and a CURRENT symlink to a prior
    version — which publish must now leave alone."""
    staging = root / 'testatlas' / 'staging'
    (staging / 'layers' / 'roads').mkdir(parents=True)
    (staging / 'layers' / 'roads' / 'roads.geojson').write_text('{"features": []}')
    (staging / 'deltas' / 'roads' / 'work').mkdir(parents=True)
    (staging / 'deltas' / 'roads' / 'pending__1__create.geojson').write_text('{}')
    (staging / 'deltas' / 'roads' / 'work' / 'applied__0__create.geojson').write_text('{}')
    for outlet in ('webmap', 'runbook'):
        (staging / 'outlets' / outlet).mkdir(parents=True)
        (staging / 'outlets' / outlet / 'index.html').write_text(outlet)

    # Shaped like a *built* atlas_config.json, not like a source geojson.
    # `bbox` and `layers` are written by atlas.create_config() at build time
    # (atlas.py:190), so every config publish_new_version actually receives has
    # them. A fixture without them is simpler than production in precisely the
    # dimension the catalog cares about, which is how this file used to pass
    # while publish_catalog raised KeyError underneath a bare except.
    dataswale = {
        'versions': [],
        'layers': [{'name': 'roads', 'access': ['public']}],
        'bbox': {'north': 39.5, 'south': 39.4, 'east': -123.7, 'west': -123.8},
    }
    if versioned_outlets is not None:
        dataswale['versioned_outlets'] = versioned_outlets
    config = {
        'name': 'testatlas', 'data_root': str(root), 'dataswale': dataswale,
        'assets': {
            'webmap': {'type': 'outlet', 'access': ['public'],
                       'in_layers': ['roads'], 'config': {'fetch_type': 'webmap'}},
            'runbook': {'type': 'outlet', 'access': ['admin']},
        },
        'cloud': {'outlets': ['webmap'], 'outlets_bucket': OUT,
                  'private_bucket': PRIV, 'public_base_url': 'https://cdn.example.org',
                  'invalidate': False},
    }
    (staging / 'atlas_config.json').write_text(json.dumps(config))

    prior = root / 'testatlas' / '0000-00-00'
    prior.mkdir()
    (root / 'testatlas' / 'CURRENT').symlink_to(prior)
    return config


class TestPublishNewVersion(unittest.TestCase):
    """Publish writes a STAC version to S3 and nothing to local disk.

    A version used to be a `shutil.copytree` of staging plus a repointed
    CURRENT symlink. Under the session model compute happens in a workspace the
    next session re-hydrates from S3, so a local snapshot would be an
    unreferenced copy that write-back never sends anywhere. The version is now
    a Catalog naming immutable objects, and `current.json` plus the
    `{atlas}/current/` mirror replace the symlink.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.client = FakeS3()
        self.addCleanup(self.tmp.cleanup)

    def publish(self, versioned_outlets=None, version='v1', config=None):
        config = config or make_atlas(self.root, versioned_outlets)
        return config, publish_new_version(config, version=version,
                                           client=self.client)

    def keys(self, bucket, prefix=''):
        return sorted(k for (b, k) in self.client.objects
                      if b == bucket and k.startswith(prefix))

    # -- nothing local ----------------------------------------------------

    def test_no_version_directory_is_created(self):
        self.publish()
        children = {p.name for p in (self.root / 'testatlas').iterdir()}
        self.assertEqual(children, {'staging', 'CURRENT', '0000-00-00'},
                         'publish must not snapshot staging onto local disk')

    def test_current_is_left_alone(self):
        """The box no longer serves a published version, so there is nothing
        for the symlink to point at. Moving it would be a stopgap that the
        cutover deletes."""
        config = make_atlas(self.root)
        current = self.root / 'testatlas' / 'CURRENT'
        before = current.resolve()
        self.publish(config=config)
        self.assertTrue(current.is_symlink())
        self.assertEqual(current.resolve(), before)

    def test_staging_is_untouched(self):
        """Publish deletes nothing: the delta history (pending + work/ archive)
        stays in staging, which is where it now lives permanently rather than
        being copied into each version."""
        self.publish()
        staging = self.root / 'testatlas' / 'staging'
        self.assertTrue((staging / 'deltas' / 'roads' / 'work' /
                         'applied__0__create.geojson').exists())
        self.assertTrue((staging / 'deltas' / 'roads' /
                         'pending__1__create.geojson').exists())
        self.assertTrue((staging / 'layers' / 'roads' / 'roads.geojson').exists())

    def test_the_config_is_not_rewritten(self):
        """The version list comes from the catalog now, so publish no longer
        edits atlas_config.json — which under a session would have carried the
        edit back into S3 staging."""
        config, _ = self.publish()
        on_disk = json.loads((self.root / 'testatlas' / 'staging' /
                              'atlas_config.json').read_text())
        self.assertEqual(on_disk['dataswale']['versions'], [])

    # -- what lands in S3 -------------------------------------------------

    def test_layer_objects_land_at_immutable_version_stamped_keys(self):
        self.publish()
        self.assertIn('testatlas/layers/roads/v1/roads.geojson',
                      self.keys(OUT, 'testatlas/layers/'))

    def test_outlets_are_archived_privately_whatever_their_tier(self):
        """One outlet directory can hold several access tiers (#177), so the
        archive is never routed by the outlet's own access."""
        self.publish()
        archive = self.keys(PRIV, 'testatlas/outlets/')
        self.assertEqual(archive, ['testatlas/outlets/runbook/v1/index.html',
                                   'testatlas/outlets/webmap/v1/index.html'])
        self.assertEqual(self.keys(OUT, 'testatlas/outlets/'), [],
                         'the archive must never reach the public bucket')

    def test_versioned_outlets_still_filters_the_archive(self):
        self.publish(versioned_outlets=['webmap'])
        self.assertEqual(self.keys(PRIV, 'testatlas/outlets/'),
                         ['testatlas/outlets/webmap/v1/index.html'])

    def test_catalog_documents_land_at_the_stable_prefix(self):
        self.publish()
        documents = self.keys(OUT, 'testatlas/catalog/')
        self.assertIn('testatlas/catalog/catalog.json', documents)
        self.assertIn('testatlas/catalog/roads/roads-v1.json', documents)
        self.assertIn('testatlas/catalog/versions/v1/catalog.json', documents)
        self.assertIn('testatlas/catalog/webmap/webmap-v1.json', documents)

    def test_the_public_mirror_and_pointer_are_written(self):
        self.publish()
        self.assertIn('testatlas/current/outlets/webmap/index.html',
                      self.keys(OUT, 'testatlas/current/'))
        self.assertIn('testatlas/current/layers/roads/roads.geojson',
                      self.keys(OUT, 'testatlas/current/'))
        pointer = atlas_store.get_json(OUT, 'testatlas/current.json',
                                       client=self.client)
        self.assertEqual(pointer['version'], 'v1')

    def test_a_protected_outlet_stays_out_of_the_mirror(self):
        self.publish()
        self.assertNotIn('testatlas/current/outlets/runbook/index.html',
                         self.keys(OUT, 'testatlas/current/'))

    def test_the_summary_reports_what_was_published(self):
        _, result = self.publish()
        self.assertEqual(result['version'], 'v1')
        self.assertEqual(result['catalog']['written_layers'], ['roads'])
        self.assertEqual(sorted(result['catalog']['written_outlets']),
                         ['runbook', 'webmap'])
        self.assertEqual(result['catalog']['versions'], ['v1'])

    # -- ordering ---------------------------------------------------------

    def test_a_failed_object_push_writes_no_catalog(self):
        """Ordering contract: objects first, documents second, pointer last.

        A catalog naming bytes that never arrived is worse than no catalog —
        it makes a broken version look published. Failing before the documents
        leaves the previous version live, which is the safe outcome.
        """
        config = make_atlas(self.root)
        with mock.patch('atlas_store.publish_layer_data',
                        side_effect=RuntimeError('bucket unreachable')):
            with self.assertRaises(RuntimeError):
                publish_new_version(config, version='v1', client=self.client)

        self.assertEqual(self.keys(OUT, 'testatlas/catalog/'), [])
        self.assertEqual(self.keys(OUT, 'testatlas/current.json'), [])

    def test_a_reported_layer_error_is_raised_not_logged(self):
        """publish_layer_data collects per-bucket failures and returns them.
        Returning `status: error` and continuing would publish a catalog over
        objects that are not there."""
        config = make_atlas(self.root)
        with mock.patch('atlas_store.publish_layer_data',
                        return_value={'status': 'error', 'errors': ['OUT: boom']}):
            with self.assertRaises(RuntimeError):
                publish_new_version(config, version='v1', client=self.client)
        self.assertEqual(self.keys(OUT, 'testatlas/catalog/'), [])

    def test_a_failed_mirror_leaves_the_pointer_where_it_was(self):
        config = make_atlas(self.root)
        publish_new_version(config, version='v1', client=self.client)

        with mock.patch('atlas_store.publish_public_outlets',
                        side_effect=RuntimeError('bucket unreachable')):
            with self.assertRaises(RuntimeError):
                publish_new_version(config, version='v2', client=self.client)

        pointer = atlas_store.get_json(OUT, 'testatlas/current.json',
                                       client=self.client)
        self.assertEqual(pointer['version'], 'v1',
                         'the previous version stays live')

    # -- reuse ------------------------------------------------------------

    def test_a_second_publish_with_no_changes_uploads_no_new_objects(self):
        """The redundancy the catalog exists to remove. westport's ten
        versions were ten full copies; the tenth should have been a document.
        """
        config = make_atlas(self.root)
        publish_new_version(config, version='v1', client=self.client)
        before = set(self.client.objects)

        result = publish_new_version(config, version='v2', client=self.client)

        self.assertEqual(result['catalog']['written_layers'], [])
        self.assertEqual(result['catalog']['reused_layers'], ['roads'])
        self.assertEqual(sorted(result['catalog']['reused_outlets']),
                         ['runbook', 'webmap'])
        new_objects = {k for k in set(self.client.objects) - before}
        self.assertTrue(all('/catalog/' in key or key.endswith('current.json')
                            for _, key in new_objects),
                        f'only documents should be new, got {sorted(new_objects)}')

    def test_an_edited_layer_is_rewritten_at_the_new_version(self):
        config = make_atlas(self.root)
        publish_new_version(config, version='v1', client=self.client)
        (self.root / 'testatlas' / 'staging' / 'layers' / 'roads' /
         'roads.geojson').write_text('{"features": [1]}')

        result = publish_new_version(config, version='v2', client=self.client)

        self.assertEqual(result['catalog']['written_layers'], ['roads'])
        self.assertIn('testatlas/layers/roads/v2/roads.geojson',
                      self.keys(OUT, 'testatlas/layers/'))
        self.assertIn('testatlas/layers/roads/v1/roads.geojson',
                      self.keys(OUT, 'testatlas/layers/'))

    def test_republishing_the_same_version_is_idempotent(self):
        """Keys are immutable and version-stamped, so a retried publish
        reconciles against what is there rather than failing on 'exists'."""
        config = make_atlas(self.root)
        publish_new_version(config, version='v1', client=self.client)
        before = set(self.client.objects)
        publish_new_version(config, version='v1', client=self.client)
        self.assertEqual(set(self.client.objects), before)


if __name__ == '__main__':
    unittest.main()
