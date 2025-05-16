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

def refresh_vector_layer(config, name, delta_queue_builder):
    """
    Rebuild the geojson for a layer in the dataswale from the current state of the Delta Queue.
    """

    layer_path = versioning.atlas_path(config, 'layers') / name / f'{name}.geojson'

    fc = delta_queue_builder(config, name)
    logger.info(f"Writing to {layer_path} FC: {fc}")
    
    with versioning.atlas_file(layer_path, 'wt') as outfile:
        geojson.dump(fc, outfile)
    return layer_path


def refresh_raster_layer(config, name, delta_queue_builder):
    """
    Rebuild the raster for a layer in the dataswale from the current state of the Delta Queue.
    """
    layer_path = versioning.atlas_path(config, 'layers') / name / f'{name}.tiff'
    deltas_dir = versioning.atlas_path(config, "deltas") / name
    for inpath in deltas_dir.glob("*.tiff"):
        shutil.copy(inpath, layer_path)
    return layer_path


    # move file unchanged to layer location from delta....raster deltas are currently silly
