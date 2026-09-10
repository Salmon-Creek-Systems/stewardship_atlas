"""Object-store access for atlas outlets — the first slice of the storage seam.

Phase 2 of `documents/cloud_native_plan.md`: get the *read path* off the box.
Publishing a version pushes its **public** outlet directories to S3, where
CloudFront serves them statically. Nothing else moves yet — staging stays on
the box, protected outlets stay behind nginx, and the local `CURRENT ->`
symlink is left exactly as it is so the running server is unaffected.

Shape of this module (deliberate, see the Phase 2/3 discussion):

  * The **pure** half — access-tier logic, key construction, content types,
    walking a directory into an upload plan — has no boto3 import and no
    network. It is unit-testable in the bare local env.
  * The **S3** half imports boto3 lazily inside functions, matching the
    existing convention in `outlets.py` / `vector_inlets.py`.

That split is the seam. A different backend (local mirror, GCS, a real
database) reimplements the second half against the same plan objects; callers
in `versioning.py` only ever see `publish_public_outlets()`.

Not a general filesystem abstraction — Phase 3 generalizes this once there is
real usage to point at. Resist growing it before then.
"""

from pathlib import Path
import datetime
import json
import logging
import mimetypes
import os

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure: configuration and access tiers
# ---------------------------------------------------------------------------

# Content types that matter for serving over CloudFront. mimetypes gets the
# common web ones right but not the geo formats, and a wrong type here is a
# browser-visible bug (a .pmtiles served as text/html will not range-request).
CONTENT_TYPES = {
    '.geojson': 'application/geo+json',
    '.json':    'application/json',
    '.html':    'text/html',
    '.js':      'application/javascript',
    '.css':     'text/css',
    '.pmtiles': 'application/octet-stream',
    '.db':      'application/octet-stream',
    '.gpkg':    'application/geopackage+sqlite3',
    '.parquet': 'application/vnd.apache.parquet',
    '.pdf':     'application/pdf',
    '.tif':     'image/tiff',
    '.tiff':    'image/tiff',
    '.png':     'image/png',
    '.jpg':     'image/jpeg',
    '.jpeg':    'image/jpeg',
    '.webp':    'image/webp',
    '.svg':     'image/svg+xml',
    '.txt':     'text/plain',
    '.md':      'text/markdown',
    '.csv':     'text/csv',
}

DEFAULT_CONTENT_TYPE = 'application/octet-stream'


def content_type_for(name) -> str:
    """Content type for a filename, geo formats included."""
    suffix = Path(name).suffix.lower()
    if suffix in CONTENT_TYPES:
        return CONTENT_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(str(name))
    return guessed or DEFAULT_CONTENT_TYPE


def normalize_access(access) -> list:
    """Coerce an ``access`` field to a list of tier names.

    The configs are inconsistent: mostly ``["admin"]`` but at least one bare
    string (``shared_outlets_config.json`` -> ``sqlquery``). Missing means
    public, matching the existing default in ``atlas.py``.
    """
    if access is None:
        return ['public']
    if isinstance(access, str):
        return [access]
    return list(access)


# Role-variant subdirectories inside a single outlet directory.
#
# `html` and `console` each generate ALL FOUR role variants in one pass —
# public/, internal/, admin/, technical/ — into one outlet directory (see the
# asset-method table in CLAUDE.md). So the outlet's own `access` field is the
# wrong granularity for them: the outlet reads as public because its *public*
# variant is, while the same directory also holds the admin console, whose HTML
# spells out the mutating API surface.
#
# Pruned at the first path segment only, which is where the variants live
# (html/admin/index.html). Deeper matches are left alone so a layer that happens
# to be named "admin" keeps its html/{layer}/attribution.html page.
PROTECTED_OUTLET_SUBDIRS = ('admin', 'internal', 'technical')


def is_protected_path(relative_posix: str) -> bool:
    """True when a path inside an outlet directory is a non-public role variant."""
    head, _, rest = relative_posix.partition('/')
    return bool(rest) and head in PROTECTED_OUTLET_SUBDIRS


def is_public(access) -> bool:
    """True when an asset is served to everyone.

    Fail-closed in spirit: only an explicit (or defaulted) ``public`` tier
    qualifies. Anything naming admin/internal/technical stays on the box.
    """
    return 'public' in normalize_access(access)


# Bucket and distribution names describe the deployed substrate, not any one
# atlas, so they default in code and are overridden by environment. That is
# what lets `-c env_name=staging` run the entire system against a parallel set
# of buckets without editing a single atlas config — the rehearsal substrate
# is an env var, not eleven config edits.
DEFAULT_OUTLETS_BUCKET = 'scs-atlas-outlets-prod'
DEFAULT_PRIVATE_BUCKET = 'scs-atlas-private-prod'
DEFAULT_DISTRIBUTION_ID = 'E1A5S5MB0K3FZG'
DEFAULT_PUBLIC_BASE_URL = 'https://next.fireatlas.org'


def cloud_settings(config: dict) -> dict:
    """Resolve the cloud storage settings in force for this atlas.

    Resolution order is per-atlas config, then environment, then the code
    default. There is no longer an `enabled` flag: S3 is the source of truth,
    so every publish writes there and an atlas cannot opt out of its own
    storage backend.

    What *is* still per-atlas is `cloud.outlets` — the explicit allowlist of
    outlet directories that may be served publicly. That is the surviving
    guardrail, and it is deliberately the fail-closed one: `access` defaults to
    public in `atlas.py`, so an atlas with no allowlist would otherwise publish
    whatever happens to read as public, `sqldb` and its every-layer `atlas.db`
    included. Replacing a boolean gate with an explicit allowlist means one
    mechanism instead of two, and the one that survives is the one that fails
    safe.
    """
    cloud = config.get('cloud') or {}
    bucket = (cloud.get('outlets_bucket')
              or os.environ.get('ATLAS_OUTLETS_BUCKET')
              or DEFAULT_OUTLETS_BUCKET)
    return {
        'bucket': bucket,
        'outlets_bucket': bucket,
        'private_bucket': (cloud.get('private_bucket')
                           or os.environ.get('ATLAS_PRIVATE_BUCKET')
                           or DEFAULT_PRIVATE_BUCKET),
        'distribution_id': (cloud.get('distribution_id')
                            or os.environ.get('ATLAS_DISTRIBUTION_ID')
                            or DEFAULT_DISTRIBUTION_ID),
        'public_base_url': (cloud.get('public_base_url')
                            or os.environ.get('ATLAS_PUBLIC_BASE_URL')
                            or DEFAULT_PUBLIC_BASE_URL).rstrip('/'),
        'invalidate': cloud.get('invalidate', True),
        'outlets': cloud.get('outlets'),
    }


def publishable_outlets(config: dict) -> list:
    """Names of outlet assets whose built directory should go to S3.

    Filters, all reading existing config rather than anything new:

    1. ``type == 'outlet'`` — inlets and eddies produce layers, not served dirs.
    2. Public tier — the Phase 2 scope decision. Protected outlets keep being
       served from the box until the API moves in Phase 4.
    3. ``cloud.outlets`` — a **required** explicit allowlist. An atlas without
       one publishes no outlets at all.
    4. ``dataswale.versioned_outlets`` when set — already the publish snapshot
       filter (issue #131, C9). An outlet excluded from the snapshot has no
       directory in the version to upload.

    **Why the allowlist is required.** ``access`` is optional and absent means
    public (``atlas.py`` does ``.get('access', ['public'])``). That default
    predates anything actually being world-readable and it sweeps in outlets
    that should not be: ``sqldb`` builds an ``atlas.db`` containing *every*
    layer, admin-only ones included. While `cloud.enabled` existed, an
    un-migrated atlas was protected by being switched off; now that S3 is
    unconditional, the allowlist is the only thing standing between a
    fail-open default and a public bucket. So it is mandatory rather than
    advisory — this is the same failure that put 17 protected layers on
    CloudFront (#177), and it is not being left to a default a second time.

    Returns names sorted for stable, diffable logs.
    """
    assets = config.get('assets') or {}
    versioned = set((config.get('dataswale') or {}).get('versioned_outlets') or [])
    allowlist = (config.get('cloud') or {}).get('outlets')

    if allowlist is None:
        logger.warning(
            f"atlas_store: atlas '{config.get('name')}' has no `cloud.outlets` "
            f"allowlist — publishing no outlets. Add the list of outlet names "
            f"that may be served publicly to its config.")
        return []

    names = []
    for name, asset in assets.items():
        if asset.get('type') != 'outlet':
            continue
        # Checked even when an allowlist names it — a typo must never be able
        # to push an admin outlet into a public bucket.
        if not is_public(asset.get('access')):
            continue
        if name not in allowlist:
            continue
        if versioned and name not in versioned:
            continue
        if asset.get('access') is None:
            logger.warning(
                f"atlas_store: outlet '{name}' is allowlisted but has no explicit "
                f"access level, so it passes the tier check only by the fail-open "
                f"default. Set `access` on the asset (#175).")
        names.append(name)
    return sorted(names)


# ---------------------------------------------------------------------------
# Pure: keys and upload plans
# ---------------------------------------------------------------------------

def atlas_prefix(atlas_name: str) -> str:
    """Root key prefix for an atlas in the outlets bucket."""
    return atlas_name.strip('/')


def current_prefix(atlas_name: str) -> str:
    """Key prefix serving the currently published version.

    Phase 2 writes the published version's contents straight here rather than
    to a versioned prefix with a pointer indirection. Reasons: URLs stay
    static (no edge resolver needed), storage is not duplicated, and version
    *history* already lives on the box. Phase 3 revisits this when S3 becomes
    the source of truth — at which point a pointer object plus a CloudFront
    KeyValueStore lookup is the better shape.
    """
    return f"{atlas_prefix(atlas_name)}/current"


def pointer_key(atlas_name: str) -> str:
    """Key of the small JSON object describing what is published.

    Deliberately *outside* ``current_prefix`` so pruning stale keys under the
    current prefix can never delete it.
    """
    return f"{atlas_prefix(atlas_name)}/current.json"


def plan_upload(local_dir, key_prefix: str) -> list:
    """Walk a directory into a list of ``(Path, key, content_type)`` tuples.

    Symlinks are followed (``is_file()`` resolves them) because version
    snapshots are copied with ``symlinks=True`` and the *content* is what
    should be served. Empty directories are skipped — S3 has no such thing.

    Non-public role-variant subdirectories are pruned — see
    ``PROTECTED_OUTLET_SUBDIRS`` for why one outlet directory can hold more than
    one access tier.
    """
    local_dir = Path(local_dir)
    if not local_dir.is_dir():
        return []

    plan = []
    pruned = set()
    for path in sorted(local_dir.rglob('*')):
        if not path.is_file():
            continue
        relative = path.relative_to(local_dir).as_posix()
        if is_protected_path(relative):
            pruned.add(relative.partition('/')[0])
            continue
        plan.append((path, f"{key_prefix.rstrip('/')}/{relative}", content_type_for(path)))
    if pruned:
        logger.info(f"atlas_store: pruned role-variant subdirs from {local_dir}: "
                    f"{sorted(pruned)}")
    return plan


def plan_publish(config: dict, version_path, outlet_names=None) -> list:
    """Upload plan for every publishable outlet directory in a version.

    ``version_path`` is the snapshot directory ``publish_new_version`` just
    created. Outlets with no directory on disk are skipped with a warning
    rather than failing the publish — a never-materialized outlet should not
    take the whole push down.
    """
    version_path = Path(version_path)
    atlas_name = config['name']
    names = publishable_outlets(config) if outlet_names is None else outlet_names

    plan = []
    for name in names:
        outlet_dir = version_path / 'outlets' / name
        if not outlet_dir.is_dir():
            logger.warning(f"atlas_store: no built directory for public outlet '{name}' "
                           f"at {outlet_dir} — skipping")
            continue
        plan.extend(plan_upload(outlet_dir, f"{current_prefix(atlas_name)}/outlets/{name}"))
    return plan


def stale_keys(existing: list, planned: list) -> list:
    """Keys present in the bucket that the new publish does not write.

    Deleting these is what makes the current prefix a true mirror of the
    published version — otherwise a removed layer's geojson lingers and keeps
    being served.
    """
    return sorted(set(existing) - set(planned))


# ---------------------------------------------------------------------------
# S3 — boto3 imported lazily, matching the convention elsewhere in python/
# ---------------------------------------------------------------------------

def _s3(region: str = None):
    import boto3
    return boto3.client('s3', region_name=region) if region else boto3.client('s3')


def list_keys(bucket: str, prefix: str, client=None) -> list:
    """Every key under a prefix, paginated."""
    client = client or _s3()
    keys = []
    token = None
    while True:
        kwargs = {'Bucket': bucket, 'Prefix': prefix}
        if token:
            kwargs['ContinuationToken'] = token
        response = client.list_objects_v2(**kwargs)
        keys.extend(item['Key'] for item in response.get('Contents', []))
        if not response.get('IsTruncated'):
            return keys
        token = response.get('NextContinuationToken')


def upload_plan(bucket: str, plan: list, client=None) -> int:
    """Upload every entry of a plan. Returns the byte count moved."""
    client = client or _s3()
    total = 0
    for path, key, content_type in plan:
        client.upload_file(str(path), bucket, key,
                           ExtraArgs={'ContentType': content_type})
        total += path.stat().st_size
    return total


def delete_keys(bucket: str, keys: list, client=None) -> int:
    """Delete keys in batches of 1000 (the API maximum)."""
    if not keys:
        return 0
    client = client or _s3()
    for start in range(0, len(keys), 1000):
        batch = keys[start:start + 1000]
        client.delete_objects(Bucket=bucket,
                              Delete={'Objects': [{'Key': k} for k in batch]})
    return len(keys)


def write_pointer(bucket: str, atlas_name: str, payload: dict, client=None) -> str:
    """Write the small JSON object describing the published version."""
    client = client or _s3()
    key = pointer_key(atlas_name)
    client.put_object(Bucket=bucket, Key=key,
                      Body=json.dumps(payload, indent=2).encode('utf-8'),
                      ContentType='application/json',
                      CacheControl='no-cache')
    return key


def invalidate_current(distribution_id: str, atlas_name: str, client=None) -> str:
    """Invalidate the atlas's current prefix in CloudFront.

    Phase 1 left the distribution's default TTL at 5 minutes precisely because
    this was not wired up; with it in place that TTL can be raised.
    """
    if not distribution_id:
        return None
    if client is None:
        import boto3
        client = boto3.client('cloudfront')
    response = client.create_invalidation(
        DistributionId=distribution_id,
        InvalidationBatch={
            'Paths': {'Quantity': 1, 'Items': [f"/{current_prefix(atlas_name)}/*"]},
            'CallerReference': f"{atlas_name}-{datetime.datetime.now().timestamp()}",
        },
    )
    return response['Invalidation']['Id']


# ---------------------------------------------------------------------------
# Orchestration — the only entry point callers should need
# ---------------------------------------------------------------------------

def publish_public_outlets(config: dict, version_path, version: str) -> dict:
    """Mirror a published version's public outlets to S3.

    Called by ``versioning.publish_new_version`` before ``CURRENT`` is moved.

    **Raises on failure**, which reverses the Phase 2 policy. That policy —
    swallow the error, because the box is still serving and local files are
    still authoritative — was correct exactly as long as its premise held. Now
    that S3 is the source of truth, a publish whose push failed is not a
    publish, and returning success for it would leave the box advertising a
    version the world cannot read. Failing here leaves the previous version
    live, which is the safe outcome.
    """
    settings = cloud_settings(config)
    atlas_name = config['name']
    bucket = settings['bucket']
    prefix = current_prefix(atlas_name)

    try:
        names = publishable_outlets(config)
        plan = plan_publish(config, version_path, names)
        if not plan:
            logger.warning(f"atlas_store: nothing to publish for {atlas_name}")
            return {'status': 'empty', 'outlets': names}

        client = _s3()
        logger.info(f"atlas_store: publishing {len(plan)} files for {atlas_name} "
                    f"({', '.join(names)}) to s3://{bucket}/{prefix}/")

        existing = list_keys(bucket, f"{prefix}/", client=client)
        moved = upload_plan(bucket, plan, client=client)
        removed = delete_keys(bucket, stale_keys(existing, [key for _, key, _ in plan]),
                              client=client)

        write_pointer(bucket, atlas_name, {
            'atlas': atlas_name,
            'version': version,
            'published_at': datetime.datetime.now().isoformat(),
            'outlets': names,
            'files': len(plan),
        }, client=client)

        invalidation = None
        if settings['invalidate']:
            invalidation = invalidate_current(settings['distribution_id'], atlas_name)

        logger.info(f"atlas_store: published {len(plan)} files ({moved} bytes), "
                    f"pruned {removed}, invalidation={invalidation}")
        return {
            'status': 'success',
            'bucket': bucket,
            'prefix': prefix,
            'version': version,
            'outlets': names,
            'files': len(plan),
            'bytes': moved,
            'pruned': removed,
            'invalidation': invalidation,
        }
    except Exception as e:
        logger.error(f"atlas_store: S3 publish failed for {atlas_name}: {e}", exc_info=True)
        raise


# ---------------------------------------------------------------------------
# Phase 3 (#159): source layer data in S3
#
# Layers are written once at an immutable, version-stamped key. A later version
# that did not change a layer points at the same key rather than writing a
# second copy — the same model the local catalog already uses, so the two stay
# describable by one set of Items.
#
# Public-tier layers go to the outlets bucket: it already has OAC, a CloudFront
# distribution, and the CORS rules COG/PMTiles range requests need. Splitting
# source data into its own bucket is #174. Everything else goes to the private
# bucket, where it has no reader until the Phase 4 protected read path.
# ---------------------------------------------------------------------------

def layer_key(atlas_name: str, layer_name: str, version: str, filename: str) -> str:
    """Immutable key for one file of one layer at one version."""
    return f"{atlas_prefix(atlas_name)}/layers/{layer_name}/{version}/{filename}"


def layer_bucket(access, settings: dict) -> str:
    """Which bucket a layer's data belongs in, from its access tiers.

    Fail-closed by construction: only an explicitly public layer can reach the
    public bucket. The tier comes from the catalog Item, where
    ``federation.layer_access`` already defaults a layer with no ``access``
    field to ``internal`` — deliberately the opposite of the outlet default in
    ``atlas.py``, which is how `sqldb` came to read as public.
    """
    # `is_public(None)` is True — normalize_access() defaults a missing tier to
    # public, matching atlas.py. That default must not reach a bucket decision,
    # so an absent or empty tier is treated as private here regardless.
    if access and is_public(access):
        return settings.get('outlets_bucket') or ''
    return settings.get('private_bucket') or ''


def plan_layer_uploads(config: dict, version_path, version: str,
                       layer_assets: dict, layer_versions: dict,
                       access_by_layer: dict) -> list:
    """The full set of objects this version's layers *should* have in S3.

    Returns ``(Path, bucket, key, content_type)`` tuples for **every** layer the
    catalog describes, keyed at the version that actually holds it — the current
    one for a rewrite, an older one for a reuse.

    This is deliberately a description of the desired end state rather than a
    diff. An earlier version planned only the layers written this publish, on
    the reasoning that a reused layer's object already exists. That reasoning
    is wrong at the boundary: reuse is computed against the **local** catalog,
    which knows nothing about what S3 holds. On kennedy's first push every
    layer was "unchanged since the last local version", so every layer was
    skipped and only the catalog documents landed. Caller reconciles this plan
    against what is actually in the bucket.

    Every servable file in the layer directory is included, not just the
    primary one. A webmap asks a raster layer for ``{layer}.tiff.jpg`` while
    the primary file is ``{layer}.tiff``, and an object store holding only the
    primary cannot serve the map.

    A reused layer's bytes are read from *this* version's directory: the
    checksum matched, so the content is identical to what the older version
    holds, and this is the copy known to be present.
    """
    atlas_name = config['name']
    settings = cloud_settings(config)
    version_path = Path(version_path)

    plan = []
    for name in sorted(layer_versions):
        spec = layer_assets.get(name)
        if not spec:
            continue
        bucket = layer_bucket(access_by_layer.get(name), settings)
        if not bucket:
            logger.warning(f"atlas_store: no bucket configured for layer '{name}' "
                           f"(access={access_by_layer.get(name)}) — skipping")
            continue
        layer_dir = version_path / 'layers' / name
        for filename in spec.get('files') or [Path(spec['href']).name]:
            path = layer_dir / filename
            if not path.is_file():
                logger.warning(f"atlas_store: {path} named by the catalog but not "
                               f"on disk — skipping")
                continue
            plan.append((path, bucket,
                         layer_key(atlas_name, name, layer_versions[name], filename),
                         content_type_for(filename)))
    return plan


def plan_catalog_upload(config: dict, version_path: str, version: str,
                        stac_dirname: str = 'stac') -> list:
    """Upload plan for a version's STAC documents.

    The catalog is the index a remote reader needs, so it goes to the same
    bucket as the outlets it describes. Documents are small JSON; they are
    uploaded whole rather than diffed.
    """
    settings = cloud_settings(config)
    bucket = settings.get('outlets_bucket') or ''
    stac_dir = Path(version_path) / stac_dirname
    if not (bucket and stac_dir.is_dir()):
        return []

    prefix = f"{atlas_prefix(config['name'])}/catalog/{version}"
    return [(path, bucket, f"{prefix}/{path.relative_to(stac_dir).as_posix()}",
             content_type_for(path))
            for path in sorted(stac_dir.rglob('*.json')) if path.is_file()]


def publish_layer_data(config: dict, version_path, version: str,
                       catalog_summary: dict) -> dict:
    """Push this version's newly written layers, and its catalog, to S3.

    Runs after ``atlas_catalog.publish_catalog`` and reuses what it already
    computed, so layer files are scanned and checksummed once per publish.

    Unconditional: layer data is the source of truth, and an atlas does not
    get to opt out of writing it. Tier routing still decides *which* bucket,
    and ``layer_bucket`` fails closed, so a layer reaches the public bucket
    only by explicit declaration.

    Raises on failure, for the same reason ``publish_public_outlets`` now does.
    """
    desired = plan_layer_uploads(
        config, version_path, version,
        catalog_summary.get('layer_assets') or {},
        catalog_summary.get('layer_versions') or {},
        catalog_summary.get('access_by_layer') or {})

    catalog_plan = plan_catalog_upload(config, version_path, version)
    if not (desired or catalog_plan):
        # Nothing to do, and nothing to do it with: return before touching
        # boto3. With the `cloud.layers` gate gone this is the only early exit,
        # and without it a publish with no layer data would demand credentials
        # to discover it had no work — which also made the path untestable in
        # the bare local env.
        return {'status': 'ok', 'objects': 0, 'bytes': 0, 'already_present': 0,
                'note': 'no layer or catalog objects to publish'}

    # Reconcile against what the bucket actually holds rather than trusting the
    # local catalog's reuse decision — see plan_layer_uploads. Keys are
    # immutable, so "present" means "correct" and re-uploading is never needed.
    # This also self-heals a partial failure from an earlier publish.
    client = _s3()
    plan = []
    skipped = 0
    for bucket in sorted({b for _, b, _, _ in desired}):
        prefix = f"{atlas_prefix(config['name'])}/layers/"
        try:
            present = set(list_keys(bucket, prefix, client=client))
        except Exception as exc:
            logger.warning(f"atlas_store: could not list s3://{bucket}/{prefix} "
                           f"({exc}); uploading everything for that bucket")
            present = set()
        for entry in desired:
            if entry[1] != bucket:
                continue
            if entry[2] in present:
                skipped += 1
            else:
                plan.append(entry)

    plan += catalog_plan

    if not plan:
        return {'status': 'ok', 'objects': 0, 'bytes': 0, 'already_present': skipped,
                'note': 'every layer object already in place'}

    by_bucket = {}
    for path, bucket, key, content_type in plan:
        by_bucket.setdefault(bucket, []).append((path, key, content_type))

    uploaded_bytes = 0
    errors = []
    for bucket, items in sorted(by_bucket.items()):
        try:
            uploaded_bytes += upload_plan(bucket, items, client=client)
            logger.info(f"atlas_store: uploaded {len(items)} object(s) to "
                        f"s3://{bucket}/")
        except Exception as exc:
            errors.append(f"{bucket}: {exc}")
            logger.error(f"atlas_store: layer push to {bucket} failed: {exc}",
                         exc_info=True)

    return {
        'status': 'error' if errors else 'ok',
        'objects': len(plan),
        'bytes': uploaded_bytes,
        'already_present': skipped,
        'buckets': sorted(by_bucket),
        'errors': errors,
    }
