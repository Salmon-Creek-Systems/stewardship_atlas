import geojson
import logging



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

def refresh_layer(config, name, delta_queue_builder):
    """
    Rebuild the geojson for a layer in the dataswale from the current state of the Delta Queue.
    """
    layer_path = versioning.atlas_path(config, 'layers') / name / f'{name}.geojson'
    fc = delta_queue_builder(config, name)
    logger.info(f"Writing to {layer_path} FC: {fc}")
    
    with versioning.atlas_file(layer_path, 'wt') as outfile:
        geojson.dump(fc, outfile)
    return layer_path
