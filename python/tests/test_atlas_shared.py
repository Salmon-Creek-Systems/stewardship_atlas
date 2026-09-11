"""Tests for atlas_shared — shared source data fetched on demand.

The behaviours that matter: fetch only what is opened, never fetch twice,
never write outside a workspace (on the box `local` *is* `/root/data`), and
bring a shapefile's sidecars along with it.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import atlas_shared
import versioning
from fake_s3 import FakeS3

BUCKET = 'scs-atlas-private-test'


class Raiser(FakeS3):
    """Any S3 call is a failure — used where nothing should be fetched."""

    def head_object(self, Bucket, Key):
        raise AssertionError(f"fetched {Key} when it should not have")

    def get_object(self, Bucket, Key):
        raise AssertionError(f"fetched {Key} when it should not have")


class SharedTestCase(unittest.TestCase):
    def setUp(self):
        atlas_shared.forget_verified()
        self.addCleanup(atlas_shared.forget_verified)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'workspace'
        self.s3 = FakeS3()

    def config(self, atlas='kennedy', link=True):
        config = {'name': atlas, 'data_root': str(self.root)}
        staging = self.root / atlas / 'staging'
        staging.mkdir(parents=True, exist_ok=True)
        if link:
            atlas_shared.link_into_workspace(self.root, staging)
        return config

    def put(self, rel, body):
        self.s3.put_object(Bucket=BUCKET, Key=atlas_shared.shared_key(rel), Body=body)

    def heads(self):
        return [c for c in self.s3.calls if c[0] == 'head_object']


class TestLink(SharedTestCase):
    def test_local_points_at_the_shared_cache(self):
        config = self.config()
        link = versioning.atlas_path(config, 'local')
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.path.realpath(link),
                         str(atlas_shared.cache_dir(self.root).resolve()))

    def test_relinking_is_idempotent(self):
        config = self.config()
        atlas_shared.link_into_workspace(self.root, self.root / 'kennedy' / 'staging')
        self.assertTrue(versioning.atlas_path(config, 'local').is_symlink())

    def test_a_real_directory_is_left_alone(self):
        staging = self.root / 'kennedy' / 'staging'
        (staging / 'local').mkdir(parents=True)
        with self.assertLogs('atlas_shared', level='WARNING'):
            atlas_shared.link_into_workspace(self.root, staging)
        self.assertFalse((staging / 'local').is_symlink())


class TestFetch(SharedTestCase):
    def test_fetches_on_demand(self):
        config = self.config()
        self.put('hillshade.tiff', 'raster bytes')
        path = atlas_shared.local_path(config, 'hillshade.tiff', client=self.s3, bucket=BUCKET)
        self.assertEqual(path.read_text(), 'raster bytes')
        # Reached through the atlas's own local/ symlink, as the call sites expect.
        self.assertEqual(path, versioning.atlas_path(config, 'local') / 'hillshade.tiff')

    def test_second_call_does_not_ask_s3_again(self):
        config = self.config()
        self.put('hillshade.tiff', 'raster bytes')
        atlas_shared.local_path(config, 'hillshade.tiff', client=self.s3, bucket=BUCKET)
        before = len(self.heads())
        atlas_shared.local_path(config, 'hillshade.tiff', client=self.s3, bucket=BUCKET)
        self.assertEqual(len(self.heads()), before)

    def test_cache_is_shared_between_atlases(self):
        kennedy = self.config('kennedy')
        scvfd = self.config('scvfd')
        self.put('hillshade.tiff', 'raster bytes')
        atlas_shared.local_path(kennedy, 'hillshade.tiff', client=self.s3, bucket=BUCKET)
        downloads = len([c for c in self.s3.calls if c[0] == 'get_object'])
        path = atlas_shared.local_path(scvfd, 'hillshade.tiff', client=self.s3, bucket=BUCKET)
        self.assertEqual(path.read_text(), 'raster bytes')
        self.assertEqual(len([c for c in self.s3.calls if c[0] == 'get_object']), downloads)

    def test_replaced_object_is_refetched(self):
        config = self.config()
        self.put('parcels.geojson', 'v1')
        atlas_shared.local_path(config, 'parcels.geojson', client=self.s3, bucket=BUCKET)
        self.put('parcels.geojson', 'v2 is longer')
        atlas_shared.forget_verified()
        path = atlas_shared.local_path(config, 'parcels.geojson', client=self.s3, bucket=BUCKET)
        self.assertEqual(path.read_text(), 'v2 is longer')

    def test_unchanged_object_is_not_downloaded_again(self):
        config = self.config()
        self.put('parcels.geojson', 'v1')
        atlas_shared.local_path(config, 'parcels.geojson', client=self.s3, bucket=BUCKET)
        atlas_shared.forget_verified()
        downloads = len([c for c in self.s3.calls if c[0] == 'get_object'])
        atlas_shared.local_path(config, 'parcels.geojson', client=self.s3, bucket=BUCKET)
        self.assertEqual(len([c for c in self.s3.calls if c[0] == 'get_object']), downloads)

    def test_shapefile_brings_its_sidecars(self):
        config = self.config()
        for suffix in ('.shp', '.dbf', '.shx', '.prj'):
            self.put(f'humtranssp27{suffix}', f'bytes{suffix}')
        path = atlas_shared.local_path(config, 'humtranssp27.shp', required=True,
                                       client=self.s3, bucket=BUCKET)
        for suffix in ('.dbf', '.shx', '.prj'):
            self.assertTrue(path.with_suffix(suffix).is_file(), f"missing {suffix}")

    def test_missing_sidecar_is_not_fatal(self):
        config = self.config()
        self.put('humtranssp27.shp', 'bytes')
        atlas_shared.local_path(config, 'humtranssp27.shp', required=True,
                                client=self.s3, bucket=BUCKET)

    def test_missing_optional_file_returns_its_path(self):
        config = self.config()
        path = atlas_shared.local_path(config, 'camera.png', client=self.s3, bucket=BUCKET)
        # The icon call sites test .exists() and fall back to templates/icons.
        self.assertFalse(path.exists())

    def test_missing_required_file_raises(self):
        config = self.config()
        with self.assertRaises(FileNotFoundError):
            atlas_shared.local_path(config, 'gone.gpkg', required=True,
                                    client=self.s3, bucket=BUCKET)


class TestNeverWritesOutsideTheWorkspace(SharedTestCase):
    def test_box_layout_is_left_untouched(self):
        # On the box, staging/local resolves to /root/data itself. Fetching
        # there would overwrite the shared data every atlas reads.
        outside = Path(self.tmp.name) / 'root-data'
        outside.mkdir()
        (outside / 'hillshade.tiff').write_text('the real one')
        config = self.config(link=False)
        staging = self.root / 'kennedy' / 'staging'
        (staging / 'local').symlink_to(outside, target_is_directory=True)

        path = atlas_shared.local_path(config, 'hillshade.tiff', client=Raiser(), bucket=BUCKET)

        self.assertEqual(path.read_text(), 'the real one')

    def test_local_pointing_at_the_data_root_itself_is_left_alone(self):
        # What the older suites produce by patching versioning.atlas_path to
        # return the data root, and what a half-configured workspace would do.
        # Only the cache this module manages is ever written into.
        config = self.config(link=False)
        (self.root).mkdir(parents=True, exist_ok=True)
        (self.root / 'hillshade.tiff').write_text('not ours either')
        (self.root / 'kennedy' / 'staging' / 'local').symlink_to(
            self.root, target_is_directory=True)

        path = atlas_shared.local_path(config, 'hillshade.tiff', client=Raiser(), bucket=BUCKET)

        self.assertEqual(path.read_text(), 'not ours either')


if __name__ == '__main__':
    unittest.main()
