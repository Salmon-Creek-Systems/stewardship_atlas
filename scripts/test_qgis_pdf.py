#!/usr/bin/env python3
"""
Minimal QGIS API test script - reads GeoJSON and exports to PDF with optional GeoTIFF basemap.
Usage: python test_qgis_pdf.py <input.geojson> <output.pdf> [basemap.tiff]
"""
import os
import sys

# Set Qt to use offscreen platform for headless operation
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from qgis.core import (
    QgsApplication,
    QgsVectorLayer,
    QgsRasterLayer,
    QgsProject,
    QgsLayoutExporter,
    QgsLayoutItemMap,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsPrintLayout,
    QgsUnitTypes
)

def main():
    if len(sys.argv) < 3 or len(sys.argv) > 4:
        print("Usage: python test_qgis_pdf.py <input.geojson> <output.pdf> [basemap.tiff]")
        sys.exit(1)
    
    input_path = sys.argv[1]
    output_path = sys.argv[2]
    basemap_path = sys.argv[3] if len(sys.argv) == 4 else None
    
    # Initialize QGIS application
    qgs = QgsApplication([], False)
    qgs.initQgis()
    
    try:
        # Get project instance
        project = QgsProject.instance()
        
        # Load optional basemap raster first (will be in background)
        if basemap_path:
            basemap = QgsRasterLayer(basemap_path, "basemap")
            if not basemap.isValid():
                print(f"Error: Failed to load basemap from {basemap_path}")
                sys.exit(1)
            project.addMapLayer(basemap)
        
        # Load the GeoJSON polygon layer
        layer = QgsVectorLayer(input_path, "polygons", "ogr")
        if not layer.isValid():
            print(f"Error: Failed to load layer from {input_path}")
            sys.exit(1)
        
        # Set polygon layer to 50% opacity
        layer.setOpacity(0.5)
        
        # Add polygon layer to project (will be on top of basemap)
        project.addMapLayer(layer)
        
        # Create print layout
        layout = QgsPrintLayout(project)
        layout.initializeDefaults()
        
        # Add map item to layout
        map_item = QgsLayoutItemMap(layout)
        map_item.attemptMove(QgsLayoutPoint(5, 5, QgsUnitTypes.LayoutMillimeters))
        map_item.attemptResize(QgsLayoutSize(200, 287, QgsUnitTypes.LayoutMillimeters))
        
        # Set extent to polygon layer extent (both layers will be visible)
        map_item.setExtent(layer.extent())
        layout.addLayoutItem(map_item)
        
        # Export to PDF
        exporter = QgsLayoutExporter(layout)
        result = exporter.exportToPdf(output_path, QgsLayoutExporter.PdfExportSettings())
        
        if result == QgsLayoutExporter.Success:
            print(f"Success! PDF created at: {output_path}")
        else:
            print(f"Error: Export failed with code {result}")
            sys.exit(1)
    
    finally:
        # Clean up project before exiting
        project.removeAllMapLayers()
        # Note: Skipping qgs.exitQgis() to avoid segfault in offscreen mode
        # The process will clean up on exit anyway

if __name__ == "__main__":
    main()

