import subprocess
import json, csv
import sys,os
from io import StringIO
from pathlib import Path
import duckdb

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
    


def extract_region_layer_ogr_grass(config, outlet_name, layer, region):
    """Grab a region of a layer and export it as a GeoJSON file using GRASS GIS. Note this assumes the entire layer is already in GRASS. Which is awful."""
    swale_name = config['name']
    outpath = versioning.atlas_path(config,  "outlets") / outlet_name / f"{layer}_{region['name']}.geojson"
    logger.info(f"Extracting region {region['name']} of vector layer {layer} to {outpath}.")

    grass_init(swale_name)  
    import grass.script as gs
    clip_bbox = region['bbox']
    gs.read_command('g.region', n=clip_bbox['north'], s=clip_bbox['south'],e=clip_bbox['east'],w=clip_bbox['west'])
    gs.read_command('v.clip', flags='r', input=layer, output=f'{layer}_clip')
    
    gs.read_command('v.out.ogr', input=f'{layer}_clip', output=outpath, format='GeoJSON')
    return outpath

def extract_region_layer_raster_grass(config, outlet_name, layer, region):
    """Grab a region of a rasterlayer and export it. Note this assumes the entire layer is already in GRASS. Which is awful."""
    swale_name = config['name']
    inpath = versioning.atlas_path(config, 'layers') / layer / f"{layer}.tiff"
    outpath = versioning.atlas_path(config,  "outlets") / outlet_name / f"{layer}_{region['name']}.tiff"
    
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


def build_region_map_grass(config, outlet_name, region):

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
    print(f"making map image for {region}.")
    gs.read_command('r.in.gdal',  band=1,input=region['raster'][1], output=raster_name)
    gs.read_command('r.colors', map=raster_name, color='grey')
    
    gs.read_command('r.mapcalc.simple', expression="1", output='ones')
    gs.read_command('r.colors', map='ones', color='grey1.0')
    gs.read_command('r.blend', flags="c", first=raster_name, second='ones', output='blended', percent='25', overwrite=True)
    
    m.d_rast(map='blended')   
    # m.d_rast(map=raster_name)  
    # add layers to map
    for lc,lp in region['vectors']:
        if lc['name'] in region.get('config', {}):
            for update_key, update_value in region['config'].items():
                lc[update_key] = update_value
                print(f"region config override: {region['name']} | {lc['name']}: {region['config']} -> {lc}")
        gs.read_command('v.import', input=lp, output=lc['name'])
        # clunky but need to skip empty sets
        lame = json.load(open(lp))
        if len(lame['features']) < 1:
            continue
        if lc.get('feature_type', 'line') == 'point':
            c = lc.get('color', (100,100,100))
            if lc.get('add_labels', False):
                m.d_vect(map=lc['name'],
                         color=f"{c[0]}:{c[1]}:{c[2]}",
                         icon=lc.get('symbol', 'basic/diamond'),size=10,
                         label_size=15,
                         attribute_column=lc.get('alterations', {}).get('label_attribute', 'name'))
            else:
                m.d_vect(map=lc['name'],
                         color=f"{c[0]}:{c[1]}:{c[2]}",
                         icon=lc.get('symbol', 'basic/diamond'),size=10)
        else:
            # This is interesting: vector width comes from features or layer conf? Former, right?
            # m.d_vect(map=lc['name'], color=lc.get('color', 'gray'), width=lc.get('width_base',5))
            c = lc.get('color', (100,100,100))
            fc = lc.get('fill_color', c)
            m.d_vect(map=lc['name'],
                     color=f"{c[0]}:{c[1]}:{c[2]}",
                     fill_color=f"{fc[0]}:{fc[1]}:{fc[2]}" if fc != 'none' else 'none',
                     width_column='vector_width',
                     attribute_column=lc.get('alterations', {}).get('label_attribute', 'name'),
                     label_color=f"{c[0]}:{c[1]}:{c[2]}", label_size=25)
   
    m.d_grid(size=0.5,color='black')
    m.d_legend_vect()

    # export map
    outpath = versioning.atlas_path(config, "outlets") / outlet_name / f"page_{region['name']}.png"
    m.save(outpath)
    # return path to map
    return outpath
    

def outlet_regions_grass(config, outlet_name, regions = [], regions_html=[], skips=[]):
    """Process regions for gazetteer and runbook outputs using versioned paths."""
    swale_name = config['name']
    outlet_config = config['assets'][outlet_name]
    if 'region_maps' not in skips:
        # Set up Grass environment
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
                    logger.debug(f"Processing vector region: {region['name']}")
                    region['vectors'].append([lc, extract_region_layer_ogr_grass(config, outlet_name, lc['name'], region)])
            else:
                gs.read_command('r.in.gdal', input=staging_path, output=lc['name'])
                for region in regions:
                    logger.debug(f"Processing raster region: {region['name']}")
                    region['raster'] = [lc,
                                        extract_region_layer_raster_grass(config, outlet_name, lc['name'], region)]
                
        # Build maps for each region
        for region in regions:
            logger.debug(f"Building map for region: {region['name']}")
            # build_region_minimap(swale_config, swale_config['data_root'], swale_name, version_string,  outlet_config['name'], region)
            build_region_map_grass(config, outlet_name, region)
    
    # Write output files
    for outfile_path, outfile_content in regions_html:
        versioned_path = versioning.atlas_path(config, "outlets") / outlet_name / outfile_path
        # os.makedirs(os.path.dirname(versioned_path), exist_ok=True)
        logger.info(f"Writing region output to: {versioned_path}")
        with open(versioned_path, "w") as f:
            f.write(outfile_content)
    
    return regions

def outlet_runbook( config, outlet_name, skips=[]):
    outlet_config = config['assets'][outlet_name]
    
    regions = outlet_config['config']['regions']

    swale_name = config['name']
    outlet_dir = versioning.atlas_path(config, "outlets") / outlet_name 
    index_html = "<html><body><h1>Run Book</h1><ul>"
    for i,r in enumerate(regions):
        index_html += f"<li><a href='{r['name'].lower()}.html'>{r['caption']}</a></li>"
    index_html += "</ul></body></html>"
    with open(f"{outlet_dir}/index.html", "w") as f:
        f.write(index_html)
    
    html_template = """
<html></body><table><tr><TD><A HREF="../runbook/"><img src='page_{region_name_lower}_minimap.png' width=400/></A></TD></td>
<td>
<center><h1>{caption}</h1></center>
<pre>{map_collar}</pre>
<center><p>{text}</p><i>Click map to zoom, advance to previous/next page in RunBook, or "Home" to return to menu.</i><hr>
(<a href='{swale_name}_page_{prev_region}.html'>prev</a>) (<a href='{swale_name}_page_{next_region}.html'>next</a>)
<a href="..">HOME</a></center></td></tr></TABLE>
<a href='page_{name}.png'><img src='page_{name}.png' width=1200/></a></center></body></html>"""
    # outpath_template = outlet_config['outpath_template'].format(**swale_config)
    gaz_html = []
    md = f"# {swale_name} RunBook\n\n"

    for i,r in enumerate(regions):
        r['next_region']=(i+1) % len(regions)
        r['prev_region']=(i-1) % len(regions)
        map_collar = "None" #build_map_collar(config, swale_name, r['bbox'], layers = outlet_config['layers'])
        gaz_html.append(
            #(outlet_config['config']['outpath_template'].format(
            #    i=i,region_name=r['name'],**config).lower(),
            (outlet_dir / f"{r['name'].lower()}.html",
            html_template.format(i=i, region_name=r['name'], region_name_lower=r['name'].lower(),
                                  swale_name=swale_name, map_collar=map_collar, **r)))
        md += f"## {r['name']}\n![{r['name']}]({outlet_dir}/page_{r['name']}.png)\n{r['caption']}\n\n{r['text']}\n\n"
    with open(f"{outlet_dir}/dataswale.md", "w") as f:
        f.write(md)
    if 'region_content' not in skips:
        res =  outlet_regions_grass(config, outlet_name, regions, gaz_html, skips=skips)
        print(subprocess.check_output(['pandoc', f"{outlet_dir}/dataswale.md", '-o', f"{outlet_dir}/runbook.pdf"]))
        
    return regions

def outlet_sql_duckdb(config: dict, outlet_name: str):
    """Create DDB tables for SQL queries."""
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
    with duckdb.connect(str(data_path)) as conn:
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


def make_root_html(atlas_config):
    atlas_html = f"<HTML><BODY><CENTER><h1>Dataswales</h1>"
    atlas_html += "<HR width='40%'><UL>".join( [f"<LI><A HREF='{a['name']}/index.html'>{a['name']}</A></LI>" for a in atlas_config['dataswales'] ] ) + "</UL>"
    atlas_html += "<h2>Curation</h2>"
    atlas_html += "<HR width='40%'>".join( [x for x in ['Refresh', 'Publish Version'] ])
    atlas_html += "<h2>Tools and Interfaces</h2>"
    atlas_html += "<HR width='40%'>".join( [x for x in ['Notebook', 'Pipeline', 'Tools'] ])
    atlas_html += "</BODY></HTML>"
    outpath = f"{atlas_config['data_root']}/index.html"
    with open(outpath, "w") as f:
        f.write(atlas_html)
    return outpath

def make_console_html(config,
                     displayed_interfaces=[], displayed_downloads=[], displayed_inlets=[], displayed_versions=[],
                      admin_controls=[], console_type='ADMINISTRATION', use_cases=[]):
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
        'consoleType': console_type,
        'interfaces': displayed_interfaces,
        'downloads': displayed_downloads,
        'useCases': use_cases,
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
    outpath.mkdir(parents=True, exist_ok=True)
    logger.info(f"Created output directory: {outpath}")
    
    # Copy CSS
    css_dir = outpath / 'css'
    logger.info(f"Creating CSS dir: {css_dir}")
    css_dir.mkdir(exist_ok=True)
    subprocess.run(['cp', '../templates/css/console.css', str(css_dir)])
    
    # Get interfaces and downloads based on access level
    public_interfaces = [
        ac for ac in config['assets'].values() 
        if ac['type'] == 'outlet' 
        and ac.get('config',{}).get('interaction') == 'interface' 
        # and ac.get('access') == 'public'
    ]
    
    public_downloads = [
        ac for ac in config['assets'].values() 
        if ac['type'] == 'outlet'
        and ac.get('config',{}).get('interaction') == 'download' 
        # and ac.get('interaction') == 'download' 
        # and ac.get('access') == 'public'
    ]
    
    internal_interfaces = [
        ac for ac in config['assets'].values() 
        if ac['type'] == 'outlet' 
        and ac.get('config',{}).get('interaction') == 'interface' 
        #and ac.get('interaction') == 'interface' 
        # and ac.get('access') in ('internal', 'public')
    ]
    
    internal_downloads = [
        ac for ac in config['assets'].values()
        if ac.get('config',{}).get('interaction') == 'download' 
        #if ac.get('interaction') == 'download' 
        and ac['type'] == 'outlet'  
        # and ac.get('access') in ('internal', 'public')
    ]
    
    admin_interfaces = [
        ac for ac in config['assets'].values() 
        if ac['type'] == 'outlet'
        and ac.get('config',{}).get('interaction') == 'interface'         
        # and ac.get('interaction') == 'interface' 
        # and ac.get('access') in ('admin', 'internal', 'public')
    ]
    
    admin_downloads = [
        ac for ac in config['assets'].values() 
        if ac['type'] == 'outlet' 
        # and ac.get('interaction') == 'download'
        and ac.get('config',{}).get('interaction') == 'download' 
        # and ac.get('access') in ('admin', 'internal', 'public')
    ]

    admin_inlets = [
        ac for ac in config['assets'].values() 
        if ac['type'] in ('inlet', 'eddy')
        and ac.get('config',{}).get('interaction') == 'interface'         
        # and ac.get('interaction') == 'interface'
    ]
    
    # Define use cases
    internal_usecases = [
        {"name": "Firefighter", "cases": ["Download Avenza version", "Share a QR Code for Avenza", "Mark an Incident", "Mark a POI"]},
        {"name": "GIS Practitioner", "cases": ["Download Layer GeoJSON", "Download GeoPKG", "Add a layer as GeoJSON"]},
        {"name": "Administrator", "cases": ["Go to Admin interface", "Switch Version"]}
    ]
    
    # Generate admin view
    admin_html = make_console_html(
        config,
        console_type='ADMINISTRATION',
        displayed_interfaces=admin_interfaces, 
        displayed_downloads=admin_downloads, 
        displayed_inlets=admin_inlets, 
        displayed_versions=['published'] + [v['version_string'] for v in config.get('versions', [])],
        admin_controls=[],
        use_cases=[]
    )
    
    admin_path = outpath / "admin.html"
    with open(admin_path, "w") as f:
        f.write(admin_html)
    logger.debug(f"Wrote admin view to: {admin_path}")

    # Generate internal view
    internal_html = make_console_html(
        config,
        console_type='INTERNAL',
        displayed_interfaces=internal_interfaces, 
        displayed_downloads=internal_downloads, 
        displayed_inlets=[], 
        displayed_versions=['published'] + [v['version_string'] for v in config.get('versions', [])],
        admin_controls=[],
        use_cases=internal_usecases
    )
    
    internal_path = outpath / "internal.html"
    with open(internal_path, "w") as f:
        f.write(internal_html)
    logger.debug(f"Wrote internal view to: {internal_path}")
        
    # Generate public view
    public_html = make_console_html(
        config,
        console_type='PUBLIC',
        displayed_interfaces=public_interfaces, 
        displayed_downloads=public_downloads, 
        displayed_inlets=[], 
        displayed_versions=[],
        use_cases=internal_usecases,
        admin_controls=[("Internal", "internal.html")]
    )
    
    public_path = outpath / "index.html"
    with open(public_path, "w") as f:
        f.write(public_html)
    logger.debug(f"Wrote public view to: {public_path}")

    return outpath

def outlet_html(config, outlet_name):
    """Generate HTML for all outlets."""
    outlet_config = config['assets'][outlet_name]
    # Create HTML for each swale
    #for swale in config.get('dataswales', []):
    make_swale_html(config, outlet_config)
        
    return versioning.atlas_path(config, "html")
   

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
    tables_list = '\n'.join([f'<li>{table}</li>' for table in available_tables])
    
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
   
blah = """
OUTLET_MATERIALIZER = {
    'outlet_gpkg': outlet_gpkg,
    'tiff': outlet_tiff,
    'pdf': outlet_geopdf,
    'html': outlet_html,
    'gazetteer': outlet_gazetteer,
    'runbook': outlet_runbook,
    'webmap': outlet_webmap,
    'webmap_public': outlet_webmap,
    'webedit': outlet_webmap_edit,
    'sqlquery': outlet_sqlquery
}
"""
