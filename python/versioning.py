from pathlib import Path
import datetime
import logging
import shutil
import json

import atlas_store
import atlas_catalog

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


def reset_staging(config):
    """
    Reset staging to match CURRENT version.
    Backs up existing staging before replacing.
    
    Returns dict with status and paths.
    """
    staging_path = atlas_path(config, version='staging')
    current_path = atlas_path(config, version='CURRENT')
    
    # Resolve CURRENT symlink to get actual version
    if not current_path.is_symlink():
        raise ValueError("CURRENT is not a symlink - cannot determine current version")
    
    current_target = current_path.resolve()
    current_version = current_target.name
    logger.info(f"Resetting staging from CURRENT ({current_version})")
    
    # Backup staging
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    backup_path = staging_path.parent / f"staging-backup-{timestamp}"
    
    logger.info(f"Backing up staging to {backup_path}")
    shutil.move(str(staging_path), str(backup_path))
    
    # Copy CURRENT to staging
    logger.info(f"Copying {current_target} to {staging_path}")
    shutil.copytree(current_target, staging_path, symlinks=True)
    
    logger.info(f"Staging reset complete")
    
    return {
        "status": "success",
        "source_version": current_version,
        "backup_path": str(backup_path),
        "staging_path": str(staging_path)
    }
