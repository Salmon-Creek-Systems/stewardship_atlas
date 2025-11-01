"""
Mapnik-based outlet rendering for static printable maps.
Drop-in replacement functions for GRASS-based rendering.
"""
import mapnik
import json
import logging
from pathlib import Path

import versioning

# Configure logging
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)
logger.setLevel(logging.INFO)


def build_region_map_mapnik(config, outlet_name, region):
    """Build a map image for a region using Mapnik.
    
    Drop-in replacement for build_region_map_grass with core features:
    - Raster basemap with blending
    - Vector layers (points, lines, polygons)
    - Colors and styling from layer config
    - Labels
    - Per-feature vector widths
    
    Args:
        config (dict): The configuration dictionary for the atlas.
        outlet_name (str): The name of the outlet.
        region (dict): The region to build the map for, containing:
            - name: region identifier
            - bbox: {north, south, east, west}
            - vectors: list of (layer_config, layer_path) tuples
            - raster: list with raster paths (if present)
    
    Returns:
        Path to the generated PNG file
    """
    import time
    t = time.time()
    
    # Determine map size based on region
    if region['name'] == 'all':
        size = 9000
    else:
        size = 2400
    
    # Create map
    m = mapnik.Map(size, size)
    m.background = mapnik.Color('white')
    
    # Get bbox and set map extent
    clip_bbox = region['bbox']
    bbox = mapnik.Box2d(
        clip_bbox['west'], 
        clip_bbox['south'], 
        clip_bbox['east'], 
        clip_bbox['north']
    )
    m.zoom_to_box(bbox)
    
    logger.info(f"Building Mapnik map for region {region['name']} at {size}x{size}px")
    
    # Handle raster basemap with blending if present
    if len(region.get('raster', [])) > 0:
        blend_percent = config['assets'][outlet_name].get('blend_percent', 10)
        raster_path = region['raster'][1]  # Index 1 like GRASS version
        
        logger.info(f"Adding raster basemap: {raster_path} with blend {blend_percent}%")
        
        # Create raster style
        raster_style = mapnik.Style()
        raster_rule = mapnik.Rule()
        raster_symbolizer = mapnik.RasterSymbolizer()
        
        # Apply blending by adjusting opacity
        # GRASS blend_percent of 10 means 10% grey, 90% original
        # So we want high opacity for low blend_percent
        opacity = 1.0 - (blend_percent / 100.0)
        raster_symbolizer.opacity = opacity
        
        # Apply greyscale effect to simulate GRASS r.colors grey
        raster_symbolizer.colorizer = mapnik.RasterColorizer(
            mapnik.COLORIZER_DISCRETE,
            mapnik.Color('transparent')
        )
        
        raster_rule.symbols.append(raster_symbolizer)
        raster_style.rules.append(raster_rule)
        m.append_style('RasterStyle', raster_style)
        
        # Add raster layer
        raster_layer = mapnik.Layer('basemap')
        raster_layer.datasource = mapnik.Gdal(file=str(raster_path), band=1)
        raster_layer.styles.append('RasterStyle')
        m.layers.append(raster_layer)
        
        logger.info(f"Raster layer added [{time.time() - t:.2f}s]")
    
    # Add vector layers
    for lc, lp in region['vectors']:
        logger.debug(f"Adding layer {lc['name']} from {lp}")
        
        # Check for empty layers
        try:
            with open(lp) as f:
                layer_data = json.load(f)
            if len(layer_data.get('features', [])) < 1:
                logger.info(f"Layer {lc['name']} is empty, skipping...")
                continue
        except Exception as e:
            logger.warning(f"Could not read layer {lc['name']}: {e}")
            continue
        
        # Get colors
        color = lc.get('color', (100, 100, 100))
        fill_color = lc.get('fill_color', color)
        
        # Convert color tuples to Mapnik Color objects
        if isinstance(color, (list, tuple)) and len(color) >= 3:
            stroke_color = mapnik.Color(color[0], color[1], color[2])
        else:
            stroke_color = mapnik.Color('grey')
        
        if fill_color == 'none':
            fill_mapnik = mapnik.Color(0, 0, 0, 0)  # Transparent
        elif isinstance(fill_color, (list, tuple)) and len(fill_color) >= 3:
            fill_mapnik = mapnik.Color(fill_color[0], fill_color[1], fill_color[2])
        else:
            fill_mapnik = stroke_color
        
        # Create style based on geometry type
        style_name = f"Style_{lc['name']}"
        style = mapnik.Style()
        rule = mapnik.Rule()
        
        geometry_type = lc.get('geometry_type', 'linestring')
        
        if geometry_type == 'point':
            # Point symbolizer
            point_sym = mapnik.MarkersSymbolizer()
            point_sym.fill = stroke_color
            point_sym.stroke = stroke_color
            point_sym.width = mapnik.Expression('10')
            point_sym.height = mapnik.Expression('10')
            point_sym.allow_overlap = True
            rule.symbols.append(point_sym)
            
            # Add labels if requested
            if lc.get('add_labels', False):
                text_sym = mapnik.TextSymbolizer(
                    mapnik.Expression(f"[{lc.get('alterations', {}).get('label_attribute', 'name')}]"),
                    'DejaVu Sans Book',
                    10,
                    stroke_color
                )
                text_sym.halo_fill = mapnik.Color('white')
                text_sym.halo_radius = 1
                text_sym.allow_overlap = False
                text_sym.avoid_edges = True
                rule.symbols.append(text_sym)
        
        else:
            # Line or polygon symbolizer
            line_sym = mapnik.LineSymbolizer()
            line_sym.stroke = stroke_color
            
            # Handle vector_width from feature properties
            if 'vector_width' in lc:
                line_sym.stroke_width = mapnik.Expression('[vector_width]')
            else:
                # Use constant width
                width = lc.get('constant_width', 2)
                line_sym.stroke_width = mapnik.Expression(str(width))
            
            rule.symbols.append(line_sym)
            
            # Add polygon fill if it's a polygon
            if geometry_type == 'polygon':
                poly_sym = mapnik.PolygonSymbolizer()
                poly_sym.fill = fill_mapnik
                rule.symbols.append(poly_sym)
            
            # Add labels if requested
            if lc.get('add_labels', False):
                label_attr = lc.get('alterations', {}).get('label_attribute', 'name')
                text_sym = mapnik.TextSymbolizer(
                    mapnik.Expression(f"[{label_attr}]"),
                    'DejaVu Sans Book',
                    12,
                    stroke_color
                )
                text_sym.halo_fill = mapnik.Color('white')
                text_sym.halo_radius = 2
                text_sym.allow_overlap = False
                text_sym.placement = mapnik.line_placement if geometry_type == 'linestring' else mapnik.point_placement
                rule.symbols.append(text_sym)
        
        style.rules.append(rule)
        m.append_style(style_name, style)
        
        # Create layer
        layer = mapnik.Layer(lc['name'])
        layer.datasource = mapnik.GeoJSON(file=str(lp))
        layer.styles.append(style_name)
        m.layers.append(layer)
        
        logger.info(f"{region['name']} : {lc['name']} [{time.time() - t:.2f}s]")
    
    # Export map
    outpath = versioning.atlas_path(config, "outlets") / outlet_name / f"page_{region['name']}.png"
    outpath.parent.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Rendering to {outpath}...")
    mapnik.render_to_file(m, str(outpath), 'png')
    
    logger.info(f"Map rendered successfully [{time.time() - t:.2f}s total]")
    
    return outpath

