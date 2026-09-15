#!/usr/bin/env python3
"""Race and wipe rehearsal for the session model, against real S3 (#159 task 9).

`session_smoke.py` settles the single-threaded S3 semantics. This settles the
two questions the unit tests structurally cannot:

  * **Races.** The fake is a dict behind a single thread. Whether the lease
    actually excludes a second worker depends on S3's conditional PUT under
    genuine concurrency, and on the heartbeat renewing before expiry while a
    second worker is hammering the same key.
  * **Wipes.** The failure everyone is actually afraid of is not a crash, it is
    a *successful* run that deleted things. Write-back then does exactly what
    it is told and the deletion reaches S3. The answer is meant to be bucket
    versioning — so this proves the objects are recoverable rather than
    assuming it, on the real buckets, where the versioning configuration is.

It also runs a complete publish end to end, which is the one path the fake
covers structurally but not against the real object store.

Safe to run: it works on a throwaway atlas prefix of its own, never touches a
real atlas, and removes every key it created. It refuses production-looking
buckets unless --force is given.

    ATLAS_PRIVATE_BUCKET=scs-atlas-private-staging \
    ATLAS_OUTLETS_BUCKET=scs-atlas-outlets-staging \
    python3 scripts/session_race.py
"""

import argparse
import json
import shutil
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "python"))

import atlas_catalog
import atlas_lock
import atlas_session
import atlas_store
import atlas_workspace as ws
import versioning

CONFIG_REL = 'atlas_config.json'
LAYER_REL = 'layers/roads/roads.geojson'
OUTLET_REL = 'outlets/webmap/index.html'

LAYER_BODY = '{"type": "FeatureCollection", "features": ["road"]}'
OUTLET_BODY = '<html><body>webmap</body></html>'

BBOX = {'north': 39.5, 'south': 39.4, 'east': -123.7, 'west': -123.8}


class Rehearsal:
    def __init__(self, client, private, outlets, atlas, root, cdn):
        self.client = client
        self.bucket = private
        self.outlets_bucket = outlets
        self.atlas = atlas
        self.root = root
        self.cdn = cdn
        self.failures = []
        self.notes = []

    # -- helpers ----------------------------------------------------------
    def key(self, rel):
        return ws.staging_prefix(self.atlas) + rel

    def local(self, rel):
        return ws.staging_dir(self.root, self.atlas) / rel

    def exists(self, bucket, key):
        try:
            self.client.head_object(Bucket=bucket, Key=key)
        except Exception as exc:
            if atlas_store.s3_error_code(exc) in atlas_store.MISSING_ERROR_CODES:
                return False
            raise
        return True

    def session(self, purpose, **kwargs):
        return atlas_session.open_session(
            self.atlas, purpose=purpose, client=self.client, bucket=self.bucket,
            workspace_root=self.root, **kwargs)

    def config(self):
        return {
            'name': self.atlas,
            'data_root': str(self.root),
            'dataswale': {'versions': [], 'bbox': BBOX,
                          'layers': [{'name': 'roads', 'access': ['public']}]},
            'assets': {
                'webmap': {'type': 'outlet', 'access': ['public'],
                           'in_layers': ['roads'],
                           'config': {'fetch_type': 'webmap'}},
            },
            'cloud': {'outlets': ['webmap'],
                      'outlets_bucket': self.outlets_bucket,
                      'private_bucket': self.bucket,
                      'public_base_url': self.cdn,
                      # A throwaway prefix nobody reads is not worth an
                      # invalidation, and CreateInvalidation is rate-limited.
                      'invalidate': False},
        }

    def run(self, name, fn):
        try:
            fn()
        except Exception as exc:
            self.failures.append(name)
            print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"  ok    {name}")

    def note(self, text):
        self.notes.append(text)
        print(f"        note: {text}")

    # -- setup and teardown -----------------------------------------------
    def seed(self):
        for rel, body in ((CONFIG_REL, json.dumps(self.config())),
                          (LAYER_REL, LAYER_BODY),
                          (OUTLET_REL, OUTLET_BODY)):
            self.client.put_object(Bucket=self.bucket, Key=self.key(rel),
                                   Body=body.encode('utf-8'),
                                   ContentType=atlas_store.content_type_for(rel))

    def teardown(self):
        removed = 0
        for bucket in {self.bucket, self.outlets_bucket}:
            keys = atlas_store.list_keys(bucket, f"{self.atlas}/", client=self.client)
            removed += atlas_store.delete_keys(bucket, keys, client=self.client)
        return removed

    # -- races -------------------------------------------------------------
    def check_only_one_of_four_workers_gets_the_lease(self):
        """The lock's whole job, under genuine concurrency.

        Nested `open_session` calls on one thread prove the code path; they do
        not prove that S3's conditional PUT excludes a second *worker*. Four
        threads starting together do.
        """
        start = threading.Barrier(4)
        won, lost, errors = [], [], []

        def worker(n):
            start.wait()
            try:
                with self.session(f'race worker {n}', wait=0, heartbeat_interval=0):
                    won.append(n)
                    time.sleep(0.5)
            except atlas_lock.AtlasLocked:
                lost.append(n)
            except Exception as exc:
                errors.append(f"{n}: {type(exc).__name__}: {exc}")

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"unexpected failures: {errors}"
        assert len(won) == 1, f"{len(won)} workers held the lease at once: {won}"
        assert len(lost) == 3, f"expected 3 refusals, got {lost}"

    def check_a_waiting_worker_acquires_after_release(self):
        """`wait` is what makes a queued job eventually run rather than fail."""
        released = threading.Event()
        acquired = []

        def holder():
            with self.session('race holder', wait=0, heartbeat_interval=0):
                time.sleep(1.5)
            released.set()

        def waiter():
            time.sleep(0.2)
            with self.session('race waiter', wait=20, heartbeat_interval=0):
                acquired.append(released.is_set())

        threads = [threading.Thread(target=holder), threading.Thread(target=waiter)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert acquired == [True], \
            f"waiter did not acquire after the holder released: {acquired}"

    def check_the_heartbeat_holds_a_lease_past_its_ttl(self):
        """A five-minute render must not lose the atlas to a waiter at minute
        two. The heartbeat is the only thing standing between a long
        materialize and someone else taking over mid-run."""
        ttl = 6.0
        with self.session('race long runner', ttl=ttl, heartbeat_interval=1.5) as s:
            time.sleep(ttl * 2)
            # Still ours: a renew that raises here means the heartbeat failed.
            atlas_lock.renew(self.client, s.lease)

    def check_a_second_worker_is_refused_for_the_whole_long_run(self):
        """The other half of the same claim: while the heartbeat holds it, a
        second worker keeps being refused rather than eventually winning.

        The hammer is stopped **and joined inside the holder's session**. Doing
        either after it is the difference between this check and a broken one:
        the holder's exit does the write-back and only then releases, so a
        hammer still looping at that point can acquire legitimately — the
        atlas really is free — and the check would report a mid-run takeover
        that never happened. That misfire cost a round trip to the box to
        diagnose, which is also why the failure message below reports offsets
        into the hold rather than bare epochs.
        """
        hold = 8.0
        refusals, wins, errors = [], [], []
        done = threading.Event()
        started = time.time()

        def hammer():
            while not done.is_set():
                try:
                    with self.session('race hammer', wait=0, heartbeat_interval=0):
                        wins.append(time.time() - started)
                except atlas_lock.AtlasLocked:
                    refusals.append(time.time() - started)
                except Exception as exc:
                    errors.append(f"{type(exc).__name__}: {exc}")
                    return
                time.sleep(0.3)

        thread = threading.Thread(target=hammer)
        with self.session('race long holder', ttl=6.0, heartbeat_interval=1.5):
            started = time.time()
            thread.start()
            time.sleep(hold)
            done.set()
            thread.join()

        assert not errors, f"unexpected failures: {errors}"
        assert not wins, (
            f"a second worker took the atlas {[f'{w:.2f}s' for w in wins]} into "
            f"a {hold:.0f}s hold whose lease TTL is 6s — the heartbeat let the "
            f"lease lapse")
        assert len(refusals) > 5, f"hammer barely ran ({len(refusals)} attempts)"
        assert max(refusals) > 6.0, (
            f"the hammer stopped attempting {max(refusals):.2f}s in, before the "
            f"6s TTL — this check proved nothing about renewal")

    def check_a_delta_written_during_a_session_is_not_lost(self):
        """`delta_upload` deliberately writes without the lock, so an edit
        never queues behind a five-minute render. The risk that buys is that
        the running session's write-back could delete a delta it never saw —
        it is not in the session's manifest, so plan_writeback must leave it
        alone.
        """
        rel = ('deltas/roads/'
               f"race__{time.strftime('%Y%m%d_%H%M%S')}__create.geojson")
        with self.session('race session that is not looking', heartbeat_interval=0):
            ws.put_staging_object(self.client, self.bucket, self.atlas, rel,
                                  b'{"type":"FeatureCollection","features":[]}')

        assert self.exists(self.bucket, self.key(rel)), \
            "a delta written mid-session was deleted by its write-back"

        # And the next session picks it up as pending, so it still applies.
        with self.session('race check pending', heartbeat_interval=0):
            assert self.local(rel).is_file(), "delta did not hydrate into the next session"
            self.local(rel).unlink()

    # -- wipes -------------------------------------------------------------
    def check_a_failed_session_discards_a_wipe(self):
        """Failure = discard. Nothing is written back, and the next hydrate
        resets the workspace from S3 — which is why an S3 outage cannot bury
        the original exception under a second one."""
        class Boom(Exception):
            pass

        try:
            with self.session('wipe then fail', heartbeat_interval=0):
                self.local(LAYER_REL).unlink()
                raise Boom('the run failed after wiping')
        except Boom:
            pass

        assert self.exists(self.bucket, self.key(LAYER_REL)), \
            "a failed session's deletion reached S3"

        with self.session('wipe recovery', heartbeat_interval=0):
            assert self.local(LAYER_REL).read_text() == LAYER_BODY, \
                "the next hydrate did not restore the wiped layer"

    def check_a_successful_wipe_reaches_s3_and_is_recoverable(self):
        """The failure people are actually afraid of: not a crash, a
        *successful* run that deleted things. Write-back then does exactly what
        it was told. Bucket versioning is the answer, so prove the object is
        recoverable rather than assuming it."""
        with self.session('wipe and succeed', heartbeat_interval=0):
            self.local(LAYER_REL).unlink()

        assert not self.exists(self.bucket, self.key(LAYER_REL)), \
            "write-back did not propagate the deletion (expected: it should)"

        versions = self.client.list_object_versions(
            Bucket=self.bucket, Prefix=self.key(LAYER_REL))
        if not versions.get('Versions'):
            raise AssertionError(
                f"s3://{self.bucket} keeps no object versions, so a wiped layer "
                f"is GONE. Enable bucket versioning before the cutover.")

        newest = max(versions['Versions'], key=lambda v: v['LastModified'])
        body = self.client.get_object(Bucket=self.bucket, Key=self.key(LAYER_REL),
                                      VersionId=newest['VersionId'])['Body'].read()
        assert body.decode() == LAYER_BODY, "the recovered version is not the layer"

        markers = versions.get('DeleteMarkers') or []
        assert markers, "deletion left no delete marker to undo"

        # Undo it the way an operator would, then confirm the atlas is whole.
        self.client.delete_object(Bucket=self.bucket, Key=self.key(LAYER_REL),
                                  VersionId=markers[0]['VersionId'])
        with self.session('wipe restored', heartbeat_interval=0):
            assert self.local(LAYER_REL).read_text() == LAYER_BODY, \
                "restoring the version did not bring the layer back"

    def check_the_delta_archive_survives_a_local_wipe(self):
        """`plan_writeback` never deletes from the archive. It is the only
        record of applied edits, and nothing a run does should be able to erase
        it by way of a write-back."""
        rel = 'deltas/roads/work/archived__20260101_000000__create.geojson'
        self.client.put_object(Bucket=self.bucket, Key=self.key(rel), Body=b'{}')

        with self.session('wipe the archive', heartbeat_interval=0):
            path = self.local(rel)
            if path.exists():
                path.unlink()

        assert self.exists(self.bucket, self.key(rel)), \
            "a write-back deleted an archived delta"

    # -- publish -----------------------------------------------------------
    def check_publish_end_to_end(self):
        """The whole task-6 path against the real object store: catalog,
        immutable layer keys, the private outlet archive, documents, and the
        pointer."""
        with self.session('publish', heartbeat_interval=0) as s:
            result = versioning.publish_new_version(s.config, version='r1',
                                                    client=self.client)
        assert result['catalog']['written_layers'] == ['roads'], result
        assert 'webmap' in result['catalog']['written_outlets'], result

        for bucket, key, what in (
                (self.outlets_bucket, f'{self.atlas}/layers/roads/r1/roads.geojson', 'layer object'),
                (self.bucket, f'{self.atlas}/outlets/webmap/r1/index.html', 'outlet archive'),
                (self.outlets_bucket, f'{self.atlas}/catalog/catalog.json', 'root catalog'),
                (self.outlets_bucket, f'{self.atlas}/catalog/versions/r1/catalog.json', 'version catalog'),
                (self.outlets_bucket, f'{self.atlas}/current/outlets/webmap/index.html', 'served outlet'),
                (self.outlets_bucket, f'{self.atlas}/current.json', 'pointer')):
            assert self.exists(bucket, key), f"{what} missing at s3://{bucket}/{key}"

    def check_a_second_publish_with_no_edits_writes_no_objects(self):
        """The reuse claim, measured on the real bucket rather than a fake."""
        before = set(atlas_store.list_keys(self.outlets_bucket,
                                           f'{self.atlas}/layers/', client=self.client))
        with self.session('publish again', heartbeat_interval=0) as s:
            result = versioning.publish_new_version(s.config, version='r2',
                                                    client=self.client)
        after = set(atlas_store.list_keys(self.outlets_bucket,
                                          f'{self.atlas}/layers/', client=self.client))

        assert result['catalog']['reused_layers'] == ['roads'], result
        assert after == before, f"reuse still wrote layer objects: {sorted(after - before)}"

    def check_the_version_list_is_rebuildable_from_a_listing(self):
        """The root catalog is the one mutable document, so it must never be
        the only record of what exists. This is the claim that makes it
        acceptable for a publish to rewrite it every time."""
        root_key = f'{self.atlas}/catalog/catalog.json'
        root = atlas_store.get_json(self.outlets_bucket, root_key, client=self.client)
        listed = sorted(atlas_catalog.known_versions(root))
        assert listed == ['r1', 'r2'], f"root catalog names {listed}"

        self.client.delete_object(Bucket=self.outlets_bucket, Key=root_key)
        keys = atlas_store.list_keys(self.outlets_bucket,
                                     f'{self.atlas}/catalog/versions/',
                                     client=self.client)
        recovered = sorted({k.split('/')[-2] for k in keys})
        assert recovered == listed, \
            f"listing recovered {recovered}, catalog had {listed}"

    def check_two_publishes_cannot_overlap(self):
        """A publish holds the lease for its whole run, so a second one is
        refused rather than interleaving two versions into one catalog."""
        errors, refused = [], []

        def second():
            time.sleep(0.2)
            try:
                with self.session('publish contender', wait=0, heartbeat_interval=0):
                    errors.append('contender acquired the lease during a publish')
            except atlas_lock.AtlasLocked:
                refused.append(True)
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")

        # Joined inside the session for the same reason as the long-run check:
        # a contender still running when the holder releases would acquire
        # correctly, and the check would read that as an overlap.
        thread = threading.Thread(target=second)
        with self.session('publish holder', heartbeat_interval=0):
            thread.start()
            time.sleep(1.0)
            thread.join()

        assert not errors, errors
        assert refused, "the contender was never refused"

    def check_lock_is_free_at_the_end(self):
        state = atlas_lock.describe(self.client, self.bucket, self.atlas)
        assert state is None, f"lock left behind: {state}"

    CHECKS = ('only_one_of_four_workers_gets_the_lease',
              'a_waiting_worker_acquires_after_release',
              'the_heartbeat_holds_a_lease_past_its_ttl',
              'a_second_worker_is_refused_for_the_whole_long_run',
              'a_delta_written_during_a_session_is_not_lost',
              'a_failed_session_discards_a_wipe',
              'a_successful_wipe_reaches_s3_and_is_recoverable',
              'the_delta_archive_survives_a_local_wipe',
              'publish_end_to_end',
              'a_second_publish_with_no_edits_writes_no_objects',
              'the_version_list_is_rebuildable_from_a_listing',
              'two_publishes_cannot_overlap',
              'lock_is_free_at_the_end')

    def main(self):
        print(f"race/wipe rehearsal: s3://{self.bucket}/{self.atlas}/ "
              f"+ s3://{self.outlets_bucket}/{self.atlas}/")
        print(f"workspace={self.root}")
        self.seed()
        try:
            for name in self.CHECKS:
                self.run(name, getattr(self, f"check_{name}"))
        finally:
            removed = self.teardown()
            print(f"cleaned up {removed} object(s) under {self.atlas}/")
        if self.failures:
            print(f"\nFAILED: {', '.join(self.failures)}")
            return 1
        print(f"\nall {len(self.CHECKS)} checks passed")
        return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--private-bucket', default=None)
    parser.add_argument('--outlets-bucket', default=None)
    parser.add_argument('--atlas', default=None,
                        help='atlas prefix to use (default: a random one)')
    parser.add_argument('--workspace', default=None)
    parser.add_argument('--keep', action='store_true', help='keep the workspace directory')
    parser.add_argument('--force', action='store_true',
                        help='allow buckets whose names look like production')
    args = parser.parse_args(argv)

    settings = atlas_store.cloud_settings({})
    private = args.private_bucket or settings['private_bucket']
    outlets = args.outlets_bucket or settings['outlets_bucket']
    if ('prod' in private or 'prod' in outlets) and not args.force:
        parser.error(
            f"{private} / {outlets} look like production. This script deletes "
            f"objects and exercises wipes — point it at the staging substrate "
            f"(ATLAS_PRIVATE_BUCKET=scs-atlas-private-staging "
            f"ATLAS_OUTLETS_BUCKET=scs-atlas-outlets-staging) or pass --force.")

    atlas = args.atlas or f"racetest-{uuid.uuid4().hex[:8]}"
    root = (Path(args.workspace) if args.workspace
            else Path(tempfile.mkdtemp(prefix='atlas-race-')))
    try:
        return Rehearsal(atlas_store._s3(), private, outlets, atlas, root,
                         settings['public_base_url']).main()
    finally:
        if args.workspace is None and not args.keep:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
