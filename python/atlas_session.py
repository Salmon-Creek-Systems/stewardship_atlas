"""Sessions: the unit of S3-backed compute on one atlas.

A session is lock → hydrate → run → write back → release, wrapped around
whatever the caller does with the atlas's config. It is the boundary Step 3 of
Phase 3 (#159) moves. Compute inside it keeps using real files through
``versioning.atlas_path()``, because ``config['data_root']`` points at a local
workspace that S3 has just been synced into::

    with atlas_session.open_session('kennedy', purpose='refresh_layer roads') as session:
        atlas_dagster.refresh_layer(session.config, 'roads')

A Dagster cascade runs inside one session — one hydrate, one write-back —
never one session per asset.

Failure semantics (decided 2026-09-11):

  * **The body raises** → nothing is written back. The workspace is left as
    the run left it, and the next session's hydrate resets it from S3.
    Resetting lazily, rather than here, means an S3 outage cannot bury the
    original exception under a second one. Side effects outside the workspace
    (``s3_upload`` outlets, COG pushes, SES registration) are not rolled back.
  * **The write-back fails part-way** → the manifest records every operation
    that completed and a marker file stays beside it. The next session on the
    same workspace finishes the write-back *before* hydrating, because
    hydrating first would discard changes that never reached S3. Uploads go
    before deletes, so an interruption can leave a consumed delta still
    pending — never a lost one. Only the same workspace can resume; another
    machine would see the half-finished state.
  * **The lease is lost** (a stall past expiry let someone take over) → the
    final renew raises ``LeaseLost`` and nothing is written back.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json
import logging
import os
import threading
import time

import atlas_lock
import atlas_store
import atlas_workspace as ws

logger = logging.getLogger(__name__)

WORKSPACE_ROOT_ENV = 'ATLAS_WORKSPACE_ROOT'
WRITEBACK_MARKER = '.writeback_in_progress'


class AtlasNotSeeded(Exception):
    """S3 holds no staging config for this atlas."""


@dataclass
class Session:
    atlas_name: str
    workspace_root: Path
    staging_dir: Path
    bucket: str
    lease: atlas_lock.Lease
    config: dict = None
    hydrated: dict = None   # plan counts, set on entry
    written: dict = None    # plan counts, set on a successful exit


def workspace_root_from_env() -> Path:
    root = os.environ.get(WORKSPACE_ROOT_ENV)
    if not root:
        raise ValueError(
            f"{WORKSPACE_ROOT_ENV} is not set. Sessions need a local directory to "
            f"hydrate atlases into, e.g. /root/atlas_workspace on the box.")
    return Path(root)


def marker_path(workspace_root, atlas_name: str) -> Path:
    """Beside staging/, like the manifest: it describes the sync, not the atlas."""
    return Path(workspace_root) / atlas_name / WRITEBACK_MARKER


class Heartbeat:
    """Renews a lease on a background thread until stopped.

    A lost lease is recorded and ends the thread; there is nobody on this
    thread to raise to. The session's final, synchronous renew is what acts on
    it. Any other failure — a network blip — is logged and retried at the next
    beat, because giving up would let a healthy lease expire.
    """

    def __init__(self, client, lease, interval: float, clock=time.time):
        self._client = client
        self._lease = lease
        self._interval = interval
        self._clock = clock
        self._stop = threading.Event()
        self.error = None
        self._thread = threading.Thread(
            target=self._run, name=f"atlas-lease-{lease.atlas_name}", daemon=True)

    def start(self):
        self._thread.start()
        return self

    def _run(self):
        while not self._stop.wait(self._interval):
            try:
                atlas_lock.renew(self._client, self._lease, clock=self._clock)
            except atlas_lock.LeaseLost as exc:
                self.error = exc
                logger.error(f"atlas_session: {exc}")
                return
            except Exception as exc:
                logger.warning(f"atlas_session: lease renewal for "
                               f"'{self._lease.atlas_name}' failed, will retry: {exc}")

    def stop(self):
        self._stop.set()
        self._thread.join()


def _counts(plan: dict) -> dict:
    return {name: len(items) for name, items in plan.items()}


def _hydrate(client, bucket, atlas_name, root, wanted) -> dict:
    staging = ws.staging_dir(root, atlas_name)
    manifest = ws.load_manifest(ws.manifest_path(root, atlas_name))
    listing = ws.list_staging(client, bucket, atlas_name)
    plan = ws.plan_hydrate(listing, manifest, ws.snapshot_local(staging), wanted=wanted)
    ws.execute_hydrate(client, bucket, atlas_name, root, plan, manifest)
    counts = _counts(plan)
    logger.info(f"atlas_session: hydrated '{atlas_name}' {counts}")
    return counts


def _write_back(client, bucket, atlas_name, root) -> dict:
    staging = ws.staging_dir(root, atlas_name)
    manifest = ws.load_manifest(ws.manifest_path(root, atlas_name))
    plan = ws.plan_writeback(manifest, ws.snapshot_local(staging),
                             lambda rel: ws.sha256_file(staging / rel))
    marker = marker_path(root, atlas_name)
    if plan['upload'] or plan['delete']:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(datetime.now().isoformat())
    ws.execute_writeback(client, bucket, atlas_name, root, plan, manifest)
    # Unconditional: a resumed write-back can find its work already done.
    if marker.exists():
        marker.unlink()
    counts = _counts(plan)
    logger.info(f"atlas_session: wrote back '{atlas_name}' {counts}")
    return counts


def _load_config(session: Session, require_config: bool):
    path = session.staging_dir / 'atlas_config.json'
    if not path.is_file():
        if require_config:
            raise AtlasNotSeeded(
                f"s3://{session.bucket}/{ws.staging_prefix(session.atlas_name)}"
                f"atlas_config.json does not exist — seed the atlas first")
        return None
    config = json.loads(path.read_text())
    if config.get('name') != session.atlas_name:
        # atlas_path() builds paths from config['name']; a mismatch would
        # quietly resolve somewhere other than the directory just hydrated.
        raise ValueError(f"staging config for '{session.atlas_name}' names "
                         f"'{config.get('name')}'")
    # Every path the atlas code builds starts here (versioning.atlas_path), so
    # this one assignment is what moves compute into the workspace. A body that
    # rewrites atlas_config.json carries the value into S3; that is harmless,
    # because every session overrides it again on load.
    config['data_root'] = str(session.workspace_root)
    return config


@contextmanager
def open_session(atlas_name: str, purpose: str = '', *, client=None, bucket: str = None,
                 workspace_root=None, wait: float = 0.0,
                 ttl: float = atlas_lock.DEFAULT_TTL_SECONDS, heartbeat_interval: float = None,
                 wanted=None, require_config: bool = True, clock=time.time):
    """Lock, hydrate and yield a ``Session``; write back and release on exit.

    ``wait``: seconds to wait for a busy atlas before raising ``AtlasLocked``.
    ``heartbeat_interval``: seconds between renewals; defaults to ``ttl / 3``,
    and ``0`` disables the heartbeat (tests, and bodies known to be short).
    ``wanted``: optional predicate restricting what is hydrated — see
    ``atlas_workspace.plan_hydrate``.
    ``require_config``: ``False`` for creating an atlas, which starts with
    nothing in S3; ``session.config`` is then ``None``.
    """
    client = client or atlas_store._s3()
    bucket = bucket or atlas_store.cloud_settings({})['private_bucket']
    root = Path(workspace_root) if workspace_root is not None else workspace_root_from_env()
    if heartbeat_interval is None:
        heartbeat_interval = ttl / 3

    lease = atlas_lock.acquire(client, bucket, atlas_name, purpose=purpose,
                               ttl=ttl, wait=wait, clock=clock)
    heartbeat = None
    try:
        if heartbeat_interval > 0:
            heartbeat = Heartbeat(client, lease, heartbeat_interval, clock).start()

        if marker_path(root, atlas_name).exists():
            logger.warning(f"atlas_session: finishing an interrupted write-back for "
                           f"'{atlas_name}' before hydrating. If it conflicts, delete "
                           f"{marker_path(root, atlas_name)} to discard those changes.")
            _write_back(client, bucket, atlas_name, root)

        session = Session(atlas_name=atlas_name, workspace_root=root,
                          staging_dir=ws.staging_dir(root, atlas_name),
                          bucket=bucket, lease=lease)
        session.hydrated = _hydrate(client, bucket, atlas_name, root, wanted)
        session.config = _load_config(session, require_config)

        yield session

        if heartbeat is not None:
            heartbeat.stop()
            heartbeat = None
        # The authoritative check that the lease is still ours, immediately
        # before anything is written.
        atlas_lock.renew(client, lease, clock=clock)
        session.written = _write_back(client, bucket, atlas_name, root)
    finally:
        if heartbeat is not None:
            heartbeat.stop()
        try:
            atlas_lock.release(client, lease, clock=clock)
        except Exception as exc:
            # Never mask the exception that brought us here.
            logger.error(f"atlas_session: could not release lease on '{atlas_name}': {exc}")
