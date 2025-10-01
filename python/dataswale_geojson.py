import geojson
import logging, json
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


def clear_vector_layer(config, name, delta_queue_builder=DQB):
    """
    Rebuild the layer in the dataswale from the current state of the Delta Queue.
    """
    layer_path = versioning.atlas_path(config, 'layers') / name / f'{name}.geojson'
    with versioning.atlas_file(layer_path, mode="wt") as outfile:
            geojson.dump(geojson.FeatureCollection(features=[]), outfile)

    # refresh_vector_layer(config, name, delta_queue_builder)
    # refresh_raster_layer(config, name, delta_queue_builder)
    # refresh_document_layer(config, name, delta_queue_builder)


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
    work_dir = deltas_dir / 'work'
    work_path = work_dir / f'{name}.tiff'
    
    for inpath in deltas_dir.glob("*.tiff"):
        logger.info(f"refreshing raster layer [{name}]: {inpath} -> {layer_path} -> {work_path}")
        shutil.copy(inpath, layer_path)
        inpath.replace(work_path)
        
    return layer_path


def refresh_document_layer(config, name, delta_queue_builder=DQB):
    """
    Rebuild the raster for a layer in the dataswale from the current state of the Delta Queue.
    """
    
    layer_dir = versioning.atlas_path(config, 'layers') / name 
    layer_dir.mkdir(parents=True, exist_ok=True)
    deltas_dir = versioning.atlas_path(config, "deltas") / name
    work_dir = deltas_dir / 'work'
    # work_path = work_dir / f'{name}.tiff'
    
    for inpath in deltas_dir.glob("*"):
        if inpath.is_dir():
            logger.info(f"Skipping directory for layer update: {inpath}")
            continue
        doc_name = inpath.stem
        logger.info(f"refreshing document layer [{name}]: {inpath} {doc_name} -> {layer_dir} -> {work_dir}")
        shutil.copy(inpath, layer_dir / inpath.name)
        inpath.replace(work_dir / inpath.name)

        doc_data = {
            "name": inpath.stem,
            "file_type": inpath.suffix,
            "corners" : config['dataswale']['bbox'],
            "image_path": str( layer_dir / inpath.name )
            }
        
        with open(layer_dir / f"{inpath.stem}.json", "w") as f:
            logger.info(f"Creating doc JSON ({inpath.stem}.json): {doc_data}")
            json.dump(doc_data, f)
        
    return layer_dir



def layer_as_featurecollection(config:Dict[str, Any], name:str):    
    layer_path = layer_as_path(config, name)
    return geojson.load(open(layer_path))

def layer_as_path(config:Dict[str, Any], name:str):    
    return versioning.atlas_path(config, 'layers') / name / f'{name}.geojson'
    
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

