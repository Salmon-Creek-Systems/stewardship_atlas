import logging, subprocess, requests
import os, shutil, zipfile, io
import versioning
import utils
import deltas_geojson as deltas
from pathlib import Path
from typing import List, Dict, Tuple, Any

DELTA_QUEUE = deltas

# Configure logging
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)
logger.setLevel(logging.INFO)



def local_raster(config=None, name=None, delta_queue=DELTA_QUEUE):
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
    
    

def url_raster(config: Dict[str, Any], name: str, delta_queue=DELTA_QUEUE):
    """Fetch data from URL and save to versioned outpath"""
    inlet_config = config['assets'][name]['config']
    bbox = config['dataswale']['bbox']
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
    

def terrain_dem_inlet(config: Dict[str, Any], name: str, delta_queue=DELTA_QUEUE):
    """Fetch COP30 DEM expanded to tile-aligned bbox.

    For small atlases the atlas bbox is a tiny fraction of the tiles that
    terrain_rgb_to_pmtiles generates. This inlet fetches COP30 for the full
    extent of those tiles so the terrain-RGB has no nodata gaps within tile
    boundaries, eliminating the pit/wall artefacts around small atlases.

    Uses the same OpenTopography endpoint as opentopo_dem but expands the
    bbox to align with mercantile tile boundaries at the appropriate zoom.
    """
    import math
    import mercantile

    inlet_config = config['assets'][name]['config']
    atlas_bbox = config['dataswale']['bbox']

    # Compute min_zoom matching what terrain_rgb_to_pmtiles will use
    dem_width = abs(atlas_bbox['east'] - atlas_bbox['west'])
    auto_min = math.ceil(math.log2(90.0 / dem_width))
    min_zoom = max(inlet_config.get('min_zoom', 8), auto_min)

    # Expand bbox to the union of tiles at min_zoom covering the atlas
    covering = list(mercantile.tiles(
        atlas_bbox['west'], atlas_bbox['south'],
        atlas_bbox['east'], atlas_bbox['north'],
        zooms=[min_zoom]
    ))
    tile_bounds = [mercantile.bounds(t) for t in covering]
    expanded = {
        'west': min(b.west for b in tile_bounds),
        'south': min(b.south for b in tile_bounds),
        'east': max(b.east for b in tile_bounds),
        'north': max(b.north for b in tile_bounds),
    }
    logger.info(f"terrain_dem_inlet: z{min_zoom}, {len(covering)} tile(s), "
                f"bbox {atlas_bbox} → {expanded}")

    url = inlet_config['inpath_template'].format(
        north=expanded['north'], south=expanded['south'],
        east=expanded['east'], west=expanded['west'],
        **config
    )
    logger.info(f"Fetching terrain DEM: {url}")
    response = requests.get(url)
    response.raise_for_status()

    out_layer = config['assets'][name]['out_layer']
    out_dir = versioning.atlas_path(config, 'layers') / out_layer
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f'{out_layer}.tiff'

    with open(out_path, 'wb') as f:
        f.write(response.content)

    logger.info(f"terrain_dem_inlet: wrote {out_path} ({out_path.stat().st_size // 1024}KB)")
    return out_path


def s3_raster(config: Dict[str, Any], name: str, delta_queue=DELTA_QUEUE):
    """Fetch GeoTIFF from a private S3 bucket and save to versioned outpath."""
    import boto3

    inlet_config = config['assets'][name]['config']
    bucket = inlet_config['s3_bucket']
    key = inlet_config['s3_key']
    outpath = delta_queue.delta_path(config, name, 'create')
    workdir = outpath.parent / 'work'
    workfile = workdir / outpath.name

    logger.info(f"Fetching s3://{bucket}/{key}")
    try:
        boto3.client('s3').download_file(bucket, key, str(workfile))
    except Exception as e:
        logger.error(f"Failed to fetch s3://{bucket}/{key}: {e}")
        raise

    return utils.canonicalize_raster(workfile,
                                     outpath,
                                     config['dataswale']['crs'],
                                     config['dataswale']['bbox'],
                                     inlet_config.get('resample_width', None))


asset_methods = {
    'url_raster': url_raster,
    'fetch_url': url_raster,  # legacy name, kept for config compatibility
    'local_raster': local_raster,
    'terrain_dem': terrain_dem_inlet,
    's3_raster': s3_raster,
}
