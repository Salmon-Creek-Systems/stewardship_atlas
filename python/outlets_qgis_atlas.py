#!/usr/bin/env python3
"""
QGIS Atlas-based outlet for runbook generation.

This module uses QGIS Atlas functionality to generate multi-page PDFs
where each page represents a region. Key advantages:
- Automatic iteration over regions
- Built-in feature clipping to region boundaries
- Single multi-page PDF output
- Cleaner code with less manual extent management
"""

import os
import sys
import json
import logging
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
    QgsLayoutItemMapOverview,
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
    QgsMapClippingRegion,
    QgsTextFormat,
    QgsLayerTreeLayer,
    QgsGeometry,
    QgsPointXY,
    QgsFillSymbol
)
from qgis.PyQt.QtGui import QColor, QFont

# Import local modules
try:
    from . import versioning
    from . import outlets_qgis
except ImportError:
    import versioning
    import outlets_qgis

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def outlet_runbook_qgis_atlas(config, outlet_name, only_generate=[]):
    """
    Generate a runbook PDF using QGIS Atlas functionality.
    
    This approach creates a single layout with atlas mode enabled,
    which automatically generates one page per region with proper clipping.
    
    Args:
        config: Atlas configuration dict
        outlet_name: Name of the outlet to process
        only_generate: List of region names to generate (default: [] = all regions)
        
    Returns:
        dict with status and output paths
    """
    outlet_config = config['assets'][outlet_name]
    
    # Get paths
    swale_path = versioning.atlas_path(config, "layers")
    regions_path = swale_path / "regions" / "regions.geojson"
    
    if not regions_path.exists():
        raise FileNotFoundError(f"Regions file not found: {regions_path}")
    
    # Initialize QGIS
    qgs = QgsApplication([], False)
    qgs.initQgis()
    
    try:
        # Create project and load layers
        project = QgsProject.instance()
        project.clear()
        
        logger.info(f"Loading layers for atlas generation...")
        
        # Load all data layers
        in_layers = outlet_config.get('in_layers', [])
        layers_config = {x['name']: x for x in config['dataswale']['layers']}
        # layers_config = config['dataswale']['layers']
        
        for layer_name in in_layers:
            if layer_name not in layers_config:
                logger.warning(f"⚠ Layer {layer_name} not found in config {layers_config.keys()}")
                continue
            
            layer_config = layers_config[layer_name]
            
            try:
                # Load layer using outlets_qgis function
                layer = outlets_qgis.load_full_layer(layer_config, config)
                if layer is None:
                    logger.warning(f"⚠ Skipping layer {layer_name} - failed to load")
                    continue
                
                # Apply styling (pass config for custom icons and feature_scale)
                feature_scale = outlet_config.get('feature_scale', 1.0)
                outlets_qgis.apply_basic_styling(layer, layer_config, config, feature_scale)
                
                # Add to project
                project.addMapLayer(layer)
                logger.info(f"✓ Loaded layer: {layer_name}")
                
            except Exception as e:
                logger.error(f"✗ Error loading layer {layer_name}: {e}")
                continue
        
        # Load regions as coverage layer
        regions_layer = QgsVectorLayer(str(regions_path), "regions", "ogr")
        if not regions_layer.isValid():
            raise RuntimeError(f"Failed to load regions layer from {regions_path}")
        
        # Filter to only_generate regions if specified
        if only_generate:
            # Build SQL-like filter expression: "name" IN ('region1', 'region2', ...)
            # Escape single quotes in region names for safety
            escaped_names = [name.replace("'", "''") for name in only_generate]
            quoted_names = "', '".join(escaped_names)
            filter_expr = f'"name" IN (\'{quoted_names}\')'
            regions_layer.setSubsetString(filter_expr)
            logger.info(f"Filtered to {len(only_generate)} specified regions: {only_generate}")
            logger.info(f"Filter expression: {filter_expr}")
            
            if regions_layer.featureCount() == 0:
                raise RuntimeError(f"No regions matched filter: {only_generate}")
        
        project.addMapLayer(regions_layer, False)  # False = don't add to legend
        logger.info(f"Loaded {regions_layer.featureCount()} regions as coverage layer")
        
        # Create atlas layout
        layout = create_atlas_layout(project, regions_layer, config, outlet_name)
        
        # Export atlas
        output_dir = versioning.atlas_path(config, outlet_config.get('outpath', 'outputs'))
        output_dir.mkdir(parents=True, exist_ok=True)
        
        results = export_atlas(layout, output_dir, config.get('name', 'atlas'))
        
        logger.info(f"Atlas generation complete: {results}")
        return results
        
    finally:
        qgs.exitQgis()


def create_atlas_layout(project, coverage_layer, config, outlet_name):
    """
    Create a print layout with atlas enabled.
    
    Args:
        project: QgsProject instance
        coverage_layer: Vector layer with regions (atlas coverage)
        config: Atlas configuration dict
        outlet_name: Name of the outlet
        
    Returns:
        QgsPrintLayout configured with atlas
    """
    outlet_config = config['assets'][outlet_name]
    
    # Get page settings
    page_size = outlet_config.get('page_size', 'Letter')
    page_orientation = outlet_config.get('page_orientation', 'Landscape')
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
        # Default to Letter Landscape
        page_width = 279.4
        page_height = 215.9
    
    # Create layout
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("Atlas_Layout")
    
    # Configure page
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
    
    # Enable atlas
    atlas = layout.atlas()
    atlas.setCoverageLayer(coverage_layer)
    atlas.setEnabled(True)
    
    # Add map item
    map_item = QgsLayoutItemMap(layout)
    
    # Position and size
    margin = 2  # mm
    map_x = margin
    map_y = margin
    map_width = page_width - (2 * margin)
    map_height = page_height - (2 * margin) - (collar_height if enable_collar else 0)
    
    map_item.attemptMove(QgsLayoutPoint(map_x, map_y, QgsUnitTypes.LayoutMillimeters))
    map_item.attemptResize(QgsLayoutSize(map_width, map_height, QgsUnitTypes.LayoutMillimeters))
    
    # Configure atlas-driven map
    map_item.setAtlasDriven(True)
    map_item.setAtlasScalingMode(QgsLayoutItemMap.Auto)
    map_item.setAtlasMargin(0.05)  # 5% margin
    
    # Get layer CRS
    layer_crs = None
    layers = project.mapLayers().values()
    for layer in layers:
        if hasattr(layer, 'crs'):
            layer_crs = layer.crs()
            break
    
    if layer_crs is None:
        layer_crs = QgsCoordinateReferenceSystem("EPSG:4326")
    
    # Determine the best CRS for rendering (needed for accurate scale bars)
    # If layer_crs is geographic (degrees), we need to use a projected CRS
    render_crs = layer_crs
    if layer_crs.isGeographic():
        # Use Web Mercator for rendering - it's a good general-purpose projected CRS
        render_crs = QgsCoordinateReferenceSystem("EPSG:3857")
        logger.info(f"Layer CRS {layer_crs.authid()} is geographic, using EPSG:3857 for rendering")
    
    map_item.setCrs(render_crs)
    
    # Enable atlas clipping - this clips features to the current coverage feature!
    # The atlasClippingSettings() returns a reference, so we just modify it directly
    atlas_clipping = map_item.atlasClippingSettings()
    atlas_clipping.setEnabled(True)
    atlas_clipping.setFeatureClippingType(QgsMapClippingRegion.FeatureClippingType.ClipToIntersection)
    
    # Explicitly set which layers to clip (all vector layers)
    layers_to_clip = [layer for layer in project.mapLayers().values() 
                      if isinstance(layer, QgsVectorLayer)]
    atlas_clipping.setLayersToClip(layers_to_clip)
    
    logger.info(f"Atlas clipping enabled: {atlas_clipping.enabled()}")
    logger.info(f"Atlas clipping type: {atlas_clipping.featureClippingType()}")
    logger.info(f"Atlas layers to clip: {len(layers_to_clip)} layers")
    
    layout.addLayoutItem(map_item)
    
    if enable_collar:
        add_map_collar(layout, map_item, config, outlet_config, page_width, page_height, 
                       margin, collar_height, render_crs, coverage_layer)
    
    logger.info(f"Created atlas layout with {coverage_layer.featureCount()} pages")
    return layout


def add_map_collar(layout, map_item, config, outlet_config, page_width, page_height, 
                   margin, collar_height, layer_crs, coverage_layer=None):
    """
    Add map collar with legend, scale bar, CRS info, region name, and overview map.
    
    Args:
        layout: QgsPrintLayout
        map_item: QgsLayoutItemMap (atlas-controlled detail map)
        config: Atlas configuration
        outlet_config: Outlet configuration
        page_width: Page width in mm
        page_height: Page height in mm
        margin: Page margin in mm
        collar_height: Collar height in mm
        layer_crs: Map CRS
        coverage_layer: QgsVectorLayer - regions layer for overview extent (optional)
    """
    map_height = page_height - (2 * margin) - collar_height
    map_width = page_width - (2 * margin)
    
    # Calculate collar position (flush at bottom of page)
    collar_y = page_height - collar_height - margin
    
    # Add white background for collar
    collar_bg = QgsLayoutItemShape(layout)
    collar_bg.setShapeType(QgsLayoutItemShape.Rectangle)
    collar_bg.attemptMove(QgsLayoutPoint(margin, collar_y, QgsUnitTypes.LayoutMillimeters))
    collar_bg.attemptResize(QgsLayoutSize(map_width, collar_height, QgsUnitTypes.LayoutMillimeters))
    collar_bg.setFrameEnabled(False)
    collar_bg.setBackgroundEnabled(True)
    collar_bg.setBackgroundColor(QColor(255, 255, 255))
    layout.addLayoutItem(collar_bg)
    
    # Add separator line
    separator = QgsLayoutItemShape(layout)
    separator.setShapeType(QgsLayoutItemShape.Rectangle)
    separator.attemptMove(QgsLayoutPoint(margin, collar_y, QgsUnitTypes.LayoutMillimeters))
    separator.attemptResize(QgsLayoutSize(map_width, 0.5, QgsUnitTypes.LayoutMillimeters))
    separator.setFrameEnabled(True)
    separator.setFrameStrokeWidth(QgsLayoutMeasurement(0.3, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(separator)
    
    collar_content_y = collar_y + 2
    
    # LEFT SECTION (30%): Legend
    legend = QgsLayoutItemLegend(layout)
    legend.setTitle("")
    legend.setLinkedMap(map_item)
    legend.attemptMove(QgsLayoutPoint(margin, collar_content_y, QgsUnitTypes.LayoutMillimeters))
    legend.setFrameEnabled(False)
    legend.setAutoUpdateModel(True)
    
    # Make legend compact with 2 columns
    legend.setSymbolWidth(4)
    legend.setSymbolHeight(3)
    legend.setLineSpacing(0.5)
    legend.setColumnCount(2)  # Use 2 columns to save vertical space
    legend.setColumnSpace(5)  # Space between columns in mm
    
    # Filter out basemap and regions, and group road layers
    legend.setAutoUpdateModel(False)
    root = legend.model().rootGroup()
    layers_to_remove = []
    road_layers = []
    
    for layer in root.children():
        if isinstance(layer, QgsLayerTreeLayer):
            layer_name = layer.name().lower()
            if 'basemap' in layer_name or 'regions' in layer_name:
                layers_to_remove.append(layer)
            elif layer_name.startswith('roads_'):
                road_layers.append(layer)
    
    # Remove basemap and regions layers
    for layer in layers_to_remove:
        root.removeChildNode(layer)
    
    # Group road layers under a single "Roads" entry (keep only first road layer as representative)
    if road_layers:
        # Keep the first road layer and rename it to "Roads"
        road_layers[0].setName("Roads")
        # Remove other road layers
        for layer in road_layers[1:]:
            root.removeChildNode(layer)
    
    layout.addLayoutItem(legend)
    
    # MIDDLE SECTION (25%): Region Name and North Indicator
    region_x = margin + (map_width * 0.30) + 5
    
    # Region name (using atlas expression)
    region_label = QgsLayoutItemLabel(layout)
    region_label.setText("[% attribute(@atlas_feature, 'name') %]")
    region_font = QFont("Arial", 12, QFont.Bold)
    region_label.setFont(region_font)
    region_label.setHAlign(1)  # Center
    region_label.attemptMove(QgsLayoutPoint(region_x, collar_content_y, QgsUnitTypes.LayoutMillimeters))
    region_label.adjustSizeToText()
    region_label.setFrameEnabled(False)
    layout.addLayoutItem(region_label)
    
    # North indicator
    north_label = QgsLayoutItemLabel(layout)
    north_label.setText("↑ N")
    north_font = QFont("Arial", 10, QFont.Bold)
    north_label.setFont(north_font)
    north_label.setHAlign(1)
    north_label.attemptMove(QgsLayoutPoint(region_x, collar_content_y + 7, QgsUnitTypes.LayoutMillimeters))
    north_label.adjustSizeToText()
    north_label.setFrameEnabled(False)
    layout.addLayoutItem(north_label)
    
    # Scale bars
    scale_x = region_x + 20
    
    # Imperial scale bar (feet/miles)
    scale_bar = QgsLayoutItemScaleBar(layout)
    scale_bar.setLinkedMap(map_item)
    scale_bar.setUnits(QgsUnitTypes.DistanceFeet)  # Explicit imperial units
    scale_bar.setNumberOfSegments(2)  # Fewer segments to reduce clutter
    scale_bar.setNumberOfSegmentsLeft(0)
    scale_bar.setUnitsPerSegment(1000)  # Will auto-adjust based on map scale
    scale_bar.setSegmentSizeMode(QgsScaleBarSettings.SegmentSizeMode.SegmentSizeFitWidth)
    scale_bar.setMaximumBarWidth(40)  # mm
    scale_bar.setMinimumBarWidth(20)  # mm
    scale_bar.setStyle('Double Box')
    scale_bar.setUnitLabel('ft')  # Show "ft" label
    scale_bar.setFont(QFont("Arial", 6))
    scale_bar.attemptMove(QgsLayoutPoint(scale_x, collar_content_y + 2, QgsUnitTypes.LayoutMillimeters))
    scale_bar.setFrameEnabled(False)
    layout.addLayoutItem(scale_bar)
    
    # Metric scale bar
    scale_bar_metric = QgsLayoutItemScaleBar(layout)
    scale_bar_metric.setLinkedMap(map_item)
    scale_bar_metric.setUnits(QgsUnitTypes.DistanceMeters)  # Explicit metric units
    scale_bar_metric.setNumberOfSegments(2)  # Fewer segments
    scale_bar_metric.setNumberOfSegmentsLeft(0)
    scale_bar_metric.setUnitsPerSegment(100)  # Start with 100m
    scale_bar_metric.setSegmentSizeMode(QgsScaleBarSettings.SegmentSizeMode.SegmentSizeFitWidth)
    scale_bar_metric.setMaximumBarWidth(40)
    scale_bar_metric.setMinimumBarWidth(20)
    scale_bar_metric.setStyle('Double Box')
    scale_bar_metric.setUnitLabel('m')  # Show "m" label (will auto-convert to km if large)
    scale_bar_metric.setFont(QFont("Arial", 6))
    scale_bar_metric.attemptMove(QgsLayoutPoint(scale_x, collar_content_y + 10, QgsUnitTypes.LayoutMillimeters))
    scale_bar_metric.setFrameEnabled(False)
    layout.addLayoutItem(scale_bar_metric)
    
    # RIGHT SECTION (45%): CRS and Attribution
    info_x = margin + (map_width * 0.55) + 5
    
    # CRS Label (show the rendering CRS)
    crs_label = QgsLayoutItemLabel(layout)
    render_crs = map_item.crs()  # Get the actual CRS being used by the map
    crs_text = f"<b>Projection:</b> {render_crs.description()}<br>({render_crs.authid()})"
    crs_label.setText(crs_text)
    crs_label.setFont(QFont("Arial", 7))
    crs_label.setMode(QgsLayoutItemLabel.ModeHtml)
    crs_label.attemptMove(QgsLayoutPoint(info_x, collar_content_y, QgsUnitTypes.LayoutMillimeters))
    crs_label.adjustSizeToText()
    crs_label.setFrameEnabled(False)
    layout.addLayoutItem(crs_label)
    
    # Attribution
    attributions = outlets_qgis.collect_layer_attributions(config, outlet_config)
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
    
    # Generation date
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
    
    # OVERVIEW MAP (far right): Small map showing location of current page
    if coverage_layer is not None:
        overview_size = 20  # mm - square overview map
        overview_x = page_width - margin - overview_size - 2
        overview_y = collar_content_y
        
        # Create overview map item
        overview_map = QgsLayoutItemMap(layout)
        overview_map.attemptMove(QgsLayoutPoint(overview_x, overview_y, QgsUnitTypes.LayoutMillimeters))
        overview_map.attemptResize(QgsLayoutSize(overview_size, overview_size, QgsUnitTypes.LayoutMillimeters))
        
        # Set extent to show all regions (coverage layer extent with 10% buffer)
        # Transform extent to match the rendering CRS if needed
        extent = coverage_layer.extent()
        coverage_crs = coverage_layer.crs()
        render_crs = map_item.crs()
        
        if coverage_crs != render_crs:
            transform = QgsCoordinateTransform(coverage_crs, render_crs, project)
            extent = transform.transformBoundingBox(extent)
            logger.info(f"Transformed overview extent from {coverage_crs.authid()} to {render_crs.authid()}")
        
        extent.scale(1.1)  # Add 10% buffer around all regions
        overview_map.setExtent(extent)
        overview_map.setCrs(render_crs)  # Use same CRS as main map
        
        logger.info(f"Overview map extent: {extent.toString()}, CRS: {render_crs.authid()}")
        
        # Get project to access layers
        project = layout.project()
        
        # Set layers for overview - show basemap and regions only (simpler view)
        overview_layers = []
        for layer in project.mapLayers().values():
            layer_name = layer.name().lower()
            # Only show basemap and regions layer in overview
            if 'basemap' in layer_name or 'region' in layer_name:
                overview_layers.append(layer)
        
        if overview_layers:
            overview_map.setKeepLayerSet(True)
            overview_map.setLayers(overview_layers)
            logger.info(f"Overview map showing {len(overview_layers)} layers: {[l.name() for l in overview_layers]}")
            # Check if layers are valid
            for layer in overview_layers:
                logger.info(f"  - {layer.name()}: valid={layer.isValid()}, extent={layer.extent().toString()}")
        else:
            # Fallback: show all layers if we can't find basemap/regions
            overview_map.setKeepLayerSet(False)
            logger.warning("Could not find basemap/regions for overview, showing all layers")
        
        # Ensure the map draws content
        overview_map.setDrawAnnotations(False)  # Don't draw annotations in overview
        overview_map.setBackgroundEnabled(True)
        overview_map.setBackgroundColor(QColor(255, 255, 255))  # White background
        
        # Add border to overview map
        overview_map.setFrameEnabled(True)
        overview_map.setFrameStrokeWidth(QgsLayoutMeasurement(0.3, QgsUnitTypes.LayoutMillimeters))
        overview_map.setFrameStrokeColor(QColor(100, 100, 100))
        
        # Add to layout first
        layout.addLayoutItem(overview_map)
        
        # Force refresh to ensure it renders
        overview_map.refresh()
        
        # Add overview frame showing current atlas extent on the overview map
        try:
            # Create a new overview object
            overview_item = QgsLayoutItemMapOverview("Current Region", overview_map)
            
            # Access the overview stack and add the overview
            overview_stack = overview_map.overviews()
            overview_stack.addOverview(overview_item)
            
            # Link to the main detail map (so it shows detail map's extent)
            overview_item.setLinkedMap(map_item)
            
            # Enable the overview
            overview_item.setEnabled(True)
            
            # Style the extent frame - red outline to show current region
            # Use simple symbol properties
            fill_symbol = QgsFillSymbol.createSimple({
                'color': '255,0,0,50',  # Semi-transparent red fill
                'outline_color': '255,0,0,255',  # Solid red outline
                'outline_width': '0.8',
                'outline_style': 'solid'
            })
            overview_item.setFrameSymbol(fill_symbol)
            
            logger.info("Added overview map to collar with extent indicator")
            
        except Exception as e:
            logger.warning(f"Could not add overview frame: {e}")
            import traceback
            logger.warning(traceback.format_exc())


def export_atlas(layout, output_dir, atlas_name):
    """
    Export atlas to both multi-page PDF and individual PDFs per region.
    
    Args:
        layout: QgsPrintLayout with atlas enabled
        output_dir: Output directory path
        atlas_name: Base name for output files
        
    Returns:
        dict with status and output paths
    """
    atlas = layout.atlas()
    exporter = QgsLayoutExporter(layout)
    
    results = {
        'status': 'success',
        'multi_page_pdf': None,
        'individual_pdfs': [],
        'total_pages': 0
    }
    
    # PDF export settings
    pdf_settings = QgsLayoutExporter.PdfExportSettings()
    pdf_settings.dpi = 300
    pdf_settings.rasterizeWholeImage = False
    pdf_settings.forceVectorOutput = True
    pdf_settings.exportMetadata = True
    
    # Export multi-page PDF
    multi_pdf_path = output_dir / f"{atlas_name}_runbook.pdf"
    logger.info(f"Exporting multi-page PDF to: {multi_pdf_path}")
    
    result = exporter.exportToPdf(atlas, str(multi_pdf_path), pdf_settings)
    
    if result == QgsLayoutExporter.Success:
        results['multi_page_pdf'] = str(multi_pdf_path)
        logger.info(f"✓ Multi-page PDF exported successfully")
    else:
        error_msg = get_export_error_message(result)
        logger.error(f"✗ Multi-page PDF export failed: {error_msg}")
        results['status'] = 'partial'
    
    # Export individual PDFs per region
    individual_dir = output_dir / "individual_pages"
    individual_dir.mkdir(exist_ok=True)
    
    logger.info(f"Exporting individual PDFs to: {individual_dir}")
    
    # Iterate through atlas features
    atlas.beginRender()
    page_num = 0
    
    while atlas.next():
        page_num += 1
        feature = atlas.layout().reportContext().feature()
        
        # Get region name from feature
        try:
            region_name = feature.attribute('name')
            if not region_name or region_name == '':
                region_name = f"region_{page_num}"
        except:
            region_name = f"region_{page_num}"
        
        # Clean region name for filename
        safe_name = "".join(c if c.isalnum() or c in ('-', '_') else '_' for c in region_name)
        individual_pdf_path = individual_dir / f"{safe_name}.pdf"
        
        result = exporter.exportToPdf(str(individual_pdf_path), pdf_settings)
        
        if result == QgsLayoutExporter.Success:
            results['individual_pdfs'].append(str(individual_pdf_path))
            logger.info(f"  ✓ Page {page_num}: {region_name}")
        else:
            error_msg = get_export_error_message(result)
            logger.error(f"  ✗ Page {page_num} ({region_name}) failed: {error_msg}")
            results['status'] = 'partial'
    
    atlas.endRender()
    results['total_pages'] = page_num
    
    logger.info(f"Atlas export complete: {page_num} pages")
    return results


def get_export_error_message(error_code):
    """Convert QgsLayoutExporter error code to human-readable message."""
    error_map = {
        0: "Success",
        1: "Canceled",
        2: "MemoryError",
        3: "FileError",
        4: "PrintError",
        5: "SvgLayerError",
        6: "IteratorError"
    }
    return error_map.get(error_code, f"Unknown error ({error_code})")


# Asset method registration
asset_methods = {
    'outlet_runbook_qgis_atlas': outlet_runbook_qgis_atlas
}

