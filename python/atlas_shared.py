"""Shared source data (`local/`) fetched on demand into a session workspace.

Step 3 of Phase 3 (#159). Every atlas's `staging/local` points at one shared
directory — `/root/data` on the box — holding the files file-based inlets read
and the PNG icons the webmap and QGIS use. In S3 those live under `shared/` in
the private bucket, and a session's workspace has a cache they are fetched
into.

**On demand, not up front.** The configs reference 5.4 GB, but 4.78 GB of that
is a single file: `NHD_H_California_State_GPKG.gpkg`, which `public_creeks`
clips in 36 atlases. Fetching everything an atlas *might* read would make a
webmap rebuild pay for a statewide GeoPackage it never opens. Only a
materializer that actually opens a file pays for it, and only the first time.

The call sites are few and explicit — two file inlets, the webmap sprite
builder and the QGIS icon loader — which is what keeps this a boundary rather
than a filesystem emulation.

**Required versus optional matches what the code already did.** An inlet whose
source is missing should fail; a missing icon should not, because both icon
sites fall back to the repo's `templates/icons/` and warn.

**It never writes outside a workspace.** On the box `local` resolves to
`/root/data` itself, and fetching there would overwrite the shared data the
whole system reads. So a fetch happens only when `local` resolves to the
workspace's own shared cache — true for a hydrated workspace, false for the
box's layout and false for anything else, both of which keep working untouched.
"""

from pathlib import Path
import logging
import os

import atlas_store
import atlas_workspace as ws
import versioning

logger = logging.getLogger(__name__)

SHARED_PREFIX = 'shared/'
CACHE_DIRNAME = '.shared'
CACHE_MANIFEST = '.shared_manifest.json'

# A shapefile is a file only by courtesy. Fetching the .shp alone leaves
# ogr2ogr opening something that looks present and is not readable.
SIDECAR_SUFFIXES = {
    '.shp': ('.dbf', '.shx', '.prj', '.cpg', '.qix', '.sbn', '.sbx'),
}

# Per-process record of what has already been checked against S3, so a sprite
# sheet with a dozen icons does not HEAD the same object a dozen times.
_verified = set()


def shared_key(rel: str) -> str:
    return SHARED_PREFIX + str(rel).lstrip('/')


def cache_dir(workspace_root) -> Path:
    """The shared cache, one per workspace rather than one per atlas."""
    return Path(workspace_root) / CACHE_DIRNAME


def link_into_workspace(workspace_root, staging_dir) -> Path:
    """Point an atlas's `staging/local` at the shared cache. Returns the cache dir."""
    cache = cache_dir(workspace_root)
    cache.mkdir(parents=True, exist_ok=True)
    link = Path(staging_dir) / 'local'
    if link.is_symlink():
        if os.path.realpath(link) == str(cache.resolve()):
            return cache
        link.unlink()
    elif link.exists():
        # A real directory where the symlink belongs: leave it be rather than
        # delete something we did not create.
        logger.warning(f"atlas_shared: {link} is a directory, not a symlink to {cache}")
        return cache
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(cache, target_is_directory=True)
    return cache


def _fetchable(config: dict, local_dir: Path) -> bool:
    """True only when `local` points at the workspace's shared cache.

    Deliberately exact, not "somewhere under data_root": the only directory
    this module may write into is the cache it manages. On the box `local`
    resolves to `/root/data` itself, and fetching there would overwrite the
    shared data every atlas reads.
    """
    try:
        resolved = local_dir.resolve()
        cache = cache_dir(config['data_root']).resolve()
    except (OSError, KeyError):
        return False
    return resolved == cache


def _settings(config: dict, bucket: str = None) -> str:
    return bucket or atlas_store.cloud_settings(config or {})['private_bucket']


def _fetch(client, bucket: str, rel: str, dest: Path, manifest: dict,
           partial_root: Path) -> bool:
    """Fetch one object if the cache does not already hold it. True if it is there after."""
    key = shared_key(rel)
    try:
        head = client.head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        if atlas_store.s3_error_code(exc) in atlas_store.MISSING_ERROR_CODES:
            logger.info(f"atlas_shared: no s3://{bucket}/{key}")
            return dest.exists()
        raise

    known = (manifest.get('files') or {}).get(rel)
    if known and dest.exists():
        st = dest.stat()
        if (known['etag'] == head['ETag']
                and (known['size'], known['mtime_ns']) == (st.st_size, st.st_mtime_ns)):
            return True

    logger.info(f"atlas_shared: fetching s3://{bucket}/{key} "
                f"({head.get('ContentLength', 0) / (1 << 20):.1f} MB)")
    etag, digest = ws.download_object(client, bucket, key, dest, partial_root)
    st = dest.stat()
    manifest.setdefault('files', {})[rel] = {
        'etag': etag, 'size': st.st_size, 'mtime_ns': st.st_mtime_ns, 'sha256': digest}
    return True


def ensure(config: dict, rel: str, client=None, bucket: str = None) -> Path:
    """Make sure the shared file is in the workspace cache; return its path.

    Sidecars come with it: a `.shp` is useless without its `.dbf`/`.shx`/`.prj`.
    """
    local_dir = versioning.atlas_path(config, 'local')
    path = local_dir / rel
    if not _fetchable(config, local_dir):
        return path

    bucket = _settings(config, bucket)
    wanted = [str(rel)]
    for suffix in SIDECAR_SUFFIXES.get(Path(rel).suffix.lower(), ()):
        wanted.append(str(Path(rel).with_suffix(suffix)))

    outstanding = [r for r in wanted if (bucket, r) not in _verified]
    if not outstanding:
        return path

    client = client or atlas_store._s3()
    root = cache_dir(config['data_root'])
    manifest_file = root / CACHE_MANIFEST
    manifest = ws.load_manifest(manifest_file)
    fetched_any = False
    for r in outstanding:
        before = (manifest.get('files') or {}).get(r)
        _fetch(client, bucket, r, root / r, manifest, root / '.partial')
        _verified.add((bucket, r))
        fetched_any = fetched_any or (manifest.get('files') or {}).get(r) != before
    if fetched_any:
        ws.save_manifest(manifest_file, manifest)
    return path


def local_path(config: dict, rel: str, required: bool = False, client=None,
               bucket: str = None) -> Path:
    """Path to a shared file, fetched into the workspace cache if it is not there.

    ``required=True`` raises when the file is still missing — an inlet whose
    source is absent should say so, rather than hand a missing path to
    ogr2ogr. The icon call sites leave it False and keep their own fallback to
    the repo's bundled icons.
    """
    path = ensure(config, rel, client=client, bucket=bucket)
    if required and not path.exists():
        raise FileNotFoundError(
            f"shared file '{rel}' is neither in the workspace nor at "
            f"s3://{_settings(config, bucket)}/{shared_key(rel)}")
    return path


def forget_verified():
    """Drop the per-process memo. For tests, and for a long-lived process that
    wants to see a shared file replaced without a restart."""
    _verified.clear()
