from pathlib import Path
import datetime
import logging
import shutil

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
    
    staging_path = atlas_path(config, version='staging')
    version_path = atlas_path(config, version=version)
    logger.info(f"Publishing NEW VERSION: {version} from {staging_path} to {version_path}")

    # add version to config
    config['dataswale']['versions'].append(version)
    atlas_config_path = atlas_path(config, version='staging', local_path="atlas_config.json")
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

 
   


    # copy all files to new version
    logger.info(f"About to `shutil.copytree` from '{staging_path}' to '{version_path}'...")
    shutil.copytree(staging_path, version_path, symlinks=True)

    # empty /work dirs in staging to prepare for new changes
    for work_dir in staging_path.glob('work/*'):
        shutil.rmtree(work_dir)

    # point "production" to new version
    # repoint symbolic link in atlas root dir to new version
    #atlas_root = Path(config['data_root']) / config['name']
    #atlas_root.symlink_to(atlas_path)

    return version_path
