#!/usr/bin/env python3
"""
Simple Mapnik test script - generates a basic map with points and lines.
Uses temporary GeoJSON files for simplicity.
"""
import mapnik
import json
import tempfile
import os

# Create temporary GeoJSON files
geojson_points = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"name": "Point 1"},
            "geometry": {"type": "Point", "coordinates": [-122.0, 37.5]}
        },
        {
            "type": "Feature",
            "properties": {"name": "Point 2"},
            "geometry": {"type": "Point", "coordinates": [-122.1, 37.6]}
        },
        {
            "type": "Feature",
            "properties": {"name": "Point 3"},
            "geometry": {"type": "Point", "coordinates": [-122.05, 37.4]}
        }
    ]
}

geojson_line = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"name": "Test Line"},
            "geometry": {
                "type": "LineString",
                "coordinates": [[-122.15, 37.3], [-121.85, 37.7]]
            }
        }
    ]
}

# Write temporary files
with tempfile.NamedTemporaryFile(mode='w', suffix='.geojson', delete=False) as f:
    points_file = f.name
    json.dump(geojson_points, f)

with tempfile.NamedTemporaryFile(mode='w', suffix='.geojson', delete=False) as f:
    lines_file = f.name
    json.dump(geojson_line, f)

try:
    # Create a map with dimensions and background
    m = mapnik.Map(800, 600)
    m.background = mapnik.Color('lightblue')

    # Create a simple point style
    point_style = mapnik.Style()
    point_rule = mapnik.Rule()
    point_symbolizer = mapnik.MarkersSymbolizer()
    point_symbolizer.fill = mapnik.Color('red')
    point_symbolizer.width = mapnik.Expression('15')
    point_symbolizer.height = mapnik.Expression('15')
    point_rule.symbols.append(point_symbolizer)
    point_style.rules.append(point_rule)
    m.append_style('PointStyle', point_style)

    # Create a line style
    line_style = mapnik.Style()
    line_rule = mapnik.Rule()
    line_symbolizer = mapnik.LineSymbolizer()
    line_symbolizer.stroke = mapnik.Color('blue')
    line_symbolizer.stroke_width = mapnik.Expression('3')
    line_rule.symbols.append(line_symbolizer)
    line_style.rules.append(line_rule)
    m.append_style('LineStyle', line_style)

    # Add line layer
    line_layer = mapnik.Layer('Lines')
    line_layer.datasource = mapnik.GeoJSON(file=lines_file)
    line_layer.styles.append('LineStyle')
    m.layers.append(line_layer)

    # Add point layer
    point_layer = mapnik.Layer('Points')
    point_layer.datasource = mapnik.GeoJSON(file=points_file)
    point_layer.styles.append('PointStyle')
    m.layers.append(point_layer)

    # Zoom to layers
    m.zoom_all()

    # Render to PNG
    output_file = 'mapnik_test.png'
    mapnik.render_to_file(m, output_file, 'png')

    print(f"✓ Success! Map rendered to {output_file}")
    print(f"  Mapnik version: {mapnik.mapnik_version()}")
    print(f"  Map size: {m.width}x{m.height}")
    print(f"  Layers: {len(m.layers)}")
    
finally:
    # Clean up temporary files
    if os.path.exists(points_file):
        os.remove(points_file)
    if os.path.exists(lines_file):
        os.remove(lines_file)

