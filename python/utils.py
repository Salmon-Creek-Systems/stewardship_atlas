
import logging, subprocess, json, copy
from datetime import datetime, date
from pathlib import Path
import gspread, geojson


def safe_float(v) -> float:
    """Convert a value to float, returning 0.0 for zero-denominator rationals.

    Pillow returns IFDRational for EXIF values; some Android devices encode
    GPS seconds as 0/0 rather than omitting the field, causing ZeroDivisionError.
    """
    try:
        return float(v)
    except ZeroDivisionError:
        return 0.0


def json_serial(obj):
    """JSON default handler for types not natively serializable.
    Covers datetime/date (from DuckDB) and Path objects.
    Use as: json.dumps(data, default=json_serial)
         or: geojson.dump(fc, f, default=json_serial)
    """
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"Type {type(obj)} not serializable")

# Configure logging
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)
logger.setLevel(logging.INFO)

def rgb_to_css(rgb_tuple):
    if len(rgb_tuple) == 3:
        return f'rgb({rgb_tuple[0]}, {rgb_tuple[1]}, {rgb_tuple[2]})'
    elif len(rgb_tuple) == 4:
        return f'rgba({rgb_tuple[0]}, {rgb_tuple[1]}, {rgb_tuple[2]}, {rgb_tuple[3]})'
    else:
        logger.error(f"Unknown RGB tuple: {rgb_tuple}")

def canonicalize_name(s):
    return "_".join(s.lower().split()).strip()    
    
##def line_width(width_base, width_delta):
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
    horiz = [geojson[i][0] for i in (0,1,2,3)]
    vert = [geojson[i][1] for i in (0,1,2,3)]
    
    return {
        "west": min(horiz),
        "east": max(horiz),
        "north": max(vert),
        "south": min(vert)
    }


def geojson_multipolygon_to_bbox(geojson):
    """Now supports multipolygons! Maybe!"""
    
    horiz = [c[0] for c in geojson[0][0]]    
    vert = [c[1] for c in geojson[0][0]]

    return {
        "west": min(horiz),
        "east": max(horiz),
        "north": max(vert),
        "south": min(vert)
    }

def tiff2jpg(tiff_path, atlas_config=None, swale_config=None):
    """Convert TIFF to JPG using versioned paths.

    For single-band rasters (e.g. LANDFIRE), applies a percentile stretch
    (2nd–98th) after masking nodata so actual data variation is visible.
    For multi-band (RGB) rasters, falls back to gdal_translate -scale.
    """
    jpg_path = str(tiff_path) + ".jpg"
    tiff_path = str(tiff_path)
    logger.debug(f"Converting TIFF to JPG: {jpg_path}")

    try:
        from osgeo import gdal
        import numpy as np
        ds = gdal.Open(tiff_path)
        n_bands = ds.RasterCount
        if n_bands >= 3:
            # RGB raster — simple auto-scale is fine
            subprocess.check_output(['gdal_translate', '-scale', tiff_path, jpg_path])
        else:
            # Single-band data raster — percentile stretch to avoid nodata poisoning scale
            band = ds.GetRasterBand(1)
            nodata = band.GetNoDataValue()
            data = band.ReadAsArray().astype(float)
            if nodata is not None:
                data = np.where(data == nodata, np.nan, data)
            valid = data[~np.isnan(data)]
            if len(valid) == 0:
                logger.warning(f"tiff2jpg: no valid data pixels in {tiff_path}")
                return jpg_path
            lo, hi = np.percentile(valid, 2), np.percentile(valid, 98)
            if hi <= lo:
                hi = lo + 1
            logger.debug(f"tiff2jpg: scaling {tiff_path} from [{lo:.1f}, {hi:.1f}] → [0, 255]")
            scaled = np.clip((data - lo) / (hi - lo) * 255, 0, 255)
            alpha = np.where(np.isnan(scaled), 0, 255).astype(np.uint8)
            scaled = np.where(np.isnan(scaled), 0, scaled).astype(np.uint8)
            from PIL import Image
            png_path = str(tiff_path) + ".png"
            rgba = np.stack([scaled, scaled, scaled, alpha], axis=-1)
            Image.fromarray(rgba, mode='RGBA').save(png_path)
            return png_path
    except Exception as e:
        logger.warning(f"tiff2jpg Python approach failed ({e}), falling back to gdal_translate")
        subprocess.check_output(['gdal_translate', '-scale', tiff_path, jpg_path])

    return jpg_path

def canonicalize_raster(inpath, outpath, target_srs, bbox, resample_width=None,
                       gdal_config=None):
    """Canonicalize raster to target CRS using versioned paths

    gdal_config is an optional {name: value} of GDAL config options, passed as
    --config pairs. A remote source read through /vsicurl/ wants
    GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR, without which GDAL lists the whole
    containing prefix before reading a byte. inpath may be any path GDAL
    accepts, including /vsicurl/ and /vsis3/.
    """
    
    logger.info(f"Canonicalizing raster: {inpath}")

    # <xmin> <ymin> <xmax> <ymax>
    #extent_str = f"{bbox['west']} {bbox['south']} {bbox['east']} {bbox['north']}"

    extent = [str(bbox['west']), str(bbox['south']),str(bbox['east']), str(bbox['north'])]
    
    warp_args = ['gdalwarp']
    for key, value in (gdal_config or {}).items():
        warp_args += ['--config', key, str(value)]
    warp_args += ['-t_srs', target_srs, '-te'] + extent
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



def alter_features(features, alt_conf):
    """Apply an `alterations` block (canonicalize, vector_width, filter) to a
    list of features in memory. Mutates properties in place and returns the
    features the filter keeps. File-based inlets go through alter_geojson;
    inlets that hold features in memory (s3_geojson) call this directly."""
    # Handle feature filtering - collect features to keep
    filtered_features = []
    
    for feature in features:
        # Handle property canonicalization
        if 'canonicalize' in alt_conf:
            for canon in alt_conf['canonicalize']:
                value = None
                if canon.get('concat') is not None:
                    
                    value = canon['concat'].join( [x if x is not None else "NA" for x in [feature['properties'].get(src,'') for src in canon['from'] ]] )
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
        
        # Handle feature filtering
        if 'filter' in alt_conf:
            keep_feature = True
            
            for filter_rule in alt_conf['filter']:
                operation, field_name, value_list = filter_rule
                field_value = feature['properties'].get(field_name)
                
                if operation == 'require':
                    # Keep feature only if field_value is in value_list
                    if field_value not in value_list:
                        keep_feature = False
                        break
                elif operation == 'remove':
                    # Remove feature if field_value is in value_list
                    if field_value in value_list:
                        keep_feature = False
                        break
                elif operation == 'endswith':
                    # Keep feature only if field_value ends with any value in value_list
                    if field_value is None or not any(str(field_value).endswith(v) for v in value_list):
                        keep_feature = False
                        break
            
            if keep_feature:
                filtered_features.append(feature)
        else:
            # No filtering, keep all features
            filtered_features.append(feature)
    
    return filtered_features


def alter_geojson(json_path, alt_conf, sample_names=True):
    """Alter GeoJSON properties"""
    logger.info(f"Altering GeoJSON in {json_path} with {alt_conf}.")
    with open(json_path, 'r') as f:
        data = json.load(f)
    data['features'] = alter_features(data['features'], alt_conf)
    with open(json_path, 'w') as f:
        json.dump(data, f)


_LAYER_REFERENCE_FIELDS = {
    'in_layer': 'str',
    'in_layers': 'list',
    'out_layer': 'str',
    'layers': 'list',
    'regions_layer': 'str',
}

def _detect_indent(path):
    """Return indent value for json.dump matching the file's existing indentation."""
    import re
    with open(path) as f:
        content = f.read()
    m = re.search(r'\n(\s+)\S', content)
    if not m:
        return 4
    indent_str = m.group(1)
    return '\t' if '\t' in indent_str else len(indent_str)


def rename_layer_key(layers_json_path, old_name, new_name, dry_run=False):
    """Update layer name in {atlas}_layers.json (a list of layer dicts with a 'name' field)."""
    path = Path(layers_json_path)
    layers = json.load(open(path))
    changed = False
    for layer in layers:
        if isinstance(layer, dict) and layer.get('name') == old_name:
            if dry_run:
                print(f"  [dry_run] {path.name}: layer name '{old_name}' → '{new_name}'")
            else:
                layer['name'] = new_name
                print(f"  {path.name}: layer name '{old_name}' → '{new_name}'")
            changed = True
    if not changed:
        print(f"  WARNING: layer '{old_name}' not found in {path.name}")
    if not dry_run and changed:
        with open(path, 'w') as f:
            json.dump(layers, f, indent=_detect_indent(path))
    return changed


def replace_layer_references(config_paths, old_name, new_name, dry_run=False):
    """Scan JSON asset config files for internal layer references and replace old_name.

    Checks fields: in_layer, in_layers, out_layer, layers, regions_layer.
    Skips external-source fields: wms_layer, landfire_layer, source_sublayer.

    Note: shared_*.json changes affect all atlases using those templates — review the
    diff carefully when shared configs are touched.
    """
    total = 0
    for path in config_paths:
        path = Path(path)
        if not path.exists():
            print(f"  {path.name}: not found, skipping")
            continue
        data = json.load(open(path))
        if not isinstance(data, dict):
            print(f"  {path.name}: skipping (not a dict)")
            continue
        changes = 0
        for asset_name, asset in data.items():
            if not isinstance(asset, dict):
                continue
            for field, kind in _LAYER_REFERENCE_FIELDS.items():
                if field not in asset:
                    continue
                val = asset[field]
                if kind == 'str' and val == old_name:
                    if dry_run:
                        print(f"  [dry_run] {path.name}: {asset_name}.{field} '{old_name}' → '{new_name}'")
                    else:
                        asset[field] = new_name
                        print(f"  {path.name}: {asset_name}.{field} '{old_name}' → '{new_name}'")
                    changes += 1
                elif kind == 'list' and isinstance(val, list) and old_name in val:
                    if dry_run:
                        print(f"  [dry_run] {path.name}: {asset_name}.{field}[] '{old_name}' → '{new_name}'")
                    else:
                        asset[field] = [new_name if v == old_name else v for v in val]
                        print(f"  {path.name}: {asset_name}.{field}[] '{old_name}' → '{new_name}'")
                    changes += 1
        if changes and not dry_run:
            with open(path, 'w') as f:
                json.dump(data, f, indent=_detect_indent(path))
        qualifier = 'would be ' if dry_run else ''
        print(f"  {path.name}: {changes} reference(s) {qualifier}updated")
        total += changes
    return total


def read_gsheet(config, sheet_name=None):
    """Read a sigle-worksheet Google Sheet into a list of dictionaries"""
    logger.info(f"Reading Google Sheet: {sheet_name}")
    if not sheet_name:
        raise ValueError("sheet_name is required")
    gc = gspread.service_account()
    wks = gc.open(sheet_name)# .get_worksheet(sheet_name)
    return wks.get_worksheet(0).get_all_records()


def deduplicate_json(json_list, key_fields=None):
    """
    Deduplicate a list of dictionaries.
    
    Args:
        json_list: List of dictionaries to deduplicate
        key_fields: Optional list of field names to use for comparison.
                   If None, entire objects are compared.
        
    Returns:
        List of unique dictionaries (keeps first occurrence)
    
    Examples:
        >>> data = [{"a": 1, "b": 2}, {"a": 1, "b": 2}, {"a": 3, "b": 4}]
        >>> deduplicate_json(data)
        [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
        
        >>> data = [{"id": 1, "name": "A"}, {"id": 1, "name": "B"}, {"id": 2, "name": "C"}]
        >>> deduplicate_json(data, ["id"])
        [{"id": 1, "name": "A"}, {"id": 2, "name": "C"}]
    """
    if not key_fields:
        # Deduplicate by entire object
        seen = set()
        result = []
        for item in json_list:
            key = json.dumps(item, sort_keys=True)
            if key not in seen:
                seen.add(key)
                result.append(item)
        return result
    
    # Deduplicate by specific fields
    seen = set()
    result = []
    for item in json_list:
        key = tuple(item.get(field) for field in key_fields)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result

def json_leaf(root, path):
    #print(f"{path} <--  {list(root.keys())[:3]}")
    this_key = path.pop(0)
    #print(f"{this_key} in {list(root.keys())[:3]}")
    if this_key not in root:
        #print("Done!")
        return None
    if path:
        #print(f"recursing with {path}")
        return json_leaf(root[this_key], path)
    else:
        #print(f"found it at {this_key}")
        return root[this_key]
    
    #print("Huh")
    return None

#import traceback

def extract_field_across_layers(field_spec_original):
    
    for l in c['dataswale']['layers']:
        
        layer_name = l['name']
        #stdout = f"{layer"}
        res = {}
        try:
            field_spec = copy.deepcopy(field_spec_original)
            #print(f"Field spec: {field_spec}")
            f = dataswale_geojson.layer_as_featurecollection(c, layer_name)['features'][0]
            # field_spec = field_spec_original.copy()
            for k in field_spec:
                orig_k = copy.deepcopy(k)
                if (v := json_leaf(f, k)) is not None:
                    
                    res[str(orig_k)] = v
            print(f"{layer_name}: {res}")
            #print(f"{layer_name}| root id: {f.get('id','')}  props - cat: {f['properties'].get('cat','')}" \
            #      + f"id: {f['properties'].get('id','')} id: {f['properties'].get('ogc_fid','')}" )
                #print(dataswale_geojson.layer_as_featurecollection(c, layer_name)['features'][0].get('fid','NA'))
        except Exception as e:
            print(f"Cannot read layer {layer_name}")
            #traceback.print_exc()


def hex_to_rgb(h):
    """'#rrggbb' (or 'rrggbb') -> (r, g, b) ints."""
    h = h.lstrip('#')
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def build_qgis_color_expression(value_expression, stops):
    """Build a QGIS expression that linearly interpolates a color across stops.

    Mirrors a MapLibre `["interpolate", ["linear"], <value>, v0, c0, v1, c1, ...]`
    paint so QGIS output (PDFs) can match the webmap gradient. Per-channel linear
    interpolation via scale_linear, bucketed by a CASE so each segment stays in its
    own domain; below the first stop and above the last clamp to the end colors.

    Args:
        value_expression: a QGIS expression string yielding the numeric value to map
            (e.g. 'min(coalesce("m_start", 14000), coalesce("m_end", 14000))').
        stops: list of [value, '#rrggbb'] pairs (any order; sorted here ascending).

    Returns:
        A QGIS expression string returning a color (usable as a data-defined
        stroke/fill color property).
    """
    stops = sorted(stops, key=lambda s: s[0])
    if not stops:
        raise ValueError("build_qgis_color_expression: stops is empty")

    v = f"({value_expression})"
    first_r, first_g, first_b = hex_to_rgb(stops[0][1])
    last_r, last_g, last_b = hex_to_rgb(stops[-1][1])

    clauses = [f"WHEN {v} <= {stops[0][0]} THEN color_rgb({first_r}, {first_g}, {first_b})"]
    for (v0, h0), (v1, h1) in zip(stops, stops[1:]):
        r0, g0, b0 = hex_to_rgb(h0)
        r1, g1, b1 = hex_to_rgb(h1)
        clauses.append(
            f"WHEN {v} <= {v1} THEN color_rgb("
            f"to_int(scale_linear({v}, {v0}, {v1}, {r0}, {r1})), "
            f"to_int(scale_linear({v}, {v0}, {v1}, {g0}, {g1})), "
            f"to_int(scale_linear({v}, {v0}, {v1}, {b0}, {b1})))"
        )

    body = "\n  ".join(clauses)
    return (
        f"CASE\n  {body}\n"
        f"  ELSE color_rgb({last_r}, {last_g}, {last_b})\nEND"
    )

def geojson_bbox(coordinates):
    """(min_x, min_y, max_x, max_y) over any nesting of Polygon/MultiPolygon rings."""
    xs, ys = [], []

    def walk(item):
        if (isinstance(item, (list, tuple)) and len(item) >= 2
                and all(isinstance(v, (int, float)) for v in item[:2])):
            xs.append(item[0])
            ys.append(item[1])
        elif isinstance(item, (list, tuple)):
            for sub in item:
                walk(sub)

    walk(coordinates)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def square_from_bbox(bbox):
    """A closed square ring covering bbox, sized by its longer side, concentric.

    The square is in degrees, which is NOT the same as square on the page.
    outlets_qgis switches to EPSG:3857 whenever the layer CRS is geographic, so
    a degree-square reaches the page about 1.30x taller than wide at 40N. The
    renderer then expands the extent to fit the frame, so nothing is distorted,
    but each page covers appreciably more ground east-west than the region
    describes.

    This is how westport's existing regions were made and reproduces them
    exactly, so it is the right transform for restoring them. It is probably
    the wrong one going forward: a region square in 3857 needs its latitude
    span scaled by cos(lat). Unchanged here because every stored region moves.
    """
    min_x, min_y, max_x, max_y = bbox
    half = max(max_x - min_x, max_y - min_y) / 2
    cx, cy = (min_x + max_x) / 2, (min_y + max_y) / 2
    return [[cx - half, cy - half], [cx + half, cy - half], [cx + half, cy + half],
            [cx - half, cy + half], [cx - half, cy - half]]


def page_square_from_bbox(bbox):
    """A closed ring covering bbox that is square ON THE PRINTED PAGE.

    outlets_qgis renders in EPSG:3857, where a degree of latitude occupies
    1/cos(lat) times the space of a degree of longitude — so the square a
    runbook page wants is wider in degrees than it is tall: dlat = dlon *
    cos(lat). At 40N that is about 0.77.

    Contrast square_from_bbox, which is square in degrees and reaches the page
    ~1.30x taller than wide.

    NOT the shape a runbook region wants. A region fills its page when
    Δlon/Δlat = frame_aspect / cos(lat), and A4 portrait with the standard
    collar has frame_aspect 0.7687 against cos(39.72N) = 0.7690 — so at these
    latitudes a DEGREE square fills the page and this one is ~30% too wide.
    Use this for a square frame; #189 covers reading the real page aspect.
    """
    import math

    min_x, min_y, max_x, max_y = bbox
    cx, cy = (min_x + max_x) / 2, (min_y + max_y) / 2
    scale = math.cos(math.radians(cy)) or 1.0      # 1.0 guards the poles
    # Compare the sides in page units, then grow the smaller one to match.
    side = max(max_x - min_x, (max_y - min_y) / scale)
    half_x, half_y = side / 2, side * scale / 2
    return [[cx - half_x, cy - half_y], [cx + half_x, cy - half_y],
            [cx + half_x, cy + half_y], [cx - half_x, cy + half_y],
            [cx - half_x, cy - half_y]]


# What a layer's `polygon_shape` can be. 'raw' keeps the drawn geometry;
# 'bbox' replaces it with its bounding rectangle; 'square_degrees' makes a
# square in degrees — what the older inlet-level `squarify` does, and the shape
# that fills a runbook page at ~40N on A4 with a collar; 'square' makes a square
# on paper, which is right for a square frame and ~30% too wide for that page.
# See #189: the general form is Δlon/Δlat = frame_aspect / cos(lat).
POLYGON_SHAPES = ('raw', 'bbox', 'square', 'square_degrees')


def bbox_feature(feature):
    """Replace a polygon's geometry with its bounding rectangle."""
    out = copy.deepcopy(feature)
    geometry = out.get('geometry') or {}
    if geometry.get('type') not in ('Polygon', 'MultiPolygon'):
        return out
    bbox = geojson_bbox(geometry.get('coordinates', []))
    if bbox is None:
        return out
    min_x, min_y, max_x, max_y = bbox
    ring = [[min_x, min_y], [max_x, min_y], [max_x, max_y], [min_x, max_y], [min_x, min_y]]
    out['geometry'] = {'type': 'Polygon', 'coordinates': [ring]}
    return out


def page_squarify_feature(feature):
    """Replace a polygon's geometry with the square that prints square."""
    out = copy.deepcopy(feature)
    geometry = out.get('geometry') or {}
    if geometry.get('type') not in ('Polygon', 'MultiPolygon'):
        return out
    bbox = geojson_bbox(geometry.get('coordinates', []))
    if bbox is None:
        return out
    out['geometry'] = {'type': 'Polygon', 'coordinates': [page_square_from_bbox(bbox)]}
    return out


def shape_feature(feature, polygon_shape):
    """Apply a layer's `polygon_shape` to one incoming feature.

    Called where features land in a layer, so an imported, uploaded and
    hand-drawn polygon all end up the same shape. Unknown modes and 'raw' pass
    through untouched.
    """
    if polygon_shape == 'bbox':
        return bbox_feature(feature)
    if polygon_shape == 'square':
        return page_squarify_feature(feature)
    if polygon_shape == 'square_degrees':
        return squarify_feature(feature)
    return feature


def squarify_feature(feature):
    """Replace a polygon's geometry with the square that covers it.

    Regions become runbook pages, and a page is a fixed shape — so a region is
    stored already squared rather than squared at render time, which keeps what
    is drawn on the webmap identical to what gets printed.

    See square_from_bbox on why "square" here means square in degrees, and why
    that is not square on the printed page.

    Non-polygons and unreadable geometries pass through untouched.
    """
    import copy

    out = copy.deepcopy(feature)
    geometry = out.get('geometry') or {}
    if geometry.get('type') not in ('Polygon', 'MultiPolygon'):
        return out

    bbox = geojson_bbox(geometry.get('coordinates', []))
    if bbox is None:
        return out

    min_x, min_y, max_x, max_y = bbox
    width, height = max_x - min_x, max_y - min_y
    out['geometry'] = {'type': 'Polygon', 'coordinates': [square_from_bbox(bbox)]}
    out.setdefault('properties', {}).update({
        '_squarified': True,
        '_original_width': width,
        '_original_height': height,
        '_square_size': max(width, height),
    })
    return out

