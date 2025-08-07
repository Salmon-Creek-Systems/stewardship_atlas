from pathlib import Path
import datetime
import logging

# Configure logging
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)
logger.setLevel(logging.INFO)

def atlas_path(config=None, local_path='', version='staging'):
    """
    Return the path to the atlas file, with versioning if provided
    """
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
    staging_path = atlas_path(config, 'staging')
    version_path = atlas_path(config, version)
    # make sure not already published
    if version_path.exists():
        raise ValueError(f"Version {version} already exists")
    # make sure parent exists
    version_path.parent.mkdir(parents=True, exist_ok=True)
    
    # trigger materialization of all versioned assets in staging
    # assume all inlsets and eddies are currently materialized (...safe?)
    for outlet_name in config['versioned_outlets']:
        logger.info(f"Materializing outlet: {outlet_name}")
        outlets.materializers(config, outlet_name)
 
   


    # copy all files to new version
    shutil.copytree(staging_path, version_path)

    # empty /work dirs in staging to prepare for new changes
    for work_dir in staging_path.glob('work/*'):
        shutil.rmtree(work_dir)

    # point "production" to new version
    # repoint symbolic link in atlas root dir to new version
    atlas_root = Path(config['data_root']) / config['name']
    atlas_root.symlink_to(atlas_path)

    return version_path
