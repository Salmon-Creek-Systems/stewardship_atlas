import subprocess, math, string
import json, csv
import sys,os,time
from io import StringIO
from pathlib import Path
import duckdb
import geopandas as gpd
import pandas as pd
import nbformat
from PIL import Image
import io

import utils
import versioning
import logging
import geojson
import gspread

import dataswale_geojson
import utils
from outlets_mapnik import build_region_map_mapnik

# Configure logging
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)
logger.setLevel(logging.INFO)

def webmap_json(config, name, sprite_json=None):
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
    atlas_url = f"{config['base_url']}/staging"
    sprite_url = atlas_url + "/outlets/" + name + "/sprite"
    map_config = {
        "container": "map",
        "style": {
            "glyphs" : "https://fonts.undpgeohub.org/fonts/{fontstack}/{range}.pbf",
            "version": 8,
            "center": [center_lon, center_lat],
            "zoom": zoom
            }
    }
    
    # Add sprite if available
    if sprite_json:
        map_config['style']['sprite'] = sprite_url
    
    map_sources = {}
    map_layers = []
    dynamic_layers = []
    outlet_config = config['assets'][name]
    layers_dict = {x['name']: x for x in config['dataswale']['layers']}
    
    logger.info(f"In webedit, got Outlet Conf: {outlet_config}")
    # for each layer used in outlet, we add a source and display layer, and possibly a label layer
    for layer_name in outlet_config['in_layers']:
        layer = layers_dict[layer_name]
        if layer['geometry_type'] == 'raster':
            map_sources[layer_name] =  {
                'type': 'image', 
                'url': f"../../layers/{layer_name}/{layer_name}.tiff.jpg",
                'coordinates': utils.bbox_to_corners(config['dataswale']['bbox'])}
        elif layer['geometry_type'] == 'documents':
            pass
            #map_sources[layer_name] =  {
            #    'type': 'image', 
            #    'url': f"../../layers/{layer_name}/{layer_name}.tiff.jpg",
            #    'coordinates': utils.bbox_to_corners(config['dataswale']['bbox'])}
                
        else:
            map_sources[layer_name] =  {
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
                "raster-opacity": 0.3,
                "raster-contrast": 0.3}})
                              
        elif layer['geometry_type'] == 'polygon':
            #map_layer['paint'] = {
            map_layer.update({
                'type': 'fill',
                'symbol_placement': 'point',
                'text_offset': [0,0],
                'paint': {
                    "fill-opacity": layer.get('fill_opacity', 1.0),
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
        


        if vis := layer.get('vis'):
            map_layer |= vis
        if paint := layer.get('paint'):
            if not 'paint' in map_layer:
                map_layer['paint'] = {}            
            map_layer['paint'] |=  paint
            
                
        map_layers.append(map_layer)
        
        # Maybe add label/icon layer:
        if layer.get('add_labels', False):            
            label_layer = {
                "id": f"{layer_name}-label-layer",
                "type": "symbol",
                "minzoom": 10,
                "maxzoom": 24,
                "source": layer_name,
                "layout": {
                    "symbol-placement": map_layer['symbol_placement'],
                    "text-offset": map_layer.get('text_offset', [0,2]),
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
            elif map_layer.get('type', 'point') == "fill":
                label_layer.update({
                    'paint': {
                        'text-color': 'rgb(0,0,0)'
                    }
                })

            #label_layer['paint'] |=  layer.get('paint', {})                
            #XSXSXblabel_layer |=  layer.get('vis', {})
            if vis := layer.get('vis'):
                label_layer |= vis
            if "symbol" not in layer:
                #if paint := layer.get('paint'):
                #    if not 'paint' in label_layer:
                #        label_layer['paint'] = {}            
                #    label_layer['paint'] |=  paint

                #label_layer['paint'] = layer.get('paint', {})
                map_layers.append(label_layer)
            else:
                if 'icon_if' in layer:
                    label_layer['filter'] = ['==', layer['icon_if']['property'], layer['icon_if']['value']]
                    logger.info(f"Generating a dynamic layer with filter: {label_layer['filter']}...")
                #else:
                #    logger.info(f"Dynamic but no filter: {layer}")
                label_layer['symbol_source'] = layer['symbol']['png']
                label_layer['name'] = layer['name']
                label_layer['layout']['icon-image'] = layer['name']
                label_layer['layout']['icon-size'] = layer.get('icon-size', 0.1)
                label_layer['layout']['icon-anchor'] = layer.get('icon-anchor', 'center')
                label_layer['paint'] = {
                    'icon-color' : utils.rgb_to_css(layer.get('color', [150,150,150])),
                    'text-halo-color': 'rgba(200,200,200,0.5)',
                    'text-halo-width': 1,
                    'icon-halo-color': 'rgba(255,255,255,0.9)',
                    'icon-halo-blur': 10                    
                }

                label_layer['paint'] |= layer.get('paint', {})
                
                # If we have sprites, add the layer to the initial style so it appears in legend
                if sprite_json and layer['name'] in sprite_json:
                    # Ensure the layer has proper source configuration
                    if layer_name not in map_sources:
                        # Add source for this layer if it doesn't exist
                        map_sources[layer_name] = {
                            'type': 'geojson',
                            'data': f"../../layers/{layer_name}/{layer_name}.geojson"
                        }
                    
                    # Add metadata for legend display
                    label_layer['metadata'] = {
                        'name': layer.get('display_name', layer['name']),
                        'description': layer.get('description', f'{layer["name"]} layer'),
                        'legend': {
                            'type': 'symbol',
                            'icon': layer['name'],  # This should match the sprite symbol name
                            'label': layer.get('display_name', layer['name'])
                        }
                    }
                    
                    # Ensure the layer has proper layout properties for sprite display
                    label_layer['layout']['icon-image'] = layer['name']  # Should match sprite symbol name
                    label_layer['layout']['icon-size'] = layer.get('icon-size', 0.5)  # Increased default size
                    label_layer['layout']['icon-anchor'] = layer.get('icon-anchor', 'bottom')  # Position icon below text
                    
                    # Ensure text and icon are both displayed
                    label_layer['layout']['text-allow-overlap'] = True
                    label_layer['layout']['icon-allow-overlap'] = True
                    
                    # Position text above icon
                    label_layer['layout']['text-anchor'] = 'top'
                    label_layer['layout']['text-offset'] = [0, 1]  # Move text up slightly
                    
                    # Add icon paint properties to make the symbol visible
                    if 'paint' not in label_layer:
                        label_layer['paint'] = {}
                    label_layer['paint']['icon-color'] = utils.rgb_to_css(layer.get('color', [150,150,150]))
                    label_layer['paint']['icon-halo-color'] = 'rgba(255,255,255,0.9)'
                    label_layer['paint']['icon-halo-blur'] = 10
                    
                    # Debug logging for sprite-based label layer
                    logger.info(f"Adding sprite-based label layer: {label_layer['id']} with icon-image: {label_layer['layout'].get('icon-image')}")
                    
                    map_layers.append(label_layer)
                else:
                    # Keep as dynamic layer for loadImage approach
                    dynamic_layers.append(label_layer)
    
    map_config['style']['sources'] = map_sources
    map_config['style']['layers'] = map_layers
    
    # Build legend targets for grouped visibility
    legend_targets = {}
    
    # Group primary layers with their label layers
    for layer in map_layers:
        layer_id = layer.get('id', '')
        
        # Skip label layers - they'll be handled as children
        if layer_id.endswith('-label-layer'):
            continue
            
        # Check if this layer has a label layer
        layer_name = layer_id.replace('-layer', '')
        label_layer_id = f"{layer_name}-label-layer"
        has_label_layer = any(l.get('id') == label_layer_id for l in map_layers)
        
        if has_label_layer:
            # Primary layer with label - create group
            legend_targets[layer_id] = layer_name
            
            # Don't add label layer to legend targets - let the plugin handle it
            
            # Add basic legend properties and group metadata
            layer['metadata'] = {
                'legend': {
                    'name': layer_name,
                    'type': 'symbol'  # or 'line', 'fill' based on layer type
                },
                'group': layer_name  # Group primary and label layers together
            }
            
            # Find and update the label layer
            for label_layer in map_layers:
                if label_layer.get('id') == label_layer_id:
                    label_layer['metadata'] = {
                        'legend': {
                            'name': f"{layer_name} Labels",
                            'type': 'symbol',
                            'hidden': True
                        },
                        'group': layer_name  # Same group as primary layer
                    }
                    break
        else:
            # Standalone layer - add normally
            legend_targets[layer_id] = layer_name
            
            # Add basic legend properties
            layer['metadata'] = {
                'legend': {
                    'name': layer_name,
                    'type': 'symbol'  # or 'line', 'fill' based on layer type
                },
                'group': layer_name  # Standalone layers get their own group
            }
    
    # Log the final map configuration for debugging
    logger.info(f"Map style layers: {[layer.get('id', 'no-id') for layer in map_layers]}")
    logger.info(f"Dynamic layers: {[layer.get('name', 'no-name') for layer in dynamic_layers]}")
    logger.info(f"Legend targets: {len(legend_targets)} layers")
  
    return {"map_config": map_config, "dynamic_layers": dynamic_layers, "legend_targets": legend_targets}

def generate_map_page(title, map_config_data, output_path, sprite_json=None):
    """Generate the complete HTML page for viewing a map"""
    # Read template files
    with open('../templates/map.html', 'r') as f:
        template = f.read()
    logger.debug(f"About to generate HTML to {output_path}: {template}.")
    
    # Read and convert markdown help content
    import markdown
    with open('../documents/webmap_help.md', 'r') as f:
        help_markdown = f.read()
    help_html = markdown.markdown(help_markdown)
    
    # Generate JavaScript for dynamic layers
    js_bit = ""
    if map_config_data['dynamic_layers']:
        if sprite_json:
            # Use sprites for dynamic layers
            for dynamic_layer in map_config_data['dynamic_layers']:
                layer_name = dynamic_layer['name']
                if layer_name in sprite_json:
                    # Remove the loadImage call and just add the layer directly
                    # The sprite is already loaded in the map style
                    js_bit += """
map.addLayer({layer_json});
""".format(layer_json=json.dumps(dynamic_layer))
                else:
                    # Fallback to original loadImage if sprite not found
                    im_uri = "/local/" + dynamic_layer['symbol_source']
                    im_name = dynamic_layer['name']
                    layer_json = dynamic_layer
                    js_bit += """
void await map.loadImage('{im_uri}',
    (error, image) => {{
        if (error) throw error;
        // Add the image to the map style.                                                              
        map.addImage('{im_name}', image);
        map.addLayer(  {layer_json} );
        }});

""".format(**locals())
        else:
            # Original loadImage approach if no sprites
            for dynamic_layer in map_config_data['dynamic_layers']:
                im_uri = "/local/" + dynamic_layer['symbol_source']
                im_name = dynamic_layer['name']
                layer_json = dynamic_layer
                js_bit += """
void await map.loadImage('{im_uri}',
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
            dynamic_layers=js_bit,
            legend_targets=json.dumps(map_config_data.get('legend_targets', {}), indent=2),
            webmap_help=help_html)

    with open(output_path, 'w') as f_out:
      f_out.write(processed_template)




def generate_sprite_from_layers(config, webmap_dir):
    """Generate a sprite file from PNG images referenced in layers configuration.
    
    Args:
        layers_config: List of layer configurations from config['dataswale']['layers']
        webmap_dir: Path to the webmap output directory
        
    Returns:
        dict: Mapping of layer names to sprite symbol names, or None if no sprites needed
    """
    layers_config = config['dataswale']['layers']
    local_path = versioning.atlas_path(config, "local")
    
    # Find all layers that have PNG symbols
    sprite_layers = []
    for layer in layers_config:
        if layer.get('add_labels') and layer.get('symbol', {}).get('png'):
            sprite_layers.append(layer)
    
    if not sprite_layers:
        return None

    
    # Collect all unique PNG files
    png_files = {}
    for layer in sprite_layers:
        png_path = layer['symbol']['png']
        if png_path not in png_files:
            png_files[png_path] = []
        png_files[png_path].append(layer['name'])
    
    # Create sprite image and JSON
    sprite_images = []
    sprite_json_1x = {}
    sprite_json_2x = {}
    
    # Standard sprite dimensions - we'll use 32x32 for each icon (1x) and 64x64 for 2x
    icon_size_1x = 32
    icon_size_2x = 64
    padding = 1
    total_width_1x = 0
    total_width_2x = 0
    max_height_1x = 0
    max_height_2x = 0
    
    # First pass: calculate dimensions and load images
    for png_path, layer_names in png_files.items():
        try:
            # Load image from /local/ path (symlink to shared datastore)
            full_path = local_path / png_path
            if os.path.exists(full_path):
                img = Image.open(full_path)
                # Create both 1x and 2x versions
                img_1x = img.resize((icon_size_1x, icon_size_1x), Image.Resampling.LANCZOS)
                img_2x = img.resize((icon_size_2x, icon_size_2x), Image.Resampling.LANCZOS)
                sprite_images.append((img_1x, img_2x, layer_names))
                total_width_1x += icon_size_1x + padding
                total_width_2x += icon_size_2x + padding
                max_height_1x = max(max_height_1x, icon_size_1x)
                max_height_2x = max(max_height_2x, icon_size_2x)
            else:
                logger.warning(f"PNG file not found: {full_path}")
        except Exception as e:
            logger.error(f"Failed to load PNG file {png_path}: {e}")
    
    if not sprite_images:
        return None
    
    # Create sprite canvases for both densities
    sprite_canvas_1x = Image.new('RGBA', (total_width_1x, max_height_1x), (0, 0, 0, 0))
    sprite_canvas_2x = Image.new('RGBA', (total_width_2x, max_height_2x), (0, 0, 0, 0))
    
    # Second pass: place images on both canvases and build JSON
    x_offset_1x = 0
    x_offset_2x = 0
    for img_1x, img_2x, layer_names in sprite_images:
        sprite_canvas_1x.paste(img_1x, (x_offset_1x, 0))
        sprite_canvas_2x.paste(img_2x, (x_offset_2x, 0))
        
        # Add to sprite JSON for each layer with separate densities
        for layer_name in layer_names:
            sprite_json_1x[layer_name] = {
                "width": icon_size_1x,
                "height": icon_size_1x,
                "x": x_offset_1x,
                "y": 0,
                "pixelRatio": 1
            }
            sprite_json_2x[layer_name] = {
                "width": icon_size_2x,
                "height": icon_size_2x,
                "x": x_offset_2x,
                "y": 0,
                "pixelRatio": 2
            }
        
        x_offset_1x += icon_size_1x + padding
        x_offset_2x += icon_size_2x + padding
    
    # Save sprite files for both densities
    sprite_png_path_1x = webmap_dir / "sprite.png"
    sprite_png_path_2x = webmap_dir / "sprite@2x.png"
    sprite_json_path_1x = webmap_dir / "sprite.json"
    sprite_json_path_2x = webmap_dir / "sprite@2x.json"
    
    sprite_canvas_1x.save(sprite_png_path_1x, "PNG")
    sprite_canvas_2x.save(sprite_png_path_2x, "PNG")
    
    with open(sprite_json_path_1x, 'w') as f:
        json.dump(sprite_json_1x, f, indent=2)
    
    with open(sprite_json_path_2x, 'w') as f:
        json.dump(sprite_json_2x, f, indent=2)
    
    logger.info(f"Generated sprites with {len(sprite_images)} icons: {sprite_png_path_1x} and {sprite_png_path_2x}")
    
    # Log the sprite JSON for debugging
    logger.info(f"Sprite 1x JSON content: {json.dumps(sprite_json_1x, indent=2)}")
    logger.info(f"Sprite 2x JSON content: {json.dumps(sprite_json_2x, indent=2)}")
    
    return sprite_json_1x

def outlet_webmap(config, name):
    """Generate an interactive web map using MapLibre GL JS.
    This creates statis HTML and JS files which can be loaded or served directly.
    
    map.html is where we insert a JSON doc with all the layer information.
    map.js is used to do dynamic map processing. 
    For example, we seem to have to load images used for markers in the map there after the map HTML is fully loaded.

    So let's consider doing it all dynamically in the JS.
    """
    

    # Generate sprite file first if needed
    webmap_dir = versioning.atlas_path(config, "outlets") / name
    webmap_dir.mkdir(parents=True, exist_ok=True)
    sprite_json = generate_sprite_from_layers(config, webmap_dir)
    
    # Generate base map configuration with sprite
    map_config = webmap_json(config, name, sprite_json)
    
    basemap_name = config['assets'][name]['in_layers'][0]
    basemap_dir = versioning.atlas_path(config, "layers") / basemap_name
    
    # make a JPG of basemap tiff..
    # TODO this should be a tiling URL instead of a local file..
    # TODO maybe resampling happens here?
    # TODO here is where we should be using Dagster and actual asset mgmt
    basemap_path = basemap_dir / f"{basemap_name}.jpg"
    if basemap_path.exists():
        logger.info(f"Using extant basemap: {basemap_path}.")
    else:
        logger.info(f"Generating basemap: {basemap_path}.")
        utils.tiff2jpg(f"{basemap_dir}/{basemap_name}.tiff", basemap_path)
    
    subprocess.run(['cp', '-r', '../templates/css/', webmap_dir / "css"])
    #subprocess.run(['cp', '../templates/js/map.js', f"{webmap_dir}/js/"])
    
    output_path = webmap_dir / "index.html"
    logger.info(f"Creating webmap HTML in {output_path}.")
    html_path = generate_map_page("Fire Atlas Webmap", map_config, output_path, sprite_json)  
  
    return output_path

def generate_edit_controls_html(editable_attributes):
    """Generate HTML for edit controls based on attribute configuration"""
    select_html = ""
    string_html = ""
    
    for edit_att in editable_attributes:
        if edit_att['type'] == 'string':
            string_html += f"""
                <div class="input-group">
                    <label>{edit_att['name']}:</label><br/>
                    <input type="text" name="{edit_att['name']}" id="{edit_att['name']}" class="input-field">
                </div>"""
        elif edit_att['type'] == 'radio':
            select_html += f"""
                <div class="input-group">
                    <label>{edit_att['name']}:</label><br/>
                    <select name="{edit_att['name']}" id="{edit_att['name']}" class="input-field">"""
            
            for value in edit_att['values']:
                #parsed_value = json.loads(value)
                str_value =  json.dumps(value)
                select_html += f"""
                    <option value='{str_value}' {('selected' if value == edit_att.get('default', '') else '')}>
                        {value[ edit_att['name'] ] }   
                    </option>"""
                # {str_value} {value[ edit_att['name'] ] }
            select_html += "</select></div>"
            
    # Add drawing action buttons at the bottom of the editing controls
    buttons_html = """
        <div class="button-group">
            <button id="reset-button" class="warning-button">Reset Drawing</button>
            <button id="upload-button" class="button">Upload GeoJSON</button>
            <button id="save-button" class="button">Save Features</button>
        </div>
    """
    
    return select_html + string_html + buttons_html

def generate_edit_page( config: dict, ea: dict, name: str, map_config: dict, action: str):
    """Generate the complete HTML page for editing a layer. Params: ea - Editable Asset (config) - Atlas config, name - name of the outlet"""
    # Read template files
    with open('../templates/edit_map.html', 'r') as f:
        template = f.read()
        
    # Generate controls HTML
    controls_html = generate_edit_controls_html(ea.get('editable_columns', []))
    
    # Prepare mode string
    mode_string = {
        'note': 'point',
        'linestring': 'linestring',
        'fill': 'polygon',
        'point': 'point',
        'polygon': 'polygon'
    }[ea.get('geometry_type', 'note')]

    if action == 'annotate':
        mode_string = 'polygon'
        
    # Prepare controls config for JavaScript
    controls_config = [
        {'name': att['name'], 'type': att['type']}
        for att in ea.get('editable_columns', [])
    ]
    
    # Format template
    return template.format(
        swale_name=config['name'],
        swalename=config['name'],
        action=action,
        edit_layer_name=ea['name'],
        controls_html=controls_html,
        map_config=json.dumps(map_config['map_config'], indent=2),
        mode_string=mode_string,
        controls_config=json.dumps(controls_config),
        legend_targets=json.dumps(map_config.get('legend_targets', {}), indent=2))

def outlet_webmap_edit(config: dict, name: str):
    """Generate an interactive web map edit using MapLibre GL JS - one for each editable asset"""
    
    webedit_dir = versioning.atlas_path(config, "outlets") / name
    webedit_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate sprite file first if needed
    sprite_json = generate_sprite_from_layers(config, webedit_dir)
    
    # Generate base map configuration with sprite
    map_config = webmap_json(config, name, sprite_json)

    subprocess.run(['cp', '-r', '../templates/css/', webedit_dir ])
    subprocess.run(['cp', '-r', '../templates/js/', webedit_dir ])
    # subprocess.run(['cp', '../templates/css/map.css', f"{webedit_dir}/css/"])
    # subprocess.run(['cp', '../templates/css/edit_controls.css', f"{webedit_dir}/css/"])
    
    # Copy the JS file
    # subprocess.run(['cp', '../templates/js/edit_map.js', f"{webedit_dir}/js/"])
    
    # Generate edit pages for each editable asset
    for ea in config['dataswale']['layers']:
        if ea.get('editable_columns'):
            for action in ['create', 'annotate']:
                html_content = generate_edit_page(config, ea, name, map_config, action)
                output_path = webedit_dir /   f"{ea['name']}_{action}.html"
                logger.debug(f"Generated WEBEDIT for {ea} | {action} into {output_path}")            
                with open(output_path, 'w') as f:
                    f.write(html_content)
    
    return webedit_dir



def grass_init(swale_name):
    grass = '/usr/bin/grass'
    sys.path.insert(
        0,subprocess.check_output([grass, "--config", "python_path"], text=True).strip())

    import grass.jupyter as gj
    my_env = os.environ.copy()
    my_env["PYTHONPATH"] = f"/usr/lib/grass83/etc/python:{my_env['PATH']}"
    loc_path =  Path("/root/grassdata/") / swale_name
    if not loc_path.exists():
        print(f"Creating Grass LOC: {loc_path}...")
        print(subprocess.check_output([grass, '-c', 'EPSG:4269', '-e',str(loc_path)], env=my_env))
            
    GRASS_LOC = swale_name
    # GRASS_LOC = GRASS_LOC_NAME + datetime.datetime.now().strftime("%I:%M%p_%B-%d-%Y")

    return gj.init("~/grassdata", GRASS_LOC, "PERMANENT")
    



def extract_region_layer_ogr_grass(config, outlet_name, layer, region, reuse=False):
    """Grab a region of a layer and export it as a GeoJSON file using GRASS GIS. Note this assumes the entire layer is already in GRASS. Which is awful."""
    swale_name = config['name']
    outpath = versioning.atlas_path(config,  "outlets") / outlet_name / f"{layer}_{region['name']}.geojson"

    if reuse and outpath.exists():
        logger.info(f"Reusing vector file at: {outpath}")
        return outpath


    logger.info(f"Extracting region {region['name']} of vector layer {layer} to {outpath}.")

    grass_init(swale_name)  
    import grass.script as gs
    clip_bbox = region['bbox']
    gs.read_command('g.region', n=clip_bbox['north'], s=clip_bbox['south'],e=clip_bbox['east'],w=clip_bbox['west'])
    try:
        gs.read_command('v.clip', flags='r', input=layer, output=f'{layer}_clip')
        gs.read_command('v.out.ogr', input=f'{layer}_clip', output=outpath, format='GeoJSON')
    except gs.CalledModuleError as e:
        logger.info(f"Clip was empty for {layer} in {region['name']}. Writing empty file.")
        geojson.dump(geojson.FeatureCollection([]), open(outpath,"w"))

    return outpath

def extract_region_layer_raster_grass(config, outlet_name, layer, region, use_jpg=False, reuse=True):
    """Grab a region of a rasterlayer and export it. Note this assumes the entire layer is already in GRASS. Which is awful."""
    swale_name = config['name']

   #  basemap_name = config['assets']
    basemap_dir = versioning.atlas_path(config, "layers") / layer
    if use_jpg:
        logger.info("Using JPG")
        basemap_path = basemap_dir / f"{layer}.tiff.jpg"
        if basemap_path.exists():
            logger.info(f"Using extant basemap: {basemap_path}.")
            inpath = basemap_path
        else:
            logger.info(f"Generating basemap: {basemap_path} for layer {layer}.")
            utils.tiff2jpg(f"{basemap_dir}/{layer}.tiff", basemap_path)
            inpath = basemap_path
    else:
        inpath = basemap_dir / f"{layer}.tiff"
    outpath = versioning.atlas_path(config,  "outlets") / outlet_name / f"{layer}_{region['name']}.tiff"

    if reuse and outpath.exists():
        logger.info(f"Reusing raster file at: {outpath}")
        return outpath
    
    logger.info(f"Extracting region {region['name']} of raster layer {layer} to {outpath}.")
    #import grass.script as gs
    # staging_path = f"{data_root}/{swale_name}/{version_string}/staging"
    grass_init(swale_name)
    import grass.script as gs
    import grass.jupyter as gj    
    clip_bbox = region['bbox']
    # Can't we extract more efficiently here? We read the whole file then just use part of it - EACH TIME
    gs.read_command('r.in.gdal', input=inpath, output=layer)
    gs.read_command('g.region', raster=layer)
    gs.read_command('g.region', n=clip_bbox['north'], s=clip_bbox['south'],e=clip_bbox['east'],w=clip_bbox['west'])
    # outpath = f"{staging_path}/{layer}_{region['name']}.tiff"
    gs.read_command('r.out.gdal', input=layer, output=outpath, format='GTiff')
 
    return outpath

def build_region_minimap_grass(swale_config,  asset_name, region):
    swale_name = swale_config['name']
    data_root = swale_config['data_root']
    # version_string = swale_config['version_string']
    grass_init(swale_name)
    import grass.script as gs
    import grass.jupyter as gj
    size = 400
    m = gj.Map(height=size, width=size)
    region_bbox = region['bbox']
    region_polygon = geojson.Polygon([utils.bbox_to_polygon(region_bbox)])

    clip_bbox = swale_config['dataswale']['bbox']
    gs.read_command('g.region', n=clip_bbox['north'], s=clip_bbox['south'],e=clip_bbox['east'],w=clip_bbox['west'])

    
    #gs.read_command('g.region', n=clip_bbox['north'], s=clip_bbox['south'],e=clip_bbox['east'],w=clip_bbox['west'])   
    # load region layers
    raster_name = 'hillshade_' + 'all'
    #raster_path = versioning.atlas_path(swale_config, "layers") / "hillshade" / "hillshade.tiff"
    raster_path = region['raster'][1]
    # raster_path = f"{data_root}/{swale_name}/{version_string}/staging/hillshade.tiff"
    print(f"making map image with {raster_path}.")
    gs.read_command('r.in.gdal',  band=1,input=raster_path, output=raster_name)
    # gs.read_command('r.colors', map=raster_name)
    #
    #gs.read_command('g.region', raster=raster_name)

    
    # generate temporary vector file with box/etc
    # use resolve path!
    region_name = region['name'].lower()
    minimap_filename = f"page_{region_name}_minimap"
    outpath = versioning.atlas_path(swale_config, "outlets") / asset_name 
    # outpath = f"{data_root}/{swale_name}/{version_string}/{asset_name}/page_{region_name}_minimap"
    f = geojson.FeatureCollection([geojson.Feature(
        properties={"name": region['caption']},
        geometry=region_polygon)])
    geojson_path = outpath / f"{minimap_filename}.geojson"
    geojson.dump(f, open(geojson_path, 'w'))
    
    print(f"writing temp GeoJSON to: {geojson_path}:\n{f}")    

    lame = json.load(open(geojson_path))
    num_feats = len(lame['features'])
    print(f"testloaded {num_feats} from {geojson_path} as:\n{lame}")
    #if 
    #    continue

    
    # read in as vector
    gs.read_command('v.import', input=geojson_path, output=region_name)

    print(f"read temp GeoJSON from: {geojson_path} -> {region_name}:\n")    
    # draw on map
    c = [100,255,150]
    
    gs.read_command('g.region', n=clip_bbox['north'], s=clip_bbox['south'],e=clip_bbox['east'],w=clip_bbox['west'])
    m.d_rast(map=raster_name)
    m.d_vect(map=region_name,
             color=f"{c[0]}:{c[1]}:{c[2]}",
             fill_color='none',
             width=5,
             icon='basic/marker',size=20)
    gs.read_command('g.region', n=clip_bbox['north'], s=clip_bbox['south'],e=clip_bbox['east'],w=clip_bbox['west'])
    #gs.read_command('g.region', raster=raster_name)
    # export map
    m.save(str(outpath / f"{minimap_filename}.png"))



def build_region_map_grass(config, outlet_name, region):
    """Build a map image for a region using GRASS GIS.
    
    Args:
        config (dict): The configuration dictionary for the atlas.
        outlet_name (str): The name of the outlet.
        region (dict): The region to build the map for.
"""
    t = time.time()
    grass_init(config['name'])
    import grass.script as gs
    import grass.jupyter as gj
    if region['name'] == 'all':
        size = 9000
    else:
        size = 2400
    m = gj.Map(height=size, width=size)
    clip_bbox = region['bbox']
    gs.read_command('g.region', n=clip_bbox['north'], s=clip_bbox['south'],e=clip_bbox['east'],w=clip_bbox['west'])   
    
    # load region layers - if we have any
    if len(region['raster']) > 0:
        # First the raster basemap. Note we blend it with a greyscale overlay 
        blend_percent = config['assets'][outlet_name].get('blend_percent', 10)
        raster_name = 'hillshadered_' + region['name']
        
        print(f"making map image for {region}.")
        gs.read_command('r.in.gdal',  band=1,input=region['raster'][1], output=raster_name)
        gs.read_command('r.colors', map=raster_name, color='grey')
        
        gs.read_command('r.mapcalc.simple', expression="1", output='ones')
        gs.read_command('r.colors', map='ones', color='grey1.0')
        gs.read_command('r.blend', flags="c", first=raster_name, second='ones', output='blended', percent=blend_percent, overwrite=True)
        logger.info(f"Blended raster using percent: {blend_percent} [{time.time() - t}]")
        
        m.d_rast(map='blended')   
        # m.d_rast(map=raster_name)  
    # if we don't, we need to set the page size and region directly to 2400x2400
    gs.read_command('r.mapcalc.simple', expression="1", output='ones')

    # add vector layers to map
    for lc,lp in region['vectors']:
        logger.debug(f"adding region {lc} to map for region {region['name']}")
        if lc['name'] in region.get('config', {}):
            for update_key, update_value in region['config'].items():
                lc[update_key] = update_value
                print(f"region config override: {region['name']} | {lc['name']}: {region['config']} -> {lc}")
        gs.read_command('v.import', input=lp, output=lc['name'])
        # clunky but need to skip empty sets
        lame = json.load(open(lp))
        if len(lame['features']) < 1:
            logger.info("layer is empty...")
            continue
        if lc.get('geometry_type', 'line') == 'point':
            c = lc.get('color', (100,100,100))
            if lc.get('add_labels', False):
                logger.debug("Adding Points")
                m.d_vect(map=lc['name'],
                         color=f"{c[0]}:{c[1]}:{c[2]}",
                         icon=lc.get('symbol', {}).get("icon",'basic/diamond'),size=50,
                         label_size=25,
                         attribute_column=lc.get('alterations', {}).get('label_attribute', 'name'))
            elif lc.get("icon_if"):
                icon_sql = f"{lc['icon_if']['property']} == \'{lc['icon_if']['value']}\'"
                logger.info(f"Conditional POINT icon! {lc['icon_if']} -> [{icon_sql}]")
                m.d_vect(map=lc['name'],
                         color=f"{c[0]}:{c[1]}:{c[2]}",
                         icon=lc['icon_if']['icon'], size=50,
                         where=icon_sql)

                
            else:
                logger.debug("Adding NON-Points")
                m.d_vect(map=lc['name'],
                         color=f"{c[0]}:{c[1]}:{c[2]}",
                         icon=lc.get('symbol', {"icon": 'basic/diamond', "png": "pin.png"})['icon'], size=10)
        else:
            # This is interesting: vector width comes from features or layer conf? Former, right?
            # m.d_vect(map=lc['name'], color=lc.get('color', 'gray'), width=lc.get('width_base',5))
            # currently, setting bool vector_width IN LAYER means look for more (per-feature) detail in asset.
            c = lc.get('color', (100,100,100))
            fc = lc.get('fill_color', c)
            if 'vector_width' in lc:
                m.d_vect(map=lc['name'],
                         color=f"{c[0]}:{c[1]}:{c[2]}",
                         fill_color=f"{fc[0]}:{fc[1]}:{fc[2]}" if fc != 'none' else 'none',
                         width_column='vector_width',
                         attribute_column=lc.get('alterations', {}).get('label_attribute', 'name'),
                         label_color=f"{c[0]}:{c[1]}:{c[2]}", label_size=50)
            else:
                m.d_vect(map=lc['name'],
                         color=f"{c[0]}:{c[1]}:{c[2]}",
                         fill_color=f"{fc[0]}:{fc[1]}:{fc[2]}" if fc != 'none' else 'none',
                         width = lc.get("constant_width", 2),
                         attribute_column=lc.get('alterations', {}).get('label_attribute', 'name'),
                         label_color=f"{c[0]}:{c[1]}:{c[2]}", label_size=50)
            logger.info(f"{region['name']} : {lc['name']} [{time.time() - t}]")

            if lc.get("icon_if"):
                icon_sql = f"{lc['icon_if']['property']} == \'{lc['icon_if']['value']}\'"
                logger.info(f"Conditional icon! {lc['icon_if']} -> [{icon_sql}]")
                gs.read_command('v.centroids', input=lc['name'], output=lc['name'] + "_centroids")
                m.d_vect(map=lc['name'] + "_centroids",
                         color="0:0:0", fill_color="0:0:0", #f"{c[0]}:{c[1]}:{c[2]}",
                         icon=lc['icon_if']['icon'], size=30,
                         where=icon_sql)





            
    m.d_grid(size='00:10:00')

    # add neighboring gazeteer text                                                                                                                                              
    nbr = region.get('gazetteer_neighbors')
    if nbr is not None:
        if nbr.get('north'):
            m.d_text(text=nbr['north'],  at="50,95", size=5)
        if nbr.get('south'):
            m.d_text(text=nbr['south'], rotation=180,  at="50,5", size=5)
        if nbr.get('west'):
            m.d_text(text=nbr['west'], rotation=90, at="5,50", size=5)
        if nbr.get('east'):
            m.d_text(text=nbr['east'], rotation=270, at="95,50", size=5)
        m.d_text(text=region['name'], flags="b", at="50,50", size=9)

    # Add legend
    m.d_legend_vect(
        fontsize=25, flags="b", overwrite=True,
        bgcolor="220:220:220", border_color="20:20:20", border_width=3,
        title="LEGEND", title_fontsize=22)

    # export map
    outpath = versioning.atlas_path(config, "outlets") / outlet_name / f"page_{region['name']}.png"
    m.save(outpath)
    # return path to map
    return outpath

def process_region(layer_config: dict, region_extract_path: str):
    """Process a region extract for a layer.
    Current this means only show each name value once.
    Args:
        layer_config (dict): The configuration dictionary for the layer.
        region_extract_path (str): The path to the region extract.
    """
    # load region layer as geopandas
    gdf = gpd.read_file(region_extract_path)
    if len(gdf) > 0:
        # Alternative approach: iterate through unique names to avoid groupby conflicts
        unique_names = gdf['name'].dropna().unique()
    
        for name in unique_names:
            if pd.isna(name) or name == '':
                continue
            
            # Get all rows with this name
            name_mask = gdf['name'] == name
            name_indices = gdf[name_mask].index
        
            if len(name_indices) > 1:
                # Randomly select one row to keep its name
                keep_idx = gdf.loc[name_mask].sample(1).index[0]
                # Set name to empty string for all other rows with this name
                other_indices = name_indices[name_indices != keep_idx]
                gdf.loc[other_indices, 'name'] = None
                
                # Fill any NaN values with empty string
        #gdf['name'] = gdf['name'].fillna(None)
    
        # save to new file
        parent_dir = region_extract_path.parent
        file_stem = region_extract_path.stem
        file_suffix = region_extract_path.suffix
        processed_path = parent_dir / f"{file_stem}_processed{file_suffix}"
        gdf.to_file(processed_path, driver='GeoJSON') 
        logger.info(f"Processed {region_extract_path} -> {processed_path}")
        return processed_path
    else:
        return region_extract_path
    
def outlet_regions_grass(config, outlet_name, regions = [], regions_html=[], skips=[], reuse_vector_extracts=True, reuse_raster_extracts=False, first_n=0):
    """Process regions for gazetteer and runbook outputs using versioned paths."""
    t = time.time()
    swale_name = config['name']
    outlet_config = config['assets'][outlet_name]
    if 'region_maps' not in skips:
        # Set up Grass environment
        if first_n > 0:
            logger.info(f"Only using first {first_n} regions...")
            regions = regions[:first_n]
        
        grass_init(swale_name)
        import grass.script as gs
        
        # Process each input layer
        for lc in config['dataswale']['layers']:
            if lc['name'] not in outlet_config['in_layers']:
                continue
            layer_format = 'tiff' if lc['geometry_type'] in ['raster'] else 'geojson'
            logger.debug(f"Processing layer: {lc}")
            # lc = config['dataswale']['layers'][layer]
            
            # Resolve staging path then extract data for each region for current layer:
            staging_path = versioning.atlas_path(config, "layers") / lc['name'] / f"{lc['name']}.{layer_format}"
            
            if layer_format in ['geojson']:

                gs.read_command('v.import', input=staging_path, output=lc['name'])
                for region in regions:
                    # Check if this region has custom in_layers, otherwise use outlet default
                    region_in_layers = region.get('in_layers', outlet_config['in_layers'])
                    if lc['name'] not in region_in_layers:
                        logger.debug(f"Skipping layer {lc['name']} for region {region['name']} (not in region's in_layers)")
                        continue
                    
                    logger.debug(f"Processing vector region: {region['name']}")
                    region_extract_path = extract_region_layer_ogr_grass(config, outlet_name, lc['name'], region, reuse=reuse_vector_extracts)
                    processed_region_extract_path = process_region(lc, region_extract_path)
                    region['vectors'].append([lc, str(processed_region_extract_path)])
            else:
                gs.read_command('r.in.gdal', input=staging_path, output=lc['name'])
                for region in regions:
                    # Check if this region has custom in_layers, otherwise use outlet default
                    region_in_layers = region.get('in_layers', outlet_config['in_layers'])
                    if lc['name'] not in region_in_layers:
                        logger.debug(f"Skipping layer {lc['name']} for region {region['name']} (not in region's in_layers)")
                        continue

                    region['raster'] = [lc,
                                        str(extract_region_layer_raster_grass(config, outlet_name, lc['name'], region, use_jpg=False, reuse=reuse_raster_extracts))]
                    logger.info(f"Processing raster region: {region['name']}: {region}")
            logger.info(f"{lc['name']}  [{time.time() - t}]")
        # Build maps for each region
        for region in regions:
            logger.info(f"Building map for region: {region['name']}  [{time.time() - t}] with wtf config: {region}")
            build_region_minimap_grass(config, outlet_name, region)
            build_region_map_grass(config, outlet_name, region)
            
            # build_region_map_mapnik(config, outlet_name, region)

        # save regions config as JSON for later use
        regions_json_path = versioning.atlas_path(config, "outlets") / outlet_name / f"regions_config.json"
        if first_n == 0:
            with open(regions_json_path, "w") as f:
                json.dump(regions, f)
    # Post-process to overlay PNG symbols if any were configured
    # Write output files

    for outfile_path, outfile_content in regions_html:
        versioned_path = versioning.atlas_path(config, "outlets") / outlet_name / outfile_path
        # os.makedirs(os.path.dirname(versioned_path), exist_ok=True)
        logger.info(f"Writing region output to: {versioned_path}  [{time.time() - t}]")
        with open(versioned_path, "w") as f:
            f.write(outfile_content)
    
    return regions

def regions_from_geojson(path, start_at=2,limit=3):
    """Load regions from a GeoJSON file."""
    regions = []
    last_region = None
    
    with open(path, 'r') as f:
        for i,region in  enumerate(json.load(f)['features']):
            if i < start_at:
                logger.info(f"skipping region {i}...")
                continue
            if (limit > 0) and (i >= limit):
                logger.info(f"hit region limit of {limit}, truncating RunBook.")
                break
            logger.debug(f"Converting region {i} from GJ: {region}")
            bbox = utils.geojson_to_bbox(region['geometry']['coordinates'][0])
            default_name =  f"Region {i}"
            regions.append({
                'name': utils.canonicalize_name(region['properties'].get('Description', default_name)),
                'caption': region['properties'].get('Description', default_name),
                'text': region['properties'].get('text', default_name),
                'bbox': bbox,
                "neighbors": region.get('neighbors'),
                "vectors": [],
                "raster": ""
            })
    for i,r in enumerate(regions):
        if r['neighbors'] is None:
            next_idx = (i + 1) % len(regions)
            prev_idx = (i - 1) % len(regions)
            r['neighbors'] = {
                "prev": regions[prev_idx]['name'],
                "next": regions[next_idx]['name']}                 
            
    return regions


def make_summary_regions(config, outlet_name):
    """Create summary regions for each summary layer covering the entire dataswale area.
    
    Each region shows one summary layer (e.g., helilandings, milemarkers) with 
    background layers for context.
    
    Args:
        config: The configuration dictionary
        outlet_name: Name of the outlet asset
        
    Returns:
        List of region dictionaries, each containing:
            - name: layer name (e.g., 'helilandings')
            - caption: display name
            - bbox: dataswale bounding box
            - vectors: empty list (will be populated by outlet_regions_grass)
            - raster: empty string
            - in_layers: list of layers to include (background + summary layer)
    """
    outlet_config = config['assets'][outlet_name]
    bbox = config['dataswale']['bbox']
    
    # Get configuration with defaults
    summary_layers = outlet_config.get('summary_layers', ['helilandings', 'milemarkers'])
    summary_background_layers = outlet_config.get('summary_background_layers', ['basemap', 'roads_primary'])
    
    regions = []
    
    for layer_name in summary_layers:
        # Build the list of layers to include for this summary
        in_layers = summary_background_layers + [layer_name]
        
        region = {
            'name': layer_name,
            'caption': f"{layer_name.replace('_', ' ').title()} Summary",
            'bbox': bbox,
            'vectors': [],
            'raster': '',
            'in_layers': in_layers  # Custom layer list for this region
        }
        
        regions.append(region)
        logger.info(f"Created summary region for {layer_name} with layers: {in_layers}")
    
    return regions


# Create the grid of regions for Gazetteer geometrically and prep them for populating from layers
def generate_gazetteerregions(config, outlet_name):
    outlet_config = config['assets'][outlet_name]
    bbox = config['dataswale']['bbox']
    # num_rows = swale_config['geometry']['num_rows']
    num_cols = outlet_config['num_cols']
    cell_size = float(abs(bbox['east'] - bbox['west']))/float(num_cols)
    num_rows = math.ceil( float(abs(bbox['north'] - bbox['south'])) / cell_size )
    
    regions = []
    everything_region = {'name': 'all', 
                            'bbox': bbox,
                            'vectors': [],
                            'raster': ''}
    # regions.append(everything_region)
    cell_width=300
    hdr = f"<HTML><BODY><table border=3 bgcolor='#FFFFFF' cellpadding=0 cellspacing=1>\n<TR><td>{config['name']}</td>"
    bdy = ''
    #vd = float(abs(bbox['north'] - bbox['south']))/float(num_rows)
    
    row_index =  list(string.ascii_uppercase)[:num_rows]
    col_index =  [str(x) for x in range(1, 1+num_cols)]
    
    for row, rowname in enumerate(row_index):
        for col, colname in enumerate(col_index):
            s = bbox['north']- (1+row)*cell_size
            n = bbox['north']- row*cell_size
            e = bbox['west'] + (1+col)*cell_size
            w = bbox['west'] + col*cell_size
            cell_name = f"{colname}_{rowname}"
            # Add neighbors to the region

            up_row = row - 1 
            down_row = row + 1
            left_col = col - 1
            right_col = col + 1
         
            up_rowname =  row_index[up_row] if up_row >= 0 else None
            down_rowname = row_index[down_row] if down_row < len(row_index) else None
            right_colname = right_col + 1 if right_col < len(col_index) else None
            left_colname = left_col + 1 if left_col >=0 else None

            neighbors = {
                    "north" : f"{colname}_{up_rowname}" if up_rowname is not None else None,
                    "south" : f"{colname}_{down_rowname}" if down_rowname is not None else None,
                    "west" : f"{left_colname}_{rowname}" if left_colname is not None else None,
                    "east" : f"{right_colname}_{rowname}" if right_colname is not None else None
            }

          
            
            regions.append({'name': cell_name, 
                            'bbox': {'south': s,'west':w,'north': n,'east': e},
                            'gazetteer_neighbors': neighbors,
                            'vectors': [],
                            'raster': ''})
            if col == 0:
                bdy += f"<TR><TD>{rowname}<br>{n:.2f}<br>{s:.2f}</TD>"
            if row == 0:
                hdr += f"<TD>{colname}<br>{w:.2f}</TD>"
            bdy += f"<TD><A HREF='page_{colname}_{rowname}.png'><img src='page_{colname}_{rowname}.png' alt='Avatar' class='image'style='width:{cell_width}'></A>\n"
        bdy += "</TR>\n"
    hdr += "</TR>\n"
    bdy += "</TABLE></BODY></HTML>"
    html_path = versioning.atlas_path(config, "outlets") / outlet_name / "index.html"
    return regions, [(html_path, hdr + bdy)]

def make_gazetteer_html(config, outlet_name):
    outlet_config = config['assets'][outlet_name]
    bbox = config['dataswale']['bbox']
    # num_rows = swale_config['geometry']['num_rows']
    num_cols = outlet_config['num_cols']
    cell_size = float(abs(bbox['east'] - bbox['west']))/float(num_cols)
    num_rows = math.ceil( float(abs(bbox['north'] - bbox['south'])) / cell_size )

    cell_width=300
    hdr = f"<HTML><BODY><table border=3 bgcolor='#FFFFFF' cellpadding=0 cellspacing=1>\n<TR><td>{config['name']}</td>"
    bdy = ''
    
    row_index =  list(string.ascii_uppercase)[:num_rows]
    col_index =  [str(x) for x in range(1, 1+num_cols)]
    
    for row, rowname in enumerate(row_index):
        for col, colname in enumerate(col_index):
            s = bbox['north']- (1+row)*cell_size
            n = bbox['north']- row*cell_size
            e = bbox['west'] + (1+col)*cell_size
            w = bbox['west'] + col*cell_size
            cell_name = f"{colname}_{rowname}"

            if col == 0:
                bdy += f"<TR><TD valign='center'>{n:.2f}<br><br><br><B><FONT SIZE='+3'>{rowname}</font></b><br><br><br>{s:.2f}</TD>"
            if row == 0:
                hdr += f"<TD align='center'>{w:.2f}&nbsp;&nbsp;&nbsp; <font size='+3'><b>{colname}</b></font>&nbsp;&nbsp;&nbsp; {e:.2f}</TD>"
            bdy += f"<TD><A HREF='page_{colname}_{rowname}.html'><img src='page_{colname}_{rowname}.png' alt='Avatar' class='image'style='width:{cell_width}'></A>\n"
            html_cell_path = versioning.atlas_path(config, "outlets") / outlet_name / f"page_{colname}_{rowname}.html"
            with open(html_cell_path, "w") as f:
                f.write(f"<html><body><center><font size='+4'><b>{colname}_{rowname}</b></font><br><img src='page_{colname}_{rowname}.png' width='1000px'></center></body></html>")
        bdy += "</TR>\n"
    hdr += "</TR>\n"
    bdy += "</TABLE></BODY></HTML>"
    html_path = versioning.atlas_path(config, "outlets") / outlet_name / "index2.html"
    with open(html_path, "w") as f:
        f.write(hdr+bdy)


    return html_path
    

def outlet_gazetteer(config, outlet_name, skips=[], first_n=0):
    gaz_regions, gaz_html = generate_gazetteerregions(config, outlet_name)
    res =  outlet_regions_grass(config, outlet_name, gaz_regions, gaz_html, skips=skips, first_n=first_n)
    return res


def outlet_runbook( config, outlet_name, skips=[], start_at=0, limit=0):
    """
    For a swale's "regions" layer, generate a runbook. This comprises a series of pages, one per region, linked by the "neighbor" array Property.
    The HTML runbook is a simple HTML page with a list of links to the region pages, and a link to the home page. 
    The PDF runbook is a simple PDF page with a list of links to the region pages, and a link to the home page.
    """


    outlet_config = config['assets'][outlet_name]
    
    #regions = outlet_config['regions']
    # get regions layer
    regions_path = versioning.atlas_path(config, "layers") / "regions" / "regions.geojson"
    regions = regions_from_geojson(regions_path, start_at=start_at, limit=limit)

    swale_name = config['name']
    outlet_dir = versioning.atlas_path(config, "outlets") / outlet_name 
    index_html = "<html><body><h1>Run Book</h1><ul>"
    for i,r in enumerate(regions):
        index_html += f"<li><a href='{r['name']}.html'>{r['caption']}</a></li>"
    index_html += "</ul></body></html>"
    with open(f"{outlet_dir}/index.html", "w") as f:
        f.write(index_html)
    
    html_template = """
<html></body><table><tr><TD><!--<A HREF="../runbook/"><img src='page_{name}_minimap.png' width=400/></A>--></TD></td>
<td>
<center><h1>{caption}</h1></center>
<pre>{map_collar}</pre>
<center><p>{text}</p><i>Click map to zoom, advance to previous/next page in RunBook, or "Home" to return to menu.</i><hr>
{neighbor_links_html}
<a href="..">HOME</a></center></td></tr></TABLE>
<a href='region_{name}.png'><img src='page_{name}.png' width=1200/></a></center></body></html>"""
    # outpath_template = outlet_config['outpath_template'].format(**swale_config)
    #    (<a href='{swale_name}_page_{prev_region}.html'>prev</a>) (<a href='{swale_name}_page_{next_region}.html'>next</a>)
    # 
    gaz_html = []
    md = f"""---
geometry: margin=1cm
---

# {swale_name} RunBook
"""
    for i,r in enumerate(regions):
        # r['next_region']=(i+1) % len(regions)
        # r['prev_region']=(i-1) % len(regions)
        nbr_links = [f"<a href='{nbr_name}.html'>{nbr_dir}</a>" for nbr_dir, nbr_name in r.get('neighbors',{}).items()]
        nbr_links_html = " | ".join(nbr_links)

        map_collar = "None" #build_map_collar(config, swale_name, r['bbox'], layers = outlet_config['layers'])
        gaz_html.append(
            #(outlet_config['config']['outpath_template'].format(
            #    i=i,region_name=r['name'],**config).lower(),
            (outlet_dir / f"{r['name']}.html",
            html_template.format(i=i, region_name=r['name'], neighbor_links_html=nbr_links_html,
                                  swale_name=swale_name, map_collar=map_collar, **r)))
        md += f"![{r['name']}]({outlet_dir}/page_{r['name']}.png){{ width=800px }}\n\n"
    with open(f"{outlet_dir}/dataswale.md", "w") as f:
        f.write(md)
    if 'region_content' not in skips:
        res =  outlet_regions_grass(config, outlet_name, regions, gaz_html, skips=skips)
    if 'pdf' not in skips:
        print(subprocess.check_output(['pandoc', f"{outlet_dir}/dataswale.md", '-o', f"{outlet_dir}/runbook.pdf"]))
        
    return regions

def outlet_sql_duckdb(config: dict, outlet_name: str):
    """Create static DDB tables for SQL queries.
    
    """
    outlet_config = config['assets'][outlet_name]
    data_path = versioning.atlas_path(config, "outlets") / outlet_name /  "atlas.db"
    with duckdb.connect(str(data_path)) as conn:
        conn.execute("INSTALL spatial; LOAD spatial; ")
        for layer in config['dataswale']['layers']:
            if layer['geometry_type'] == 'raster':
                logger.info(f"skipping raster layer {layer['name']}...")
                continue
            if layer['name'] not in outlet_config.get('layers', config['dataswale']['layers']):
                logger.info(f"skipping un-included layer {layer['name']} from { outlet_config.get('layers', config['dataswale']['layers'])}...")
                continue
            layer_path = versioning.atlas_path(config, "layers") / layer['name'] / f"{layer['name']}.geojson"
            logger.info(f"Creating DDB tables for {layer_path} into {data_path}.")
            sql = f"DROP TABLE IF EXISTS {layer['name']}; ;CREATE TABLE {layer['name']} AS SELECT * FROM ST_Read('{layer_path}');"
            
            logger.info(f"executing SQL: {sql}")
            conn.execute(sql)
    return data_path


def sql_query_duckdb(config: dict, outlet_name: str, query: str):
    """Query the DDB for an outlet."""
    data_path = versioning.atlas_path(config, "outlets") / outlet_name /  "atlas.db"
    logger.info(f"Querying DDB: {data_path} with {query}")
    with duckdb.connect(str(data_path)) as conn:
        conn.execute("INSTALL spatial; LOAD spatial; ")
        return conn.execute(query).fetchall()
    
def sql_query(config: dict, outlet_name: str, query: str, return_format: str = 'csv'):
    """Query the DDB for an outlet."""
    result_rows = sql_query_duckdb(config, outlet_name, query)

    file_like = StringIO()
    if return_format == 'json':
        json.dump(result_rows, file_like)
        return file_like.getvalue()

    elif return_format == 'csv':
        
        writer = csv.writer(file_like)
        # writer.writerow(result_rows[0].keys())
        for row in result_rows:
            writer.writerow(row)
        
    else:
        raise ValueError(f"Invalid return format: {return_format}")
    return file_like.getvalue()



def make_attribution_html(atlas_config, swale_config, lc):
    outpath = versioning.atlas_path(atlas_config, "outlets") / swale_config['name'] / f"{lc['name']}"


    
    download_uri = lc.get('inpath_template', 'outpath_template')
    os.makedirs(outpath, exist_ok=True)
    with open(outpath + "/attribution.html", "w") as f:
        f.write(f"""
<html>
<body>
<h1>{lc['name']}</h1>
<p>Description: {lc['attribution']['description']}</p>
<p>About: {lc['attribution']['url']}</p>
<p>Source: {download_uri}</p>
<p>License: {lc['attribution']['license']}</p>
</body>
</html>
        """)


def make_root_html(root_path_str):
    """Given a root directory path, make a root html file. 
    This will be a list of the atlases available. We get that from looking for subdirectories with atlas_config.json file.
    The root html file will have links to the atlases. The text of the link is just the name given in the atlas_config.json file.
    """
    root_path = Path(root_path_str)
    atlas_config_path_list = list(root_path.glob('*/staging/atlas_config.json'))
    #logger.info(f"In root {root_path} found: {list(atlas_config_path_list)}...")
    atlas_path_list = [x.parent.parent.relative_to(root_path) for x in atlas_config_path_list]
    logger.info(f"In root {root_path} found: {list(atlas_path_list)}...")
    atlas_name_list = [json.load(open(x))['name'] for x in atlas_config_path_list]
    #logger.info(f"In root {root_path} found: {list(atlas_name_list)}...")
    atlas_html = """<html>
  <head>
    <style>
      body {
	  background-image: url('http://fireatlas.org/bearbutte1.jpeg');
	  background-repeat: no-repeat;
	  background-attachment: fixed;
	  background-size: 100% 100%;
      }
    </style>
<link rel="stylesheet" href="/local/css/console.css">
    </head>
  <body>
    <center><font size="+2">
      f i r e a t l a s . o r g
      </font>
      <hr width="30%">
      
"""

    atlas_html += "<HR width='40%'><UL>"
    atlas_html += "\n".join( [f"<LI><A HREF='{a}/index.html'>{b}</A></LI>" for a,b in zip(atlas_path_list, atlas_name_list) ] )
    
    atlas_html += "</UL></CENTER></BODY></HTML>"
    outpath = f"{root_path}/index.html"
    logger.info(atlas_html)
    with open(outpath, "w") as f:
        f.write(atlas_html)
    return outpath

def make_console_html(config,
                      displayed_interfaces=[], displayed_downloads=[], displayed_inlets=[], displayed_versions=[], spreadsheets={},
                      admin_controls=[], console_type='ADMINISTRATION', panel_header="", use_cases=[]):
    """Generate HTML for the console interface."""
    logger.info(f"Making Console for {console_type}...")
    
    # Read the template file
    template_path = Path('../templates/console.html')
    with open(template_path, 'r') as f:
        template = f.read()
    
    # Prepare the data for the template
    data = {
        'version_string': config.get('version_string', 'staging'),
        'versions': displayed_versions,
        'logo': config.get('logo', ''),
        'swaleName': config['name'],
        'baseurl': config.get('base_url', 'http://local'),
        'consoleType': console_type,
        'panelHeader': panel_header,
        'interfaces': displayed_interfaces,
        'downloads': displayed_downloads,
        'useCases': use_cases,
        'spreadsheets': spreadsheets,
        'layers': displayed_inlets
    }
    
    # Insert the data initialization script
    script_tag = f'<script>initializePage({json.dumps(data)});</script>'
    html = template.replace('</body>', f'{script_tag}</body>')
    
    return html


def make_swale_html(config, outlet_config, store_materialized=True):
    """Generate HTML for the swale interface."""
    # Get version string
    version_string = config.get('version_string', 'staging')
    
    # Create output directory
    outpath = versioning.atlas_path(config, "outlets") / outlet_config['name']
    #outpath.mkdir(parents=True, exist_ok=True)
    #logger.info(f"Created output directory: {outpath}")
    
    # Copy CSS
    css_dir =  versioning.atlas_path(config, "local") / 'css'
    logger.debug(f"Creating CSS dir: {css_dir}")
    css_dir.mkdir(exist_ok=True)
    subprocess.run(['cp', '../templates/css/console.css', str(css_dir)])
    
    # Get interfaces and downloads based on access level
    public_interfaces = [
        ac for ac in config['assets'].values() 
        if ac['type'] == 'outlet' 
        and ac.get('config',{}).get('interaction') == 'interface' 
        and ac.get('access',['public']).count('public') > 0
        # and ac.get('access') == 'public'
    ]
    
    public_downloads = [
        ac for ac in config['assets'].values() 
        if ac['type'] == 'outlet'
        and ac.get('config',{}).get('interaction') == 'download' 
        and ac.get('access',['public']).count('public') > 0
        # and ac.get('interaction') == 'download' 
        # and ac.get('access') == 'public'
    ]
    
    internal_interfaces = [
        ac for ac in config['assets'].values() 
        if ac['type'] == 'outlet' 
        and ac.get('config',{}).get('interaction') == 'interface' 
        and ac.get('access',['public']).count('internal') > 0
        #and ac.get('interaction') == 'interface' 
        # and ac.get('access') in ('internal', 'public')
    ]
    logger.info(f"Generated Internal interfaces: {internal_interfaces}")
    
    internal_downloads = [
        ac for ac in config['assets'].values()
        if ac.get('config',{}).get('interaction') == 'download' 
        and ac.get('access',['public']).count('internal') > 0
        #if ac.get('interaction') == 'download' 
        and ac['type'] == 'outlet'  
        # and ac.get('access') in ('internal', 'public')
    ]
    
    admin_interfaces = [
        ac for ac in config['assets'].values() 
        if ac['type'] == 'outlet'
        and ac.get('config',{}).get('interaction') == 'interface'         
        and ac.get('access',['public']).count('admin') > 0
        # and ac.get('interaction') == 'interface' 
        # and ac.get('access') in ('admin', 'internal', 'public')
    ]
    logger.info(f"Generated Admin interfaces: {admin_interfaces}")
    admin_downloads = [
        ac for ac in config['assets'].values() 
        if ac['type'] == 'outlet' 
        # and ac.get('interaction') == 'download'
        and ac.get('config',{}).get('interaction') == 'download' 
        and ac.get('access',['public']).count('internal') > 0
        # and ac.get('access') in ('admin', 'internal', 'public')
    ]

    admin_layers = [
        lc for lc in config['dataswale']['layers'] 
        if lc.get('interaction','') == 'interface'
        # and ac.get('access',['public']).count('admin') > 0      
        # and ac.get('interaction') == 'interface'
    ]
    
    # Define use cases
    use_case_paths = list(Path("../documents/help/").glob('*.md'))
    use_cases = { path.stem: [path.read_text().splitlines()[0].replace('# ', ''), str("/local/documents/help/" + path.name).replace('.md', '.html')] for path in use_case_paths}
    
    # Convert markdown files to HTML and write to local documents/help directory
    import markdown
    local_docs_path = versioning.atlas_path(config, "local") / "documents" / "help"
    local_docs_path.mkdir(parents=True, exist_ok=True)
    logger.info(f"Generating help into docs dir: {str(local_docs_path)}: {use_case_paths}")

    # Read the help template
    help_template_path = Path("../templates/help.html")
    help_template = help_template_path.read_text()

    # Track titles and filenames for index generation
    page_list = []
    
    for path in use_case_paths:
        # Read markdown content
        logger.info(f"Converting {path.name} to HTML...")
        markdown_content = path.read_text()
        
        # Convert to HTML
        html_content = markdown.markdown(markdown_content)
        
        # Extract title from first line (remove "# " prefix)
        title = markdown_content.splitlines()[0].replace('# ', '') if markdown_content.startswith('#') else path.stem
        
        # Generate styled HTML using template
        styled_html = help_template.format(
            atlas_name=config['name'],
            title=title,
            content=html_content
        )
        
        # Write HTML file
        html_filename = path.stem + ".html"
        html_path = local_docs_path / html_filename
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(styled_html)
        
        # Store for index generation
        page_list.append((title, html_filename))
        
        logger.info(f"Converted {path.name} to {html_filename}")
    
    # Generate index.html with list of all help pages
    if page_list:
        # Create list items for all pages
        page_links = "\n".join([f'        <li><a href="{filename}">{title}</a></li>' for title, filename in sorted(page_list)])
        
        index_content = f"""
        <h2>Help Topics</h2>
        <p>Browse the available help documentation:</p>
        <ul>
{page_links}
        </ul>
        """
        
        # Generate styled HTML using template
        index_html = help_template.format(
            atlas_name=config['name'],
            title="Help Index",
            content=index_content
        )
        
        # Write index.html
        index_path = local_docs_path / "index.html"
        with open(index_path, 'w', encoding='utf-8') as f:
            f.write(index_html)
        
        logger.info(f"Generated index.html with {len(page_list)} help pages")
    
    # Generate contact page in documents directory (one level up from help)
    contact_content = """
        <h2>Contact Information</h2>
        <p>For questions, support, or more information about this atlas, please reach out:</p>
        
        <h3>General Inquiries</h3>
        <p>
            <strong>Email:</strong> <a href="mailto:info@example.org">info@example.org</a><br>
            <strong>Website:</strong> <a href="https://www.example.org" target="_blank">www.example.org</a>
        </p>
        
        <h3>Technical Support</h3>
        <p>
            <strong>Email:</strong> <a href="mailto:support@example.org">support@example.org</a><br>
            <strong>Website:</strong> <a href="https://support.example.org" target="_blank">support.example.org</a>
        </p>
        
        <h3>Administration</h3>
        <p>
            <strong>Email:</strong> <a href="mailto:admin@example.org">admin@example.org</a><br>
            <strong>Phone:</strong> (555) 123-4567
        </p>
        """
    
    # Generate styled HTML using template
    contact_html = help_template.format(
        atlas_name=config['name'],
        title="Contact",
        content=contact_content
    )
    
    # Write contact.html to documents directory (parent of help directory)
    contact_path = local_docs_path.parent / "contact.html"
    with open(contact_path, 'w', encoding='utf-8') as f:
        f.write(contact_html)
    
    logger.info(f"Generated contact page at {contact_path}")
    
    # Generate about page in documents directory
    about_content = """
        <h2>About Stewardship Atlas</h2>
        
        <p>
            Stewardship Atlas is a powerful geospatial platform designed to help community organizations, volunteer fire departments, 
            land stewards, and local agencies manage and share critical location-based information. By combining interactive web maps, 
            downloadable data packages, and collaborative editing tools, the platform makes it easy to maintain up-to-date records of 
            roads, buildings, water sources, access points, and other essential infrastructure. Whether you're planning emergency response 
            routes, coordinating land management activities, or simply need to share accurate maps with your team, Stewardship Atlas 
            provides the tools to do it efficiently.
        </p>
        
        <p>
            The platform is built around the concept of making geospatial data accessible to everyone, not just GIS professionals. 
            Community members can view and interact with maps through any web browser, download mobile-ready formats for offline use 
            in the field, and contribute updates through simple web interfaces. Administrators have fine-grained control over data 
            layers, user access levels, and versioning, ensuring that information stays accurate and secure while remaining accessible 
            to those who need it.
        </p>
        
        <p>
            Whether your organization needs to maintain a comprehensive atlas of local roads and structures, coordinate with multiple 
            agencies on shared resources, or simply provide your volunteers with reliable maps they can use on their phones, 
            Stewardship Atlas offers a flexible, scalable solution. The platform supports standard GIS formats, integrates with 
            existing workflows, and can be customized to meet the specific needs of your community.
        </p>
        """
    
    # Generate styled HTML using template
    about_html = help_template.format(
        atlas_name=config['name'],
        title="About",
        content=about_content
    )
    
    # Write about.html to documents directory
    about_path = local_docs_path.parent / "about.html"
    with open(about_path, 'w', encoding='utf-8') as f:
        f.write(about_html)
    
    logger.info(f"Generated about page at {about_path}")

    # use_cases = {"add_road": ["Howto: Add a new road.", "https://internal.fireatlas.org/documentation/"],
    #              "add_building" :["Howto: Add a new building.", "https://internal.fireatlas.org/documentation/"],
    #              "download_geojson": ["Howto: Download vector layer as GeoJSON.", "https://internal.fireatlas.org/documentation/"],
    #              "download_gpkg": ["Howto: Download atlas as GeoPKG", "https://internal.fireatlas.org/documentation/"],
    #              "download_runbook": ["Howto: download Run Book","https://internal.fireatlas.org/documentation/"],
    #              "download_runbook": ["Howto: Hide/Show layers in interactive web map.","https://internal.fireatlas.org/documentation/"],
    #              "hide_layers": ["Howto: Hide/Show layers in interactive web map.","https://internal.fireatlas.org/documentation/"],
    #              "upload_geojson": ["Howto: Add a GeoJSON file to an existing vector layer.","https://internal.fireatlas.org/documentation/"],
    #              "platform_overview": ["Overview of Stewardship Atlas Platform","https://internal.fireatlas.org/documentation/"],
    #              "python_overview": ["Python Documentation","https://internal.fireatlas.org/documentation/"],
    #              "schema_overview": ["Schema Documentation","https://internal.fireatlas.org/documentation/"]
                 
    #              }
                                   
    user_cases_config = [
        #{"name": "Firefighter", "cases": ["Download Avenza version", "Share a QR Code for Avenza", "Mark an Incident", "Mark a POI"]},
        #{"name": "GIS Practitioner", "cases": ["Download Layer GeoJSON", "Download GeoPKG", "Add a layer as GeoJSON"]},
        #{"name": "Administrator", "cases": ["Go to Admin interface", "Switch Version"]},
        {"name": "VFD Member", "cases": ["download_runbook", "download_gpkg", "hide_layers"]},
        {"name": "VFD Administrator", "cases": ['add_road', 'add_building', 'download_gpkg']},
        {"name": "GIS Practitioner", "cases": ["platform_overview",'download_geojson', 'download_gpkg', 'add_road', 'add_building', "upload_geojson", "schema_overview"]},
        {"name": "Developer", "cases": ["platform_overview", "python_overview", "schema_overview", "download_gpkg"]}
        ]
    user_cases = []
    for u in user_cases_config:
        uc = [{'name': use_cases[label][0], 'uri': use_cases[label][1]} for label in u['cases'] if label in use_cases]
        user_cases.append( {'name': u['name'], 'cases': uc} )
    user_cases.append( {"name": "All Users", "cases": [ {'name': use_cases[label][0], 'uri': use_cases[label][1]} for label in use_cases.keys()]})
    logger.info(f"Generated User Cases: {user_cases}")
         
    # Generate admin view
    admin_html = make_console_html(
        config,
        console_type='ADMINISTRATION',
        panel_header = 'Layer operations and Access',        
        displayed_interfaces=admin_interfaces, 
        displayed_downloads=admin_downloads, 
        displayed_inlets=admin_layers,
        spreadsheets = config['spreadsheets'],
        displayed_versions=[str(v)for v in config.get('dataswale', {}).get('versions', [])],
        admin_controls=[],
        use_cases=[]
    )
    
    admin_path = outpath / "admin"
    admin_path.mkdir(parents=True, exist_ok=True)
    with open(admin_path / "index.html", "w") as f:
        f.write(admin_html)
    logger.debug(f"Wrote admin view to: {admin_path}")

    # Generate internal view
    internal_html = make_console_html(
        config,
        console_type='INTERNAL',
        panel_header = 'Help and How-To',
        displayed_interfaces=internal_interfaces, 
        displayed_downloads=internal_downloads, 
        displayed_inlets=[], 
        displayed_versions=[str(v) for v in config.get('dataswale', {}).get('versions', [])],
        admin_controls=[],
        use_cases=user_cases
    )
    
    internal_path = outpath / "internal" 
    internal_path.mkdir(parents=True, exist_ok=True)
    with open(internal_path/ "index.html", "w") as f:
        f.write(internal_html)
    logger.debug(f"Wrote internal view to: {internal_path}")
        
    # Generate public view
    public_html = make_console_html(
        config,
        console_type='PUBLIC',
        panel_header = 'Help and How-to',
        displayed_interfaces=public_interfaces, 
        displayed_downloads=public_downloads, 
        displayed_inlets=[], 
        displayed_versions=[],
        use_cases=[],
        admin_controls=[("Internal", "internal.html")]
    )
    
    public_path = outpath / "public"
    public_path.mkdir(parents=True, exist_ok=True)
    with open(public_path / "index.html", "w") as f:
        f.write(public_html)
    logger.debug(f"Wrote public view to: {public_path}")

    return outpath

def outlet_html(config, outlet_name):
    """Generate HTML for all outlets."""
    outlet_config = config['assets'][outlet_name]
    # Create HTML for each swale
    #for swale in config.get('dataswales', []):
    return make_swale_html(config, outlet_config)

def outlet_sqlquery(config: dict, outlet_name: str):
    """Generate HTML interface for SQL queries."""
    outlet_config = config['assets'][outlet_name]
    outpath = versioning.atlas_path(config, "outlets") / outlet_name
    outpath.mkdir(parents=True, exist_ok=True)
    
    # Create CSS and JS directories
    css_dir = outpath / 'css'
    js_dir = outpath / 'js'
    css_dir.mkdir(exist_ok=True)
    js_dir.mkdir(exist_ok=True)
    
    # Get available tables from sqldb outlet
    sqldb_config = config['assets'].get('sqldb', {})
    available_tables = sqldb_config.get('layers', [])
    tables_list = ''
    for table in available_tables:
        columns = sql_query(config, outlet_name, f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table}'")
        columns_list = ', '.join([f'<li>{column}</li>' for column in columns])
        tables_list += f'<li>{table}: {columns_list}</li>\n'
    
    # Read and process template
    with open('../templates/sqlquery.html', 'r') as f:
        template = f.read()
    
    # Replace placeholders
    template = template.replace('{atlas_name}', config['name'])
    template = template.replace('{tables_list}', tables_list)
    
    # Write processed template
    with open(outpath / 'index.html', 'w') as f:
        f.write(template)
    
    # Copy CSS and JS files
    subprocess.run(['cp', '../templates/css/sqlquery.css', str(css_dir)])
    subprocess.run(['cp', '../templates/js/sqlquery.js', str(js_dir)])
    
    return outpath / 'index.html'

def outlet_config_editor(config: dict, outlet_name: str):
    """Generate HTML interface for editing the atlas configuration file.
    
    This creates an interactive JSON editor using JSONEditor by Jos de Jong
    that allows administrators to view and edit the atlas_config.json file
    with syntax highlighting, validation, and a user-friendly interface.
    """
    outlet_config = config['assets'][outlet_name]
    outpath = versioning.atlas_path(config, "outlets") / outlet_name
    outpath.mkdir(parents=True, exist_ok=True)
    
    # Read the template file
    with open('../templates/config_editor.html', 'r') as f:
        template = f.read()
    
    # Replace placeholders
    html_content = template.format(
        atlas_name=config['name'],
        swale_name=config['name'],
        base_url=config.get('base_url', 'http://localhost')
    )
    
    # Write the HTML file
    with open(outpath / 'index.html', 'w') as f:
        f.write(html_content)
    
    logger.info(f"Created config editor at {outpath / 'index.html'}")
    
    return outpath / 'index.html'
   
#blah = """
asset_methods = {
    #'outlet_gpkg': outlet_gpkg,
    #'tiff': outlet_tiff,
    #'pdf': outlet_geopdf,
    'html': outlet_html,
    #'gazetteer': outlet_gazetteer,
    'runbook': outlet_runbook,
    'webmap': outlet_webmap,
    #'webmap_public': outlet_webmap,
    'webedit': outlet_webmap_edit,
    'sqlquery': outlet_sqlquery,
    'config_editor': outlet_config_editor
}
#"""

def _overlay_png_symbols_on_map(map_image_path, region, config):
    """
    Post-process a map image to overlay PNG symbols at point coordinates.
    
    Args:
        map_image_path: Path to the generated map image
        region: Region configuration dictionary
        config: Atlas configuration dictionary
    """
    try:
        from PIL import Image, ImageDraw
        import json
        from pathlib import Path
        
        # Load the map image
        map_img = Image.open(map_image_path)
        map_width, map_height = map_img.size
        
        # Get region bounds for coordinate transformation
        bbox = region['bbox']
        region_width = bbox['east'] - bbox['west']
        region_height = bbox['north'] - bbox['south']
        
        # Process each vector layer for PNG symbols
        for lc, lp in region['vectors']:
            if lc.get('geometry_type') != 'point':
                continue
                
            png_config = lc.get('png_symbol', {})
            if not png_config or not png_config.get('path'):
                continue
                
            png_path = png_config['path']
            png_size = png_config.get('size', 20)
            
            # Check if PNG file exists
            if not Path(png_path).exists():
                logger.warning(f"PNG symbol file not found: {png_path}")
                continue
                
            # Load PNG symbol
            try:
                png_symbol = Image.open(png_path)
                # Resize PNG to specified size
                png_symbol = png_symbol.resize((png_size, png_size), Image.Resampling.LANCZOS)
            except Exception as e:
                logger.warning(f"Failed to load PNG symbol {png_path}: {e}")
                continue
            
            # Load point coordinates from GeoJSON
            try:
                with open(lp, 'r') as f:
                    geojson_data = json.load(f)
                    
                for feature in geojson_data.get('features', []):
                    if feature['geometry']['type'] == 'Point':
                        coords = feature['geometry']['coordinates']
                        lon, lat = coords[0], coords[1]
                        
                        # Convert geographic coordinates to image coordinates
                        # Note: This assumes the map image covers the exact region bounds
                        x = int((lon - bbox['west']) / region_width * map_width)
                        y = int((bbox['north'] - lat) / region_height * map_height)
                        
                        # Calculate position for centered placement
                        x_offset = x - png_size // 2
                        y_offset = y - png_size // 2
                        
                        # Paste PNG symbol onto map
                        if (x_offset >= 0 and y_offset >= 0 and 
                            x_offset + png_size <= map_width and 
                            y_offset + png_size <= map_height):
                            map_img.paste(png_symbol, (x_offset, y_offset), png_symbol)
                            
            except Exception as e:
                logger.warning(f"Failed to process points for layer {lc['name']}: {e}")
                continue
        
        # Save the modified map image
        map_img.save(map_image_path)
        logger.info(f"PNG symbols overlaid on map: {map_image_path}")
        
    except ImportError:
        logger.warning("PIL/Pillow not available. PNG symbols will not be overlaid.")
    except Exception as e:
        logger.error(f"Failed to overlay PNG symbols: {e}")


def build_region_map_grass_png(config, outlet_name, region):
    """
    Build a region map using GRASS GIS with PNG images for point features instead of icons.
    
    Args:
        config: Atlas configuration dictionary
        outlet_name: Name of the outlet
        region: Region configuration dictionary
        
    Returns:
        Path to the generated map image
    """
    
    grass_init(config['name'])
    import grass.script as gs
    import grass.jupyter as gj
    
    if region['name'] == 'all':
        size = 9000
    else:
        size = 2400
    
    m = gj.Map(height=size, width=size)
    clip_bbox = region['bbox']
    gs.read_command('g.region', n=clip_bbox['north'], s=clip_bbox['south'],e=clip_bbox['east'],w=clip_bbox['west'])   
    
    # load region layers
    raster_name = 'hillshadered_' + region['name']
    logger.info(f"making map image for {region}.")
    gs.read_command('r.in.gdal',  band=1,input=region['raster'][1], output=raster_name)
    gs.read_command('r.colors', map=raster_name, color='grey')
    
    gs.read_command('r.mapcalc.simple', expression="1", output='ones')
    gs.read_command('r.colors', map='ones', color='grey1.0')
    gs.read_command('r.blend', flags="c", first=raster_name, second='ones', output='blended', percent='55', overwrite=True)
    
    m.d_rast(map='blended')
    
    # Track which layers have PNG symbols for post-processing
    png_layers = []
    
    # add layers to map
    for lc,lp in region['vectors']:
        logger.debug(f"adding region {lc} to map for region {region['name']}")
        if lc['name'] in region.get('config', {}):
            for update_key, update_value in region['config'].items():
                lc[update_key] = update_value
                logger.info(f"region config override: {region['name']} | {lc['name']}: {region['config']} -> {lc}")
        
        gs.read_command('v.import', input=lp, output=lc['name'])
        
        # clunky but need to skip empty sets
        lame = json.load(open(lp))
        if len(lame['features']) < 1:
            logger.info("layer is empty...")
            continue
            
        if lc.get('geometry_type', 'line') == 'point':
            c = lc.get('color', (100,100,100))
            
            # Check if PNG configuration exists
            png_config = lc.get('png_symbol', {})
            if png_config and png_config.get('path'):
                png_path = png_config['path']
                png_size = png_config.get('size', 20)
                
                logger.info(f"PNG symbol configured for {lc['name']}: {png_path} (size: {png_size})")
                
                # For PNG symbols, use minimal/invisible icons so we can overlay PNGs later
                if lc.get('add_labels', False):
                    logger.debug("Adding Points with PNG symbols and labels")
                    m.d_vect(map=lc['name'],
                             color=f"{c[0]}:{c[1]}:{c[2]}",
                             icon='basic/circle',  # Minimal icon
                             size=1,  # Very small to be nearly invisible
                             label_size=25,
                             attribute_column=lc.get('alterations', {}).get('label_attribute', 'name'))
                else:
                    logger.debug("Adding Points with PNG symbols (no labels)")
                    m.d_vect(map=lc['name'],
                             color=f"{c[0]}:{c[1]}:{c[2]}",
                             icon='basic/circle',  # Minimal icon
                             size=1)  # Very small to be nearly invisible
                
                # Track this layer for PNG overlay
                png_layers.append((lc, lp, png_config))
                
            else:
                # No PNG config, use default icon behavior
                if lc.get('add_labels', False):
                    logger.debug("Adding Points with default symbol and labels")
                    m.d_vect(map=lc['name'],
                             color=f"{c[0]}:{c[1]}:{c[2]}",
                             icon=lc.get('symbol', 'basic/diamond'),size=20,
                             label_size=25,
                             attribute_column=lc.get('alterations', {}).get('label_attribute', 'name'))
                else:
                    logger.debug("Adding Points with default symbol")
                    m.d_vect(map=lc['name'],
                             color=f"{c[0]}:{c[1]}:{c[2]}",
                             icon=lc.get('symbol', 'basic/diamond'),size=10)
        else:
            # Handle non-point geometries (lines, polygons) the same as original
            c = lc.get('color', (100,100,100))
            fc = lc.get('fill_color', c)
            if 'vector_width' in lc:
                m.d_vect(map=lc['name'],
                         color=f"{c[0]}:{c[1]}:{c[2]}",
                         fill_color=f"{fc[0]}:{fc[1]}:{fc[2]}" if fc != 'none' else 'none',
                         width_column='vector_width',
                         attribute_column=lc.get('alterations', {}).get('label_attribute', 'name'),
                         label_color=f"{c[0]}:{c[1]}:{c[2]}", label_size=50)
            else:
                m.d_vect(map=lc['name'],
                         color=f"{c[0]}:{c[1]}:{c[2]}",
                         fill_color=f"{fc[0]}:{fc[1]}:{fc[2]}" if fc != 'none' else 'none',
                         width = lc.get("constant_width", 2),
                         attribute_column=lc.get('alterations', {}).get('label_attribute', 'name'),
                         label_color=f"{c[0]}:{c[1]}:{c[2]}", label_size=50)
                
    m.d_grid(size=0.5,color='black')
    m.d_legend_vect()

    # export map
    outpath = versioning.atlas_path(config, "outlets") / outlet_name / f"page_{region['name']}.png"
    m.save(outpath)
    
    # Post-process to overlay PNG symbols if any were configured
    if png_layers:
        _overlay_png_symbols_on_map(outpath, region, config)
    
    # return path to map
    return outpath

def outlet_3dview(atlas_name, config):
    """Generate a 3D terrain view using MapLibre GL JS."""
    from pathlib import Path
    import _3dview
    
    # Generate the 3D terrain HTML file
    output_path = Path("outlets") / "3dview.html"
    _3dview.create_3d_terrain_view(atlas_name, config, output_path)
    
    return {
        'type': 'html',
        'path': str(output_path),
        'description': '3D terrain visualization using MapLibre GL JS'
    }

def outlet_notebook_jupyter(config, outlet_name):
    """Generate a Jupyter notebook for the outlet."""
 

    # Create a new Jupyter notebook


    notebook = nbformat.v4.new_notebook()
    notebook['metadata'] = {
        'kernelspec': {
            'name': 'python3',
            'display_name': 'Python 3'
        }
    }
    
    # Add python cells directly - would be cool load them from outlet config
    config_path = versioning.atlas_path(config, "atlas_config.json")

    notebook.cells.append(nbformat.v4.new_markdown_cell("""
![atlas](atlas4.png)

A fire atlas is a configuration convention for geospatial assets related to community fire planning and response together with a configuration for ways to instantiate, edit, and manage those assets.

A stewardship atlas is a data set, a confuration for storing, processing, and sharing that data set, and a set of implementions to do so.

This is a minimal notebook intended as a starting point for working with a specific atlas. It comes prebuilt to get started with:
* generate atlas - bootstrap configuration
* populate atlas with data
* materialize some core interfaces to the atlas
  * web map
  * web edit
  * html console
  * sql query
"""))
    
    notebook.cells.append(nbformat.v4.new_code_cell("""
import sys, os, subprocess, time, json, string, datetime, random, math
sys.path.insert(0, "/root/stewardship_atlas/python")
import dataswale_geojson as dataswale
import deltas_geojson as deltas
import versioning
import outlets
import atlas
"""))
    notebook.cells.append(
        nbformat.v4.new_code_cell(f"c = json.load(open('{config_path}'))"))
    notebook.cells.append(nbformat.v4.new_code_cell("""
# Core elevation derived layers
atlas.materialize(config=c, asset_name="dem", delta_queue=deltas)
dataswale.refresh_raster_layer(c, 'elevation', deltas.apply_deltas)
atlas.materialize(c, 'derived_hillshade')
atlas.materialize(c, 'gdal_contours')

#Core vector lyaers
atlas.materialize(c, 'public_roads')
dataswale.refresh_vector_layer(c, 'roads')
atlas.materialize(materializers, c, 'public_creeks')
dataswale.refresh_vector_layer(c, 'creeks', deltas.apply_deltas)

atlas.materialize(config=c, name="public_buildings", delta_queue=deltas)
dataswale.refresh_vector_layer(c, 'buildings', deltas.apply_deltas)

# Outlets
outlets.outlet_html(c, 'html')
atlas.materialize(c, 'webmap')
atlas.materialize(c, 'webedit')
"""))

    
    nb_name = f"{outlet_name}-{config['name']}"
    with open(f'{nb_name}.ipynb', 'w', encoding='utf-8') as f:
        nbformat.write(notebook, f)

    

def gsheet_export(config: dict, outlet_name: str) -> str:
    """Create a Google Sheet layer from an atlas layer."""
 
    # set up spreadsheet
    statefile_path = versioning.atlas_path(config) / "state.json"
    gc = gspread.service_account()
    links = {}
    # get layers in outlet config
    for layer_name in config['assets'][outlet_name]['in_layers']:
        # get path to layer
        layer = dataswale_geojson.layer_as_featurecollection(config, layer_name)
        gsheet_name = f"{config['name']} Fire Atlas: {layer_name}"
        sh = gc.create(gsheet_name)
        sh.share('gateless@gmail.com', perm_type='user', role='writer')

        sh.add_worksheet(title=layer_name, rows=100, cols=20)
        wks = sh.get_worksheet(0)
        header = []
        cells = []
        for i,f in enumerate(layer['features']):
            #logger.info(f"Adding row for Prop [{type(f['properties'])}]: {f['properties']} and geom [{type(f['properties'])}]:: {f['geometry']}...")
                
            for j,p in enumerate(f['properties'].items()):
                if i == 0:
                    cells.append(gspread.Cell(row=i+1, col=j+1, value=p[0]))
                    #wks.update_cell(i+1, j+1, p[0])
                #wks.update_cell(i+2, j+1, p[1])
                cells.append( gspread.Cell(row=i+2, col=j+1, value=p[1]))
            cells.append( gspread.Cell(row=i+1, col=j+2, value=geojson.dumps(f['geometry'])) )
        wks.update_cells(cells)
        links[layer_name] =  sh.url
        #sh.close()
        
        #state = json.load(open(statefile_path))

        # Set and write state to reflect generated spreadsheets
        config_path = versioning.atlas_path(config) / "atlas_config.json"
        config['spreadsheets'] = links
        #json.dump(state, open(statefile_path, "w"))
        json.dump(config, open(config_path, "w"))
                  
        return statefile_path

