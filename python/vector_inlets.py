import logging, subprocess, os
import duckdb
import geojson

import versioning
import utils
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
        config, None, feature_collection, 'create', layer_name=layer_name)
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
    delta_queue.add_deltas_from_features(config, None, fc, 'create', layer_name=layer_name)
    logger.info(f"Generated {len(features)} gazetteer grid cells ({num_cols}x{num_rows})")


def s3_geojson(config=None, name=None, delta_queue=DELTA_QUEUE):
    """Fetch a GeoJSON file from a private S3 bucket, filter to atlas bbox, write to delta queue.
    Copies image_url → URL on each feature to enable webmap click-to-open behavior."""
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

    features = []
    for feature in fc.get('features', []):
        coords = feature.get('geometry', {}).get('coordinates', [])
        if coords and len(coords) >= 2:
            lon, lat = coords[0], coords[1]
            if bbox['west'] <= lon <= bbox['east'] and bbox['south'] <= lat <= bbox['north']:
                props = feature.get('properties', {})
                if props.get('image_url'):
                    props['URL'] = props['image_url']
                common = props.get('common_name', '')
                scientific = props.get('scientific_name', '')
                user = props.get('user_name', '')
                date = props.get('observed_on_string', '')
                species = f"{common} ({scientific})" if common and scientific else common or scientific
                observer = ' @ '.join(filter(None, [user, date]))
                props['name'] = props.get('common_name', '')
                features.append(feature)

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
    delta_queue.add_deltas_from_features(config, None, fc, 'create', layer_name=out_layer)
    logger.info(f"h3_grid_inlet: generated {len(features)} cells at resolution {resolution}")


asset_methods = {
    "overture_duckdb": overture_duckdb,
    "local_ogr": local_ogr,
    "gazetteer_grid": gazetteer_grid,
    "fetch_osm": fetch_osm,
    "s3_geojson": s3_geojson,
    "h3_grid": h3_grid_inlet,
    }
