"""Tests for atlas_lock — the per-atlas S3 lease.

Runs against the in-memory fake, which fails conditional PUTs the way S3 does.
Time is an injected clock, so nothing here sleeps.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import atlas_lock as lock
import atlas_workspace
from fake_s3 import FakeClientError, FakeS3

BUCKET = 'scs-atlas-private-test'
ATLAS = 'kennedy'


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class LockTestCase(unittest.TestCase):
    def setUp(self):
        self.s3 = FakeS3()
        self.clock = Clock()

    def take(self, owner, s3=None, **kwargs):
        kwargs.setdefault('clock', self.clock)
        return lock.acquire(s3 or self.s3, BUCKET, ATLAS, owner=owner, **kwargs)

    def holder(self):
        return lock.describe(self.s3, BUCKET, ATLAS)


class TestKey(unittest.TestCase):
    def test_lock_is_outside_the_staging_tree(self):
        # A hydrate lists staging/; the lock must never show up as atlas data.
        self.assertEqual(lock.lock_key(ATLAS), 'kennedy/lock')
        self.assertFalse(lock.lock_key(ATLAS).startswith(atlas_workspace.staging_prefix(ATLAS)))


class TestAcquire(LockTestCase):
    def test_free_lock_is_taken(self):
        lease = self.take('a', purpose='refresh_layer roads', ttl=300)
        self.assertEqual(lease.expires_at, 1300.0)
        holder = self.holder()
        self.assertEqual(holder['owner'], 'a')
        self.assertEqual(holder['purpose'], 'refresh_layer roads')
        self.assertEqual(holder['expires_at'], 1300.0)

    def test_held_lock_is_refused_with_the_holder(self):
        self.take('a', purpose='publish')
        with self.assertRaises(lock.AtlasLocked) as ctx:
            self.take('b')
        self.assertEqual(ctx.exception.holder['owner'], 'a')
        self.assertIn('publish', str(ctx.exception))

    def test_expired_lease_is_taken_over(self):
        self.take('a', ttl=300)
        self.clock.now += 301
        with self.assertLogs('atlas_lock', level='WARNING'):
            lease = self.take('b')
        self.assertEqual(lease.owner, 'b')
        self.assertEqual(self.holder()['owner'], 'b')

    def test_unreadable_lock_document_cannot_wedge_the_atlas(self):
        self.s3.put_object(Bucket=BUCKET, Key=lock.lock_key(ATLAS), Body=b'not json')
        with self.assertLogs('atlas_lock', level='WARNING'):
            self.assertEqual(self.take('b').owner, 'b')

    def shared(self, cls):
        """An instance of a FakeS3 subclass backed by this test's object store.

        Assigned after construction — FakeS3.__init__ gives every instance a
        fresh store, and a race against a separate store is no race at all.
        """
        client = cls()
        client.objects = self.s3.objects
        return client

    def test_racing_takeovers_of_one_stale_lease_have_one_winner(self):
        class Competitor(FakeS3):
            """Another process takes the stale lease between our GET and our PUT."""
            raced = False

            def get_object(self, Bucket, Key):
                response = FakeS3.get_object(self, Bucket, Key)
                if not self.raced:
                    self.raced = True
                    FakeS3.put_object(self, Bucket=Bucket, Key=Key, IfMatch=response['ETag'],
                                      Body=json.dumps({'owner': 'c', 'expires_at': 1e12}))
                return response

        self.take('a', ttl=300)
        self.clock.now += 301
        with self.assertRaises(lock.AtlasLocked):
            self.take('b', s3=self.shared(Competitor))
        self.assertEqual(self.holder()['owner'], 'c')

    def test_lock_released_mid_attempt_is_taken(self):
        class Releaser(FakeS3):
            """The holder releases between our failed PUT and our GET."""
            released = False

            def get_object(self, Bucket, Key):
                if not self.released:
                    self.released = True
                    self.delete_object(Bucket=Bucket, Key=Key)
                return FakeS3.get_object(self, Bucket, Key)

        self.take('a')
        self.assertEqual(self.take('b', s3=self.shared(Releaser)).owner, 'b')

    def test_waiting_acquires_once_the_holder_releases(self):
        held = self.take('a')
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            lock.release(self.s3, held, clock=self.clock)
            self.clock.now += seconds

        lease = self.take('b', wait=10, poll=1, sleep=sleep)
        self.assertEqual(lease.owner, 'b')
        self.assertEqual(sleeps, [1])

    def test_waiting_gives_up_at_the_deadline(self):
        self.take('a', ttl=300)
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            self.clock.now += seconds

        with self.assertRaises(lock.AtlasLocked):
            self.take('b', wait=3, poll=1, sleep=sleep)
        self.assertEqual(sleeps, [1, 1, 1])

    def test_errors_other_than_contention_propagate(self):
        class Denied(FakeS3):
            def put_object(self, **kwargs):
                raise FakeClientError('AccessDenied', 403, 'PutObject')

        with self.assertRaises(FakeClientError):
            self.take('a', s3=Denied())


class TestRenew(LockTestCase):
    def test_renew_extends_the_lease(self):
        lease = self.take('a', ttl=300)
        first_etag = lease.etag
        self.clock.now += 200
        lock.renew(self.s3, lease, clock=self.clock)
        self.assertEqual(lease.expires_at, 1500.0)
        self.assertNotEqual(lease.etag, first_etag)
        # Past the original expiry, but renewed: still refused.
        self.clock.now += 150
        with self.assertRaises(lock.AtlasLocked):
            self.take('b')

    def test_renew_past_expiry_succeeds_if_nobody_took_over(self):
        lease = self.take('a', ttl=300)
        self.clock.now += 1000
        lock.renew(self.s3, lease, clock=self.clock)
        self.assertEqual(self.holder()['owner'], 'a')

    def test_renew_after_takeover_is_lease_lost(self):
        lease = self.take('a', ttl=300)
        self.clock.now += 301
        with self.assertLogs('atlas_lock', level='WARNING'):
            self.take('b')
        with self.assertRaises(lock.LeaseLost):
            lock.renew(self.s3, lease, clock=self.clock)
        self.assertEqual(self.holder()['owner'], 'b')

    def test_renew_after_removal_is_lease_lost(self):
        lease = self.take('a')
        self.s3.delete_object(Bucket=BUCKET, Key=lock.lock_key(ATLAS))
        with self.assertRaises(lock.LeaseLost):
            lock.renew(self.s3, lease, clock=self.clock)


class TestRelease(LockTestCase):
    def test_release_frees_the_atlas(self):
        lease = self.take('a')
        self.assertTrue(lock.release(self.s3, lease, clock=self.clock))
        self.assertIsNone(self.holder())
        self.assertEqual(self.take('b').owner, 'b')

    def test_release_never_removes_someone_elses_lease(self):
        lease = self.take('a', ttl=300)
        self.clock.now += 301
        with self.assertLogs('atlas_lock', level='WARNING'):
            self.take('b')
            self.assertFalse(lock.release(self.s3, lease, clock=self.clock))
        self.assertEqual(self.holder()['owner'], 'b')

    def test_release_of_an_expired_lease_leaves_it_to_expire(self):
        lease = self.take('a', ttl=300)
        self.clock.now += 301
        with self.assertLogs('atlas_lock', level='WARNING'):
            self.assertFalse(lock.release(self.s3, lease, clock=self.clock))
        self.assertEqual(self.holder()['owner'], 'a')

    def test_release_of_a_missing_lock(self):
        lease = self.take('a')
        self.s3.delete_object(Bucket=BUCKET, Key=lock.lock_key(ATLAS))
        with self.assertLogs('atlas_lock', level='WARNING'):
            self.assertFalse(lock.release(self.s3, lease, clock=self.clock))


if __name__ == '__main__':
    unittest.main()
