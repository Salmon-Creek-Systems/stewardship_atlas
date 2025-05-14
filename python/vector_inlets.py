
def overture_duckdb(config=None, name=None, delta_queue=None):
    """Fetch Overture data and return a Delta to push into queue"""
    version_string = 'staging'
    inlet_config = config['assets'][name]

    
    # Get query from template
    query = inlet_config['inpath_template'].format(**config['bbox'])
    print(f"Fetching Overture data with query: {query}")
    
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
  
    delta_queue.add_delta(config,feature_collection)
    return len(feature_collection['features'])
