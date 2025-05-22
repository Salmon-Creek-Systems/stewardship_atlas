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
    utils.set_crs_raster(config, outpath)
    # then resample
    if inlet_config.get('resample', False):
        logger.info(f"resampling to {inlet_config['resample']}")
        utils.resample_raster_gdal( config, outpath,inlet_config['resample'])
    return outpath

