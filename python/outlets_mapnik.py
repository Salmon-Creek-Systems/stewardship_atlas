"""
Mapnik-based outlet rendering for static printable maps.
Drop-in replacement functions for GRASS-based rendering.
"""
import mapnik
import json
import logging
import tempfile
import os
from pathlib import Path

import versioning

# Configure logging
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)
logger.setLevel(logging.INFO)


def ensure_label_attribute(layer_data, label_attr, layer_name):
    """Ensure all features have the specified label attribute.
    
    If a feature is missing the attribute, add a synthetic one like
    'layer_name_1', 'layer_name_2', etc.
    
    Args:
        layer_data (dict): GeoJSON data with features
        label_attr (str): The attribute name to check/add
        layer_name (str): Name of the layer for synthetic labels
        
    Returns:
        bool: True if modifications were made, False otherwise
    """
    modified = False
    features = layer_data.get('features', [])
    
    for idx, feature in enumerate(features, start=1):
        props = feature.get('properties', {})
        if label_attr not in props or props[label_attr] is None or props[label_attr] == '':
            # Add synthetic label
            synthetic_label = f"{layer_name}_{idx}"
            if 'properties' not in feature:
                feature['properties'] = {}
            feature['properties'][label_attr] = synthetic_label
            modified = True
            logger.debug(f"Added synthetic label '{synthetic_label}' to feature {idx}")
    
    return modified


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
    
    # Track temporary files for cleanup
    temp_files = []
    
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
        logger.info(f"Adding layer {lc['name']} from {lp}")
        
        # Check for empty layers and load data
        try:
            with open(lp) as f:
                layer_data = json.load(f)
            if len(layer_data.get('features', [])) < 1:
                logger.info(f"Layer {lc['name']} is empty, skipping...")
                continue
        except Exception as e:
            logger.warning(f"Could not read layer {lc['name']}: {e}")
            continue
        
        # If labels are requested, ensure the label attribute exists
        layer_file_to_use = lp
        if lc.get('add_labels', False):
            label_attr = lc.get('alterations', {}).get('label_attribute', 'name')
            
            # Debug: check what properties exist
            if layer_data.get('features'):
                sample_props = layer_data['features'][0].get('properties', {})
                logger.info(f"Layer {lc['name']} sample properties: {list(sample_props.keys())}")
            
            modified = ensure_label_attribute(layer_data, label_attr, lc['name'])

            
            # Always write to temp file when labels are requested to ensure Mapnik can find the attribute
            with tempfile.NamedTemporaryFile(mode='w', suffix='.geojson', delete=False) as tf:
                json.dump(layer_data, tf)
                layer_file_to_use = tf.name
                temp_files.append(tf.name)
            
            # Verify the temp file has the label attribute
            with open(layer_file_to_use, 'r') as vf:
                verify_data = json.load(vf)
                if verify_data.get('features'):
                    verify_props = verify_data['features'][0].get('properties', {})
                    if label_attr in verify_props:
                        logger.info(f"✓ Label attribute '{label_attr}' confirmed in temp file for {lc['name']}")
                    else:
                        logger.warning(f"✗ Label attribute '{label_attr}' NOT found in temp file for {lc['name']}!")
                        logger.warning(f"  Available properties: {list(verify_props.keys())}")
            

            if modified:
                logger.info(f"Added synthetic labels to layer {lc['name']}")
        
        # Create layer and datasource FIRST so Mapnik can validate field names
        layer = mapnik.Layer(lc['name'])
        layer.datasource = mapnik.GeoJSON(file=str(layer_file_to_use))
        
        # Debug: check what fields Mapnik sees in the datasource
        if lc.get('add_labels', False):
            ds_fields = layer.datasource.fields()
            logger.info(f"Mapnik datasource fields for {lc['name']}: {ds_fields}")
        
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
                label_attr = lc.get('alterations', {}).get('label_attribute', 'name')
                try:
                    text_sym = mapnik.TextSymbolizer()
                    text_sym.name = mapnik.Expression(f"[{label_attr}]")
                    text_sym.face_name = 'DejaVu Sans Book'
                    text_sym.text_size = 10
                    text_sym.fill = stroke_color
                    text_sym.halo_fill = mapnik.Color('white')
                    text_sym.halo_radius = 1
                    text_sym.allow_overlap = False
                    text_sym.avoid_edges = True
                    rule.symbols.append(text_sym)
                except RuntimeError as e:
                    logger.warning(f"Could not create text symbolizer for {lc['name']} with attribute '{label_attr}': {e}")
                    logger.warning(f"Labels will be skipped for this layer")
        
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
                try:
                    text_sym = mapnik.TextSymbolizer()
                    text_sym.name = mapnik.Expression(f"[{label_attr}]")
                    text_sym.face_name = 'DejaVu Sans Book'
                    text_sym.text_size = 12
                    text_sym.fill = stroke_color
                    text_sym.halo_fill = mapnik.Color('white')
                    text_sym.halo_radius = 2
                    text_sym.allow_overlap = False
                    text_sym.placement_type = mapnik.label_placement.line_placement if geometry_type == 'linestring' else mapnik.label_placement.point_placement
                    rule.symbols.append(text_sym)
                except RuntimeError as e:
                    logger.warning(f"Could not create text symbolizer for {lc['name']} with attribute '{label_attr}': {e}")
                    logger.warning(f"Labels will be skipped for this layer")
        
        style.rules.append(rule)
        m.append_style(style_name, style)
        
        # Attach style to layer and add to map
        layer.styles.append(style_name)
        m.layers.append(layer)
        
        logger.info(f"{region['name']} : {lc['name']} [{time.time() - t:.2f}s]")
    
    # Export map
    outpath = versioning.atlas_path(config, "outlets") / outlet_name / f"page_{region['name']}.png"
    outpath.parent.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Rendering to {outpath}...")
    mapnik.render_to_file(m, str(outpath), 'png')
    
    # Clean up temporary files
    for temp_file in temp_files:
        try:
            os.unlink(temp_file)
            logger.debug(f"Cleaned up temporary file: {temp_file}")
        except Exception as e:
            logger.warning(f"Could not remove temporary file {temp_file}: {e}")
    
    logger.info(f"Map rendered successfully [{time.time() - t:.2f}s total]")
    
    return outpath

