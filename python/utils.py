
import logging, subprocess, json

# Configure logging
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)
logger.setLevel(logging.INFO)

def rgb_to_css(rgb_tuple):
    return f'rgb({rgb_tuple[0]}, {rgb_tuple[1]}, {rgb_tuple[2]})'
             
#def line_width(width_base, width_delta):
#    return width_base + width_delta

def bbox_to_corners(b):
    return [
        [b['west'], b['north']],
        [b['east'], b['north']],
        [b['east'], b['south']],
        [b['west'], b['south']],
        ]

def bbox_to_polygon(b):
    corners = bbox_to_corners(b)
    return corners + [corners[0]]


def geojson_to_bbox(geojson):
    return {
        "west": geojson[0][0],
        "east": geojson[2][0],
        "north": geojson[3][1],
        "south": geojson[1][1]
    }

def tiff2jpg(tiff_path, atlas_config=None, swale_config=None):
    """Convert TIFF to JPG using versioned paths"""
    # Construct JPG path
    jpg_path = tiff_path + ".jpg"
    
    
    logger.debug(f"Converting TIFF to JPG: {jpg_path}")
    subprocess.check_output(['gdal_translate', '-b', '1', '-scale',tiff_path, jpg_path])
    
    return jpg_path

def canonicalize_raster(inpath, outpath, target_srs, bbox, resample_width=None):
    """Canonicalize raster to target CRS using versioned paths"""
    
    logger.info(f"Canonicalizing raster: {inpath}")

    # <xmin> <ymin> <xmax> <ymax>
    #extent_str = f"{bbox['west']} {bbox['south']} {bbox['east']} {bbox['north']}"

    extent = [str(bbox['west']), str(bbox['south']),str(bbox['east']), str(bbox['north'])]
    
    warp_args = [ 'gdalwarp', '-t_srs', target_srs, '-te'] + extent
    if resample_width:
        warp_args += [ '-ts', str(resample_width), '0', '-r', 'bilinear']
    warp_args += [str(inpath), str(outpath)]
    logger.info(f"Warp args: {warp_args}")
    subprocess.check_output(warp_args)
    return inpath




    subprocess.check_output(['gdalwarp', '-t_srs', config['dataswale']['crs'], str(inpath), str(temp_path)])
    # Move temp file to final location
    os.rename(temp_path, inpath)  
    return inpath





def resample_raster_gdal(config, inpath, resample_width=400):
    """Resample raster to target CRS using versioned paths"""
    logger.info(f"Resampling for: [{inpath}]")
    inpath = Path(inpath)
    temp_path = inpath.parent / f"tmp.{inpath.name}"

    # Move input to temp path
    os.rename(inpath, temp_path)
    logger.debug(f"Resampling raster to {config['dataswale']['crs']} @ width {resample_width}: {inpath}")
    
    # Perform resampling
    subprocess.check_output([
        'gdalwarp', '-r', 'bilinear',
        '-ts', str(resample_width), '0',
        '-t_srs', config['dataswale']['crs'],
        str(temp_path), str(inpath)
    ])
    # TODO - remove temp path
    return inpath



def set_crs_raster(config, inpath):
    """Set CRS for raster using versioned paths"""
    inpath = Path(inpath)
    temp_path = inpath.parent / f"tmp.{inpath.name}"
    logger.debug(f"Setting raster CRS to {config['dataswale']['crs']}: {inpath}")
    subprocess.check_output(['gdalwarp', '-t_srs', config['dataswale']['crs'], str(inpath), str(temp_path)])
    # Move temp file to final location
    os.rename(temp_path, inpath)  
    return inpath



def alter_geojson(json_path, alt_conf, sample_names=True):
    """Alter GeoJSON properties"""
    logger.info(f"Altering GeoJSON in {json_path} with {alt_conf}.")
    with open(json_path, 'r') as f:
        data = json.load(f)
    
    for feature in data['features']:
        # Handle property canonicalization
        if 'canonicalize' in alt_conf:
            for canon in alt_conf['canonicalize']:
                value = None
                if canon.get('concat') is not None:
                    value = canon['concat'].join( [feature['properties'].get(src,'') for src in canon['from'] ] )
                else:
                    for src in canon['from']:
                        if src in feature['properties']:
                            value = feature['properties'][src]
                            if value:
                                if 'remove_prefix' in canon:
                                    for prefix in canon['remove_prefix']:
                                        if value.startswith(prefix):
                                            value = value[len(prefix):].strip()
                            break
                

                if canon['to'] == 'REMOVE':
                    for src in canon['from']:
                        feature['properties'].pop(src, None)
                else:        
                    if value is None:
                        if 'default' in canon:
                            feature['properties'][canon['to']] = canon['default']
                    else:
                        feature['properties'][canon['to']] = value        
        # Handle vector width
        if 'vector_width' in alt_conf:
            width_conf = alt_conf['vector_width']
            if 'attribute' in width_conf:
                attr_value = feature['properties'].get(width_conf['attribute'])
                width = width_conf['map'].get(attr_value, width_conf['default'])
                feature['properties']['vector_width'] = width
            else:
                feature['properties']['vector_width'] = width_conf['default']
    
    with open(json_path, 'w') as f:
        json.dump(data, f)
