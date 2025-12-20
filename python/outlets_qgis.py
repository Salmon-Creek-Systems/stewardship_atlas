#!/usr/bin/env python3
"""
QGIS-based outlets for region map generation.

This module provides an alternative to GRASS-based region processing using QGIS API.
Key advantages:
- Direct rendering from full datasets (no intermediate file extraction)
- Faster processing via spatial indexing
- GeoPDF output with layer structure retained
- Simpler code with fewer dependencies
"""

import os
import sys
import json
import time
import logging
import tempfile
from pathlib import Path
from datetime import datetime

# Set Qt to use offscreen platform for headless operation
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from qgis.core import (
    QgsApplication,
    QgsVectorLayer,
    QgsRasterLayer,
    QgsProject,
    QgsLayoutExporter,
    QgsLayoutItemMap,
    QgsLayoutItemLegend,
    QgsLayoutItemScaleBar,
    QgsLayoutItemLabel,
    QgsLayoutItemShape,
    QgsLayoutItemPage,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsLayoutMeasurement,
    QgsPrintLayout,
    QgsScaleBarSettings,
    QgsUnitTypes,
    QgsRectangle,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsSymbol,
    QgsSymbolLayer,
    QgsRendererCategory,
    QgsCategorizedSymbolRenderer,
    QgsSingleSymbolRenderer,
    QgsSimpleLineSymbolLayer,
    QgsSimpleFillSymbolLayer,
    QgsSimpleMarkerSymbolLayer,
    QgsTextFormat,
    QgsVectorLayerSimpleLabeling,
    QgsPalLayerSettings,
    QgsProperty,
    QgsLayerTreeLayer,
    QgsLabeling,
    QgsGeometry,
    QgsPointXY
)
from qgis.PyQt.QtGui import QColor, QFont
from qgis.PyQt.QtCore import QSizeF

# Import local modules
try:
    from . import versioning
    from . import utils
except ImportError:
    import versioning
    import utils

#logger = logging.getLogger(__name__)

# Configure logging
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)
logger.setLevel(logging.INFO)


# Global QGIS application instance
_qgs_app = None


def qgis_init():
    """Initialize QGIS application for headless operation."""
    global _qgs_app
    if _qgs_app is None:
        _qgs_app = QgsApplication([], False)
        _qgs_app.initQgis()
        logger.info("QGIS application initialized")
    return _qgs_app


def load_regions_from_geojson(geojson_path, first_n=0):
    """
    Load regions directly from a GeoJSON file.
    
    Args:
        geojson_path: Path to regions GeoJSON file
        first_n: Only load first N regions (0 = all)
        
    Returns:
        List of region dicts with bbox, name, caption, etc.
    """
    geojson_path = Path(geojson_path)
    if not geojson_path.exists():
        logger.error(f"Regions GeoJSON not found: {geojson_path}")
        return []
    
    regions = []
    
    try:
        with open(geojson_path, 'r') as f:
            geojson_data = json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse regions GeoJSON: {e}")
        return []
    
    features = geojson_data.get('features', [])
    logger.info(f"Found {len(features)} feature(s) in regions GeoJSON")
    
    for i, feature in enumerate(features):
        # Apply first_n limit
        if first_n > 0 and i >= first_n:
            logger.info(f"Reached limit of {first_n} regions, stopping")
            break
        
        try:
            # Extract bbox from geometry coordinates
            coords = feature['geometry']['coordinates'][0]
            bbox = utils.geojson_to_bbox(coords)
            
            # Get properties
            props = feature.get('properties', {})
            default_name = f"Region_{i}"
            name = props.get('name', props.get('Description', default_name))
            caption = props.get('caption', props.get('Description', default_name))
            
            region = {
                'name': utils.canonicalize_name(name),
                'caption': caption,
                'text': props.get('text', caption),
                'bbox': bbox,
                'neighbors': props.get('neighbors'),
                'vectors': [],
                'raster': '',
                'properties': props  # Keep all properties for reference
            }
            
            regions.append(region)
            logger.debug(f"Loaded region {i}: {region['name']}")
            
        except (KeyError, IndexError, TypeError) as e:
            logger.warning(f"Skipping malformed region feature {i}: {e}")
            continue
    
    # Set up neighbor links if not already present
    for i, r in enumerate(regions):
        if r['neighbors'] is None:
            next_idx = (i + 1) % len(regions)
            prev_idx = (i - 1) % len(regions)
            r['neighbors'] = {
                "prev": regions[prev_idx]['name'],
                "next": regions[next_idx]['name']
            }
    
    logger.info(f"Loaded {len(regions)} region(s) from GeoJSON")
    return regions


def qgis_cleanup():
    """Cleanup QGIS application (note: may cause segfaults in offscreen mode)."""
    global _qgs_app
    if _qgs_app is not None:
        # Note: Skipping exitQgis() to avoid segfault in offscreen mode
        # The process will clean up on exit anyway
        logger.info("QGIS cleanup requested (skipping exitQgis)")


def clean_grass_geojson(geojson_path, temp_path):
    """
    Remove GRASS-generated fields that cause GDAL/QGIS issues.
    
    GRASS's v.out.ogr adds 'id' at feature level and 'cat' in properties,
    which can cause "Wrong field type for fid" errors in QGIS.
    
    Args:
        geojson_path: Path to original GeoJSON file
        temp_path: Path to write cleaned GeoJSON
        
    Returns:
        True if cleaning was successful, False otherwise
    """
    try:
        with open(geojson_path, 'r') as f:
            data = json.load(f)
        
        # Clean each feature
        cleaned_features = []
        for feature in data.get('features', []):
            # Remove 'id' at feature level (GRASS adds this)
            if 'id' in feature:
                del feature['id']
            
            # Remove GRASS-specific fields from properties
            props = feature.get('properties', {})
            grass_fields = ['cat', 'fid', 'ogc_fid', 'gml_id']
            for field in grass_fields:
                if field in props:
                    del props[field]
            
            cleaned_features.append(feature)
        
        # Write cleaned version
        data['features'] = cleaned_features
        with open(temp_path, 'w') as f:
            json.dump(data, f)
        
        logger.debug(f"Cleaned {len(cleaned_features)} features, removed GRASS metadata")
        return True
        
    except Exception as e:
        logger.error(f"Failed to clean GeoJSON: {e}")
        return False


def load_full_layer(layer_config, config):
    """
    Load a full layer (vector or raster) from staging area.
    
    Args:
        layer_config: Layer configuration dict with 'name' and 'geometry_type'
        config: Atlas configuration dict
        
    Returns:
        QgsVectorLayer or QgsRasterLayer, or None if loading fails
    """
    layer_name = layer_config['name']
    geometry_type = layer_config.get('geometry_type', 'polygon')
    
    # Determine layer format and path
    if geometry_type == 'raster':
        layer_format = 'tiff'
        layer_path = versioning.atlas_path(config, "layers") / layer_name / f"{layer_name}.{layer_format}"
        
        layer = QgsRasterLayer(str(layer_path), layer_name)
        if not layer.isValid():
            logger.warning(f"Failed to load raster layer: {layer_name} from {layer_path}")
            return None
        logger.info(f"Loaded raster layer: {layer_name}")
        return layer
    else:
        layer_format = 'geojson'
        layer_path = versioning.atlas_path(config, "layers") / layer_name / f"{layer_name}.{layer_format}"
        
        # Create cleaned temporary GeoJSON (remove GRASS metadata that confuses GDAL)
        temp_dir = Path(tempfile.gettempdir()) / "stewardship_atlas_qgis"
        temp_dir.mkdir(exist_ok=True)
        temp_path = temp_dir / f"{layer_name}_clean.geojson"
        
        if not clean_grass_geojson(layer_path, temp_path):
            logger.warning(f"Failed to clean GeoJSON for {layer_name}, trying original")
            load_path = layer_path
        else:
            load_path = temp_path
            logger.debug(f"Using cleaned GeoJSON: {temp_path}")
        
        # Load the cleaned GeoJSON
        options = QgsVectorLayer.LayerOptions()
        options.loadDefaultStyle = False
        
        logger.debug(f"Loading {layer_name} from: {load_path}")
        layer = QgsVectorLayer(str(load_path), layer_name, "ogr", options)
            
        if not layer.isValid():
            logger.warning(f"Failed to load vector layer: {layer_name}")
            logger.warning(f"Layer error: {layer.error().message()}")
            return None
        
        # Check if layer has a valid CRS
        if not layer.crs().isValid():
            logger.warning(f"Layer {layer_name} has invalid CRS, setting to WGS84")
            layer.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
        
        logger.info(f"Loaded vector layer: {layer_name} ({layer.featureCount()} features, CRS: {layer.crs().authid()})")
        return layer


def apply_basic_styling(layer, layer_config):
    """
    Apply basic styling to a QGIS layer based on configuration.
    
    Args:
        layer: QgsVectorLayer or QgsRasterLayer
        layer_config: Layer configuration dict with color, width, labels, etc.
    """
    if isinstance(layer, QgsRasterLayer):
        # Raster styling - just set opacity if configured
        opacity = layer_config.get('opacity', 1.0)
        layer.setOpacity(opacity)
        return
    
    # Vector styling
    geometry_type = layer_config.get('geometry_type', 'linestring')
    color = layer_config.get('color', [100, 100, 100])
    fill_color = layer_config.get('fill_color', color)
    
    # Create QColor objects
    qcolor = QColor(color[0], color[1], color[2])
    qfill_color = QColor(fill_color[0], fill_color[1], fill_color[2]) if fill_color != 'none' else None
    
    # Create symbol based on geometry type
    if geometry_type == 'point':
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        symbol.setColor(qcolor)
        symbol.setSize(3)  # Basic point size
        
    elif geometry_type == 'linestring':
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        symbol.setColor(qcolor)
        
        # Check if width should come from per-feature attribute
        # If 'vector_width' key exists in config (any value), use feature's vector_width attribute
        if 'vector_width' in layer_config:
            # Check if the layer actually has a vector_width field
            fields = layer.fields()
            if fields.indexOf('vector_width') >= 0:
                # Use data-defined width from each feature's vector_width attribute
                width = layer_config.get('constant_width', 2)
                symbol.setWidth(width )  # Default/fallback width
                logger.info(f"set constant width: {width}")
                
                # Set data-defined property to read from feature attribute
                symbol_layer = symbol.symbolLayer(0)
                if symbol_layer:
                    logger.info(f"using feature vector_width")
                    # Width from 'vector_width' attribute in feature properties, scaled to mm
                    symbol_layer.setDataDefinedProperty(
                        QgsSymbolLayer.PropertyStrokeWidth,
                        QgsProperty.fromExpression('"vector_width"')
                    )
                    logger.debug(f"Using per-feature vector_width attribute for {layer.name()}")
            else:
                logger.warning(f"Layer {layer.name()} config has 'vector_width' but features don't have that attribute")
                # Fall back to constant width
                width = layer_config.get('constant_width', 2)
                symbol.setWidth(width * 0.1)
        else:
            # Use constant width from config
            width = layer_config.get('constant_width', 2)
            symbol.setWidth(width * 0.1)  # Scale to mm
        
    elif geometry_type == 'polygon':
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        symbol.setColor(qcolor)
        if qfill_color:
            # Set fill opacity
            fill_opacity = layer_config.get('fill_opacity', 0.5)
            qfill_color.setAlphaF(fill_opacity)
        else:
            # No fill
            qfill_color = QColor(0, 0, 0, 0)
        
        # Get the symbol layer and set fill color
        symbol_layer = symbol.symbolLayer(0)
        if isinstance(symbol_layer, QgsSimpleFillSymbolLayer):
            symbol_layer.setFillColor(qfill_color)
            symbol_layer.setStrokeColor(qcolor)
            symbol_layer.setStrokeWidth(0.3)
    
    # Apply symbol renderer
    renderer = QgsSingleSymbolRenderer(symbol)
    layer.setRenderer(renderer)
    
    # Add labels if configured
    if layer_config.get('add_labels', False):
        label_attr = layer_config.get('alterations', {}).get('label_attribute', 'name')
        
        # Check if attribute exists
        fields = layer.fields()
        if fields.indexOf(label_attr) >= 0:
            pal_settings = QgsPalLayerSettings()
            pal_settings.fieldName = label_attr
            pal_settings.enabled = True
            
            # Force display of all labels (don't hide on collision)
            pal_settings.displayAll = True
            
            # Disable obstacle avoidance - show labels even if they overlap features
            pal_settings.obstacleSettings().setIsObstacle(False)
            
            # Text format - MUST be set before placement settings
            text_format = QgsTextFormat()
            
            # For linestrings, use larger white labels; otherwise use layer color
            if geometry_type == 'linestring':
                text_format.setSize(14)  # Larger size for linestrings
                text_format.setColor(QColor(255, 255, 255))  # White labels for linestrings
                font = QFont()
                font.setPointSize(14)
                font.setBold(True)  # Make labels bold for better visibility
            else:
                text_format.setSize(10)
                text_format.setColor(qcolor)
                font = QFont()
                font.setPointSize(10)
            
            text_format.setFont(font)
            pal_settings.setFormat(text_format)
            
            # For linestrings, enable placement along the line
            if geometry_type == 'linestring':
                # Line placement with proper orientation flags
                pal_settings.placement = QgsPalLayerSettings.Line
                
                # Set placement flags to follow line orientation
                # OnLine = place on the line, MapOrientation = follow the line's angle
                flags = QgsLabeling.LinePlacementFlags()
                flags |= QgsLabeling.LinePlacementFlag.OnLine
                flags |= QgsLabeling.LinePlacementFlag.MapOrientation
                pal_settings.lineSettings().setPlacementFlags(flags)
                
                # Optional: Repeat labels along long lines (off by default)
                repeat_distance = layer_config.get('label_repeat_distance', 0)
                if repeat_distance > 0:
                    pal_settings.repeatDistance = repeat_distance
                    pal_settings.repeatDistanceUnit = QgsUnitTypes.RenderMapUnits
                    logger.debug(f"Label repeat enabled: {repeat_distance} map units")
                
                # Use negative distance to place below line, which centers better
                # A small negative value shifts the label's baseline down
                pal_settings.dist = -5  # Negative to shift down for vertical centering
                pal_settings.distUnits = QgsUnitTypes.RenderPoints
                
                logger.info(f"Configured line placement with MapOrientation for {layer.name()}")
            
            # Deduplicate labels: only show label on first feature with each unique label value
            # This ensures each label text appears only once per layer (per region when filtered)
            # Can be disabled per-layer with 'deduplicate_labels': false
            if layer_config.get('deduplicate_labels', True):
                # Only apply if the label attribute is not NULL/empty
                dedup_expr = f'("{label_attr}" IS NOT NULL AND "{label_attr}" != \'\') AND ($id = minimum($id, group_by:="{label_attr}"))'
                pal_settings.dataDefinedProperties().setProperty(
                    QgsPalLayerSettings.Show,
                    QgsProperty.fromExpression(dedup_expr)
                )
                logger.debug(f"Enabled label deduplication for {layer.name()} on attribute: {label_attr}")
            else:
                # Just check for non-empty labels
                show_expr = f'"{label_attr}" IS NOT NULL AND "{label_attr}" != \'\''
                pal_settings.dataDefinedProperties().setProperty(
                    QgsPalLayerSettings.Show,
                    QgsProperty.fromExpression(show_expr)
                )
                logger.debug(f"Label deduplication disabled for {layer.name()}, showing all non-empty labels")
            
            # Apply labeling
            labeling = QgsVectorLayerSimpleLabeling(pal_settings)
            layer.setLabeling(labeling)
            layer.setLabelsEnabled(True)
            logger.info(f"Added labels to {layer.name()} using attribute: {label_attr}")
        else:
            logger.warning(f"Label attribute '{label_attr}' not found in layer {layer.name()}")
    
    layer.triggerRepaint()


def collect_layer_attributions(config, outlet_config):
    """
    Collect attribution information from all layers in the outlet.
    
    Args:
        config: Atlas configuration dict
        outlet_config: Outlet configuration dict
        
    Returns:
        List of unique attribution descriptions
    """
    attributions = set()
    in_layers = outlet_config.get('in_layers', [])
    
    # For each layer, find its source asset and get attribution
    for layer_name in in_layers:
        # Find assets that output to this layer
        for asset_name, asset_config in config.get('assets', {}).items():
            if asset_config.get('out_layer') == layer_name:
                # Get the inlet config definition
                config_def = asset_config.get('config_def')
                if config_def:
                    # Look up in inlets config
                    inlet_config = config.get('inlets', {}).get(config_def, {})
                    attribution = inlet_config.get('attribution', {})
                    description = attribution.get('description', '')
                    if description:
                        attributions.add(description)
    
    return sorted(list(attributions))


def create_region_layout(region, project, config, outlet_name):
    """
    Create a print layout for a region.
    
    Args:
        region: Region dict with bbox, name, caption
        project: QgsProject instance
        config: Atlas configuration dict
        outlet_name: Name of the outlet
        
    Returns:
        QgsPrintLayout configured for the region
    """
    outlet_config = config['assets'][outlet_name]
    
    # Get page size from config (default to A4)
    page_size = outlet_config.get('page_size', 'A4')
    page_orientation = outlet_config.get('page_orientation', 'Portrait')
    
    # Create layout
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName(f"Region_{region['name']}")
    
    # Set page size
    page_collection = layout.pageCollection()
    page = page_collection.page(0)
    
    if page_size == 'A4':
        if page_orientation == 'Landscape':
            page.setPageSize('A4', QgsLayoutItemPage.Landscape)
        else:
            page.setPageSize('A4')
    elif page_size == 'Letter':
        if page_orientation == 'Landscape':
            page.setPageSize('Letter', QgsLayoutItemPage.Landscape)
        else:
            page.setPageSize('Letter')
    
    # Check if map collar is enabled (default True)
    enable_collar = outlet_config.get('map_collar', True)
    collar_height = 25  # mm
    
    # Get page dimensions
    if page_size == 'A4':
        if page_orientation == 'Landscape':
            page_width = 297  # mm
            page_height = 210  # mm
        else:
            page_width = 210  # mm
            page_height = 297  # mm
    elif page_size == 'Letter':
        if page_orientation == 'Landscape':
            page_width = 279.4  # mm (11 inches)
            page_height = 215.9  # mm (8.5 inches)
        else:
            page_width = 215.9  # mm
            page_height = 279.4  # mm
    else:
        # Default to A4 portrait
        page_width = 210  # mm
        page_height = 297  # mm
    
    # Add map item
    map_item = QgsLayoutItemMap(layout)
    
    # Position and size (adjust for collar if enabled)
    margin = 2  # mm - small margin to avoid cutting off content at edges
    map_x = margin
    map_y = margin
    map_width = page_width - (2 * margin)
    map_height = page_height - (2 * margin) - (collar_height if enable_collar else 0)
    
    map_item.attemptMove(QgsLayoutPoint(map_x, map_y, QgsUnitTypes.LayoutMillimeters))
    map_item.attemptResize(QgsLayoutSize(map_width, map_height, QgsUnitTypes.LayoutMillimeters))
    
    # Set to NOT keep scale - allows map to fill the frame
    map_item.setKeepLayerSet(True)  # Keep the same layers visible
    
    # Set extent to region bbox
    bbox = region['bbox']
    
    # Get layer CRS (assume first layer in project)
    layer_crs = None
    layers = project.mapLayers().values()
    for layer in layers:
        if hasattr(layer, 'crs'):
            layer_crs = layer.crs()
            break
    
    if layer_crs is None:
        # Default to WGS84
        layer_crs = QgsCoordinateReferenceSystem("EPSG:4326")
    
    # Create rectangle in WGS84 (bbox is in lat/long)
    bbox_rect = QgsRectangle(
        bbox['west'], bbox['south'],
        bbox['east'], bbox['north']
    )
    
    # Transform if needed
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    if layer_crs != wgs84:
        transform = QgsCoordinateTransform(wgs84, layer_crs, project)
        bbox_rect = transform.transformBoundingBox(bbox_rect)
    
    # Keep original bbox for clipping
    original_bbox_rect = QgsRectangle(bbox_rect)
    
    # Calculate map frame aspect ratio
    frame_aspect = map_width / map_height
    
    # Calculate bbox aspect ratio
    bbox_width = bbox_rect.width()
    bbox_height = bbox_rect.height()
    bbox_aspect = bbox_width / bbox_height
    
    # Adjust bbox to match frame aspect ratio (expand to fill frame)
    if bbox_aspect < frame_aspect:
        # Bbox is too tall, expand width
        new_width = bbox_height * frame_aspect
        width_diff = new_width - bbox_width
        bbox_rect.setXMinimum(bbox_rect.xMinimum() - width_diff / 2)
        bbox_rect.setXMaximum(bbox_rect.xMaximum() + width_diff / 2)
    else:
        # Bbox is too wide, expand height
        new_height = bbox_width / frame_aspect
        height_diff = new_height - bbox_height
        bbox_rect.setYMinimum(bbox_rect.yMinimum() - height_diff / 2)
        bbox_rect.setYMaximum(bbox_rect.yMaximum() + height_diff / 2)
    
    map_item.setExtent(bbox_rect)
    map_item.setCrs(layer_crs)
    
    # Apply layout-specific clipping to the original bbox region
    # This clips features at render time for the layout only
    clip_points = [
        QgsPointXY(original_bbox_rect.xMinimum(), original_bbox_rect.yMinimum()),
        QgsPointXY(original_bbox_rect.xMaximum(), original_bbox_rect.yMinimum()),
        QgsPointXY(original_bbox_rect.xMaximum(), original_bbox_rect.yMaximum()),
        QgsPointXY(original_bbox_rect.xMinimum(), original_bbox_rect.yMaximum())
    ]
    clip_geometry = QgsGeometry.fromPolygonXY([clip_points])
    
    # Try to use atlas clipping settings for map content clipping (QGIS 3.16+)
    try:
        from qgis.core import QgsMapClippingRegion
        
        # Create clipping region for map rendering
        clipping_region = QgsMapClippingRegion(clip_geometry)
        clipping_region.setFeatureClip(QgsMapClippingRegion.FeatureClippingType.ClipToIntersection)
        
        # Use atlas clipping settings (which handles map content clipping)
        atlas_settings = map_item.atlasClippingSettings()
        atlas_settings.setEnabled(True)
        atlas_settings.setFeatureClippingType(QgsMapClippingRegion.FeatureClippingType.ClipToIntersection)
        atlas_settings.setClipGeometry(clip_geometry)
        map_item.setAtlasClippingSettings(atlas_settings)
        
        logger.info(f"Applied atlas clipping for map content bbox: {original_bbox_rect}")
        
    except (ImportError, AttributeError, TypeError) as e:
        logger.warning(f"Layout clipping not available: {e}")
        logger.warning("Features may extend beyond region boundary")
    
    layout.addLayoutItem(map_item)
    
    if enable_collar:
        # Calculate collar position (flush at bottom of page)
        collar_y = page_height - collar_height - margin
        
        # Add white background for collar
        collar_bg = QgsLayoutItemShape(layout)
        collar_bg.setShapeType(QgsLayoutItemShape.Rectangle)
        collar_bg.attemptMove(QgsLayoutPoint(margin, collar_y, QgsUnitTypes.LayoutMillimeters))
        collar_bg.attemptResize(QgsLayoutSize(map_width, collar_height, QgsUnitTypes.LayoutMillimeters))
        collar_bg.setFrameEnabled(False)
        collar_bg.setBackgroundEnabled(True)
        collar_bg.setBackgroundColor(QColor(255, 255, 255))  # White background
        layout.addLayoutItem(collar_bg)
        
        # Add separator line
        separator = QgsLayoutItemShape(layout)
        separator.setShapeType(QgsLayoutItemShape.Rectangle)
        separator.attemptMove(QgsLayoutPoint(margin, collar_y, QgsUnitTypes.LayoutMillimeters))
        separator.attemptResize(QgsLayoutSize(map_width, 0.5, QgsUnitTypes.LayoutMillimeters))
        separator.setFrameEnabled(True)
        separator.setFrameStrokeWidth(QgsLayoutMeasurement(0.3, QgsUnitTypes.LayoutMillimeters))
        layout.addLayoutItem(separator)
        
        collar_content_y = collar_y + 2  # Start content below separator
        
        # LEFT SECTION (35%): Legend
        legend = QgsLayoutItemLegend(layout)
        legend.setTitle("")  # No title, save space
        legend.setLinkedMap(map_item)
        legend.attemptMove(QgsLayoutPoint(margin, collar_content_y, QgsUnitTypes.LayoutMillimeters))
        legend.setFrameEnabled(False)
        
        # First set auto-update to populate the legend
        legend.setAutoUpdateModel(True)
        
        # Make legend compact
        legend.setSymbolWidth(4)
        legend.setSymbolHeight(3)
        legend.setLineSpacing(0.5)
        
        # Now disable auto-update so we can filter without affecting the map
        legend.setAutoUpdateModel(False)
        
        # Filter out basemap from legend (only affects legend display, not map)
        root = legend.model().rootGroup()
        layers_to_remove = []
        for layer in root.children():
            if isinstance(layer, QgsLayerTreeLayer):
                layer_name = layer.name().lower()
                if 'basemap' in layer_name:
                    layers_to_remove.append(layer)
        
        # Remove the collected layers
        for layer in layers_to_remove:
            root.removeChildNode(layer)
        
        layout.addLayoutItem(legend)
        
        # MIDDLE SECTION (25%): Scale Bar with North Indicator
        scale_x = margin + (map_width * 0.35) + 5
        
        # North Indicator (simple text)
        north_label = QgsLayoutItemLabel(layout)
        north_label.setText("↑ N")  # Up arrow + N
        north_font = QFont("Arial", 10, QFont.Bold)
        north_label.setFont(north_font)
        north_label.setHAlign(1)  # Center align
        north_label.attemptMove(QgsLayoutPoint(scale_x, collar_content_y, QgsUnitTypes.LayoutMillimeters))
        north_label.adjustSizeToText()
        north_label.setFrameEnabled(False)
        layout.addLayoutItem(north_label)
        
        # Scale Bar (imperial)
        scale_bar = QgsLayoutItemScaleBar(layout)
        scale_bar.setLinkedMap(map_item)
        scale_bar.setNumberOfSegments(3)  # Fewer segments to reduce clutter
        scale_bar.setNumberOfSegmentsLeft(0)
        scale_bar.setUnitsPerSegment(1000)  # Will auto-adjust based on map scale
        scale_bar.setSegmentSizeMode(QgsScaleBarSettings.SegmentSizeMode.SegmentSizeFitWidth)
        scale_bar.setMaximumBarWidth(45)  # mm
        
        # Set to double box style (USGS traditional)
        scale_bar.setStyle('Double Box')
        
        # Make text smaller to prevent overlap
        scale_font = QFont("Arial", 6)
        scale_bar.setFont(scale_font)
        scale_bar.setNumberOfSegmentsLeft(0)
        
        # Position in middle section
        scale_bar.attemptMove(QgsLayoutPoint(scale_x + 15, collar_content_y + 2, QgsUnitTypes.LayoutMillimeters))
        scale_bar.setFrameEnabled(False)
        layout.addLayoutItem(scale_bar)
        
        # Add metric scale bar below imperial
        scale_bar_metric = QgsLayoutItemScaleBar(layout)
        scale_bar_metric.setLinkedMap(map_item)
        scale_bar_metric.setNumberOfSegments(3)  # Fewer segments
        scale_bar_metric.setNumberOfSegmentsLeft(0)
        scale_bar_metric.setUnitsPerSegment(1)
        scale_bar_metric.setSegmentSizeMode(QgsScaleBarSettings.SegmentSizeMode.SegmentSizeFitWidth)
        scale_bar_metric.setMaximumBarWidth(45)
        scale_bar_metric.setStyle('Double Box')
        scale_bar_metric.setUnitLabel('km')
        scale_bar_metric.setUnits(QgsUnitTypes.DistanceKilometers)
        scale_bar_metric.setFont(scale_font)  # Same small font
        scale_bar_metric.attemptMove(QgsLayoutPoint(scale_x + 15, collar_content_y + 10, QgsUnitTypes.LayoutMillimeters))
        scale_bar_metric.setFrameEnabled(False)
        layout.addLayoutItem(scale_bar_metric)
        
        # RIGHT SECTION (40%): CRS and Attribution
        info_x = margin + (map_width * 0.60) + 5
        
        # CRS Label
        crs_label = QgsLayoutItemLabel(layout)
        crs_text = f"<b>Projection:</b> {layer_crs.description()}<br>({layer_crs.authid()})"
        crs_label.setText(crs_text)
        crs_label.setFont(QFont("Arial", 7))
        crs_label.setMode(QgsLayoutItemLabel.ModeHtml)
        crs_label.attemptMove(QgsLayoutPoint(info_x, collar_content_y, QgsUnitTypes.LayoutMillimeters))
        crs_label.adjustSizeToText()
        crs_label.setFrameEnabled(False)
        layout.addLayoutItem(crs_label)
        
        # Attribution Label
        attributions = collect_layer_attributions(config, outlet_config)
        if attributions:
            attr_label = QgsLayoutItemLabel(layout)
            attr_text = f"<b>Data Sources:</b><br>{', '.join(attributions)}"
            attr_label.setText(attr_text)
            attr_label.setFont(QFont("Arial", 6))
            attr_label.setMode(QgsLayoutItemLabel.ModeHtml)
            attr_label.attemptMove(QgsLayoutPoint(info_x, collar_content_y + 8, QgsUnitTypes.LayoutMillimeters))
            attr_label.adjustSizeToText()
            attr_label.setFrameEnabled(False)
            layout.addLayoutItem(attr_label)
        
        # Generation Date Label
        date_label = QgsLayoutItemLabel(layout)
        atlas_name = config.get('name', 'Atlas')
        gen_date = datetime.now().strftime('%Y-%m-%d')
        date_text = f"<b>{atlas_name}</b><br>Generated: {gen_date}"
        date_label.setText(date_text)
        date_label.setFont(QFont("Arial", 6))
        date_label.setMode(QgsLayoutItemLabel.ModeHtml)
        date_label.attemptMove(QgsLayoutPoint(info_x, collar_content_y + 15, QgsUnitTypes.LayoutMillimeters))
        date_label.adjustSizeToText()
        date_label.setFrameEnabled(False)
        layout.addLayoutItem(date_label)
        
    else:
        # No collar - use traditional legend position
        legend = QgsLayoutItemLegend(layout)
        legend.setTitle("Legend")
        legend.setLinkedMap(map_item)
    
        # Position legend in lower right
        legend_x = page_width - 60  # 60mm from right edge
        legend_y = page_height - 60  # 60mm from bottom
        legend.attemptMove(QgsLayoutPoint(legend_x, legend_y, QgsUnitTypes.LayoutMillimeters))
    
        legend.setFrameEnabled(True)
        legend.setAutoUpdateModel(True)
        layout.addLayoutItem(legend)
    
    logger.info(f"Created layout for region: {region['name']} (collar: {enable_collar})")
    return layout


def export_region_geopdf(layout, output_path):
    """
    Export a layout to GeoPDF.
    
    Args:
        layout: QgsPrintLayout to export
        output_path: Path for output PDF file
        
    Returns:
        True if successful, False otherwise
    """
    # Ensure output directory exists
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Error code mapping for debugging
    error_codes = {
        0: "Success",
        1: "Canceled", 
        2: "MemoryError",
        3: "FileError",
        4: "PrintError",
        5: "SvgLayerError",
        6: "IteratorError"
    }
    
    # Configure PDF export settings
    settings = QgsLayoutExporter.PdfExportSettings()
    settings.rasterizeWholeImage = False  # Keep vectors as vectors
    settings.exportMetadata = True  # Include georeferencing
    settings.writeGeoPdf = True  # Enable GeoPDF
    settings.dpi = 300  # High quality
    
    logger.debug(f"Exporting to: {output_path}")
    logger.debug(f"Layout has {len(layout.items())} items")
    
    # Export
    exporter = QgsLayoutExporter(layout)
    result = exporter.exportToPdf(str(output_path), settings)
    
    if result == QgsLayoutExporter.Success:
        logger.info(f"✓ Successfully exported GeoPDF to: {output_path}")
        return True
    else:
        error_name = error_codes.get(result, f"Unknown({result})")
        logger.error(f"Failed to export GeoPDF: {error_name} (code {result})")
        
        # Try simpler export without GeoPDF features (fallback)
        logger.info("Retrying with rasterized fallback (non-GeoPDF)...")
        settings.writeGeoPdf = False
        settings.rasterizeWholeImage = True  # Rasterize to avoid vector issues
        
        result2 = exporter.exportToPdf(str(output_path), settings)
        if result2 == QgsLayoutExporter.Success:
            logger.warning(f"⚠ Exported as rasterized PDF (not GeoPDF) to: {output_path}")
            logger.warning(f"  (GeoPDF failed - check layer data for issues)")
            return True
        else:
            logger.error(f"✗ Rasterized export also failed with code: {result2}")
            return False


def outlet_regions_qgis(config, outlet_name, regions_geojson_path=None, regions=None, 
                        regions_html=[], skips=[], reuse_extracts=False, first_n=0):
    """
    Generate region maps using QGIS API (GeoPDF output).
    
    This is a simplified version that reads regions directly from GeoJSON,
    eliminating the need for pre-processing and intermediate file extraction.
    
    Args:
        config: Atlas configuration dict
        outlet_name: Name of the outlet (e.g., 'gazetteer', 'runbook')
        regions_geojson_path: Path to regions GeoJSON file (preferred method)
        regions: List of region dicts (legacy compatibility, use regions_geojson_path instead)
        regions_html: List of (path, content) tuples for HTML outputs
        skips: List of processing steps to skip
        reuse_extracts: Ignored (for compatibility with GRASS version)
        first_n: Only process first N regions (for testing)
        
    Returns:
        List of region dicts with output paths
    """
    t = time.time()
    swale_name = config['name']
    outlet_config = config['assets'][outlet_name]
    
    logger.info(f"=== QGIS Outlet Regions Start ===")
    logger.info(f"Atlas: {swale_name}, Outlet: {outlet_name}")
    
    if 'region_maps' in skips:
        logger.info("Skipping region maps generation (in skips list)")
        return []
    
    # Load regions from GeoJSON if path provided
    if regions_geojson_path:
        logger.info(f"Loading regions from: {regions_geojson_path}")
        regions_list = load_regions_from_geojson(regions_geojson_path, first_n=first_n)
        logger.info(f"Loaded {len(regions_list)} region(s)")
    elif regions:
        # Legacy compatibility - regions already provided
        logger.info(f"Using provided regions list: {len(regions)} region(s)")
        regions_list = regions
        if first_n > 0:
            logger.info(f"Limiting to first {first_n} regions...")
            regions_list = regions_list[:first_n]
    else:
        logger.error("No regions provided! Specify regions_geojson_path or regions parameter")
        return []
    
    if not regions_list:
        logger.warning("No regions to process!")
        return []
    
    logger.info(f"Starting QGIS initialization...")
    qgis_init()
    logger.info(f"QGIS initialized")    
    try:
        # Create project
        project = QgsProject.instance()
        project.clear()
        
        # Load all full layers once
        logger.info("Loading full layers...")
        loaded_layers = {}
        in_layers = outlet_config.get('in_layers', [])
 
        for layer_config in config['dataswale']['layers']:
            layer_name = layer_config['name']
            
            # Skip if not in outlet's in_layers
            if layer_name not in in_layers:
                logger.debug(f"Skipping {layer_name} - not in outlet's in_layers")
                continue
            
            logger.info(f"Processing layer: {layer_name}...")
            
            try:
                # Load layer
                layer = load_full_layer(layer_config, config)
                if layer is None:
                    logger.warning(f"⚠ Skipping layer {layer_name} - failed to load")
                    continue
                
                # Apply styling
                apply_basic_styling(layer, layer_config)
                
                # Add to project
                project.addMapLayer(layer)
                loaded_layers[layer_name] = layer
                
                logger.info(f"✓ Loaded and styled layer: {layer_name} [{time.time() - t:.2f}s]")
                
            except Exception as e:
                logger.error(f"✗ Error loading layer {layer_name}: {e}")
                logger.debug(f"Layer config: {layer_config}")
                # Continue with other layers
                continue
        
        logger.info(f"Loaded {len(loaded_layers)} layer(s) total")
        
        # Process each region
        for i, region in enumerate(regions_list):
            logger.info(f"Processing region {i+1}/{len(regions_list)}: {region['name']} [{time.time() - t:.2f}s]")
            
            # Check if this region has custom in_layers
            region_in_layers = region.get('in_layers', in_layers)
            
            # Get region bbox for spatial filtering
            bbox = region['bbox']
            bbox_wkt = f"POLYGON(({bbox['west']} {bbox['south']}, {bbox['east']} {bbox['south']}, {bbox['east']} {bbox['north']}, {bbox['west']} {bbox['north']}, {bbox['west']} {bbox['south']}))"
            
            # Apply spatial filter to each layer for this region
            # This ensures labels are deduplicated per-region (not globally)
            for layer_name, layer in loaded_layers.items():
                visible = layer_name in region_in_layers
                
                if visible and isinstance(layer, QgsVectorLayer):
                    # Filter to only show features that intersect this region's bbox
                    # Use st_intersects with the bbox geometry
                    filter_expr = f"intersects($geometry, geom_from_wkt('{bbox_wkt}'))"
                    layer.setSubsetString(filter_expr)
                    feature_count = layer.featureCount()
                    logger.info(f"Applied spatial filter to {layer_name}: {feature_count} features in region {region['name']}")
                elif isinstance(layer, QgsVectorLayer):
                    # Clear filter for invisible layers (though they won't be rendered anyway)
                    layer.setSubsetString("")
            
            # Create layout for region
            layout = create_region_layout(region, project, config, outlet_name)
            
            # Validate layout before export
            if layout is None:
                logger.error(f"Failed to create layout for region {region['name']}")
                continue
            
            map_items = [item for item in layout.items() if isinstance(item, QgsLayoutItemMap)]
            if not map_items:
                logger.error(f"No map items in layout for region {region['name']}")
                continue
            
            map_item = map_items[0]
            logger.debug(f"Map extent: {map_item.extent().toString()}")
            logger.debug(f"Map CRS: {map_item.crs().authid()}")
            logger.debug(f"Visible layers in project: {len(project.mapLayers())}")
            
            # Export to GeoPDF
            output_path = versioning.atlas_path(config, "outlets") / outlet_name / f"page_{region['name']}.pdf"
            success = export_region_geopdf(layout, output_path)
            
            if success:
                # Store output path in region
                if 'outputs' not in region:
                    region['outputs'] = {}
                region['outputs']['pdf'] = str(output_path)
                logger.info(f"✓ Completed region {region['name']} [{time.time() - t:.2f}s]")
            else:
                logger.error(f"✗ Failed to export region {region['name']}")
        
        # Clear spatial filters from all layers (cleanup)
        for layer_name, layer in loaded_layers.items():
            if isinstance(layer, QgsVectorLayer):
                layer.setSubsetString("")
        logger.debug("Cleared spatial filters from all layers")
        
        # Save regions config as JSON
        if first_n == 0:
            regions_json_path = versioning.atlas_path(config, "outlets") / outlet_name / "regions_config.json"
            with open(regions_json_path, "w") as f:
                json.dump(regions_list, f, indent=2)
            logger.info(f"Saved regions config to: {regions_json_path}")
        
        # Write HTML outputs
        for outfile_path, outfile_content in regions_html:
            versioned_path = versioning.atlas_path(config, "outlets") / outlet_name / outfile_path
            logger.info(f"Writing region output to: {versioned_path}")
            with open(versioned_path, "w") as f:
                f.write(outfile_content)
        
        logger.info(f"=== Completed all regions in {time.time() - t:.2f}s ===")
        return regions_list
        
    finally:
        # Note: Not calling qgis_cleanup() to avoid segfault
        pass




def outlet_runbook_qgis(config, outlet_name='runbook', skips=[], start_at=0, limit=0, first_n=0):
    """
    Generate runbook using QGIS (simplified - reads regions GeoJSON directly).
    
    Args:
        config: Atlas configuration dict
        outlet_name: Name of the runbook outlet (default: 'runbook')
        skips: List of processing steps to skip
        start_at: Start at this region index (deprecated, use first_n instead)
        limit: Limit to this many regions (deprecated, use first_n instead)
        first_n: Only process first N regions (0 = all)
        
    Returns:
        List of processed regions
    """
    
    # get regions layer from asset config
    regions_layer_name = config['assets'][outlet_name].get('regions_layer', 'regions')

# Get regions GeoJSON path
    regions_path = versioning.atlas_path(config, "layers") / regions_layer_name / f"{regions_layer_name}.geojson"
    
    if not regions_path.exists():
        logger.error(f"Regions layer not found: {regions_path}")
        return []
    
    # Convert legacy limit parameter to first_n
    if limit > 0 and first_n == 0:
        first_n = limit
    
    logger.info(f"Running QGIS runbook outlet from: {regions_path}")
    
    return outlet_regions_qgis(
        config=config,
        outlet_name=outlet_name,
        regions_geojson_path=str(regions_path),
        skips=skips,
        first_n=first_n
    )


def outlet_gazetteer_qgis(config, outlet_name='gazetteer', skips=[], first_n=0):
    """
    Generate gazetteer using QGIS (generates grid regions automatically).
    
    For now, this still uses the GRASS approach for region generation
    but renders with QGIS. Full QGIS implementation coming in Phase 2.
    
    Args:
        config: Atlas configuration dict
        outlet_name: Name of the gazetteer outlet (default: 'gazetteer')
        skips: List of processing steps to skip
        first_n: Only process first N regions (0 = all)
        
    Returns:
        List of processed regions
    """
    # Import here to avoid circular dependency
    from outlets import generate_gazetteerregions
    
    logger.info(f"Running QGIS gazetteer outlet")
    
    # Generate grid regions (still uses existing logic)
    gaz_regions, gaz_html = generate_gazetteerregions(config, outlet_name)
    
    # Render with QGIS
    return outlet_regions_qgis(
        config=config,
        outlet_name=outlet_name,
        regions=gaz_regions,
        regions_html=gaz_html,
        skips=skips,
        first_n=first_n
    )

asset_methods = {
    'qgis_runbook': outlet_runbook_qgis
    }

if __name__ == "__main__":
    # Basic test
    print("QGIS outlets module loaded successfully")
    qgis_init()
    print(f"QGIS version: {QgsApplication.version()}")

