#!/usr/bin/env python3
"""Exercise the Step 3 session machinery against a real S3 bucket (#159).

Everything under python/tests runs against an in-memory fake, and the recurring
Phase 3 lesson is that a fixture simpler than production in the dimension that
matters is how bugs get through. The dimensions only AWS can settle:

  * the error codes S3 actually returns for a failed conditional write,
  * whether ETags come back quoted, and how object metadata reads back,
  * what HEAD returns for a key that is not there,
  * and whether the box's IAM role may do any of this — the fake has no
    permissions model at all, and the role was deliberately given no delete.

Safe to run: it works on a throwaway atlas prefix of its own, never touches a
real atlas, and removes every key it created. It refuses a production-looking
bucket unless --force is given.

    ATLAS_PRIVATE_BUCKET=scs-atlas-private-staging python3 scripts/session_smoke.py
"""

import argparse
import json
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "python"))

import atlas_lock
import atlas_session
import atlas_store
import atlas_workspace as ws

CONFIG_REL = 'atlas_config.json'
LAYER_REL = 'layers/roads/roads.geojson'
PENDING_REL = 'deltas/roads/assetless__20260101_000000__create.geojson'
CONSUMED_REL = 'deltas/roads/work/assetless__20260101_000000__create.geojson'

LAYER_BODY = '{"type": "FeatureCollection", "features": ["road"]}'
PENDING_BODY = '{"type": "FeatureCollection", "features": ["new road"]}'
EDITED_BODY = '{"type": "FeatureCollection", "features": ["road", "new road"]}'


class Smoke:
    def __init__(self, client, bucket, atlas, root):
        self.client = client
        self.bucket = bucket
        self.atlas = atlas
        self.root = root
        self.failures = []

    # -- helpers ----------------------------------------------------------
    def key(self, rel):
        return ws.staging_prefix(self.atlas) + rel

    def body(self, rel):
        return self.client.get_object(
            Bucket=self.bucket, Key=self.key(rel))['Body'].read().decode()

    def missing(self, rel):
        try:
            self.client.head_object(Bucket=self.bucket, Key=self.key(rel))
        except Exception as exc:
            if atlas_store.s3_error_code(exc) in atlas_store.MISSING_ERROR_CODES:
                return True
            raise
        return False

    def session(self, purpose, **kwargs):
        kwargs.setdefault('heartbeat_interval', 0)
        return atlas_session.open_session(
            self.atlas, purpose=purpose, client=self.client, bucket=self.bucket,
            workspace_root=self.root, **kwargs)

    def local(self, rel):
        return ws.staging_dir(self.root, self.atlas) / rel

    def marker(self):
        return atlas_session.marker_path(self.root, self.atlas)

    def run(self, name, fn):
        try:
            fn()
        except Exception as exc:
            self.failures.append(name)
            print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"  ok    {name}")

    # -- setup and teardown -----------------------------------------------
    def seed(self):
        for rel, body in ((CONFIG_REL, json.dumps({'name': self.atlas, 'data_root': '/unused'})),
                          (LAYER_REL, LAYER_BODY),
                          (PENDING_REL, PENDING_BODY)):
            self.client.put_object(Bucket=self.bucket, Key=self.key(rel),
                                   Body=body.encode('utf-8'),
                                   ContentType=atlas_store.content_type_for(rel))

    def teardown(self):
        keys = atlas_store.list_keys(self.bucket, f"{self.atlas}/", client=self.client)
        atlas_store.delete_keys(self.bucket, keys, client=self.client)
        return len(keys)

    # -- checks ------------------------------------------------------------
    def check_hydrate_and_writeback(self):
        with self.session('smoke: hydrate and write back') as session:
            assert self.local(LAYER_REL).read_text() == LAYER_BODY, "layer not hydrated"
            assert session.config['data_root'] == str(self.root), "data_root not repointed"
            self.local(LAYER_REL).write_text(EDITED_BODY)
            self.local(CONSUMED_REL).parent.mkdir(parents=True, exist_ok=True)
            self.local(PENDING_REL).rename(self.local(CONSUMED_REL))

        assert self.body(LAYER_REL) == EDITED_BODY, "edited layer did not reach S3"
        assert self.body(CONSUMED_REL) == PENDING_BODY, "consumed delta was not archived"
        # The delete the EC2 role was never granted before this branch.
        assert self.missing(PENDING_REL), "pending delta was not deleted"

    def check_etag_and_metadata_round_trip(self):
        manifest = ws.load_manifest(ws.manifest_path(self.root, self.atlas))
        entry = manifest['files'][LAYER_REL]
        assert entry['etag'].startswith('"'), f"ETag not quoted: {entry['etag']}"
        head = self.client.head_object(Bucket=self.bucket, Key=self.key(LAYER_REL))
        assert head['ETag'] == entry['etag'], "ETag from PUT differs from HEAD"
        # Resuming an interrupted write-back depends on reading this back.
        assert (head.get('Metadata') or {}).get('sha256') == entry['sha256'], \
            f"sha256 metadata missing or renamed: {head.get('Metadata')}"

    def check_warm_session_transfers_nothing(self):
        with self.session('smoke: warm') as session:
            pass
        assert session.hydrated['download'] == 0, f"re-downloaded {session.hydrated}"
        assert session.written['upload'] == 0, f"re-uploaded {session.written}"

    def check_lock_contention(self):
        held = atlas_lock.acquire(self.client, self.bucket, self.atlas,
                                  owner='smoke-holder', purpose='smoke: hold')
        try:
            try:
                with self.session('smoke: should not start'):
                    raise AssertionError("session started while the atlas was locked")
            except atlas_lock.AtlasLocked as exc:
                assert exc.holder.get('owner') == 'smoke-holder', f"wrong holder: {exc.holder}"
        finally:
            atlas_lock.release(self.client, held)
        assert atlas_lock.describe(self.client, self.bucket, self.atlas) is None, \
            "lock outlived its release"

    def check_expired_lease_takeover(self):
        first = atlas_lock.acquire(self.client, self.bucket, self.atlas,
                                   owner='smoke-a', purpose='smoke: expires', ttl=1)
        time.sleep(1.5)
        second = atlas_lock.acquire(self.client, self.bucket, self.atlas, owner='smoke-b')
        try:
            assert second.owner == 'smoke-b', "takeover did not happen"
            try:
                atlas_lock.renew(self.client, first)
                raise AssertionError("renewing a taken-over lease succeeded")
            except atlas_lock.LeaseLost:
                pass
        finally:
            atlas_lock.release(self.client, second)

    def check_writeback_conflict(self):
        try:
            with self.session('smoke: conflict') as session:
                self.local(LAYER_REL).write_text('{"features": ["ours"]}')
                self.client.put_object(Bucket=self.bucket, Key=self.key(LAYER_REL),
                                       Body=b'{"features": ["theirs"]}')
            raise AssertionError("conflicting write-back was allowed")
        except ws.WritebackConflict:
            pass
        assert self.body(LAYER_REL) == '{"features": ["theirs"]}', "our write won a conflict"
        assert self.marker().exists(), "no marker left after a failed write-back"

        # Discard the conflicted changes the way the log tells an operator to,
        # and confirm the next session resets the workspace from S3.
        self.marker().unlink()
        with self.session('smoke: reset after conflict'):
            assert self.local(LAYER_REL).read_text() == '{"features": ["theirs"]}', \
                "workspace was not reset from S3"

    def check_resume_recognises_own_upload(self):
        path = self.local(LAYER_REL)
        path.write_text('{"features": ["resumed"]}')
        digest = ws.sha256_file(path)
        # Exactly what an interrupted write-back leaves behind: the object is in
        # S3, the manifest never recorded it, the marker is still there.
        self.client.put_object(Bucket=self.bucket, Key=self.key(LAYER_REL),
                               Body=path.read_bytes(), Metadata={'sha256': digest},
                               ContentType=atlas_store.content_type_for(LAYER_REL))
        self.marker().write_text('smoke')
        with self.session('smoke: resume'):
            pass
        assert not self.marker().exists(), "marker survived a completed resume"
        assert self.body(LAYER_REL) == '{"features": ["resumed"]}', "resume lost the content"

    def check_lock_is_free_at_the_end(self):
        assert atlas_lock.describe(self.client, self.bucket, self.atlas) is None, \
            "an atlas was left locked"

    CHECKS = ('hydrate_and_writeback', 'etag_and_metadata_round_trip',
              'warm_session_transfers_nothing', 'lock_contention',
              'expired_lease_takeover', 'writeback_conflict',
              'resume_recognises_own_upload', 'lock_is_free_at_the_end')

    def main(self):
        print(f"smoke: s3://{self.bucket}/{self.atlas}/ workspace={self.root}")
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
    parser.add_argument('--bucket', default=None,
                        help='private bucket (default: ATLAS_PRIVATE_BUCKET, else the code default)')
    parser.add_argument('--atlas', default=None, help='atlas prefix to use (default: a random one)')
    parser.add_argument('--workspace', default=None, help='workspace root (default: a temp dir)')
    parser.add_argument('--keep', action='store_true', help='keep the workspace directory')
    parser.add_argument('--force', action='store_true',
                        help='allow a bucket whose name looks like production')
    args = parser.parse_args(argv)

    bucket = args.bucket or atlas_store.cloud_settings({})['private_bucket']
    if 'prod' in bucket and not args.force:
        parser.error(f"{bucket} looks like production. Point at the staging bucket "
                     f"(ATLAS_PRIVATE_BUCKET=scs-atlas-private-staging) or pass --force.")

    atlas = args.atlas or f"smoketest-{uuid.uuid4().hex[:8]}"
    root = Path(args.workspace) if args.workspace else Path(tempfile.mkdtemp(prefix='atlas-smoke-'))
    try:
        return Smoke(atlas_store._s3(), bucket, atlas, root).main()
    finally:
        if args.workspace is None and not args.keep:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
