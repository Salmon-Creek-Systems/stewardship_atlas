"""Tests for atlas_workspace — the sync planning behind S3-backed sessions.

Pure planners take plain dicts; the one S3 function runs against the in-memory
fake. Nothing here needs boto3 or credentials.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import atlas_workspace as ws
from fake_s3 import FakeS3


def entry(etag, size=10, mtime_ns=1, sha256='sha'):
    return {'etag': etag, 'size': size, 'mtime_ns': mtime_ns, 'sha256': sha256}


def manifest(files):
    return {'format': ws.MANIFEST_FORMAT, 'files': files}


def remote(etag, size=10):
    return {'etag': etag, 'size': size}


def no_hashing(rel):
    raise AssertionError(f"hashed {rel}, whose stat matched the manifest")


class TestLayout(unittest.TestCase):
    def test_staging_prefix(self):
        # Also a guard against a stubbed atlas_store leaking in from another
        # suite: a MagicMock prefix would not render as a plain atlas name.
        self.assertEqual(ws.staging_prefix('kennedy'), 'kennedy/staging/')

    def test_manifest_lives_beside_staging_not_inside(self):
        root = Path('/tmp/ws')
        staging = ws.staging_dir(root, 'kennedy')
        self.assertEqual(staging, root / 'kennedy' / 'staging')
        self.assertNotIn(staging, ws.manifest_path(root, 'kennedy').parents)

    def test_archive_paths(self):
        self.assertTrue(ws.is_archive_path('deltas/roads/work/a__1__create.geojson'))
        self.assertTrue(ws.is_archive_path('deltas/roads/processed/a__1__create.geojson'))
        self.assertFalse(ws.is_archive_path('deltas/roads/a__1__create.geojson'))
        # A layer that happens to be called "work" is not the archive.
        self.assertFalse(ws.is_archive_path('deltas/work/a__1__create.geojson'))
        self.assertFalse(ws.is_archive_path('layers/roads/work/roads.geojson'))
        self.assertFalse(ws.is_archive_path('outlets/runbook/work/page.pdf'))


class TestManifestIO(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'kennedy' / ws.MANIFEST_FILENAME

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip(self):
        m = manifest({'layers/roads/roads.geojson': entry('"a"')})
        ws.save_manifest(self.path, m)
        self.assertEqual(ws.load_manifest(self.path), m)
        self.assertFalse(self.path.with_name(self.path.name + '.tmp').exists())

    def test_missing_reads_as_empty(self):
        self.assertEqual(ws.load_manifest(self.path), ws.empty_manifest())

    def test_corrupt_reads_as_empty(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"format": 1, "files": {')
        self.assertEqual(ws.load_manifest(self.path), ws.empty_manifest())

    def test_other_format_reads_as_empty(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"format": 99, "files": {"x": {}}}')
        self.assertEqual(ws.load_manifest(self.path), ws.empty_manifest())


class TestSnapshotLocal(unittest.TestCase):
    def test_walks_files_and_skips_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / 'kennedy' / 'staging'
            shared = Path(tmp) / 'shared'
            (staging / 'layers' / 'roads').mkdir(parents=True)
            shared.mkdir()
            (shared / 'NHD.gpkg').write_bytes(b'x' * 50)
            (staging / 'layers' / 'roads' / 'roads.geojson').write_bytes(b'12345')
            (staging / 'atlas_config.json').write_text('{}')
            (staging / 'local').symlink_to(shared, target_is_directory=True)
            (staging / 'linked.gpkg').symlink_to(shared / 'NHD.gpkg')

            state = ws.snapshot_local(staging)

            self.assertEqual(sorted(state), ['atlas_config.json', 'layers/roads/roads.geojson'])
            self.assertEqual(state['layers/roads/roads.geojson'][0], 5)

    def test_missing_staging_is_empty(self):
        self.assertEqual(ws.snapshot_local('/nonexistent/staging'), {})


class TestListing(unittest.TestCase):
    def test_strips_prefix_and_skips_markers_and_strangers(self):
        objects = [
            {'Key': 'kennedy/staging/atlas_config.json', 'ETag': '"a"', 'Size': 3},
            {'Key': 'kennedy/staging/layers/', 'ETag': '"d"', 'Size': 0},
            {'Key': 'kennedy/lock', 'ETag': '"l"', 'Size': 9},
        ]
        self.assertEqual(ws.listing_from_objects(objects, 'kennedy/staging/'),
                         {'atlas_config.json': {'etag': '"a"', 'size': 3}})

    def test_list_staging_paginates_and_stays_inside_the_atlas(self):
        s3 = FakeS3(page_size=3)
        for i in range(7):
            s3.put_object(Bucket='b', Key=f'kennedy/staging/layers/l{i}/l{i}.geojson', Body=f'{i}')
        s3.put_object(Bucket='b', Key='kennedy/lock', Body='{}')
        s3.put_object(Bucket='b', Key='kennedy_2/staging/atlas_config.json', Body='{}')

        listing = ws.list_staging(s3, 'b', 'kennedy')

        self.assertEqual(len(listing), 7)
        self.assertEqual(len([c for c in s3.calls if c[0] == 'list_objects_v2']), 3)
        etag = listing['layers/l0/l0.geojson']['etag']
        self.assertTrue(etag.startswith('"') and etag.endswith('"'))

    def test_list_staging_of_an_empty_atlas(self):
        self.assertEqual(ws.list_staging(FakeS3(), 'b', 'nobody'), {})


class TestPlanHydrate(unittest.TestCase):
    def test_fresh_workspace_downloads_everything_but_the_archive(self):
        listing = {
            'atlas_config.json': remote('"c"'),
            'layers/roads/roads.geojson': remote('"r"'),
            'deltas/roads/a__2__create.geojson': remote('"p"'),
            'deltas/roads/work/a__1__create.geojson': remote('"w"'),
        }
        plan = ws.plan_hydrate(listing, ws.empty_manifest(), {})
        self.assertEqual(plan['download'], ['atlas_config.json',
                                            'deltas/roads/a__2__create.geojson',
                                            'layers/roads/roads.geojson'])
        self.assertEqual(plan['remove'], [])
        self.assertEqual(plan['unchanged'], [])

    def test_in_sync_file_is_left_alone(self):
        plan = ws.plan_hydrate({'atlas_config.json': remote('"c"')},
                               manifest({'atlas_config.json': entry('"c"', 10, 7)}),
                               {'atlas_config.json': (10, 7)})
        self.assertEqual(plan, {'download': [], 'remove': [], 'unchanged': ['atlas_config.json']})

    def test_remote_change_is_downloaded(self):
        plan = ws.plan_hydrate({'atlas_config.json': remote('"new"')},
                               manifest({'atlas_config.json': entry('"old"', 10, 7)}),
                               {'atlas_config.json': (10, 7)})
        self.assertEqual(plan['download'], ['atlas_config.json'])

    def test_local_leftover_from_a_failed_run_is_reset(self):
        # Same ETag remotely, but the local file was modified since the last
        # sync — the failed run's change is discarded by downloading again.
        plan = ws.plan_hydrate({'layers/roads/roads.geojson': remote('"r"')},
                               manifest({'layers/roads/roads.geojson': entry('"r"', 10, 7)}),
                               {'layers/roads/roads.geojson': (12, 9)})
        self.assertEqual(plan['download'], ['layers/roads/roads.geojson'])

    def test_wiped_local_file_is_downloaded(self):
        plan = ws.plan_hydrate({'layers/roads/roads.geojson': remote('"r"')},
                               manifest({'layers/roads/roads.geojson': entry('"r"', 10, 7)}),
                               {})
        self.assertEqual(plan['download'], ['layers/roads/roads.geojson'])

    def test_local_file_s3_does_not_hold_is_removed(self):
        local = {
            'deltas/roads/assetless__3__create.geojson': (5, 1),   # failed run's new delta
            'layers/gone/gone.geojson': (5, 1),                    # deleted elsewhere
        }
        plan = ws.plan_hydrate({}, manifest({'layers/gone/gone.geojson': entry('"g"', 5, 1)}), local)
        self.assertEqual(plan['remove'], sorted(local))

    def test_archive_is_kept_if_verified_and_removed_if_not(self):
        listing = {
            'deltas/roads/work/a__1__create.geojson': remote('"w1"'),
            'deltas/roads/work/a__2__create.geojson': remote('"w2"'),
        }
        m = manifest({'deltas/roads/work/a__1__create.geojson': entry('"w1"', 10, 7)})
        local = {
            'deltas/roads/work/a__1__create.geojson': (10, 7),   # verified
            'deltas/roads/work/a__2__create.geojson': (10, 7),   # no manifest entry
            'deltas/roads/work/a__3__create.geojson': (10, 7),   # not in S3 at all
        }
        plan = ws.plan_hydrate(listing, m, local)
        self.assertEqual(plan['unchanged'], ['deltas/roads/work/a__1__create.geojson'])
        self.assertEqual(plan['download'], [])
        self.assertEqual(plan['remove'], ['deltas/roads/work/a__2__create.geojson',
                                          'deltas/roads/work/a__3__create.geojson'])

    def test_wanted_restricts_downloads_and_drops_unverified_files(self):
        listing = {
            'layers/roads/roads.geojson': remote('"r"'),
            'layers/creeks/creeks.geojson': remote('"c"'),
        }
        plan = ws.plan_hydrate(listing, ws.empty_manifest(),
                               {'layers/creeks/creeks.geojson': (3, 3)},
                               wanted=lambda rel: rel.startswith('layers/roads/'))
        self.assertEqual(plan['download'], ['layers/roads/roads.geojson'])
        self.assertEqual(plan['remove'], ['layers/creeks/creeks.geojson'])


class TestPlanWriteback(unittest.TestCase):
    def test_untouched_tree_hashes_nothing_and_writes_nothing(self):
        m = manifest({'atlas_config.json': entry('"c"', 10, 7),
                      'layers/roads/roads.geojson': entry('"r"', 20, 8)})
        local = {'atlas_config.json': (10, 7), 'layers/roads/roads.geojson': (20, 8)}
        plan = ws.plan_writeback(m, local, no_hashing)
        self.assertEqual(plan['upload'], [])
        self.assertEqual(plan['delete'], [])
        self.assertEqual(plan['restat'], [])
        self.assertEqual(plan['unchanged'], sorted(local))

    def test_rewritten_but_identical_file_is_not_uploaded(self):
        m = manifest({'outlets/webmap/index.html': entry('"i"', 10, 7, sha256='same')})
        plan = ws.plan_writeback(m, {'outlets/webmap/index.html': (10, 99)},
                                 lambda rel: 'same')
        self.assertEqual(plan['upload'], [])
        self.assertEqual(plan['restat'], ['outlets/webmap/index.html'])

    def test_changed_file_uploads_conditionally_on_the_hydrated_etag(self):
        m = manifest({'layers/roads/roads.geojson': entry('"r"', 10, 7, sha256='old')})
        plan = ws.plan_writeback(m, {'layers/roads/roads.geojson': (11, 8)}, lambda rel: 'new')
        self.assertEqual(plan['upload'], [{'rel': 'layers/roads/roads.geojson',
                                           'sha256': 'new', 'if_match': '"r"'}])

    def test_new_file_uploads_as_create_only(self):
        plan = ws.plan_writeback(ws.empty_manifest(), {'layers/h3/h3.geojson': (5, 1)},
                                 lambda rel: 'h')
        self.assertEqual(plan['upload'], [{'rel': 'layers/h3/h3.geojson',
                                           'sha256': 'h', 'if_match': None}])

    def test_deleted_file_is_deleted(self):
        m = manifest({'outlets/runbook/old.pdf': entry('"o"')})
        plan = ws.plan_writeback(m, {}, no_hashing)
        self.assertEqual(plan['delete'], [{'rel': 'outlets/runbook/old.pdf', 'etag': '"o"'}])

    def test_consumed_delta_is_an_archive_upload_plus_a_pending_delete(self):
        pending = 'deltas/roads/assetless__20260911_120000__create.geojson'
        archived = 'deltas/roads/work/assetless__20260911_120000__create.geojson'
        m = manifest({pending: entry('"p"', 10, 7, sha256='d'),
                      'layers/roads/roads.geojson': entry('"r"', 100, 7, sha256='before')})
        local = {archived: (10, 9), 'layers/roads/roads.geojson': (110, 9)}
        hashes = {archived: 'd', 'layers/roads/roads.geojson': 'after'}

        plan = ws.plan_writeback(m, local, hashes.__getitem__)

        self.assertEqual([u['rel'] for u in plan['upload']],
                         [archived, 'layers/roads/roads.geojson'])
        self.assertEqual(plan['delete'], [{'rel': pending, 'etag': '"p"'}])

    def test_archive_missing_locally_is_never_deleted(self):
        m = manifest({'deltas/roads/work/a__1__create.geojson': entry('"w"')})
        with self.assertLogs('atlas_workspace', level='WARNING'):
            plan = ws.plan_writeback(m, {}, no_hashing)
        self.assertEqual(plan['delete'], [])


if __name__ == '__main__':
    unittest.main()
