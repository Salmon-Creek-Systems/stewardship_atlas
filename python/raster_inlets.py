import logging, subprocess, requests
import os, shutil, zipfile, io
import versioning
import utils
from pathlib import Path
from typing import List, Dict, Tuple, Any

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
    
    

def url_raster(config: Dict[str, Any], name: str, delta_queue: [Any, None]):
    """Fetch data from URL and save to versioned outpath"""
    inlet_config = config['assets'][name]['config']
    bbox = config['dataswale']['bbox']
    # inpath = versioning.atlas_path(config, "local") / inlet_config['inpath_template'].format(**config)
    outpath = delta_queue.delta_path(config, name, 'create')
    workdir = outpath.parent / 'work'
    workfile = workdir / outpath.name 
    
    url = inlet_config['inpath_template'].format(
        north=bbox['north'],
        south=bbox['south'],
        east=bbox['east'],
        west=bbox['west'],
        **config)
    logger.info(f"Fetching data from URL: {url}")
    try:
        response = requests.get(url)
        response.raise_for_status()

        if url.endswith('.zip') or  inlet_config.get('unzip', False):
            logger.debug("Extracting zip contents")
            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                z.extractall(os.path.dirname(workfile))
        else:
            # Write content directly to file
            with open(workfile, 'wb') as f:
                f.write(response.content)
                logger.debug(f"Successfully wrote content to: {workfile}")
                
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch URL {url}: {e}")
        raise
    except Exception as e:
        logger.error(f"Error processing data from {url}: {e}")
        raise
 
    return utils.canonicalize_raster(workfile, 
                                    outpath, 
                                    config['dataswale']['crs'], 
                                    config['dataswale']['bbox'], 
                                    inlet_config.get('resample_width', None))
    

asset_methods = {
    'url_raster': url_raster,
    'local_raster': local_raster
    }
