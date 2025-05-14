import json
import os
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Iterator, Dict, Any, List, Tuple
import geojson
from geojson import Feature, FeatureCollection

import versioning

# Configure logging
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)
logger.setLevel(logging.INFO)

class InvalidDelta(Exception):
    """Exception raised for invalid delta data"""
    def __init__(self, message: str, details: Dict[str, Any] = None):
        self.message = message
        self.details = details or {}
        super().__init__(self.message)

def create(config: Dict[str, Any]) -> None:
    """
    Create the deltas directory structure for a layer.
    
    Args:
        config: Configuration dictionary containing at least:
            - name: Layer name
            - data_root: Root directory for data storage
    
    Raises:
        ValueError: If required config fields are missing
    """
    if not config.get('name'):
        raise ValueError("Configuration must include 'name' field")
    if not config.get('data_root'):
        print(f"WWTF: config: {config}")
        raise ValueError("Configuration must include 'data_root' field")
    
    deltas_dir = Path(config['data_root']) / f"deltas_{config['name']}"
    processed_dir = deltas_dir / "processed"
    
    # Create directories if they don't exist
    deltas_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(exist_ok=True)
    
    logger.info(f"Created/verified deltas directory structure at {deltas_dir}")

def transform(feature: Feature, config: Dict[str, Any]) -> Feature:
    """
    Apply transformations to a GeoJSON feature.
    
    Args:
        feature: GeoJSON Feature to transform
        config: Configuration dictionary containing transformation settings
    
    Returns:
        Transformed GeoJSON Feature
    """
    if not feature.get('properties'):
        feature['properties'] = {}
    
    # Set vector width from config or default
    vector_width = config.get('vector_width', 2)
    feature['properties']['vector_width'] = vector_width
    
    return feature

def add_deltas(config: Dict[str, Any], asset_name: str, feature_collection: FeatureCollection, ) -> Tuple[int, str]:
    """
    Add a new delta file to the layer's deltas directory.
    
    Args:
        feature_collection: GeoJSON FeatureCollection to store
        config: Configuration dictionary
        asset_name: the asset generating the delta
    Returns:
        Tuple of (number of features written, path to written file)
    
    Raises:
        InvalidDelta: If the feature collection is invalid
    """    
    # Create timestamp-based filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    layer_name = config['assets'][asset_name]['config']['out_layer']
    local_filename = f"deltas/{layer_name}/{asset_name}_{timestamp}.geojson"
    outpath = versioning.atlas_path(config, local_filename)

    with versioning.atlas_file(outpath, mode="wt") as outfile:
        json.dump(feature_collection, outfile)
    
    logger.info(f"Wrote {len(feature_collection['features'])} features to {outpath}")
    return len(feature_collection['features']), str(outpath)

def queue(config: Dict[str, Any]) -> Iterator[Feature]:
    """
    Return an iterator over all unprocessed delta files, applying transformations.
    
    Args:
        config: Configuration dictionary
    
    Returns:
        Iterator yielding transformed GeoJSON Features
    
    Raises:
        InvalidDelta: If any delta file is invalid
    """
    deltas_dir = Path(config['data_root']) / f"deltas_{config['name']}"
    processed_dir = deltas_dir / "processed"
    
    # Get all .geojson files in the deltas directory
    delta_files = sorted(deltas_dir.glob("*.geojson"))
    
    for filepath in delta_files:
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
            
            if not isinstance(data, dict) or 'features' not in data:
                raise InvalidDelta(f"Invalid GeoJSON structure in {filepath}")
            
            # Process each feature
            for feature in data['features']:
                try:
                    transformed = transform(feature, config)
                    yield transformed
                except Exception as e:
                    raise InvalidDelta(f"Failed to transform feature in {filepath}", {"error": str(e)})
            
            # Move file to processed directory
            processed_path = processed_dir / filepath.name
            shutil.move(filepath, processed_path)
            logger.info(f"Processed and moved {filepath} to {processed_path}")
            
        except json.JSONDecodeError as e:
            raise InvalidDelta(f"Invalid JSON in {filepath}", {"error": str(e)})
        except Exception as e:
            raise InvalidDelta(f"Error processing {filepath}", {"error": str(e)}) 
