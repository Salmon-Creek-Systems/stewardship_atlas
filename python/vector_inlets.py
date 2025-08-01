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


def import_gsheet(config, name, delta_queue=DELTA_QUEUE):
    """Import a Google Sheet layer into a dataswale atlas"""

    gsheet_name = f'{config["name"]} Fire Atlas'
    gc = gspread.service_account()
    wks = gc.open(gsheet_name).sheet1
    features = wks.get_all_records()

    # convert to geojson
    geojson_features = []
    for feature in features:
        geojson_feature = {
            "type": "Feature",
            "geometry": json.loads(feature['geometry']),
            "properties": feature
        }
        geojson_features.append(geojson_feature)

    geojson_layer = {
    


    # get path to layer
    layer = dataswale_geojson.layer_as_featurecollection(config, name)






asset_methods = {
    "overture_duckdb": overture_duckdb,
    "local_ogr": local_ogr
    }
