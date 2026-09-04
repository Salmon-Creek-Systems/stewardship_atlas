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





    
def publish_new_version(config, version=None):
    """
    Publish a new version of the atlas
    """
    if not version:
        version = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    logger.info(f"Publishing NEW VERSION: {version}")
    staging_path = atlas_path(config, version='staging')
    version_path = atlas_path(config, version=version)
    logger.info(f"Publishing NEW VERSION: {version} from {staging_path} to {version_path}")

    # add version to config
    config['dataswale']['versions'].append(version)
    atlas_config_path = atlas_path(config, version='staging', local_path="atlas_config.json")
    logger.info(f"Adding version {version} to config at {atlas_config_path}")
    with open(atlas_config_path, 'w') as f:
        json.dump(config, f, indent=2)
    logger.info(f"Added version {version} to config")

    # make sure not already published
    if version_path.exists():
        logger.error(f"Version {version} already exists")
        raise ValueError(f"Version {version} already exists")
    # make sure parent exists
    logger.info(f"Making sure parent exists for {version_path}")
    version_path.parent.mkdir(parents=True, exist_ok=True)
    #logger.info(f"Parent exists for {version_path}")
    logger.info(f"Copying from {staging_path} to {version_path}")

 
   


    # Copy staging to the new version. 'versioned_outlets' is a copy filter
    # (issue #131, C9): which staging/outlets/<name> dirs are included in
    # the snapshot. Missing or empty means all. It is not a build list —
    # publish materializes nothing. The full deltas tree (pending + work/
    # archive) is always copied: versions keep their edit history (C10).
    versioned = config.get('dataswale', {}).get('versioned_outlets') or []
    outlets_path = staging_path / 'outlets'

    def _ignore_unversioned(dirpath, names):
        if versioned and Path(dirpath) == outlets_path:
            excluded = set(names) - set(versioned)
            if excluded:
                logger.info(f"Snapshot excludes non-versioned outlets: {sorted(excluded)}")
            return excluded
        return set()

    logger.info(f"About to `shutil.copytree` from '{staging_path}' to '{version_path}'...")
    shutil.copytree(staging_path, version_path, symlinks=True, ignore=_ignore_unversioned)

    # Phase 3 (#159): note what CURRENT points at *before* it is repointed. The
    # new version's catalog needs the previous version's Items so a layer whose
    # bytes did not change can be referenced there instead of re-stamped.
    previous_version_path = None
    _current_probe = atlas_path(config, version='CURRENT')
    try:
        if _current_probe.is_symlink():
            previous_version_path = _current_probe.resolve()
    except OSError as exc:
        logger.warning(f"Could not resolve CURRENT for catalog history: {exc}")

    # point "production" to new version
    # repoint symbolic link in atlas root dir to new version
    #atlas_root = Path(config['data_root']) / config['name']
    #atlas_root.symlink_to(atlas_path)

    current_path = atlas_path(config, version='CURRENT')
    current_path.unlink()
    current_path.symlink_to(version_path)
    logger.info(f"Linked {current_path} to {version_path}")
    print(f"Linked {current_path} to {version_path}")

    # Phase 2 (cloud_native_plan.md): mirror the public outlets of this version
    # to S3 so CloudFront can serve the read path. The local CURRENT symlink
    # above is deliberately untouched — the box keeps serving exactly as it
    # did, and this is additive until an atlas is cut over. A no-op unless the
    # atlas has `cloud.enabled`, and it never raises: a failed push must not
    # fail a good publish.
    # Phase 3 (#159): describe this version as a STAC catalog — Collection per
    # layer, Item per version — written into {version}/stac/. Additive: nothing
    # reads it yet, and like the S3 push below it must never fail a good
    # publish. A missing index is recoverable; a failed publish is an outage.
    try:
        catalog_summary = atlas_catalog.publish_catalog(
            config, version_path, version, previous_version_path)
        logger.info(f"STAC catalog: {catalog_summary}")
    except Exception as exc:
        logger.error(f"STAC catalog write failed for version {version} "
                     f"(publish continues): {exc}", exc_info=True)

    push = atlas_store.publish_public_outlets(config, version_path, version)
    logger.info(f"S3 outlet publish: {push}")

    return version_path


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
