import json
import os
import sys
import unittest
from pathlib import Path
import shutil
import tempfile

# Add the python directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from versioning import atlas_path, atlas_file, publish_new_version

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

def make_atlas(root: Path, versioned_outlets=None):
    """Build a minimal staging tree: layers, deltas (+work archive), two
    outlets, atlas_config.json, and a CURRENT symlink to a prior version."""
    staging = root / 'testatlas' / 'staging'
    (staging / 'layers' / 'roads').mkdir(parents=True)
    (staging / 'layers' / 'roads' / 'roads.geojson').write_text('{"features": []}')
    (staging / 'deltas' / 'roads' / 'work').mkdir(parents=True)
    (staging / 'deltas' / 'roads' / 'pending__1__create.geojson').write_text('{}')
    (staging / 'deltas' / 'roads' / 'work' / 'applied__0__create.geojson').write_text('{}')
    for outlet in ('webmap', 'runbook'):
        (staging / 'outlets' / outlet).mkdir(parents=True)
        (staging / 'outlets' / outlet / 'index.html').write_text(outlet)

    dataswale = {'versions': []}
    if versioned_outlets is not None:
        dataswale['versioned_outlets'] = versioned_outlets
    config = {'name': 'testatlas', 'data_root': str(root), 'dataswale': dataswale}
    (staging / 'atlas_config.json').write_text(json.dumps(config))

    prior = root / 'testatlas' / '0000-00-00'
    prior.mkdir()
    (root / 'testatlas' / 'CURRENT').symlink_to(prior)
    return config


class TestPublishNewVersion(unittest.TestCase):
    """Publish is a pure snapshot (issue #131, T4): versioned_outlets is a
    copy filter (missing or empty = all), the deltas tree (pending + work/
    archive) is always retained, and nothing in staging is deleted."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def publish(self, versioned_outlets=None, version='v1'):
        config = make_atlas(self.root, versioned_outlets)
        version_path = publish_new_version(config, version=version)
        return config, Path(version_path)

    def test_no_filter_copies_all_outlets(self):
        _, vp = self.publish(versioned_outlets=None)
        self.assertTrue((vp / 'outlets' / 'webmap' / 'index.html').exists())
        self.assertTrue((vp / 'outlets' / 'runbook' / 'index.html').exists())

    def test_empty_filter_copies_all_outlets(self):
        _, vp = self.publish(versioned_outlets=[])
        self.assertTrue((vp / 'outlets' / 'webmap' / 'index.html').exists())
        self.assertTrue((vp / 'outlets' / 'runbook' / 'index.html').exists())

    def test_filter_excludes_unlisted_outlets(self):
        _, vp = self.publish(versioned_outlets=['webmap'])
        self.assertTrue((vp / 'outlets' / 'webmap' / 'index.html').exists())
        self.assertFalse((vp / 'outlets' / 'runbook').exists())
        # Filter applies to outlets only — layers and deltas unaffected.
        self.assertTrue((vp / 'layers' / 'roads' / 'roads.geojson').exists())

    def test_deltas_tree_retained_in_version_and_staging(self):
        config, vp = self.publish()
        # Full delta history in the published version (C10)...
        self.assertTrue((vp / 'deltas' / 'roads' / 'pending__1__create.geojson').exists())
        self.assertTrue((vp / 'deltas' / 'roads' / 'work' / 'applied__0__create.geojson').exists())
        # ...and staging untouched: publish deletes nothing.
        staging = self.root / 'testatlas' / 'staging'
        self.assertTrue((staging / 'deltas' / 'roads' / 'work' / 'applied__0__create.geojson').exists())

    def test_current_repointed_and_version_recorded(self):
        config, vp = self.publish()
        current = self.root / 'testatlas' / 'CURRENT'
        self.assertEqual(current.resolve(), vp.resolve())
        self.assertIn('v1', config['dataswale']['versions'])
        # Version list persisted back to the staging config.
        on_disk = json.loads((self.root / 'testatlas' / 'staging' / 'atlas_config.json').read_text())
        self.assertIn('v1', on_disk['dataswale']['versions'])

    def test_existing_version_rejected(self):
        config = make_atlas(self.root)
        (self.root / 'testatlas' / 'v1').mkdir()
        with self.assertRaises(ValueError):
            publish_new_version(config, version='v1')


if __name__ == '__main__':
    unittest.main()