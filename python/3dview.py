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
    Generate a 3D terrain HTML page using MapLibre GL JS + PMTiles.

    Requires terrain_rgb_tiles eddy to have been run first to produce
    layers/terrain_rgb_tiles/terrain_rgb_tiles.pmtiles.
    """

    atlas_name = config['name']
    atlas_path = versioning.atlas_path(config)

    # Verify terrain-RGB PMTiles exists
    terrain_rgb_file = atlas_path / "layers" / "terrain_rgb_tiles" / "terrain_rgb_tiles.pmtiles"
    if not terrain_rgb_file.exists():
        raise FileNotFoundError(
            f"terrain_rgb_tiles.pmtiles not found at {terrain_rgb_file} — "
            "run the terrain_rgb_tiles eddy first"
        )
    terrain_rgb_path = f"/staging/layers/terrain_rgb_tiles/terrain_rgb_tiles.pmtiles"

    # Center from bbox
    bbox = config['dataswale']['bbox']
    center_lng = (bbox['west'] + bbox['east']) / 2
    center_lat = (bbox['south'] + bbox['north']) / 2

    # Generate the HTML content
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <title>3D Terrain - {atlas_name.title()}</title>
    <meta charset='utf-8'>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel='stylesheet' href='https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.css' />
    <script src='https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.js'></script>
    <script src='https://unpkg.com/pmtiles@3/dist/pmtiles.js'></script>
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
            <p><em>Drag to pan · Scroll to zoom · Right-click drag to rotate</em></p>
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

        <a href="../../html/admin" class="back-link">← Back</a>
    </div>

    <script>
        // Register PMTiles protocol
        const protocol = new pmtiles.Protocol();
        maplibregl.addProtocol('pmtiles', protocol.tile.bind(protocol));

        const terrainUrl = 'pmtiles://' + window.location.origin + '{terrain_rgb_path}';

        // Initialize the map
        const map = new maplibregl.Map({{
            container: 'map',
            zoom: 14,
            center: [{center_lng}, {center_lat}],
            pitch: 60,
            bearing: 0,
            style: {{
                version: 8,
                sources: {{
                    satellite: {{
                        type: 'raster',
                        tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}'],
                        tileSize: 256,
                        attribution: '&copy; Esri',
                        maxzoom: 19
                    }},
                    terrain: {{
                        type: 'raster-dem',
                        url: terrainUrl,
                        encoding: 'mapbox',
                        tileSize: 256
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
                    source: 'terrain',
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
            source: 'terrain',
            exaggeration: 1.5
        }}));

        // Handle terrain exaggeration slider
        const exaggerationSlider = document.getElementById('exaggeration');
        const exaggerationValue = document.getElementById('exaggeration-value');

        exaggerationSlider.addEventListener('input', (e) => {{
            const value = parseFloat(e.target.value);
            exaggerationValue.textContent = value + 'x';
            map.setTerrain({{
                source: 'terrain',
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
            console.log('3D Terrain map loaded — atlas: {atlas_name}');
            console.log('Terrain PMTiles:', terrainUrl);
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
    
    output_path = versioning.atlas_path(config) / "outlets" / "3dview" / "index.html"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    
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
