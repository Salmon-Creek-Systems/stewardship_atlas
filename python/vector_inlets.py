import logging, subprocess, os
import duckdb
import geojson
from shapely.geometry import shape

import versioning
import utils
import federation
import deltas_geojson as deltas

import overpass

DELTA_QUEUE = deltas

# Configure logging
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)
logger.setLevel(logging.INFO)


def overture_duckdb(config=None, name=None, delta_queue=DELTA_QUEUE, quick=False):
    """Fetch Overture data and return a Delta to push into queue"""
    version_string = 'staging'
    inlet_config = config['assets'][name]['config']

    
    # Get query from template
    query = inlet_config['inpath_template'].format(**config['dataswale']['bbox'])
    logger.info(f"Fetching Overture data with query: {query}")
    
    # Execute query
    duckdb.sql("""                                                                                                                   
INSTALL spatial;                                                                                                                     
LOAD spatial;                                                                                                                        
""")
    response = duckdb.sql(query)

    features = []
    for row in [dict(zip(response.columns, row)) for row in response.fetchall()]:
        f = geojson.Feature(geometry=geojson.loads(row['geom']))
        del(row['geom'])
        f['properties'] = row
        features.append(f)
    feature_collection = geojson.FeatureCollection(features)
  
    delta_paths = delta_queue.add_deltas_from_features(config,name, feature_collection, 'create')
    if 'alterations' in inlet_config:
        for outpath in delta_paths:
            utils.alter_geojson(outpath, inlet_config['alterations'])
    return len(feature_collection['features'])

def fetch_osm(config=None, name=None, delta_queue=DELTA_QUEUE, quick=False):
    """Fetch OpenStreetMap data and store in versioned directory"""
    
    # Initialize Overpass API
    api = overpass.API(timeout=60)
    
    # Get query from template
    query = config['assets'][name]['config']['template'].format(**config['dataswale']['bbox'])
    logger.info(f"Fetching OSM data with query: {query}")
    
    # Execute query
    response = api.get(query, responseformat="geojson")

    outpath = delta_queue.delta_path(config, name, 'create')

    # Ensure directory exists
    os.makedirs(os.path.dirname(outpath), exist_ok=True)
    
    # Write response
    with open(outpath, 'w') as f:
        geojson.dump(response, f)
    delta_queue.stamp_provenance(config, outpath, source=name, asset=name)

    return outpath


def local_ogr(config, name, delta_queue=DELTA_QUEUE):
    """Load OGR datafile and store in versioned directory"""
   
    # Get input path from template
    inlet_config = config['assets'][name]['config']

    inpath = versioning.atlas_path(config, "local") / inlet_config['inpath_template'].format(**config)
    outpath = delta_queue.delta_path(config, name, 'create')

    # Extract data using ogr2ogr and CLI args
    args = ['ogr2ogr', '-f', 'GeoJSON', '-t_srs', config['dataswale']['crs']]
    if 'geometry' in inlet_config:
        # Add spatial filter if geometry is specified
        bbox = config['dataswale']['bbox']
        args.extend(['-spat',
                    str(bbox['west']), str(bbox['south']),
                    str(bbox['east']), str(bbox['north'])])
        args.extend(['-spat_srs', config['dataswale']['crs']])
    args.extend([str(outpath), str(inpath)])
    if 'source_sublayer' in inlet_config:
        args.append(inlet_config['source_sublayer'])
    
    print(f"Running ogr2ogr with args: {args}")
    subprocess.check_output(args)

    if 'alterations' in inlet_config:
        utils.alter_geojson(outpath, inlet_config['alterations'])
    delta_queue.stamp_provenance(config, outpath, source=name, asset=name)
    return outpath


def import_sheet(config, layer_name, delta_queue=DELTA_QUEUE):
    logger.info(f"Importing Google Sheet: {layer_name}")
    rows = utils.read_gsheet(config, sheet_name=f"{config['name']} Fire Atlas: {layer_name}")
    geojson_features = []
    for feature in rows:
        # print(f"Row: {feature}")
        f = geojson.Feature(geometry=geojson.loads(feature['geometry']))
        del(feature['geometry'])
        f.properties = feature
        geojson_features.append(f)
    
    # Create feature collection from the features
    feature_collection = geojson.FeatureCollection(geojson_features)
    
    # Add deltas and return paths
    delta_paths = delta_queue.add_deltas_from_features(
        config, None, feature_collection, 'create', layer_name=layer_name,
        source='sheet_import')
    return delta_paths
    






def gazetteer_grid(config=None, name=None, delta_queue=DELTA_QUEUE):
    """Generate a grid of bbox regions covering the atlas area for use as gazetteer regions.

    Cells are square in EPSG:3857 (Web Mercator) so they render as equal squares on
    the map and on printed pages. Corners are stored in WGS84 (EPSG:4326) for GeoJSON.
    The grid always covers the full atlas bbox; it may extend slightly beyond the south
    edge if the height is not an exact multiple of the cell size.

    Convention: 1A is the NW corner, numbers increase east, letters increase south.
    """
    import math, string
    from pyproj import Transformer

    inlet_config = config['assets'][name]['config']
    bbox = config['dataswale']['bbox']
    num_cols = inlet_config['num_cols']

    to_3857 = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    to_4326 = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)

    west_m, south_m = to_3857.transform(bbox['west'], bbox['south'])
    east_m, north_m = to_3857.transform(bbox['east'], bbox['north'])

    cell_m = (east_m - west_m) / num_cols
    num_rows = math.ceil((north_m - south_m) / cell_m)

    row_index = list(string.ascii_uppercase)[:num_rows]
    col_index = [str(x) for x in range(1, num_cols + 1)]

    features = []
    for row, rowname in enumerate(row_index):
        for col, colname in enumerate(col_index):
            w_m = west_m + col * cell_m
            e_m = west_m + (col + 1) * cell_m
            n_m = north_m - row * cell_m
            s_m = north_m - (row + 1) * cell_m

            sw = to_4326.transform(w_m, s_m)
            se = to_4326.transform(e_m, s_m)
            ne = to_4326.transform(e_m, n_m)
            nw = to_4326.transform(w_m, n_m)

            cell_name = f"{colname}_{rowname}"
            up_rowname = row_index[row - 1] if row > 0 else None
            down_rowname = row_index[row + 1] if row + 1 < num_rows else None
            right_colname = str(col + 2) if col + 1 < num_cols else None
            left_colname = str(col) if col > 0 else None
            features.append(geojson.Feature(
                geometry=geojson.Polygon([[
                    list(sw), list(se), list(ne), list(nw), list(sw)
                ]]),
                properties={
                    'name': cell_name,
                    'north_neighbor': f"{colname}_{up_rowname}" if up_rowname else None,
                    'south_neighbor': f"{colname}_{down_rowname}" if down_rowname else None,
                    'east_neighbor': f"{right_colname}_{rowname}" if right_colname else None,
                    'west_neighbor': f"{left_colname}_{rowname}" if left_colname else None,
                }
            ))

    layer_name = config['assets'][name]['out_layer']
    fc = geojson.FeatureCollection(features)
    delta_queue.add_deltas_from_features(config, name, fc, 'create', layer_name=layer_name)
    logger.info(f"Generated {len(features)} gazetteer grid cells ({num_cols}x{num_rows})")


def s3_gpkg(config=None, name=None, delta_queue=DELTA_QUEUE):
    """Fetch a GeoPackage from S3, extract in_layer filtered to atlas bbox, write to delta queue."""
    import boto3, tempfile

    inlet_config = config['assets'][name]['config']
    bucket = inlet_config['s3_bucket']
    key = inlet_config['s3_key']
    in_layer = inlet_config.get('in_layer')
    bbox = config['dataswale']['bbox']

    with tempfile.NamedTemporaryFile(suffix='.gpkg', delete=False) as tmp:
        tmp_path = tmp.name

    logger.info(f"Fetching s3://{bucket}/{key}")
    try:
        boto3.client('s3').download_file(bucket, key, tmp_path)
    except Exception as e:
        logger.error(f"Failed to fetch s3://{bucket}/{key}: {e}")
        raise

    try:
        outpath = delta_queue.delta_path(config, name, 'create')
        args = [
            'ogr2ogr', '-f', 'GeoJSON',
            '-t_srs', config['dataswale']['crs'],
            '-spat',
            str(bbox['west']), str(bbox['south']),
            str(bbox['east']), str(bbox['north']),
            '-spat_srs', 'EPSG:4326',
            str(outpath), tmp_path,
        ]
        if in_layer:
            args.append(in_layer)
        logger.info(f"s3_gpkg: extracting {'layer ' + repr(in_layer) if in_layer else 'first layer'} from {key}")
        subprocess.check_output(args, stderr=subprocess.STDOUT)
        if 'alterations' in inlet_config:
            utils.alter_geojson(outpath, inlet_config['alterations'])
        delta_queue.stamp_provenance(config, outpath, source=name, asset=name)
    finally:
        import os
        os.unlink(tmp_path)

    return outpath


def s3_geojson(config=None, name=None, delta_queue=DELTA_QUEUE):
    """Fetch a GeoJSON file from a private S3 bucket, filter to atlas bbox, write to delta queue.
    Copies image_url → URL on each feature to enable webmap click-to-open behavior.

    squarify: replace each polygon with the square covering it (see
    utils.squarify_feature). Intended for regions, which are printed one per
    runbook page.

    Geometry handling:
    - null geometry: included unconditionally (tabular layers)
    - Point: bbox-filter on the coordinate
    - Polygon / MultiPolygon / other: bbox-filter on the centroid
    """
    import boto3, json, tempfile

    inlet_config = config['assets'][name]['config']
    bucket = inlet_config['s3_bucket']
    key = inlet_config['s3_key']
    bbox = config['dataswale']['bbox']

    with tempfile.NamedTemporaryFile(suffix='.geojson') as tmp:
        logger.info(f"Fetching s3://{bucket}/{key}")
        try:
            boto3.client('s3').download_file(bucket, key, tmp.name)
        except Exception as e:
            logger.error(f"Failed to fetch s3://{bucket}/{key}: {e}")
            raise
        with open(tmp.name) as f:
            fc = json.load(f)

    def _in_bbox(lon, lat):
        return bbox['west'] <= lon <= bbox['east'] and bbox['south'] <= lat <= bbox['north']

    features = []
    for feature in fc.get('features', []):
        geom = feature.get('geometry')
        props = feature.get('properties', {}) or {}

        if geom is None:
            # tabular / no-geometry features: always include
            features.append(feature)
            continue

        geom_type = geom.get('type', '')
        coords = geom.get('coordinates', [])

        if geom_type == 'Point':
            lon, lat = coords[0], coords[1]
            if not _in_bbox(lon, lat):
                continue
        else:
            # Use shapely centroid for polygon / multipolygon / linestring
            try:
                centroid = shape(geom).centroid
                if not _in_bbox(centroid.x, centroid.y):
                    continue
            except Exception:
                pass  # malformed geometry: include rather than drop

        if props.get('image_url'):
            props['URL'] = props['image_url']
        props['name'] = props.get('common_name', props.get('name', ''))
        features.append(feature)

    # After the name defaulting above, so a canonicalize-to-name (the label
    # property chosen in the console's Add Layer form) wins.
    if 'alterations' in inlet_config:
        features = utils.alter_features(features, inlet_config['alterations'])

    # Regions become runbook pages, and a page is a fixed shape, so a region is
    # stored already squared rather than squared at render time — what the
    # webmap draws is then what gets printed.
    if inlet_config.get('squarify'):
        features = [utils.squarify_feature(f) for f in features]
        logger.info(f"s3_geojson: squarified {len(features)} feature(s)")

    filtered = geojson.FeatureCollection(features)
    logger.info(f"s3_geojson: {len(features)} features within bbox (of {len(fc.get('features', []))} total)")
    delta_queue.add_deltas_from_features(config, name, filtered, 'create')


def h3_grid_inlet(config=None, name=None, delta_queue=DELTA_QUEUE):
    """Generate an H3 hexagonal grid covering the atlas bbox at a fixed resolution."""
    import h3

    inlet_config = config['assets'][name]['config']
    resolution = inlet_config['resolution']
    bbox = config['dataswale']['bbox']
    west, south, east, north = bbox['west'], bbox['south'], bbox['east'], bbox['north']

    bbox_poly = h3.LatLngPoly([
        (north, west), (north, east), (south, east), (south, west), (north, west)
    ])
    cells = h3.geo_to_cells(bbox_poly, resolution)

    features = []
    for cell in cells:
        boundary = h3.cell_to_boundary(cell)  # [(lat, lng), ...]
        coords = [[lng, lat] for lat, lng in boundary]
        coords.append(coords[0])
        features.append(geojson.Feature(
            geometry=geojson.Polygon([coords]),
            properties={'h3_index': cell, 'h3_resolution': resolution}
        ))

    out_layer = inlet_config.get('out_layer', f'h3_grid_r{resolution}')
    fc = geojson.FeatureCollection(features)
    delta_queue.add_deltas_from_features(config, name, fc, 'create', layer_name=out_layer)
    logger.info(f"h3_grid_inlet: generated {len(features)} cells at resolution {resolution}")


def game_safe_exits(config=None, name=None, delta_queue=DELTA_QUEUE):
    """Seed the safe_locations layer: points where the board's roads leave the atlas.

    One per main road, side and connected network, and at least one for every
    network that reaches the edge (game_board.seed_exits). road_filter and
    main_roads come from this asset, else the atlas's game_kit outlet, else are
    guessed from the road data. A crossing that already has a point within the
    merge distance is skipped, so re-running after the layer is refreshed adds
    only new ones. Points carry source "edge"; edit or delete them in webedit
    like any point layer.
    """
    import game_kit
    import game_roads

    asset = config['assets'][name]
    inlet_config = {**asset.get('config', {}), **asset}
    in_layer = inlet_config.get('in_layer', 'roads')
    out_layer = inlet_config.get('out_layer', 'safe_locations')

    def features(layer):
        path = versioning.atlas_path(config, 'layers') / layer / f'{layer}.geojson'
        return geojson.load(open(path))['features'] if path.exists() else []

    roads = features(in_layer)
    kit_asset = next((a for a in config['assets'].values() if a.get('config_def') == 'game_kit'), {})
    road_layer = next((l for l in config['dataswale']['layers'] if l.get('name') == in_layer), None)
    options = game_kit.road_options(roads, {**kit_asset, **inlet_config}, road_layer)
    game_kit.apply_name_field(roads, options['name_field'])
    points = game_roads.edge_exits(roads, config['dataswale']['bbox'], options['road_filter'],
                                   main_roads=options['main_roads'], existing=features(out_layer))
    if points:
        delta_queue.add_deltas_from_features(config, name, geojson.FeatureCollection(points), 'create',
                                             layer_name=out_layer)
    logger.info(f"game_safe_exits: {len(points)} new edge point(s) for {out_layer}")
    return {'status': 'success', 'added': len(points)}


# Query params inaturalist_inlet owns: the bbox comes from the atlas region and
# the rest drive the id_above paging loop. Letting config override any of these
# would silently break paging or scope, so api_params may not set them.
INAT_RESERVED_PARAMS = frozenset({
    'swlng', 'swlat', 'nelng', 'nelat',
    'per_page', 'order', 'order_by', 'id_above',
})


def _inat_api_params(inlet_config):
    """Validate and normalise the config's api_params passthrough.

    Exposes the whole iNaturalist observations API to config (threatened,
    native, endemic, introduced, popular, iconic_taxa, taxon_id, ...) without
    plumbing each parameter by hand. Booleans become 'true'/'false' strings,
    which is what the API expects; None values are dropped so a key can be
    declared-but-unset in a shared template.
    """
    api_params = inlet_config.get('api_params') or {}
    if not isinstance(api_params, dict):
        raise ValueError(f"api_params must be a dict, got {type(api_params).__name__}")

    reserved = INAT_RESERVED_PARAMS & set(api_params)
    if reserved:
        raise ValueError(
            f"api_params may not override {sorted(reserved)} — the bbox comes from "
            f"the atlas region and the paging keys drive the fetch loop")

    out = {}
    for key, value in api_params.items():
        if value is None:
            continue
        out[key] = str(value).lower() if isinstance(value, bool) else value
    return out


def inaturalist_inlet(config=None, name=None, delta_queue=DELTA_QUEUE):
    """Fetch iNaturalist observations for configured bbox regions into a point layer."""
    import requests, time

    inlet_config = config['assets'][name]['config']
    atlas_bbox = config['dataswale']['bbox']

    regions = inlet_config.get('regions') or [{'bbox': [
        atlas_bbox['west'], atlas_bbox['south'], atlas_bbox['east'], atlas_bbox['north']
    ]}]
    users = inlet_config.get('users') or []
    time_range = inlet_config.get('time_range')
    quality_grade = inlet_config.get('quality_grade')
    api_params = _inat_api_params(inlet_config)
    out_layer = inlet_config.get('out_layer', 'inaturalist')

    features = []
    for region in regions:
        swlng, swlat, nelng, nelat = region['bbox']
        base_params = {
            'swlng': swlng, 'swlat': swlat,
            'nelng': nelng, 'nelat': nelat,
            'per_page': 200,
            'order': 'asc',
            'order_by': 'id',
        }
        if users:
            base_params['user_login'] = ','.join(users)
        if time_range:
            base_params['d1'], base_params['d2'] = time_range[0], time_range[1]
        if quality_grade:
            base_params['quality_grade'] = quality_grade
        # applied last so an explicit api_params entry wins over the named keys
        base_params.update(api_params)

        id_above = None
        while True:
            params = dict(base_params)
            if id_above is not None:
                params['id_above'] = id_above

            resp = requests.get(
                'https://api.inaturalist.org/v1/observations',
                params=params, timeout=30
            )
            resp.raise_for_status()
            results = resp.json().get('results', [])
            if not results:
                break

            for obs in results:
                geom = obs.get('geojson')
                if not geom:
                    continue  # obscured/private observations have no coordinates

                taxon = obs.get('taxon') or {}
                photos = obs.get('photos') or []
                image_url = None
                if photos:
                    image_url = photos[0].get('url', '').replace('/square.', '/medium.')

                features.append(geojson.Feature(
                    geometry=geom,
                    properties={
                        'name': taxon.get('preferred_common_name') or taxon.get('name'),
                        'common_name': taxon.get('preferred_common_name'),
                        'observation_id': obs['id'],
                        'username': (obs.get('user') or {}).get('login'),
                        'observed_on': obs.get('observed_on'),
                        'quality_grade': obs.get('quality_grade'),
                        'taxon_name': taxon.get('name'),
                        'taxon_id': taxon.get('id'),
                        'iconic_taxon': taxon.get('iconic_taxon_name'),
                        'image_url': image_url,
                        'inat_url': obs.get('uri'),
                        'updated_at': obs.get('updated_at'),
                    }
                ))

            id_above = results[-1]['id']
            if len(results) < 200:
                break
            time.sleep(0.5)  # iNat rate limit: 100 req/min

    fc = geojson.FeatureCollection(features)
    delta_queue.add_deltas_from_features(config, name, fc, 'create', layer_name=out_layer)
    logger.info(f"inaturalist_inlet: {len(features)} observations across {len(regions)} region(s)")
    # TODO: delta refresh — pass updated_since=<last_run> to the API to skip unchanged observations


def federation_inlet(config=None, name=None, delta_queue=DELTA_QUEUE, quick=False):
    """Pull a published layer from another atlas's static STAC catalog.

    Federation consumer side: reads the source catalog.json, resolves the target
    collection by id, follows its `data` asset to the (already-masked) GeoJSON,
    optionally filters to a bbox, writes the features into out_layer, and records
    provenance for the attribution system. Build-time only — no runtime
    dependency on the source after ingest. Consumes exactly what's published;
    never attempts to unmask or re-derive. See python/federation.py.

    Config keys:
        source_catalog: URL of the source atlas catalog.json (required)
        collection_id:  collection (layer) id to pull (required)
        out_layer:      consumer layer to write into (required)
        bbox:           [w, s, e, n] to filter to an explicit box, or truthy to
                        filter to the atlas bbox; falsy/absent ⇒ no spatial filter
    """
    import json
    import requests
    # FUTURE: transport is plain HTTP over a static catalog; a STAC-API / read-
    # endpoint variant (overview §7) would slot in here without changing callers.
    from urllib.parse import urljoin

    inlet_config = config['assets'][name]['config']
    source_catalog = inlet_config['source_catalog']
    collection_id = inlet_config['collection_id']
    out_layer = inlet_config.get('out_layer') or config['assets'][name].get('out_layer')
    use_bbox = inlet_config.get('bbox')

    def _get_json(url, step):
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            raise RuntimeError(f"federation_inlet: failed to {step} from {url}: {e}")

    catalog = _get_json(source_catalog, "fetch catalog")
    child_href = federation.resolve_collection_href(catalog, collection_id)
    collection_url = urljoin(source_catalog, child_href)
    collection = _get_json(collection_url, "fetch collection")
    data_url = urljoin(collection_url, federation.data_href_from_collection(collection))
    fc = _get_json(data_url, "fetch layer geojson")

    if use_bbox:
        if isinstance(use_bbox, (list, tuple)) and len(use_bbox) == 4:
            w, s, e, n = use_bbox
            filter_bbox = {'west': w, 'south': s, 'east': e, 'north': n}
        else:
            filter_bbox = config['dataswale']['bbox']
        fc = federation.filter_features_to_bbox(fc, filter_bbox)

    fc = geojson.FeatureCollection(fc.get('features', []))
    delta_queue.add_deltas_from_features(config, name, fc, 'create', layer_name=out_layer)

    # Provenance → folded into attribution by generate_attributions.py
    provenance = federation.build_provenance(
        source_catalog=source_catalog,
        collection_id=collection_id,
        source_version=federation.source_version_from_collection(collection),
        consumer_version=config.get('version'),
    )
    prov_path = versioning.atlas_path(config, 'layers') / out_layer / 'provenance.json'
    prov_path.parent.mkdir(parents=True, exist_ok=True)
    with open(prov_path, 'w') as f:
        json.dump(provenance, f, indent=2)

    logger.info(f"federation_inlet: pulled {len(fc['features'])} features for collection "
                f"'{collection_id}' from {source_catalog} into '{out_layer}'")


def s3_map_polygons(config=None, name=None, delta_queue=DELTA_QUEUE):
    """Polygons drawn as colored outlines on a georeferenced map (GeoPDF or GeoTIFF) in S3.

    For maps that arrive flattened — no vector layers to read — such as a THP map
    with its unit boundaries in red. The file is rendered with the system GDAL
    (rasterio's bundled GDAL has no PDF driver), pixels near `color` are traced
    into polygons along the middle of the drawn line (map_trace.py), and those
    are written as a `create` delta in EPSG:4326.

    Config (top-level keys on the asset):
      s3_bucket, s3_key   the map file
      out_layer           target polygon layer
      color               [r, g, b] of the outline, e.g. [230, 0, 0]
      tolerance           color distance accepted (default 60)
      close_px            gap-bridging radius in pixels (default 2)
      dpi                 render resolution for a PDF (default 150)
      min_area_m2         smaller enclosed areas are dropped (default 1000)
      simplify_m          vertex simplification (default 1.0)
      width_m             drawn line width; measured per outline if omitted
      names               names for the polygons, largest first; else "Polygon N"
      properties          extra properties set on every feature

    Re-running appends another copy of every polygon (inlets write `create`
    deltas); clear the layer or delete the old delta first.
    """
    import boto3, tempfile
    from pathlib import PurePosixPath
    from affine import Affine
    from osgeo import gdal, osr
    from shapely.geometry import mapping
    from shapely.ops import transform as shapely_transform
    import map_trace

    inlet_config = config['assets'][name]['config']
    bucket, key = inlet_config['s3_bucket'], inlet_config['s3_key']
    color = inlet_config['color']
    bbox = config['dataswale']['bbox']
    suffix = PurePosixPath(key).suffix or '.pdf'

    with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
        logger.info(f"s3_map_polygons: fetching s3://{bucket}/{key}")
        boto3.client('s3').download_file(bucket, key, tmp.name)
        open_options = [f"DPI={inlet_config.get('dpi', 150)}"] if suffix.lower() == '.pdf' else []
        ds = gdal.OpenEx(tmp.name, gdal.OF_RASTER, open_options=open_options)
        if ds is None or ds.RasterCount < 3:
            raise ValueError(f"s3_map_polygons: {key} did not open as an RGB raster")
        if not ds.GetProjection():
            raise ValueError(f"s3_map_polygons: {key} has no georeferencing")
        rgb = ds.ReadAsArray(band_list=[1, 2, 3])
        transform = Affine.from_gdal(*ds.GetGeoTransform())
        src_srs = osr.SpatialReference(wkt=ds.GetProjection())
        ds = None

    mask = map_trace.color_mask(rgb, color, inlet_config.get('tolerance', map_trace.DEFAULT_TOLERANCE))
    mask = map_trace.close_mask(mask, inlet_config.get('close_px', map_trace.DEFAULT_CLOSE_PX))
    logger.info(f"s3_map_polygons: {int(mask.sum())} pixels match {color}")
    polygons, skipped = map_trace.trace_polygons(
        mask, transform,
        min_area_m2=inlet_config.get('min_area_m2', map_trace.DEFAULT_MIN_AREA_M2),
        simplify_m=inlet_config.get('simplify_m', map_trace.DEFAULT_SIMPLIFY_M),
        width_m=inlet_config.get('width_m'))
    for reason in skipped:
        logger.warning(f"s3_map_polygons: skipped {reason}")

    wgs84 = osr.SpatialReference()
    wgs84.ImportFromEPSG(4326)
    for srs in (src_srs, wgs84):
        srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)  # x=lon, y=lat
    to_wgs84 = osr.CoordinateTransformation(src_srs, wgs84)

    def reproject(x, y, z=None):
        pts = to_wgs84.TransformPoints(list(zip(x, y)))
        return [p[0] for p in pts], [p[1] for p in pts]

    names = inlet_config.get('names') or []
    extra = inlet_config.get('properties') or {}
    features = []
    for i, poly in enumerate(polygons):
        geom = shapely_transform(reproject, poly['geometry'])
        c = geom.centroid
        if not (bbox['west'] <= c.x <= bbox['east'] and bbox['south'] <= c.y <= bbox['north']):
            logger.warning(f"s3_map_polygons: polygon {i + 1} lies outside the atlas bbox; dropped")
            continue
        props = {
            'name': names[i] if i < len(names) else f"Polygon {i + 1}",
            'area_acres': map_trace.acres(poly['geometry']),
            'line_width_m': poly['width_m'],
            'source_file': f"s3://{bucket}/{key}",
            **extra,
        }
        features.append(geojson.Feature(geometry=mapping(geom), properties=props))

    logger.info(f"s3_map_polygons: {len(features)} polygon(s) from {key}: " +
                ", ".join(f"{f['properties']['name']} {f['properties']['area_acres']} ac" for f in features))
    if not features:
        raise ValueError(f"s3_map_polygons: no polygons found in {key} for color {color}")
    delta_queue.add_deltas_from_features(config, name, geojson.FeatureCollection(features), 'create')


asset_methods = {
    "overture_duckdb": overture_duckdb,
    "local_ogr": local_ogr,
    "gazetteer_grid": gazetteer_grid,
    "fetch_osm": fetch_osm,
    "s3_geojson": s3_geojson,
    "s3_gpkg": s3_gpkg,
    "federation": federation_inlet,
    "h3_grid": h3_grid_inlet,
    "inaturalist": inaturalist_inlet,
    "game_safe_exits": game_safe_exits,
    "s3_map_polygons": s3_map_polygons,
    }
