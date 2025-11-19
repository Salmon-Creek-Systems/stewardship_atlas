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
from pathlib import Path

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
    QgsLayoutItemPage,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsPrintLayout,
    QgsUnitTypes,
    QgsRectangle,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsSymbol,
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
    QgsLayerTreeLayer
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
    
    with open(geojson_path, 'r') as f:
        geojson_data = json.load(f)
    
    features = geojson_data.get('features', [])
    logger.info(f"Found {len(features)} feature(s) in regions GeoJSON")
    
    for i, feature in enumerate(features):
        # Apply first_n limit
        if first_n > 0 and i >= first_n:
            logger.info(f"Reached limit of {first_n} regions, stopping")
            break
        
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
        
        layer = QgsVectorLayer(str(layer_path), layer_name, "ogr")
        if not layer.isValid():
            logger.warning(f"Failed to load vector layer: {layer_name} from {layer_path}")
            return None
        logger.info(f"Loaded vector layer: {layer_name}")
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
            
            # Text format
            text_format = QgsTextFormat()
            text_format.setSize(10)
            text_format.setColor(qcolor)
            font = QFont()
            font.setPointSize(10)
            text_format.setFont(font)
            pal_settings.setFormat(text_format)
            
            # Apply labeling
            labeling = QgsVectorLayerSimpleLabeling(pal_settings)
            layer.setLabeling(labeling)
            layer.setLabelsEnabled(True)
            logger.info(f"Added labels to {layer.name()} using attribute: {label_attr}")
        else:
            logger.warning(f"Label attribute '{label_attr}' not found in layer {layer.name()}")
    
    layer.triggerRepaint()


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
    
    # Add map item
    map_item = QgsLayoutItemMap(layout)
    
    # Position and size (leave margins for legend)
    if page_orientation == 'Landscape':
        map_item.attemptMove(QgsLayoutPoint(5, 5, QgsUnitTypes.LayoutMillimeters))
        map_item.attemptResize(QgsLayoutSize(240, 180, QgsUnitTypes.LayoutMillimeters))
    else:
        map_item.attemptMove(QgsLayoutPoint(5, 5, QgsUnitTypes.LayoutMillimeters))
        map_item.attemptResize(QgsLayoutSize(200, 240, QgsUnitTypes.LayoutMillimeters))
    
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
    
    map_item.setExtent(bbox_rect)
    map_item.setCrs(layer_crs)
    layout.addLayoutItem(map_item)
    
    # Add legend
    legend = QgsLayoutItemLegend(layout)
    legend.setTitle("Legend")
    legend.setLinkedMap(map_item)
    
    # Position legend in lower right
    if page_orientation == 'Landscape':
        legend.attemptMove(QgsLayoutPoint(250, 150, QgsUnitTypes.LayoutMillimeters))
    else:
        legend.attemptMove(QgsLayoutPoint(5, 250, QgsUnitTypes.LayoutMillimeters))
    
    legend.setFrameEnabled(True)
    legend.setAutoUpdateModel(True)
    layout.addLayoutItem(legend)
    
    logger.info(f"Created layout for region: {region['name']}")
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
    
    # Configure PDF export settings
    settings = QgsLayoutExporter.PdfExportSettings()
    settings.rasterizeWholeImage = False  # Keep vectors as vectors
    settings.exportMetadata = True  # Include georeferencing
    settings.writeGeoPdf = True  # Enable GeoPDF
    settings.dpi = 300  # High quality
    
    # Export
    exporter = QgsLayoutExporter(layout)
    result = exporter.exportToPdf(str(output_path), settings)
    
    if result == QgsLayoutExporter.Success:
        logger.info(f"Successfully exported GeoPDF to: {output_path}")
        return True
    else:
        logger.error(f"Failed to export PDF with code: {result}")
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
            logger.info(f"Processing layer: {layer_name}...")
            
            # Skip if not in outlet's in_layers
            if layer_name not in in_layers:
                logger.debug(f"Skipping {layer_name} - not in outlet's in_layers")
                continue
            
            # Load layer
            layer = load_full_layer(layer_config, config)
            if layer is None:
                logger.warning(f"Skipping layer {layer_name} - failed to load")
                continue
            
            # Apply styling
            apply_basic_styling(layer, layer_config)
            
            # Add to project
            project.addMapLayer(layer)
            loaded_layers[layer_name] = layer
            
            logger.info(f"✓ Loaded and styled layer: {layer_name} [{time.time() - t:.2f}s]")
        
        logger.info(f"Loaded {len(loaded_layers)} layer(s) total")
        
        # Process each region
        for i, region in enumerate(regions_list):
            logger.info(f"Processing region {i+1}/{len(regions_list)}: {region['name']} [{time.time() - t:.2f}s]")
            
            # Check if this region has custom in_layers
            region_in_layers = region.get('in_layers', in_layers)
            
            # Temporarily hide layers not in this region's config
            layer_visibility = {}
            for layer_name, layer in loaded_layers.items():
                visible = layer_name in region_in_layers
                layer_visibility[layer_name] = visible
                # Note: Layer visibility is controlled via the layer tree in the layout
            
            # Create layout for region
            layout = create_region_layout(region, project, config, outlet_name)
            
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



asset_methods = {
    'qgis_regions': outlet_regions_qgis
    }
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
    # Get regions GeoJSON path
    regions_path = versioning.atlas_path(config, "layers") / "regions" / "regions.geojson"
    
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


if __name__ == "__main__":
    # Basic test
    print("QGIS outlets module loaded successfully")
    qgis_init()
    print(f"QGIS version: {QgsApplication.version()}")

