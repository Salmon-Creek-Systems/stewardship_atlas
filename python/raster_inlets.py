import logging, subprocess
import os, shutil, zipfile, io
import versioning
import utils
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
    logger.info(f"Delta raster in {outpath}")   
    return utils.canonicalize_raster(inpath, 
                                    outpath, 
                                    config['dataswale']['crs'], 
                                    config['dataswale']['bbox'], 
                                    inlet_config.get('resample_width', None))
    
    

    

