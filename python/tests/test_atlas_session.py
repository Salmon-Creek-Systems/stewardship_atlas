"""Tests for atlas_session — lock → hydrate → run → write back → release.

End to end against the in-memory S3 fake and a real temporary workspace. These
are the behaviours the Step 3 rehearsal depends on, so they are exercised
through the public context manager rather than piece by piece.
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import atlas_lock
import atlas_session
import atlas_workspace as ws
from fake_s3 import FakeClientError, FakeS3

BUCKET = 'scs-atlas-private-test'
ATLAS = 'kennedy'
PREFIX = 'kennedy/staging/'

ROADS = 'layers/roads/roads.geojson'
CREEKS = 'layers/creeks/creeks.geojson'
PENDING = 'deltas/roads/assetless__20260911_120000__create.geojson'
CONSUMED = 'deltas/roads/work/assetless__20260911_120000__create.geojson'
ARCHIVED = 'deltas/roads/work/assetless__20260901_120000__create.geojson'

SEED = {
    'atlas_config.json': json.dumps({'name': ATLAS, 'data_root': '/root/swales_dev'}),
    ROADS: '{"features": ["road"]}',
    CREEKS: '{"features": ["creek"]}',
    PENDING: '{"features": ["new road"]}',
    ARCHIVED: '{"features": ["old edit"]}',
    'outlets/runbook/old.pdf': 'pdf',
}


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class SessionTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'workspace'
        self.s3 = FakeS3()
        for rel, body in SEED.items():
            self.s3.put_object(Bucket=BUCKET, Key=PREFIX + rel, Body=body)

    def tearDown(self):
        self.tmp.cleanup()

    def session(self, s3=None, **kwargs):
        kwargs.setdefault('heartbeat_interval', 0)
        return atlas_session.open_session(ATLAS, purpose='test', client=s3 or self.s3,
                                          bucket=BUCKET, workspace_root=self.root, **kwargs)

    def remote(self, rel):
        obj = self.s3.objects.get((BUCKET, PREFIX + rel))
        return None if obj is None else obj['body'].decode()

    def local(self, rel):
        return ws.staging_dir(self.root, ATLAS) / rel

    def shared(self, cls):
        """A FakeS3 subclass instance backed by this test's object store."""
        client = cls()
        client.objects = self.s3.objects
        return client

    def lock_holder(self):
        return atlas_lock.describe(self.s3, BUCKET, ATLAS)

    def marker(self):
        return atlas_session.marker_path(self.root, ATLAS)


class TestHydrate(SessionTestCase):
    def test_workspace_holds_staging_and_config_points_at_it(self):
        with self.session() as session:
            self.assertEqual(session.config['data_root'], str(self.root))
            self.assertEqual(self.local(ROADS).read_text(), SEED[ROADS])
            self.assertTrue(self.local(PENDING).is_file())
            self.assertFalse(self.local(ARCHIVED).exists())
            self.assertEqual(self.lock_holder()['purpose'], 'test')
            self.assertEqual(session.hydrated['download'], 5)
        self.assertIsNone(self.lock_holder())

    def test_paths_built_like_atlas_path_resolve_into_the_workspace(self):
        # versioning.atlas_path: Path(data_root) / name / version / local_path
        with self.session() as session:
            c = session.config
            self.assertTrue((Path(c['data_root']) / c['name'] / 'staging' / ROADS).is_file())

    def test_partial_downloads_never_land_inside_staging(self):
        with self.session() as session:
            self.assertEqual(sorted(ws.snapshot_local(session.staging_dir)),
                             sorted(rel for rel in SEED if rel != ARCHIVED))

    def test_unseeded_atlas_is_refused_and_left_unlocked(self):
        self.s3.objects.clear()
        with self.assertRaises(atlas_session.AtlasNotSeeded):
            with self.session():
                self.fail('body must not run')
        self.assertIsNone(self.lock_holder())

    def test_new_atlas_can_start_without_a_config(self):
        self.s3.objects.clear()
        with self.session(require_config=False) as session:
            self.assertIsNone(session.config)
            session.staging_dir.mkdir(parents=True, exist_ok=True)
            (session.staging_dir / 'atlas_config.json').write_text(json.dumps({'name': ATLAS}))
        self.assertEqual(json.loads(self.remote('atlas_config.json')), {'name': ATLAS})

    def test_config_naming_another_atlas_is_refused(self):
        self.s3.put_object(Bucket=BUCKET, Key=PREFIX + 'atlas_config.json',
                           Body=json.dumps({'name': 'Kennedy'}))
        with self.assertRaises(ValueError):
            with self.session():
                self.fail('body must not run')
        self.assertIsNone(self.lock_holder())

    def test_workspace_root_is_required(self):
        with mock.patch.dict(os.environ):
            os.environ.pop(atlas_session.WORKSPACE_ROOT_ENV, None)
            with self.assertRaises(ValueError):
                with atlas_session.open_session(ATLAS, client=self.s3, bucket=BUCKET,
                                                heartbeat_interval=0):
                    self.fail('body must not run')
        self.assertIsNone(self.lock_holder())


class TestWriteBack(SessionTestCase):
    def test_changes_reach_s3(self):
        with self.session() as session:
            staging = session.staging_dir
            (staging / ROADS).write_text('{"features": ["road", "new road"]}')
            (staging / 'layers/h3').mkdir()
            (staging / 'layers/h3/h3.geojson').write_text('{"features": []}')
            (staging / 'outlets/runbook/old.pdf').unlink()
            (staging / CONSUMED).parent.mkdir(parents=True, exist_ok=True)
            (staging / PENDING).rename(staging / CONSUMED)

        self.assertEqual(self.remote(ROADS), '{"features": ["road", "new road"]}')
        self.assertEqual(self.remote('layers/h3/h3.geojson'), '{"features": []}')
        self.assertIsNone(self.remote('outlets/runbook/old.pdf'))
        self.assertEqual(self.remote(CONSUMED), SEED[PENDING])
        self.assertIsNone(self.remote(PENDING))
        # Never hydrated, so never at risk.
        self.assertEqual(self.remote(ARCHIVED), SEED[ARCHIVED])
        self.assertEqual(session.written['upload'], 3)
        self.assertEqual(session.written['delete'], 2)
        self.assertFalse(self.marker().exists())
        self.assertIsNone(self.lock_holder())

    def test_the_consumed_delta_is_archived_before_the_pending_copy_is_deleted(self):
        with self.session() as session:
            (session.staging_dir / CONSUMED).parent.mkdir(parents=True, exist_ok=True)
            (session.staging_dir / PENDING).rename(session.staging_dir / CONSUMED)
        calls = [c for c in self.s3.calls if c[1] in (PREFIX + CONSUMED, PREFIX + PENDING)]
        self.assertEqual(calls[-2:], [('put_object', PREFIX + CONSUMED),
                                      ('delete_object', PREFIX + PENDING)])

    def test_warm_session_with_no_changes_transfers_nothing(self):
        with self.session():
            pass
        self.s3.calls.clear()
        with self.session() as session:
            pass
        transfers = [c for c in self.s3.calls
                     if c[1].startswith(PREFIX) and c[0] != 'list_objects_v2']
        self.assertEqual(transfers, [])
        self.assertEqual(session.hydrated['unchanged'], 5)

    def test_rebuilt_but_identical_output_is_not_uploaded(self):
        with self.session() as session:
            path = session.staging_dir / ROADS
            path.write_text(path.read_text())
            os.utime(path, ns=(1, 1))
        self.assertEqual(session.written['upload'], 0)
        self.assertEqual(session.written['restat'], 1)


class TestFailure(SessionTestCase):
    def test_body_failure_writes_nothing_and_the_next_session_resets(self):
        with self.assertRaises(RuntimeError):
            with self.session() as session:
                (session.staging_dir / ROADS).write_text('half-built')
                (session.staging_dir / 'layers/stray.geojson').write_text('stray')
                raise RuntimeError('materializer blew up')
        self.assertEqual(self.remote(ROADS), SEED[ROADS])
        self.assertIsNone(self.remote('layers/stray.geojson'))
        self.assertIsNone(self.lock_holder())

        with self.session():
            self.assertEqual(self.local(ROADS).read_text(), SEED[ROADS])
            self.assertFalse(self.local('layers/stray.geojson').exists())

    def test_locked_atlas_is_refused_before_hydrating(self):
        atlas_lock.acquire(self.s3, BUCKET, ATLAS, owner='someone', purpose='publish')
        with self.assertRaises(atlas_lock.AtlasLocked):
            with self.session():
                self.fail('body must not run')
        self.assertFalse(ws.staging_dir(self.root, ATLAS).exists())
        self.assertEqual(self.lock_holder()['owner'], 'someone')

    def test_lost_lease_writes_nothing(self):
        clock = Clock()
        with self.assertRaises(atlas_lock.LeaseLost):
            with self.assertLogs('atlas_lock', level='WARNING'):
                with self.session(clock=clock, ttl=300) as session:
                    (session.staging_dir / ROADS).write_text('stale result, written too late')
                    clock.now += 301
                    atlas_lock.acquire(self.s3, BUCKET, ATLAS, owner='other', clock=clock)
        self.assertEqual(self.remote(ROADS), SEED[ROADS])
        self.assertEqual(self.lock_holder()['owner'], 'other')

    def test_remote_change_under_the_lock_is_a_conflict_not_an_overwrite(self):
        with self.assertRaises(ws.WritebackConflict):
            with self.session() as session:
                (session.staging_dir / ROADS).write_text('ours, and longer')
                self.s3.put_object(Bucket=BUCKET, Key=PREFIX + ROADS, Body='theirs')
        self.assertEqual(self.remote(ROADS), 'theirs')
        self.assertTrue(self.marker().exists())
        self.assertIsNone(self.lock_holder())

    def test_interrupted_writeback_is_finished_before_the_next_hydrate(self):
        class Flaky(FakeS3):
            staging_puts = 0

            def put_object(self, Bucket, Key, **kwargs):
                if Key.startswith(PREFIX):
                    self.staging_puts += 1
                    if self.staging_puts == 2:
                        raise FakeClientError('InternalError', 500, 'PutObject')
                return FakeS3.put_object(self, Bucket=Bucket, Key=Key, **kwargs)

        new_roads = '{"features": ["road", "new road"]}'
        new_creeks = '{"features": ["creek", "new creek"]}'
        with self.assertRaises(FakeClientError):
            with self.session(s3=self.shared(Flaky)) as session:
                staging = session.staging_dir
                (staging / CREEKS).write_text(new_creeks)
                (staging / ROADS).write_text(new_roads)
                (staging / CONSUMED).parent.mkdir(parents=True, exist_ok=True)
                (staging / PENDING).rename(staging / CONSUMED)

        self.assertTrue(self.marker().exists())
        # The delete was never reached: the delta is still pending, not lost.
        self.assertEqual(self.remote(PENDING), SEED[PENDING])

        with self.assertLogs('atlas_session', level='WARNING'):
            with self.session():
                # Finished rather than reset: the run's output survived.
                self.assertEqual(self.local(ROADS).read_text(), new_roads)
        self.assertFalse(self.marker().exists())
        self.assertEqual(self.remote(ROADS), new_roads)
        self.assertEqual(self.remote(CREEKS), new_creeks)
        self.assertEqual(self.remote(CONSUMED), SEED[PENDING])
        self.assertIsNone(self.remote(PENDING))

    def test_resuming_recognises_its_own_upload(self):
        # A crash between S3 accepting an upload and the manifest recording it.
        with self.session():
            pass
        path = self.local(ROADS)
        path.write_text('{"features": ["road", "resumed"]}')
        self.s3.put_object(Bucket=BUCKET, Key=PREFIX + ROADS, Body=path.read_bytes(),
                           Metadata={'sha256': ws.sha256_file(path)})
        self.marker().write_text('interrupted')

        with self.assertLogs('atlas_session', level='WARNING'):
            with self.session():
                pass
        self.assertEqual(self.remote(ROADS), '{"features": ["road", "resumed"]}')
        self.assertFalse(self.marker().exists())


class TestHeartbeat(SessionTestCase):
    def test_lease_outlives_its_ttl_while_the_body_runs(self):
        with self.session(ttl=0.3, heartbeat_interval=0.05):
            time.sleep(0.5)
            with self.assertRaises(atlas_lock.AtlasLocked):
                atlas_lock.acquire(self.s3, BUCKET, ATLAS, owner='other')
        self.assertIsNone(self.lock_holder())


class TestSharedData(SessionTestCase):
    def test_local_is_linked_to_the_shared_cache(self):
        with self.session() as session:
            link = session.staging_dir / 'local'
            self.assertTrue(link.is_symlink())
            self.assertEqual(os.path.realpath(link),
                             str((self.root / '.shared').resolve()))

    def test_writes_through_local_are_not_written_back(self):
        # outlets.py copies CSS and generated help HTML into local/. They are
        # derived from repo templates, regenerated by any materialize, and are
        # not this atlas's data — so they must not land in its staging prefix
        # (#181 covers where they do belong).
        with self.session() as session:
            css = session.staging_dir / 'local' / 'css'
            css.mkdir(parents=True, exist_ok=True)
            (css / 'console.css').write_text('body {}')
            self.assertEqual(session.hydrated['download'], 5)
        self.assertIsNone(self.remote('local/css/console.css'))
        self.assertEqual(session.written['upload'], 0)


if __name__ == '__main__':
    unittest.main()
