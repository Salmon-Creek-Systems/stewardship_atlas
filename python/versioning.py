from pathlib import Path
import datetime
import logging

import atlas_catalog
import atlas_store
import atlas_workspace
import federation

# Configure logging
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)
logger.setLevel(logging.INFO)

def atlas_path(config=None, local_path='', version='staging'):
    """
    Return the path to the atlas file, with versioning if provided.
    Special case: version='app' returns the root of the currently-running
    code (i.e. the repo containing this file), so templates and scripts
    always come from the live codebase regardless of per-atlas app/ checkouts.
    """
    if version == 'app':
        return Path(__file__).parent.parent / local_path
    atlas_name = config['name']
    data_root = config['data_root']
    atlas_path = Path(data_root) / atlas_name / version / local_path
    logger.debug(f"Atlas path: {atlas_path}")
    return atlas_path


def atlas_file(p, mode='rt'):
    d = p.parent
    d.mkdir(parents=True, exist_ok=True)
    return open(p, mode=mode)





    
def publish_new_version(config, version=None, *, client=None):
    """Publish a new version of the atlas.

    A version is no longer a copy of staging. It is a STAC Catalog naming the
    immutable objects that constitute it — most of which the previous version
    already put in S3 — so publishing writes only what changed and links to the
    rest. The `shutil.copytree` this replaces duplicated the whole tree every
    time, which is what made westport's ten versions cost 6 GB of layers and
    outlets that were mostly identical.

    Nothing is written to the local disk and the `CURRENT` symlink is not
    moved: compute runs in a workspace that the next session re-hydrates from
    S3, so a local snapshot would be an unreferenced copy that write-back never
    sends anywhere. What used to be "which directory does CURRENT point at" is
    now `{atlas}/current.json` plus the mirror under `{atlas}/current/`.

    **Order is the whole point, and it is unchanged from Phase 3:** the objects
    go first, then the documents that name them, then the pointer that makes
    the version live. A catalog can never outlive the bytes it describes, and a
    failure at any step leaves the previous version serving. Raises rather than
    logging — a publish whose push failed is not a publish.
    """
    if not version:
        version = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    staging_path = atlas_path(config, version='staging')
    logger.info(f"Publishing NEW VERSION: {version} from {staging_path}")

    client = client or atlas_store._s3()
    catalog_bucket = atlas_store.catalog_bucket(config)

    # 1. Describe the version. Reads staging, reads the previous catalog from
    #    S3 for reuse, and writes nothing.
    catalog_summary = atlas_catalog.publish_catalog(
        config, staging_path, version, client=client, bucket=catalog_bucket)
    logger.info(
        f"STAC catalog: version={version} "
        f"layers written={len(catalog_summary['written_layers'])} "
        f"reused={len(catalog_summary['reused_layers'])} "
        f"missing={len(catalog_summary['missing_layers'])} "
        f"outlets written={len(catalog_summary['written_outlets'])} "
        f"reused={len(catalog_summary['reused_outlets'])}")

    # 2. The objects the documents will name.
    layer_push = atlas_store.publish_layer_data(
        config, staging_path, version, catalog_summary, client=client)
    logger.info(f"S3 layer publish: {layer_push}")
    if layer_push.get('status') == 'error':
        raise RuntimeError(f"layer publish failed: {layer_push.get('errors')}")

    outlet_push = atlas_store.publish_outlet_archive(
        config, staging_path, catalog_summary, client=client)
    logger.info(f"S3 outlet archive: {outlet_push}")

    # 3. The documents. Only now can they name nothing but objects that exist.
    documents = catalog_summary['documents']
    atlas_store.upload_documents(catalog_bucket, documents, client=client)
    logger.info(f"STAC documents: {len(documents)} written to "
                f"s3://{catalog_bucket}/{atlas_store.catalog_prefix(config['name'])}/")

    # 4. The serving mirror and the pointer that makes this version current.
    #    Last, because until it moves the previous version is still the one
    #    being served — which is the safe outcome of any failure above.
    push = atlas_store.publish_public_outlets(
        config, staging_path, version, client=client)
    logger.info(f"S3 outlet publish: {push}")

    return {
        'version': version,
        'catalog': {k: catalog_summary[k] for k in (
            'written_layers', 'reused_layers', 'missing_layers',
            'written_outlets', 'reused_outlets', 'missing_outlets', 'versions')},
        'layers': layer_push,
        'outlet_archive': outlet_push,
        'published': push,
    }


DISCARDED_DELTAS_DIRNAME = 'discarded'


def discard_pending_deltas(staging_path) -> list:
    """Move every unapplied delta into ``deltas/{layer}/discarded/``.

    A staging reset throws away edits, and a pending delta *is* an edit — left
    in place it would simply be re-applied by the next refresh, quietly undoing
    the reset. Deleting it outright is the other obvious answer and it is the
    wrong one: the delta system's whole premise is that an edit is a record,
    not a mutation, so a discarded edit is archived rather than erased.

    Not ``work/``, which means applied. Saying an edit was applied when it was
    thrown away would put a false statement in the only audit trail there is.
    """
    staging_path = Path(staging_path)
    deltas_root = staging_path / 'deltas'
    if not deltas_root.is_dir():
        return []

    moved = []
    for layer_dir in sorted(p for p in deltas_root.iterdir() if p.is_dir()):
        for delta in sorted(layer_dir.glob('*.geojson')):
            target = layer_dir / DISCARDED_DELTAS_DIRNAME / delta.name
            target.parent.mkdir(parents=True, exist_ok=True)
            delta.rename(target)
            moved.append(str(target.relative_to(staging_path)))
    if moved:
        logger.info(f"Discarded {len(moved)} pending delta(s) to "
                    f"{DISCARDED_DELTAS_DIRNAME}/")
    return moved


def _version_files(config, items, client=None) -> dict:
    """``{staging-relative path: (bucket, key)}`` for everything a version holds.

    Layer keys come off each Item's ``alternate.s3`` href and outlet keys from
    the archive prefix its Item records, so nothing here has to guess which
    version holds which bytes — the catalog already answers that.
    """
    settings = atlas_store.cloud_settings(config)
    private = settings.get('private_bucket') or ''

    wanted = {}
    for item in items:
        name = item.get('collection') or item.get('id')
        if federation.is_outlet_item(item):
            prefix = (item.get('properties') or {}).get(federation.PREFIX_PROP)
            if not (prefix and private):
                continue
            prefix = prefix.rstrip('/')
            for key in atlas_store.list_keys(private, f'{prefix}/', client=client):
                wanted[f'outlets/{name}/{key[len(prefix) + 1:]}'] = (private, key)
        else:
            for src_bucket, key, filename in atlas_catalog.item_source_keys(item):
                wanted[f'layers/{name}/{filename}'] = (src_bucket, key)
    return wanted


def current_version(config, client=None):
    """The version being served, from ``{atlas}/current.json``."""
    pointer = atlas_store.read_pointer(config, client=client)
    return (pointer or {}).get('version')


def published_versions(config, client=None) -> list:
    """Every published version, newest first, from the atlas's root Catalog."""
    bucket = atlas_store.catalog_bucket(config)
    if not bucket:
        return []
    root = atlas_store.get_json(
        bucket, f"{atlas_store.catalog_prefix(config['name'])}/catalog.json",
        client=client)
    return sorted(atlas_catalog.known_versions(root), reverse=True)


def set_current_version(config, version=None, client=None) -> dict:
    """Serve an already-published version — rollback, by default to the previous one.

    The published objects are already in S3, so this is a server-side copy into
    ``{atlas}/current/`` and a moved pointer. It replaces repointing the local
    `CURRENT` symlink, which described nothing once the box stopped holding
    version directories.
    """
    client = client or atlas_store._s3()
    versions = published_versions(config, client=client)
    if version is None:
        live = current_version(config, client=client)
        candidates = [v for v in versions if v != live]
        if not candidates:
            raise ValueError(
                f"Not enough versions to roll back: found {len(versions)}, "
                f"need at least 2.")
        version = candidates[0]
    elif version not in versions:
        raise ValueError(f"'{config['name']}' has no published version {version}")

    items = atlas_catalog.read_version_items(
        client, atlas_store.catalog_bucket(config), config['name'], version,
        atlas_store.catalog_base_url(config))
    if not items:
        raise ValueError(f"version {version} has no catalog to serve from")

    logger.info(f"Serving {config['name']} version {version}")
    return atlas_store.serve_version(config, version, items, client=client)


def reset_staging(config, client=None) -> dict:
    """Reset staging to the version currently being served.

    Restores the layer and outlet files that version holds, removes the ones it
    does not, and archives any unapplied delta. Runs against the *workspace*:
    the session's write-back is what carries the result to S3, which is also
    what makes it recoverable — the private bucket is versioned.

    There is no ``staging-backup-{timestamp}`` copy any more. It was a local
    directory nothing would have written back, and it existed to make a
    `copytree` reversible; the bucket's own versioning does that job properly.

    Every file is downloaded rather than diffed. A reset is rare and rarely
    cheap — it is the operation that throws work away — so the simple, obviously
    correct version is the right one.
    """
    client = client or atlas_store._s3()
    version = current_version(config, client=client)
    if not version:
        raise ValueError(f"'{config['name']}' has no published version to reset to")

    items = atlas_catalog.read_version_items(
        client, atlas_store.catalog_bucket(config), config['name'], version,
        atlas_store.catalog_base_url(config))
    if not items:
        raise ValueError(f"version {version} has no catalog to reset from")

    staging_path = atlas_path(config, version='staging')
    wanted = _version_files(config, items, client=client)
    logger.info(f"Resetting staging for {config['name']} to {version} "
                f"({len(wanted)} file(s))")

    removed = []
    for subdir in ('layers', 'outlets'):
        root = staging_path / subdir
        if not root.is_dir():
            continue
        for path in sorted(root.rglob('*')):
            if not path.is_file():
                continue
            relative = path.relative_to(staging_path).as_posix()
            if relative not in wanted:
                path.unlink()
                removed.append(relative)

    partial_root = atlas_workspace.partial_dir(config['data_root'], config['name'])
    for relative, (bucket, key) in sorted(wanted.items()):
        atlas_workspace.download_object(
            client, bucket, key, staging_path / relative, partial_root)

    discarded = discard_pending_deltas(staging_path)
    logger.info(f"Staging reset complete: {len(wanted)} restored, "
                f"{len(removed)} removed, {len(discarded)} delta(s) discarded")

    return {
        'status': 'success',
        'source_version': version,
        'restored': len(wanted),
        'removed': removed,
        'discarded_deltas': discarded,
        'staging_path': str(staging_path),
    }
