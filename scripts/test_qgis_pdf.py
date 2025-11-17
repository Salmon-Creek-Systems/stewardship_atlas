#!/usr/bin/env python3
"""
Minimal QGIS API test script - reads GeoJSON and exports to PDF.
Usage: python test_qgis_pdf.py <input.geojson> <output.pdf>
"""
import sys
from qgis.core import (
    QgsApplication,
    QgsVectorLayer,
    QgsProject,
    QgsLayoutExporter,
    QgsLayoutItemMap,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsPrintLayout,
    QgsUnitTypes
)

def main():
    if len(sys.argv) != 3:
        print("Usage: python test_qgis_pdf.py <input.geojson> <output.pdf>")
        sys.exit(1)
    
    input_path = sys.argv[1]
    output_path = sys.argv[2]
    
    # Initialize QGIS application
    qgs = QgsApplication([], False)
    qgs.initQgis()
    
    try:
        # Load the GeoJSON layer
        layer = QgsVectorLayer(input_path, "polygons", "ogr")
        if not layer.isValid():
            print(f"Error: Failed to load layer from {input_path}")
            sys.exit(1)
        
        # Add layer to project
        project = QgsProject.instance()
        project.addMapLayer(layer)
        
        # Create print layout
        layout = QgsPrintLayout(project)
        layout.initializeDefaults()
        
        # Add map item to layout
        map_item = QgsLayoutItemMap(layout)
        map_item.attemptMove(QgsLayoutPoint(5, 5, QgsUnitTypes.LayoutMillimeters))
        map_item.attemptResize(QgsLayoutSize(200, 287, QgsUnitTypes.LayoutMillimeters))
        
        # Set extent to layer extent
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
        # Clean up
        qgs.exitQgis()

if __name__ == "__main__":
    main()

