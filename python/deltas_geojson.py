"""
This module provides functionality for creating and processing delta files as local geojson files.
This implementation also depends on DuckDB for spatial joins and data processing.
The module includes the following main functions:

- create: Create the deltas directory structure for a layer.
- transform: Apply transformations to a GeoJSON feature.
- add_deltas: Add a new delta file to the layer's deltas directory.

- apply_deltas: Apply delta batch to a layer.
- build_layer: Return a FeatureCollection from a queue of delta batches.

"""




import json
import os
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Iterator, Dict, Any, List, Tuple
import geojson
from geojson import Feature, FeatureCollection
import duckdb

import versioning
import delta_provenance
import utils
import eddies
import reshape
import utils

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

def _unique_delta_path(directory: Path, source: str, action: str, ext: str) -> Path:
    """``{source}__{ts}__{action}.{ext}``, with a ``_cNNN`` suffix on the timestamp
    if a delta from the same second already exists (#180). Apply order comes
    from ``delta_sort_key``, which orders the suffix after the bare timestamp."""
    timestamp = datetime.now().strftime(delta_provenance.TIMESTAMP_FORMAT)

    def taken(ts):
        # Any source counts: two sources in the same second must still order.
        return any(directory.glob(f"*__{ts}__*")) or any((directory / 'work').glob(f"*__{ts}__*"))

    ts, n = timestamp, 1
    while taken(ts):
        ts = f"{timestamp}_c{n:03d}"
        n += 1
    return directory / f"{source}__{ts}__{action}.{ext}"


def delta_sort_key(path: Path):
    """Apply order: by the filename's timestamp field, not the whole name.

    The first field is the source, so a plain sort would apply every
    ``email_photo`` delta before every ``webedit`` one regardless of time.
    """
    parts = Path(path).stem.split("__")
    ts = parts[1] if len(parts) == 3 else ''
    return (ts, Path(path).name)


def delta_path(config: Dict[str, Any], asset_name: str, delta_action: str) -> str:
    """
    Return the path to the delta file for a given asset and delta action.
    The asset name is the delta's source token.
    """
    delta_provenance.validate_source(asset_name)
    delta_type = config['assets'][asset_name]['config']['data_type']
    layer_name = config['assets'][asset_name]['out_layer']
    d = versioning.atlas_path(config, "deltas") / layer_name
    d.mkdir(parents=True, exist_ok=True)
    (d / 'work').mkdir(exist_ok=True)
    return _unique_delta_path(d, asset_name, delta_action, delta_type)


def delta_path_from_layer(config: Dict[str, Any], layer_name: str, delta_action: str,
                          source: str) -> str:
    """
    Return the path to a new delta file for a layer, named for its source.

    ``source`` is required: an inlet asset name or one of
    ``delta_provenance.SOURCES`` (``webedit``, ``email_photo``, ...). Prefer
    ``write_delta``, which also embeds the provenance record.
    """
    delta_provenance.validate_source(source)
    d = versioning.atlas_path(config, "deltas") / layer_name
    d.mkdir(parents=True, exist_ok=True)
    (d / 'work').mkdir(exist_ok=True)
    return _unique_delta_path(d, source, delta_action, 'geojson')


def write_delta(config: Dict[str, Any], layer_name: str, feature_collection: Dict[str, Any],
                delta_action: str, source: str, asset: str = None,
                detail: Dict[str, Any] = None) -> Path:
    """Write a delta for ``layer_name`` with its provenance recorded.

    The single entry point for every non-inlet write (web edits, uploads,
    sheet imports, photos, comments, moves, /create). Returns the path.
    """
    outpath = Path(delta_path_from_layer(config, layer_name, delta_action, source))
    fc = dict(feature_collection)
    fc['provenance'] = delta_provenance.record(
        config, source=source, action=delta_action, layer=layer_name,
        feature_count=len(fc.get('features') or []), asset=asset, detail=detail)
    with versioning.atlas_file(outpath, mode="wt") as outfile:
        json.dump(fc, outfile, default=utils.json_serial)
    logger.info(f"Wrote delta {outpath} ({fc['provenance']['feature_count']} features, source={source})")
    return outpath


def stamp_provenance(config: Dict[str, Any], path: Path, source: str, asset: str = None,
                     detail: Dict[str, Any] = None) -> None:
    """Add the provenance member to a GeoJSON delta written by another tool
    (ogr2ogr, an Overpass dump). No-op for non-GeoJSON deltas (rasters)."""
    path = Path(path)
    if path.suffix not in ('.geojson', '.json') or not path.exists():
        return
    try:
        with open(path) as f:
            fc = json.load(f)
    except ValueError:
        logger.warning(f"Could not stamp provenance on {path}: not JSON")
        return
    parts = delta_provenance.parse_filename(path)
    layer_name = path.parent.name
    fc['provenance'] = delta_provenance.record(
        config, source=source, action=parts['action'] or 'create', layer=layer_name,
        feature_count=len(fc.get('features') or []), asset=asset, detail=detail)
    with open(path, 'w') as f:
        json.dump(fc, f, default=utils.json_serial)


def add_deltas_from_features(
        config: Dict[str, Any],
        asset_name: str,
        feature_collection: FeatureCollection,
        delta_action: str,
        layer_name: str = None,
        source: str = None) -> List[str]:
    """
    Add a new delta file to the layer's deltas directory.

    Args:
        config: Configuration dictionary
        asset_name: the asset generating the delta (its name is the source
            token unless ``source`` is given)
        feature_collection: GeoJSON FeatureCollection to store
        delta_action: create / annotate / match / reshape / delete
        layer_name: target layer; defaults to the asset's ``out_layer``
        source: explicit source token (e.g. ``s3_photo_import``)
    Returns:
        [path to written file]
    """
    source = source or asset_name
    if not layer_name:
        layer_name = config['assets'][asset_name]['out_layer']
    outpath = write_delta(config, layer_name, feature_collection, delta_action,
                          source=source, asset=asset_name)
    return [str(outpath)]


def extract_intersecting_features(layer_path: Path, polygon_fc: dict) -> List[Feature]:
    """Return features from layer_path whose geometry intersects any polygon in polygon_fc.

    polygon_fc must be a GeoJSON FeatureCollection dict (not a path).
    """
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.geojson', delete=False) as f:
        json.dump(polygon_fc, f)
        sel_path = f.name
    try:
        duckdb.sql("INSTALL spatial; LOAD spatial;")
        duckdb.sql(f"DROP TABLE IF EXISTS _sel_poly; CREATE TABLE _sel_poly AS SELECT * FROM ST_Read('{sel_path}');")
        duckdb.sql(f"DROP TABLE IF EXISTS _src_feats; CREATE TABLE _src_feats AS SELECT COLUMNS('.*') AS \"feat_\\0\" FROM ST_Read('{layer_path}');")
        res = duckdb.sql("""
            SELECT ST_AsGeoJSON(feat_geom) AS geometry, * EXCLUDE (feat_geom)
            FROM _src_feats
            WHERE EXISTS (
                SELECT 1 FROM _sel_poly WHERE ST_Intersects(_sel_poly.geom, feat_geom)
            )
        """)
        return [
            Feature(
                geometry=geojson.loads(row[0]),
                properties={col[len("feat_"):]: val for col, val in zip(res.columns[1:], row[1:])}
            )
            for row in res.fetchall()
        ]
    finally:
        os.unlink(sel_path)


def _apply_delete_delta(layer_filepath: Path, delta_filepath: Path) -> None:
    """Remove layer features that intersect the drawn polygon in the delete delta.

    Uses DuckDB spatial join (same pattern as annotate) — keeps all features
    that do NOT intersect the delta polygon, discards the rest.

    Moves the consumed delta to the work/ subdirectory when done.
    """
    duckdb.sql("INSTALL spatial; LOAD spatial;")
    duckdb.sql(f"DROP TABLE IF EXISTS del_poly; CREATE TABLE del_poly AS SELECT * FROM ST_Read('{delta_filepath}');")
    duckdb.sql(f"DROP TABLE IF EXISTS feat; CREATE TABLE feat AS SELECT COLUMNS('.*') AS \"feat_\\0\" FROM ST_Read('{layer_filepath}');")

    # Anti-join: keep features with no intersection with the delete polygon.
    # Use NOT EXISTS to avoid including del_poly geometry columns in the result,
    # then explicitly convert feat_geom to GeoJSON and exclude the raw geometry column.
    res = duckdb.sql("""
        SELECT ST_AsGeoJSON(feat_geom) AS geometry, * EXCLUDE (feat_geom)
        FROM feat
        WHERE NOT EXISTS (
            SELECT 1 FROM del_poly WHERE ST_Intersects(del_poly.geom, feat_geom)
        )
    """)

    surviving = [
        geojson.Feature(
            geometry=geojson.loads(row[0]),
            properties={
                col[len("feat_"):]: val
                for col, val in zip(res.columns[1:], row[1:])
            }
        )
        for row in res.fetchall()
    ]

    geojson.dump(geojson.FeatureCollection(surviving), open(layer_filepath, "w"), default=utils.json_serial)
    logger.info(f"Delete delta removed features; {len(surviving)} remaining in {layer_filepath}")

    moved_path = delta_filepath.parent / "work" / delta_filepath.name
    delta_filepath.rename(moved_path)
    logger.info(f"Moving consumed delete delta: {delta_filepath} -> {moved_path}")


def layer_polygon_shape(config: Dict[str, Any], layer_name: str) -> str:
    """The layer's `polygon_shape` setting, or 'raw'.

    Read where create deltas are applied, so an imported, uploaded and
    hand-drawn polygon all get the same treatment — regions, in particular,
    have to be page-shaped however they arrived.
    """
    for layer in config.get('dataswale', {}).get('layers', []):
        if layer.get('name') == layer_name:
            return layer.get('polygon_shape', 'raw')
    return 'raw'


def apply_deltas(config: Dict[str, Any], layer_name: str, overwrite: bool = False) -> FeatureCollection:
    """
   Apply all delta file sin order.

    """
    layer_dir = versioning.atlas_path(config, "layers") / layer_name
    deltas_dir = versioning.atlas_path(config, "deltas") / layer_name
    processed_dir = deltas_dir / "processed"
    work_dir = deltas_dir / "work"
    
    work_dir.mkdir(parents=True, exist_ok=True)

    # Get all .geojson files in the deltas directory, sorted by filename (timestamp order)
    delta_files = sorted(deltas_dir.glob("*.geojson"), key=delta_sort_key)

    
    # start an empypty layer file
    layer_filepath = layer_dir / f"{layer_name}.geojson"
    if (not layer_filepath.exists()) or overwrite:
        with versioning.atlas_file(layer_filepath, mode="wt") as outfile:
            geojson.dump(FeatureCollection(features=[]), outfile)
    logger.info(f"Starting Apply Deltas: {deltas_dir}: {delta_files} -> {work_dir}")
    for i,filepath in enumerate(delta_files):
        logger.info(f"delta file {filepath}")
        asset_name, ts, action = filepath.stem.split("__")
        logger.info(f"delta file {filepath}: {asset_name} @ {ts} -> {action}")
        # This is a Delta of new features
        if action == "create":
            logger.info(f"delta file {filepath}: {asset_name} @ {ts} -> {action}")
            # read the layer file
            with open(layer_filepath, mode="rt") as infile:
                layer = geojson.load(infile)
                logger.info(f"Apply Delta loaded {len(layer['features'])} LAYER  features from {layer_filepath}...")
            with versioning.atlas_file(filepath, mode="rt") as infile:                
                # for now, apply transforms to delta here, as static file...
                delta = geojson.load(infile)
                logger.info(f"Apply Delta loaded {len(delta['features'])} DELTA features from {filepath}...")
            new_features = delta['features']
            polygon_shape = layer_polygon_shape(config, layer_name)
            if polygon_shape != 'raw':
                new_features = [utils.shape_feature(f, polygon_shape) for f in new_features]
                logger.info(f"Applied polygon_shape='{polygon_shape}' to {len(new_features)} features")
            with versioning.atlas_file(layer_filepath, mode="wt") as outfile:
                geojson.dump(FeatureCollection(features=layer['features'] + new_features), outfile)

                moved_path = filepath.parent / 'work' / filepath.name
            logger.info(f"moving consumed delta: {filepath} -> {moved_path}")
            filepath.rename(moved_path)
            
        # This is a Delta of anotation features which update properties for existing features
        elif action == "annotate":
            # delta_annotate_spatial_duckdb(config:Dict[str, Any], layer_name:str, delta_name:str, anno_type: str = "deltas", updated_properties: List[str] = [])
            output = eddies.delta_annotate_spatial_duckdb(
                config=config,
                layer_name = layer_name,
                delta_name=filepath,
                anno_in_path=filepath,
                anno_type= "deltas")

        # ID-based property update — finds features by atlas_id, no spatial join needed
        elif action == "match":
            with open(layer_filepath, mode="rt") as infile:
                layer = geojson.load(infile)
            with open(filepath, mode="rt") as infile:
                delta = geojson.load(infile)
            delta_by_id = {
                f['properties']['atlas_id']: f
                for f in delta['features']
                if f.get('properties', {}).get('atlas_id')
            }
            for feature in layer['features']:
                fid = feature.get('properties', {}).get('atlas_id')
                if fid in delta_by_id:
                    for k, v in delta_by_id[fid]['properties'].items():
                        if k != 'atlas_id' and v is not None:
                            feature.setdefault('properties', {})[k] = v
            with versioning.atlas_file(layer_filepath, mode="wt") as outfile:
                geojson.dump(FeatureCollection(features=layer['features']), outfile)
            filepath.rename(filepath.parent / 'work' / filepath.name)

        # ID-based geometry replacement (webedit Reshape) — properties untouched
        elif action == "reshape":
            with open(layer_filepath, mode="rt") as infile:
                layer = geojson.load(infile)
            with open(filepath, mode="rt") as infile:
                delta = geojson.load(infile)
            polygon_shape = layer_polygon_shape(config, layer_name)
            shape_fn = ((lambda f: utils.shape_feature(f, polygon_shape))
                        if polygon_shape != 'raw' else None)
            reshaped, skipped = reshape.apply_reshape(layer['features'], delta['features'], shape_fn)
            logger.info(f"Reshape delta {filepath}: {len(reshaped)} reshaped, {len(skipped)} skipped")
            with versioning.atlas_file(layer_filepath, mode="wt") as outfile:
                geojson.dump(FeatureCollection(features=layer['features']), outfile)
            filepath.rename(filepath.parent / 'work' / filepath.name)

        # This is a Delta containing a drawn polygon — remove all layer features it intersects
        elif action == "delete":
            _apply_delete_delta(layer_filepath, filepath)

        else:
            raise InvalidDelta(f"Invalid action: {action}") 
        #delta_in_path = Path(filepath)
        #delta_out_path = delta_in_path.parent / "work" / delta_in_path.name

        
    return geojson.load(open(layer_filepath))
    
    
def apply_deltas_overwrite(config: Dict[str, Any], layer_name: str, overwrite: bool = False) -> FeatureCollection:
    return apply_deltas(config=config, layer_name=layer_name, overwrite=True)
     
