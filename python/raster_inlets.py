import logging, subprocess
import os, shutil, zipfile, io
import versioning

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
    set_crs_raster(config, outpath)
    # then resample
    if inlet_config.get('resample', False):
        logger.info(f"resampling to {inlet_config['resample']}")
        resample_raster_gdal(inlet_config['resample'], config, outpath)
    return outpath

def fetch_url(config=None, name=None):
    """Fetch data from URL and save to versioned outpath"""

    
    # Resolve versioned path if configs are provided
    # if atlas_config and swale_config:
    # outlet path stuff
    #version_string = swale_config.get('version_string')
    #local_path = inlet_config['outpath_template'].format(**swale_config)
    inlet_conf = config['assets'][name]
    outpath = resolve_versioned_path(config, f"{inlet_conf['out_layer']}/delta/{inlet_conf['outpath_template']}")
    # Ensure output directory exists
    os.makedirs(os.path.dirname(outpath), exist_ok=True)
    
    url = inlet_config['inpath_template'].format(
        shared_data_root=config['data_root'],
        north=config['dataswale']['bbox']['north'],
        south=config['dataswale']['bbox']['south'],
        east=config['dataswale']['bbox']['east'],
        west=config['dataswale']['bbox']['west'],
        **config)
    logger.debug(f"Fetching data from URL: {url}")
 
    
    try:
        response = requests.get(url)
        response.raise_for_status()
        
        # Handle zip files
        if url.endswith('.zip') or  inlet_config.get('unzip', False):
            logger.debug("Extracting zip contents")
            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                z.extractall(os.path.dirname(outpath))
        else:
            # Write content directly to file
            with open(outpath, 'wb') as f:
                f.write(response.content)
                logger.debug(f"Successfully wrote content to: {outpath}")
                    
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch URL {url}: {e}")
        raise
    except Exception as e:
        logger.error(f"Error processing data from {url}: {e}")
        raise

    #if inlet_config.get('unzip', False):
    #    with ZipFile(BytesIO(r.content)) as zip_ref:
    #        zip_ref.extractall(os.path.dirname(outpath))
    #else:
    #    with open(outpath, mode="wb") as f:
    #        f.write(r.content)
    #    z = ZipFile(BytesIO(r.content.read()))
    #    z.extractall(path=outpath)
    if inlet_config['data_type'] == 'tiff':
        logger.info(f"Setting raster CRS and resampling...")
        # first, set the crs
        set_crs_raster(swale_config, outpath)
        # then resample
        if inlet_config.get('resample', False):
            resample_raster_gdal(inlet_config['resample'], swale_config, local_path, atlas_config=atlas_config)
         #else:
         #    logger.debug(f"data type in fetch_url: {inlet_config['data_type']}")
    
                
    return outpath
