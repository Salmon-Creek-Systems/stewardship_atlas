"""Local workspaces over S3-held staging — the compute side of the storage seam.

Step 3 of Phase 3 (#159). S3 holds each atlas's staging tree; compute keeps
running against real files. A session hydrates a local directory laid out
exactly like ``{data_root}/{atlas}/staging/``, points ``config['data_root']``
at it, runs the unchanged materializer, and writes back what changed.

Why a workspace rather than an S3-aware path type: QGIS, rasterio, gdal2tiles
and DuckDB call ``os.fspath()`` on whatever they are handed and open a real
file, so partial emulation fails silently inside a dependency.
``versioning.atlas_path()`` already builds every path from
``config['data_root']``, so repointing that one value moves every call site at
once and none of them change.

Split the same way as ``atlas_store``:

  * The **pure** half plans what to download before a run and what to upload
    or delete after it. Plain dicts in, plain dicts out, no boto3.
  * The **S3** half takes a client argument, so tests pass a fake one.

Change detection is by content hash, never by mtime alone — rebuilding a
webmap that comes out byte-identical must upload nothing. To avoid hashing
every file on every run, the manifest records each file's size and mtime at
the last sync, and a file whose stat still matches is trusted without being
read. That is the same trick git's index uses.
"""

from pathlib import Path
import hashlib
import json
import logging
import os

import atlas_store

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure: layout
# ---------------------------------------------------------------------------

MANIFEST_FORMAT = 1

# Beside staging/, not inside it: the manifest describes the sync, it is not
# atlas data, and it must never be written back or copied into a version.
MANIFEST_FILENAME = '.workspace_manifest.json'

# Where apply_deltas and refresh_raster_layer move a consumed delta.
ARCHIVE_DIRNAMES = ('work', 'processed')


def staging_prefix(atlas_name: str) -> str:
    """Key prefix of an atlas's staging tree in the private bucket."""
    return f"{atlas_store.atlas_prefix(atlas_name)}/staging/"


def staging_dir(workspace_root, atlas_name: str) -> Path:
    """The workspace's staging directory — what `atlas_path()` resolves into."""
    return Path(workspace_root) / atlas_name / 'staging'


def manifest_path(workspace_root, atlas_name: str) -> Path:
    return Path(workspace_root) / atlas_name / MANIFEST_FILENAME


def is_archive_path(rel: str) -> bool:
    """True for a consumed delta: ``deltas/{layer}/work/...`` (or ``processed/``).

    The archive is never hydrated. Nothing reads it during a run — a rebuild
    does not replay it (see ``atlas_dagster.refresh_layer``) — and it is the
    single largest part of staging (2.1 GB of 7 GB on the box, 2026-09-11).

    It is still written back: a run that consumes a delta creates a new
    archive file, and for an interactive edit that file is the only record of
    it.
    """
    parts = rel.split('/')
    return len(parts) >= 4 and parts[0] == 'deltas' and parts[2] in ARCHIVE_DIRNAMES


# ---------------------------------------------------------------------------
# Pure: manifest
#
# One entry per file the workspace holds in a verified state:
#
#   {"etag": '"9b2c..."', "size": 1234, "mtime_ns": 17..., "sha256": "ab12..."}
#
# `etag` is S3's, kept as S3 returns it (quotes included) and only ever
# compared for equality — it is not an MD5 for multipart uploads, so nothing
# here tries to derive it. `sha256` is ours, for deciding whether content
# changed. `size`/`mtime_ns` are the local stat at the last sync.
# ---------------------------------------------------------------------------

def empty_manifest() -> dict:
    return {'format': MANIFEST_FORMAT, 'files': {}}


def load_manifest(path) -> dict:
    """Read a manifest; anything missing or unreadable reads as empty.

    Empty is the safe failure: the next hydrate treats every file as
    unverified and downloads it again, which costs time but can never leave a
    stale file trusted.
    """
    try:
        data = json.loads(Path(path).read_text())
    except (FileNotFoundError, ValueError):
        return empty_manifest()
    if not isinstance(data, dict) or data.get('format') != MANIFEST_FORMAT:
        return empty_manifest()
    data.setdefault('files', {})
    return data


def save_manifest(path, manifest: dict) -> None:
    """Write beside the target and rename, so a crash never leaves half a manifest."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(manifest, indent=1, sort_keys=True))
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Pure: local state
# ---------------------------------------------------------------------------

def snapshot_local(staging_root) -> dict:
    """``{relative path: (size, mtime_ns)}`` for every regular file under staging.

    Symlinks are skipped, not followed. ``staging/local`` points at the shared
    data directory, which is not atlas data and is synced separately.
    """
    root = Path(staging_root)
    state = {}
    if not root.is_dir():
        return state
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_symlink():
                continue
            st = path.stat()
            state[path.relative_to(root).as_posix()] = (st.st_size, st.st_mtime_ns)
    return state


def sha256_file(path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(chunk_size), b''):
            digest.update(chunk)
    return digest.hexdigest()


def listing_from_objects(objects, prefix: str) -> dict:
    """``{relative path: {'etag', 'size'}}`` from ListObjectsV2 ``Contents`` entries.

    Keys ending in ``/`` are folder markers (the S3 console makes them) and
    are not files.
    """
    listing = {}
    for obj in objects:
        key = obj['Key']
        if not key.startswith(prefix) or key.endswith('/'):
            continue
        listing[key[len(prefix):]] = {'etag': obj['ETag'], 'size': obj['Size']}
    return listing


# ---------------------------------------------------------------------------
# Pure: plans
# ---------------------------------------------------------------------------

def _in_sync(known, local, remote) -> bool:
    return (known is not None and local is not None
            and known['etag'] == remote['etag']
            and (known['size'], known['mtime_ns']) == tuple(local))


def plan_hydrate(listing: dict, manifest: dict, local_state: dict, wanted=None) -> dict:
    """What to download and what to remove so the workspace matches S3.

    S3 is the source of truth, so hydrating also *resets*: a local file that
    no longer matches its last-synced state — left behind by a failed run,
    whose changes are discarded — is downloaded again, and a local file S3 does
    not hold is removed. After executing the plan, every local file is one the
    manifest vouches for.

    ``wanted`` is an optional predicate on relative paths restricting what is
    downloaded. ``None`` means everything except the delta archive. It is the
    hook for hydrating only an asset's inputs once those are verified (the
    serverless follow-up); a file that is not wanted and not verified is
    removed rather than left untrusted.

    Returns ``{'download': [rel], 'remove': [rel], 'unchanged': [rel]}``.
    Anything in the manifest that is in none of those lists is stale and
    should be dropped from it.
    """
    files = manifest.get('files') or {}

    def include(rel):
        return not is_archive_path(rel) and (wanted is None or wanted(rel))

    download, remove, unchanged = [], [], []
    for rel in sorted(listing):
        local = local_state.get(rel)
        if _in_sync(files.get(rel), local, listing[rel]):
            unchanged.append(rel)
        elif include(rel):
            download.append(rel)
        elif local is not None:
            remove.append(rel)

    remove.extend(rel for rel in sorted(local_state) if rel not in listing)
    return {'download': download, 'remove': sorted(remove), 'unchanged': unchanged}


def plan_writeback(manifest: dict, local_state: dict, content_hash) -> dict:
    """What a finished run changed, as S3 operations.

    ``content_hash(rel)`` returns the sha256 of a workspace file. It is only
    called for files whose stat differs from the manifest, so an untouched
    tree costs a directory walk and nothing more.

    Returns::

        {'upload':    [{'rel', 'sha256', 'if_match'}],   # if_match None = new file
         'delete':    [{'rel', 'etag'}],
         'restat':    [rel],    # touched but byte-identical: refresh the stat only
         'unchanged': [rel]}

    Uploads carry the ETag seen at hydrate so the executor can make them
    conditional — a backstop behind the per-atlas lock, not a replacement for
    it. The executor must upload before it deletes: a consumed delta appears
    as an upload to ``work/`` plus a delete of the pending file, and the other
    order would lose the edit on a crash in between.

    The delta archive is never deleted from here. It is the only record of
    applied edits, and nothing a run does should be able to erase it by way of
    a write-back.
    """
    files = manifest.get('files') or {}
    upload, delete, restat, unchanged = [], [], [], []

    for rel in sorted(local_state):
        size, mtime_ns = local_state[rel]
        known = files.get(rel)
        if known is not None and (known['size'], known['mtime_ns']) == (size, mtime_ns):
            unchanged.append(rel)
            continue
        digest = content_hash(rel)
        if known is not None and known['sha256'] == digest:
            restat.append(rel)
        else:
            upload.append({'rel': rel, 'sha256': digest,
                           'if_match': known['etag'] if known is not None else None})

    for rel in sorted(files):
        if rel in local_state:
            continue
        if is_archive_path(rel):
            logger.warning(f"atlas_workspace: archived delta {rel} is missing locally; "
                           f"leaving the S3 copy in place")
            continue
        delete.append({'rel': rel, 'etag': files[rel]['etag']})

    return {'upload': upload, 'delete': delete, 'restat': restat, 'unchanged': unchanged}


# ---------------------------------------------------------------------------
# S3 — every function takes a client, so tests pass a fake one
# ---------------------------------------------------------------------------

def list_staging(client, bucket: str, atlas_name: str) -> dict:
    """The atlas's whole staging tree as a listing, paginated.

    One call per 1000 keys. The largest atlas has ~650 files, so this is a
    single request for every atlas we run today.
    """
    prefix = staging_prefix(atlas_name)
    objects = []
    token = None
    while True:
        kwargs = {'Bucket': bucket, 'Prefix': prefix}
        if token:
            kwargs['ContinuationToken'] = token
        response = client.list_objects_v2(**kwargs)
        objects.extend(response.get('Contents', []))
        if not response.get('IsTruncated'):
            return listing_from_objects(objects, prefix)
        token = response.get('NextContinuationToken')


class WritebackConflict(Exception):
    """An object changed in S3 after this workspace hydrated it."""


class StagingObjectExists(Exception):
    """A create-only staging write found the key already taken."""


def put_staging_object(client, bucket: str, atlas_name: str, rel: str, body: bytes,
                       create_only: bool = True) -> str:
    """Write one object into an atlas's staging prefix without a session.

    For the one write that must not wait on the lock: a delta. Delta files are
    new, uniquely named and append-only, so writing one races with nothing —
    and making an edit queue behind a five-minute render is how edits get lost
    instead. Applying it still happens under the lock; until then it simply
    stays pending, which the delta system already supports.

    ``create_only`` sends ``If-None-Match: *``, so a name collision raises
    instead of overwriting. Delta filenames are second-resolution, so two edits
    to one layer in the same second collide (#180); this at least makes that
    loud rather than silent.
    """
    key = _key(atlas_name, rel)
    kwargs = {'IfNoneMatch': '*'} if create_only else {}
    try:
        client.put_object(Bucket=bucket, Key=key, Body=body,
                          ContentType=atlas_store.content_type_for(rel), **kwargs)
    except Exception as exc:
        if atlas_store.s3_error_code(exc) in atlas_store.PRECONDITION_ERROR_CODES:
            raise StagingObjectExists(f"s3://{bucket}/{key} already exists") from exc
        raise
    return key


def _key(atlas_name: str, rel: str) -> str:
    return staging_prefix(atlas_name) + rel


def _entry(path, etag: str, sha256: str) -> dict:
    st = Path(path).stat()
    return {'etag': etag, 'size': st.st_size, 'mtime_ns': st.st_mtime_ns, 'sha256': sha256}


def partial_dir(workspace_root, atlas_name: str) -> Path:
    """Where downloads are written before being renamed into place.

    Beside staging/, never inside it: a half-written file inside staging would
    look like something a run created, and be written back.
    """
    return Path(workspace_root) / atlas_name / '.downloading'


def download_object(client, bucket: str, key: str, dest, partial_root,
                    chunk_size: int = 1 << 20) -> tuple:
    """Stream one object to ``dest``, hashing as it goes. Returns ``(etag, sha256)``."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial_root = Path(partial_root)
    partial_root.mkdir(parents=True, exist_ok=True)
    partial = partial_root / (dest.name + '.part')

    response = client.get_object(Bucket=bucket, Key=key)
    body = response['Body']
    digest = hashlib.sha256()
    with open(partial, 'wb') as f:
        for chunk in iter(lambda: body.read(chunk_size), b''):
            digest.update(chunk)
            f.write(chunk)
    os.replace(partial, dest)
    return response['ETag'], digest.hexdigest()


def execute_hydrate(client, bucket: str, atlas_name: str, workspace_root,
                    plan: dict, manifest: dict) -> dict:
    """Carry out a hydrate plan. Saves and returns the manifest that now describes the workspace.

    The manifest is saved once, at the end. A crash part-way is still safe:
    it leaves files the old manifest does not vouch for, and the next hydrate
    downloads those again.
    """
    root = staging_dir(workspace_root, atlas_name)
    old = manifest.get('files') or {}
    files = {rel: old[rel] for rel in plan['unchanged']}

    for rel in plan['remove']:
        try:
            (root / rel).unlink()
        except FileNotFoundError:
            pass

    partial_root = partial_dir(workspace_root, atlas_name)
    for rel in plan['download']:
        path = root / rel
        etag, digest = download_object(client, bucket, _key(atlas_name, rel), path, partial_root)
        files[rel] = _entry(path, etag, digest)

    updated = {'format': MANIFEST_FORMAT, 'files': files}
    save_manifest(manifest_path(workspace_root, atlas_name), updated)
    return updated


def _already_uploaded(client, bucket: str, key: str, sha256: str):
    """The object's ETag if S3 already holds exactly these bytes, else None.

    Covers a crash between S3 accepting an upload and the manifest recording
    it: resuming would otherwise fail its own condition and report a conflict
    with itself. Uploads carry their sha256 as object metadata for this.
    """
    try:
        head = client.head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        if atlas_store.s3_error_code(exc) in atlas_store.MISSING_ERROR_CODES:
            return None
        raise
    if (head.get('Metadata') or {}).get('sha256') == sha256:
        return head['ETag']
    return None


def execute_writeback(client, bucket: str, atlas_name: str, workspace_root,
                      plan: dict, manifest: dict) -> dict:
    """Carry out a write-back plan: uploads first, then deletes.

    The manifest is saved after every operation, so an interrupted write-back
    is resumed by planning again — completed uploads then read as unchanged.

    Uploads are conditional: ``If-Match`` on the ETag the workspace hydrated,
    or ``If-None-Match: *`` for a new file. A failed condition means S3 changed
    underneath a session that held the lock. That should be impossible, so it
    stops the write-back with ``WritebackConflict`` rather than overwriting.
    """
    root = staging_dir(workspace_root, atlas_name)
    manifest_file = manifest_path(workspace_root, atlas_name)
    files = dict(manifest.get('files') or {})
    updated = {'format': MANIFEST_FORMAT, 'files': files}

    for rel in plan['restat']:
        files[rel] = _entry(root / rel, files[rel]['etag'], files[rel]['sha256'])
    if plan['restat']:
        save_manifest(manifest_file, updated)

    for op in plan['upload']:
        rel = op['rel']
        key = _key(atlas_name, rel)
        path = root / rel
        if op['if_match']:
            condition = {'IfMatch': op['if_match']}
        else:
            condition = {'IfNoneMatch': '*'}
        try:
            with open(path, 'rb') as f:
                etag = client.put_object(
                    Bucket=bucket, Key=key, Body=f,
                    ContentType=atlas_store.content_type_for(rel),
                    Metadata={'sha256': op['sha256']}, **condition)['ETag']
        except Exception as exc:
            code = atlas_store.s3_error_code(exc)
            if code not in atlas_store.PRECONDITION_ERROR_CODES | atlas_store.MISSING_ERROR_CODES:
                raise
            etag = _already_uploaded(client, bucket, key, op['sha256'])
            if etag is None:
                raise WritebackConflict(
                    f"s3://{bucket}/{key} changed since this workspace hydrated it "
                    f"({code}); not overwriting") from exc
        files[rel] = _entry(path, etag, op['sha256'])
        save_manifest(manifest_file, updated)

    for op in plan['delete']:
        client.delete_object(Bucket=bucket, Key=_key(atlas_name, op['rel']))
        files.pop(op['rel'], None)
        save_manifest(manifest_file, updated)

    return updated
