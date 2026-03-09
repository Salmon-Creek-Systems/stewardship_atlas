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
    api = overpass.API()
    
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
    if 'layer' in inlet_config:
        args.append(inlet_config['layer'])
    
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
    """Generate a grid of bbox regions covering the atlas area for use as gazetteer regions."""
    import math, string
    inlet_config = config['assets'][name]['config']
    bbox = config['dataswale']['bbox']

    num_cols = inlet_config['num_cols']
    cell_size = abs(bbox['east'] - bbox['west']) / num_cols
    num_rows = math.ceil(abs(bbox['north'] - bbox['south']) / cell_size)

    row_index = list(string.ascii_uppercase)[:num_rows]
    col_index = [str(x) for x in range(1, num_cols + 1)]

    features = []
    for row, rowname in enumerate(row_index):
        for col, colname in enumerate(col_index):
            s = bbox['north'] - (1 + row) * cell_size
            n = bbox['north'] - row * cell_size
            e = bbox['west'] + (1 + col) * cell_size
            w = bbox['west'] + col * cell_size
            cell_name = f"{colname}_{rowname}"
            up_rowname = row_index[row - 1] if row > 0 else None
            down_rowname = row_index[row + 1] if row + 1 < len(row_index) else None
            right_colname = str(col + 2) if col + 1 < len(col_index) else None
            left_colname = str(col) if col > 0 else None
            features.append(geojson.Feature(
                geometry=geojson.Polygon([[
                    [w, s], [e, s], [e, n], [w, n], [w, s]
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


asset_methods = {
    "overture_duckdb": overture_duckdb,
    "local_ogr": local_ogr,
    "gazetteer_grid": gazetteer_grid,
    }
