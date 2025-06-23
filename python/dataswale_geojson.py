import geojson
import logging
import shutil
from typing import Iterator, Dict, Any, List, Tuple
import eddies
import deltas_geojson as deltas

DQB=deltas.apply_deltas

# Configure logging
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)
logger.setLevel(logging.INFO)

from pathlib import Path

import versioning

def create(config) -> str:
    pass

def delete():
    pass

def new_version():
    pass

def asset():
    pass

def refresh_vector_layer(config, name, delta_queue_builder=DQB):
    """
    Rebuild the geojson for a layer in the dataswale from the current state of the Delta Queue.
    """

    layer_path = versioning.atlas_path(config, 'layers') / name / f'{name}.geojson'

    fc = delta_queue_builder(config, name)
    logger.debug(f"Writing to {layer_path} FC: {fc}")
    logger.info(f"Writing to {layer_path}")
    
    with versioning.atlas_file(layer_path, 'wt') as outfile:
        geojson.dump(fc, outfile)
    return layer_path


def refresh_raster_layer(config, name, delta_queue_builder=DQB):
    """
    Rebuild the raster for a layer in the dataswale from the current state of the Delta Queue.
    """
    
    layer_path = versioning.atlas_path(config, 'layers') / name / f'{name}.tiff'
    layer_path.parent.mkdir(parents=True, exist_ok=True)
    deltas_dir = versioning.atlas_path(config, "deltas") / name
    
    for inpath in deltas_dir.glob("*.tiff"):
        shutil.copy(inpath, layer_path)
    return layer_path

def eddy(config:Dict[str, Any], eddy_name:str):
    """Apply Eddy to transform a dataswale layer into a new layer."
    """
    eddy_config = config['assets'][eddy_name]
    f = eddies.asset_methods[eddy_name]
    return f(config, eddy_name)
    
    #in_path = versioning.atlas_path(config, 'layers') / in_layer / f'{in_layer}.tiff'
    #out_path = versioning.atlas_path(config, 'layers') / out_layer / f'{out_layer}.tiff'
    #out_path.parent.mkdir(parents=True, exist_ok=True)
    #shutil.copy(in_path, out_path)
    #return out_path

