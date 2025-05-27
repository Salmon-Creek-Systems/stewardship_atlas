import subprocess
import json


import utils
import versioning
import logging


# Configure logging
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)
logger.setLevel(logging.INFO)

def webmap_json(config, name):
    """Generate a JSON object for a web map in MapLibre.
    We will set up sources and layers as static content loaded initially in HTML where possible.
    Layers which invole dynamic content - marker images for example - will be added seperately 
    since the layer must be set up inside the callback for the image load.
    """
    # Calculate center and zoom from bbox
    bbox = config['dataswale']['bbox']
    center_lat = (bbox['north'] + bbox['south']) / 2
    center_lon = (bbox['east'] + bbox['west']) / 2 
    lat_diff = bbox['north'] - bbox['south']
    zoom = 12  # Default zoom, could be calculated based on bbox size

    # Set up the map config general properties
    map_config = {
        "container": "map",
        "style": {
            "glyphs" : "https://fonts.undpgeohub.org/fonts/{fontstack}/{range}.pbf",
            "version": 8,
            "center": [center_lon, center_lat],
            "zoom": zoom
            }
    }
    map_sources = {}
    map_layers = []
    dynamic_layers = []
    outlet_config = config['assets'][name]
    layers_dict = {x['name']: x for x in config['dataswale']['layers']}
    
    
    # for each layer used in outlet, we add a source and display layer, and possibly a label layer
    for layer_name in outlet_config['in_layers']:
        layer = layers_dict[layer_name]
        map_sources[layer_name] =  {
            'type': 'image', 
            'url': f"../../layers/{layer_name}/{layer_name}.tiff.jpg",
            'coordinates': utils.bbox_to_corners(config['dataswale']['bbox'])
        } if layer['geometry_type'] == 'raster' else {
                'type': 'geojson',
                'data': f"../../layers/{layer_name}/{layer_name}.geojson"
                }
        # Add Display Layer
        map_layer = {
                'id': f"{layer_name}-layer",
                'source': layer_name
                }
        if layer['geometry_type'] == 'raster':            
            map_layer.update({
                'type': 'raster',
                'paint' : {
                "raster-opacity": 0.1,
                "raster-contrast": 0.3}})
                              
        elif layer['geometry_type'] == 'polygon':
            #map_layer['paint'] = {
            map_layer.update({
                'type': 'fill',
                'symbol_placement': 'line-center',
                'paint': {
                    "fill-color": utils.rgb_to_css(layer.get('fill_color', [150,150,150])),
                    "fill-outline-color": utils.rgb_to_css(layer.get('color', [150,150,150]))}
            })
        elif layer['geometry_type'] == 'linestring':
            map_layer.update({
                'type': 'line',
                'symbol_placement': 'line',
                'paint': {
                    "line-color": utils.rgb_to_css(layer.get('color', [150,150,150])),
                    "line-width": ["get", "vector_width"]
                }
                })
        elif layer['geometry_type'] == 'point':
            map_layer.update({
                'type': 'circle',
                'symbol_placement': 'point',
                "icon-color": utils.rgb_to_css(layer.get('color', [150,150,150])),
                "icon-size": 20
                
                })
        
        map_layers.append(map_layer)
        
        # Maybe add label/icon layer:
        if layer.get('add_labels', False):            
            label_layer = {
                    "id": f"{layer_name}-label-layer",
                    "type": "symbol",
                    "source": layer_name,
                    "layout": {
                        "symbol-placement": map_layer['symbol_placement'],
                        "text-offset": [0,2],
                        "text-font": ["Open Sans Regular"],
                        "text-field": ["get", "name"],
                        "text-size": 20
                        }
                }
 
            if  map_layer.get('type', 'line') == 'note':    
                label_layer.update({
                    'paint': {
                        'text-halo-width': 2,
                        'text-halo-blur': 1,
                        'text-halo-color': '#FF9999',
                        'text-color': '#000000'
                    }
                })
            if "symbol" not in layer:
                map_layers.append(label_layer)
            else:
                label_layer['symbol'] = layer['symbol']
                label_layer['name'] = layer['name']
                label_layer['layout']['icon-image'] = layer['name']
                label_layer['layout']['icon-color'] = utils.rgb_to_css(layer.get('color', [150,150,150]))
                
                dynamic_layers.append(label_layer)

        #else:
        #    logger.error(f"not an outlet layer: {layer_name}.")
    map_config['style']['sources'] = map_sources
    map_config['style']['layers'] = map_layers
  
    return {"map_config": map_config, "dynamic_layers": dynamic_layers}

def generate_map_page(title, map_config_data, output_path):
    """Generate the complete HTML page for viewing a map"""
    # Read template files
    with open('../templates/map.html', 'r') as f:
        template = f.read()
    logger.info(f"About to generate HTML to {output_path}: {template}.")
    # TODO there is a much better way to do this, just handlign it dynamically in JS.
    # For now though, let's just generate the JS as a string. Ugh.
    js_bit = ""
    for dynamic_layer in map_config_data['dynamic_layers']:
        im_uri = "local/" + dynamic_layer['symbol']
        im_name = dynamic_layer['name']
        layer_json = dynamic_layer
        js_bit += """
unused_image_{im_name} = await map.loadImage('{im_uri}',
    (error, image) => {{
        if (error) throw error;
        // Add the image to the map style.                                                              
        map.addImage('{im_name}', image);
        map.addLayer(  {layer_json} );
        }});

""".format(**locals())
    processed_template = template.format(
            title=title,
            map_config=json.dumps(map_config_data['map_config'],  indent=2),
            dynamic_layers=js_bit)

    with open(output_path, 'w') as f_out:
      f_out.write(processed_template)          
        




def outlet_webmap(config, name):
    """Generate an interactive web map using MapLibre GL JS.
    This creates statis HTML and JS files which can be loaded or served directly.
    
    map.html is where we insert a JSON doc with all the layer information.
    map.js is used to do dynamic map processing. 
    For example, we seem to have to load images used for markers in the map there after the map HTML is fully loaded.

    So let's consider doing it all dynamically in the JS.
    """
    

    # Generate base map configuration
    
    map_config = webmap_json(config, name)
    webmap_dir = versioning.atlas_path(config, "outlets") / name
    webmap_dir.mkdir(parents=True, exist_ok=True)
    basemap_dir = versioning.atlas_path(config, "layers") / "basemap"
    
    # make a JPG of basemap tiff..
    # TODO this should be a tiling URL instead of a local file..
    # TODO maybe resampling happens here?
    # TODO here is where we should be using Dagster and actual asset mgmt
    basemap_path = basemap_dir / "basemap.jpg"
    if basemap_path.exists():
        logger.info(f"Using extant basemap: {basemap_path}.")
    else:
        logger.info(f"Generating basemap: {basemap_path}.")
        utils.tiff2jpg(f"{basemap_dir}/basemap.tiff", basemap_path)
    
    subprocess.run(['cp', '-r', '../templates/css/', webmap_dir / "css"])
    #subprocess.run(['cp', '../templates/js/map.js', f"{webmap_dir}/js/"])
    
    output_path = webmap_dir / "index.html"
    logger.info(f"Creating webmap HTML in {output_path}.")
    html_path = generate_map_page("test Webmap", map_config, output_path)  
  
    return output_path
