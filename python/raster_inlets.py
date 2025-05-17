import logging, subprocess
import os, shutil, zipfile, io
import versioning
from pathlib import Path

# Configure logging
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)
logger.setLevel(logging.INFO)


def resample_raster_gdal(config, inpath, resample_width=400):
    """Resample raster to target CRS using versioned paths"""
    logger.info(f"Resampling for: [{inpath}]")
    inpath = Path(inpath)
    temp_path = inpath.parent / f"tmp.{inpath.name}"

    # Move input to temp path
    os.rename(inpath, temp_path)
    logger.debug(f"Resampling raster to {config['dataswale']['crs']} @ width {resample_width}: {inpath}")
    
    # Perform resampling
    subprocess.check_output([
        'gdalwarp', '-r', 'bilinear',
        '-ts', str(resample_width), '0',
        '-t_srs', config['dataswale']['crs'],
        str(temp_path), str(inpath)
    ])
    # TODO - remove temp path
    return inpath



def set_crs_raster(config, inpath):
    """Set CRS for raster using versioned paths"""
    inpath = Path(inpath)
    temp_path = inpath.parent / f"tmp.{inpath.name}"
    logger.debug(f"Setting raster CRS to {config['dataswale']['crs']}: {inpath}")
    subprocess.check_output(['gdalwarp', '-t_srs', config['dataswale']['crs'], str(inpath), str(temp_path)])
    # Move temp file to final location
    os.rename(temp_path, inpath)  
    return inpath

def local_raster(config=None, name=None, delta_queue=None):
    """Fetch data from local file and save to versioned outpath.
    Do some CRS and rescaling if needed."""
    inlet_config = config['assets'][name]['config']
    inpath = versioning.atlas_path(config, "local") / inlet_config['inpath_template'].format(**config)
    outpath = delta_queue.delta_path(config, name, 'create')

    # TODO make this do more - should select subregion, etc.
    shutil.copy(inpath, outpath)

    
    logger.info(f"Setting raster CRS to {config['dataswale']['crs']}")
        # first, set the crs
    set_crs_raster(config, outpath)
    # then resample
    if inlet_config.get('resample', False):
        logger.info(f"resampling to {inlet_config['resample']}")
        resample_raster_gdal( config, outpath,inlet_config['resample'])
    return outpath

