#!/usr/bin/env python3
"""
3D Terrain View Generator for Stewardship Atlas
Uses MapLibre GL JS to create 3D terrain visualization from existing elevation data.
"""

import os
import json
from pathlib import Path
from typing import Dict, Any, Optional
import versioning


def generate_3d_terrain_html(config: Dict[str, Any]) -> str:
    """
    Generate a 3D terrain HTML page using MapLibre GL JS.
    
    Args:
        atlas_name: Name of the atlas
        config: Atlas configuration dictionary
    
    Returns:
        HTML content as string
    """
    
    # Get paths for existing data
    atlas_name = config['name']
    atlas_path = versioning.atlas_path(config)
    elevation_dir = atlas_path / "layers" / "elevation"
    basemap_dir = atlas_path / "layers" / "basemap"
    
    # Find elevation files (TIFF or PNG)
    elevation_files = list(elevation_dir.glob("*.tiff")) + list(elevation_dir.glob("*.tif")) + list(elevation_dir.glob("*.png"))
    if not elevation_files:
        raise FileNotFoundError(f"No elevation files found in {elevation_dir}")
    
    # Use the first elevation file found
    elevation_file = elevation_files[0]
    elevation_url = f"/{atlas_name}/staging/layers/elevation/{elevation_file.name}"
    
    # Find satellite basemap files
    satellite_files = list(basemap_dir.glob("*satellite*")) + list(basemap_dir.glob("*sat*"))
    if satellite_files:
        basemap_file = satellite_files[0]
        basemap_url = f"/atlas/{atlas_name}/layers/basemap/{basemap_file.name}"
        use_local_basemap = True
    else:
        # Fallback to external satellite source
        basemap_url = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
        use_local_basemap = False
    
    # Get map bounds from config if available
    bounds = config.get('bounds', None)
    if bounds:
        center_lng = (bounds[0] + bounds[2]) / 2
        center_lat = (bounds[1] + bounds[3]) / 2
    else:
        # Default center if no bounds specified
        center_lng, center_lat = -123.0, 40.0
    
    # Generate the HTML content
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <title>3D Terrain - {atlas_name.title()}</title>
    <meta charset='utf-8'>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel='stylesheet' href='https://unpkg.com/maplibre-gl@5.6.1/dist/maplibre-gl.css' />
    <link rel='stylesheet' href='/static/map.css' />
    <link rel='stylesheet' href='/static/edit_controls.css' />
    <script src='https://unpkg.com/maplibre-gl@5.6.1/dist/maplibre-gl.js'></script>
    <style>
        body {{ 
            margin: 0; 
            padding: 0; 
            font-family: Arial, sans-serif;
        }}
        html, body, #map {{ 
            height: 100%; 
            width: 100%;
        }}
        .map-container {{
            position: relative;
            width: 100%;
            height: 100%;
        }}
        .info-panel {{
            position: absolute;
            top: 10px;
            left: 10px;
            background: rgba(255, 255, 255, 0.9);
            padding: 10px;
            border-radius: 5px;
            z-index: 1000;
            max-width: 300px;
        }}
        .info-panel h3 {{
            margin: 0 0 10px 0;
            color: #333;
        }}
        .info-panel p {{
            margin: 5px 0;
            font-size: 14px;
        }}
        .terrain-controls {{
            position: absolute;
            top: 10px;
            right: 10px;
            background: rgba(255, 255, 255, 0.9);
            padding: 10px;
            border-radius: 5px;
            z-index: 1000;
        }}
        .terrain-controls label {{
            display: block;
            margin: 5px 0;
            font-size: 14px;
        }}
        .terrain-controls input {{
            width: 100px;
            margin-left: 10px;
        }}
        .back-link {{
            position: absolute;
            bottom: 10px;
            left: 10px;
            background: rgba(255, 255, 255, 0.9);
            padding: 8px 15px;
            border-radius: 5px;
            z-index: 1000;
            text-decoration: none;
            color: #333;
            font-weight: bold;
        }}
        .back-link:hover {{
            background: rgba(255, 255, 255, 1);
        }}
    </style>
</head>
<body>
    <div class="map-container">
        <div id="map"></div>
        
        <div class="info-panel">
            <h3>3D Terrain View</h3>
            <p><strong>Atlas:</strong> {atlas_name.title()}</p>
            <p><strong>Elevation:</strong> {elevation_file.name}</p>
            <p><strong>Basemap:</strong> {'Local Satellite' if use_local_basemap else 'External Satellite'}</p>
            <p><em>Use mouse to navigate: drag to pan, scroll to zoom, right-click to rotate</em></p>
        </div>
        
        <div class="terrain-controls">
            <label>
                Terrain Exaggeration:
                <input type="range" id="exaggeration" min="0.1" max="3" step="0.1" value="1.5">
                <span id="exaggeration-value">1.5x</span>
            </label>
            <label>
                Pitch:
                <input type="range" id="pitch" min="0" max="85" step="5" value="60">
                <span id="pitch-value">60°</span>
            </label>
        </div>
        
        <a href="/atlas/{atlas_name}/map.html" class="back-link">← Back to 2D Map</a>
    </div>

    <script>
        // Initialize the map
        const map = new maplibregl.Map({{
            container: 'map',
            zoom: 12,
            center: [{center_lng}, {center_lat}],
            pitch: 60,
            bearing: 0,
            style: {{
                version: 8,
                sources: {{
                    // Satellite basemap
                    satellite: {{
                        type: 'raster',
                        tiles: ['{basemap_url}'],
                        tileSize: 256,
                        attribution: '&copy; Satellite Imagery',
                        maxzoom: 19
                    }},
                    // Elevation data for terrain
                    elevation: {{
                        type: 'raster-dem',
                        tiles: ['{elevation_url}'],
                        tileSize: 256,
                        maxzoom: 15
                    }}
                }},
                layers: [
                    {{
                        id: 'satellite',
                        type: 'raster',
                        source: 'satellite',
                        paint: {{
                            'raster-opacity': 0.9
                        }}
                    }}
                ],
                terrain: {{
                    source: 'elevation',
                    exaggeration: 1.5
                }}
            }},
            maxZoom: 18,
            maxPitch: 85
        }});

        // Add navigation controls
        map.addControl(new maplibregl.NavigationControl({{
            visualizePitch: true,
            showZoom: true,
            showCompass: true
        }}));

        // Add terrain control
        map.addControl(new maplibregl.TerrainControl({{
            source: 'elevation',
            exaggeration: 1.5
        }}));

        // Handle terrain exaggeration slider
        const exaggerationSlider = document.getElementById('exaggeration');
        const exaggerationValue = document.getElementById('exaggeration-value');
        
        exaggerationSlider.addEventListener('input', (e) => {{
            const value = parseFloat(e.target.value);
            exaggerationValue.textContent = value + 'x';
            map.setTerrain({{
                source: 'elevation',
                exaggeration: value
            }});
        }});

        // Handle pitch slider
        const pitchSlider = document.getElementById('pitch');
        const pitchValue = document.getElementById('pitch-value');
        
        pitchSlider.addEventListener('input', (e) => {{
            const value = parseInt(e.target.value);
            pitchValue.textContent = value + '°';
            map.setPitch(value);
        }});

        // Update sliders when map changes
        map.on('pitch', () => {{
            const pitch = map.getPitch();
            pitchSlider.value = pitch;
            pitchValue.textContent = Math.round(pitch) + '°';
        }});

        // Add some helpful console logging
        map.on('load', () => {{
            console.log('3D Terrain map loaded successfully');
            console.log('Atlas:', '{atlas_name}');
            console.log('Elevation source:', '{elevation_url}');
            console.log('Basemap source:', '{basemap_url}');
        }});

        // Handle errors gracefully
        map.on('error', (e) => {{
            console.error('MapLibre error:', e);
        }});
    </script>
</body>
</html>"""
    
    return html_content


def create_3d_terrain_view(config: Dict[str, Any]) -> Path:
    """
    Create a 3D terrain HTML file for the given atlas.
    
    Args:
        atlas_name: Name of the atlas
        config: Atlas configuration dictionary
        output_path: Optional output path (defaults to outlets/3dview.html)
    
    Returns:
        Path to the generated HTML file
    """
    
    output_path = versioning.atlas_path(config)  / "outlets" / "3dview" /  "index.html"
    #output_path = atlas_path / "outlets" / "3dview" /  "index.html"
    
    
    # Generate HTML content
    html_content = generate_3d_terrain_html(config)
    
    # Write to file
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print(f"3D terrain view generated: {output_path}")
    return output_path


if __name__ == "__main__":
    # Test function - can be run directly for testing
    import sys
    if len(sys.argv) > 1:
        config_path = sys.argv[1]
        config = json.load(open(config_path))
        create_3d_terrain_view(config)
    else:
        print("Usage: python 3dview.py <atlas_name>") 
