import geojson
import logging, json
import shutil
import uuid
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
import utils

# Layer geometry_types backed by a {layer}.geojson file. 'raster', 'document'
# and 'wms' layers are refreshed by other means.
VECTOR_GEOMETRY_TYPES = {'point', 'linestring', 'polygon'}


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


def add_webmap_urls(config, layer_name, fc, zoom=17):
    """
    Add webmap_url property to each feature in the feature collection.
    
    Args:
        config: Atlas configuration dict
        layer_name: Name of the layer
        fc: GeoJSON FeatureCollection
        zoom: Zoom level for the webmap link (default: 14)
    
    Returns:
        Modified FeatureCollection with webmap_url in each feature's properties
    """
    from shapely.geometry import shape
    
    base_url = config.get('base_url', '')
    if not base_url:
        logger.warning(f"No base_url in config, webmap_url will be relative")
    
    feature_count = 0
    for feature in fc.get('features', []):
        try:
            # Get geometry and calculate centroid
            geom = shape(feature['geometry'])
            centroid = geom.centroid
            
            # Construct webmap URL
            webmap_url = f"{base_url}/staging/outlets/webmap/?lat={centroid.y}&lng={centroid.x}&zoom={zoom}"
            
            # Add to properties
            if 'properties' not in feature:
                feature['properties'] = {}
            feature['properties']['webmap_url'] = webmap_url
            feature_count += 1
            
        except Exception as e:
            logger.warning(f"Failed to add webmap_url to feature in {layer_name}: {e}")
            continue
    
    logger.info(f"Added webmap_url to {feature_count} features in {layer_name}")
    return fc


def add_show_labels(config, layer_name, fc):
    """
    Set show_label property on each feature for layers with label_deduplicate enabled.

    When label_deduplicate is true on the layer config, one feature per unique name
    value gets show_label=True; all others get show_label=False. Features with a
    missing or null name get show_label=False.

    When label_deduplicate is not set, returns fc unchanged.
    """
    layer_config = next(
        (l for l in config.get('dataswale', {}).get('layers', []) if l.get('name') == layer_name),
        {}
    )
    if not layer_config.get('label_deduplicate', False):
        return fc

    seen_names = set()
    for feature in fc.get('features', []):
        if 'properties' not in feature:
            feature['properties'] = {}
        name_val = feature['properties'].get('name')
        if name_val and name_val not in seen_names:
            feature['properties']['show_label'] = True
            seen_names.add(name_val)
        else:
            feature['properties']['show_label'] = False

    logger.info(f"show_label assigned for {layer_name}: {len(seen_names)} unique labels")
    return fc


def refresh_vector_layer(config, name, delta_queue_builder=DQB):
    """
    Rebuild the geojson for a layer in the dataswale from the current state of the Delta Queue.
    """

    layer_path = versioning.atlas_path(config, 'layers') / name / f'{name}.geojson'

    fc = delta_queue_builder(config, name)
    new_features = []
    for feature in fc['features']:
        if len(feature['geometry']['coordinates']) < 1:
            logger.warning(f"Feature {feature} has no coordinates")
        elif feature['geometry']['type'] in ['LineString', 'Polygon', 'MultiLineString'] and len(feature['geometry']['coordinates'][0]) < 2:
            logger.warning(f"Feature {feature} has insufficient  coordinates")
        else:
            if not feature.get('properties', {}).get('atlas_id'):
                feature.setdefault('properties', {})['atlas_id'] = str(uuid.uuid4())
            new_features.append(feature)
    fc['features'] = new_features
    # Add webmap URLs to each feature
    fc = add_webmap_urls(config, name, fc)
    # Assign show_label for layers with label_deduplicate
    fc = add_show_labels(config, name, fc)
    
    logger.debug(f"Writing to {layer_path} FC: {fc}")
    logger.info(f"Writing to {layer_path}")
    
    with versioning.atlas_file(layer_path, 'wt') as outfile:
        geojson.dump(fc, outfile, default=utils.json_serial)
    return layer_path


def refresh_all_vector_layers(config, delta_queue_builder=DQB):
    """
    Bring every vector layer in the atlas into a valid on-disk state.

    Two distinct gaps, both of which leave a layer defined-but-empty:

    1. An inlet writes deltas, not a layer — the layer geojson only appears once
       the deltas are applied. Any vector layer with pending deltas is refreshed.
       We sweep the deltas tree rather than reading out_layer off each asset,
       because some inlets name their target layer internally (inaturalist,
       h3_grid, federation and gazetteer all write via layer_name=).
    2. A layer with no inlet at all (hand-entry ones: photos, hydrants,
       watertanks, private_notes, hazards) never gets a file written, and the
       webmap 404s on it. Those are initialized to an empty FeatureCollection.

    Raster, document and wms layers are refreshed by other means and are skipped.

    Returns a list of (layer_name, action) tuples, where action is 'refreshed',
    'initialized', or an error string prefixed with 'failed: '. Each layer
    appears at most once.
    """
    vector_layers = {l['name'] for l in config.get('dataswale', {}).get('layers', [])
                     if l.get('geometry_type') in VECTOR_GEOMETRY_TYPES}
    layers_root = versioning.atlas_path(config, 'layers')
    deltas_root = versioning.atlas_path(config, 'deltas')
    results = []
    failed = set()

    if deltas_root.is_dir():
        for layer_dir in sorted(d for d in deltas_root.iterdir() if d.is_dir()):
            name = layer_dir.name
            if name not in vector_layers or not any(layer_dir.glob('*.geojson')):
                continue
            try:
                refresh_vector_layer(config, name, delta_queue_builder)
                results.append((name, 'refreshed'))
            except Exception as e:
                logger.warning(f"Refresh of layer {name} failed: {e}")
                results.append((name, f'failed: {e}'))
                failed.add(name)

    for name in sorted(vector_layers):
        # Don't paper over a failed refresh with an empty file — that would
        # report success and hide a layer that genuinely has data pending.
        if name in failed or (layers_root / name / f'{name}.geojson').exists():
            continue
        try:
            clear_vector_layer(config, name, delta_queue_builder)
            results.append((name, 'initialized'))
        except Exception as e:
            logger.warning(f"Initializing layer {name} failed: {e}")
            results.append((name, f'failed: {e}'))

    return results


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



def rename_layer_file(staging_path, old_name, new_name, dry_run=False):
    """Rename the layer directory and GeoJSON file within the dataswale."""
    staging_path = Path(staging_path)
    src_dir = staging_path / 'layers' / old_name
    dst_dir = staging_path / 'layers' / new_name
    if dry_run:
        print(f"  [dry_run] layers/{old_name}/ → layers/{new_name}/")
        print(f"  [dry_run] layers/{new_name}/{old_name}.geojson → layers/{new_name}/{new_name}.geojson")
        return
    if not src_dir.exists():
        raise FileNotFoundError(f"Layer directory not found: {src_dir}")
    src_dir.rename(dst_dir)
    geojson_file = dst_dir / f'{old_name}.geojson'
    if geojson_file.exists():
        geojson_file.rename(dst_dir / f'{new_name}.geojson')
    print(f"  layers/{old_name}/ → layers/{new_name}/")
    print(f"  layers/{new_name}/{old_name}.geojson → layers/{new_name}/{new_name}.geojson")


def copy_layer_file(staging_path, old_name, new_name, dry_run=False):
    """Copy a layer's data directory to a new layer name (data only, no deltas).

    Copies layers/{old_name}/ → layers/{new_name}/, then renames any file whose
    stem is old_name (e.g. {old}.geojson, {old}.tiff, {old}.tiff.jpg) to new_name.
    Sidecar files like stats.json are left untouched.
    """
    staging_path = Path(staging_path)
    src_dir = staging_path / 'layers' / old_name
    dst_dir = staging_path / 'layers' / new_name
    if dry_run:
        print(f"  [dry_run] layers/{old_name}/ → layers/{new_name}/ (copy)")
        print(f"  [dry_run] rename files with stem '{old_name}' → '{new_name}' in copy")
        return
    if not src_dir.exists():
        raise FileNotFoundError(f"Layer directory not found: {src_dir}")
    if dst_dir.exists():
        raise FileExistsError(f"Destination layer directory already exists: {dst_dir}")
    shutil.copytree(src_dir, dst_dir)
    for f in list(dst_dir.iterdir()):
        # rename only files named after the layer, preserving compound suffixes
        # (e.g. hydrants.tiff.jpg → hydrants stem, ".tiff.jpg" suffix)
        if f.is_file() and (f.name == old_name or f.name.startswith(old_name + '.')):
            suffix = f.name[len(old_name):]
            f.rename(dst_dir / f'{new_name}{suffix}')
    print(f"  layers/{old_name}/ → layers/{new_name}/ (copied)")


def rename_deltas_dir(staging_path, old_name, new_name, dry_run=False):
    """Rename the deltas directory for a layer (includes work/ subdir)."""
    staging_path = Path(staging_path)
    src = staging_path / 'deltas' / old_name
    dst = staging_path / 'deltas' / new_name
    if dry_run:
        print(f"  [dry_run] deltas/{old_name}/ → deltas/{new_name}/")
        return
    if src.exists():
        src.rename(dst)
        print(f"  deltas/{old_name}/ → deltas/{new_name}/")
    else:
        print(f"  deltas/{old_name}/ not found, skipping")


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

