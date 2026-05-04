#!/usr/bin/env python3
"""
3D Terrain View Generator for Stewardship Atlas
Uses MapLibre GL JS to create 3D terrain visualization from existing elevation data.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple
import versioning
import utils


_VECTOR_INCLUDE_TYPES = {'linestring', 'point', 'polygon'}
_INDEX_SUFFIXES = ('_index',)
_TERRAIN_URL_PLACEHOLDER = '__TERRAIN_URL__'
_GLYPHS_URL = 'https://fonts.undpgeohub.org/fonts/{fontstack}/{range}.pbf'

# Layer names containing any of these substrings start visible; all others start hidden.
_DEFAULT_VISIBLE_PATTERNS = ('road', 'creek', 'stream', 'photo')


def _is_default_visible(name: str) -> bool:
    name_lower = name.lower()
    return any(p in name_lower for p in _DEFAULT_VISIBLE_PATTERNS)


def _build_vector_overlay(config: Dict[str, Any]) -> Tuple[dict, list, list]:
    """
    Build MapLibre sources, layers, and toggle info for non-index vector layers.
    Returns (sources_dict, layers_list, toggle_list).
    Layer order: polygons first, then lines, then points.
    Each toggle_list item: {ids, label, visible} where ids covers geometry + label layers.
    """
    sources = {}
    polygon_layers: List[dict] = []
    line_layers: List[dict] = []
    point_layers: List[dict] = []
    toggle_list: List[dict] = []

    for layer in config['dataswale']['layers']:
        name = layer['name']
        geom_type = layer.get('geometry_type', '')
        if geom_type not in _VECTOR_INCLUDE_TYPES:
            continue
        if any(name.endswith(s) for s in _INDEX_SUFFIXES):
            continue

        visible = _is_default_visible(name)
        visibility = 'visible' if visible else 'none'

        sources[name] = {'type': 'geojson', 'data': f'../../layers/{name}/{name}.geojson'}
        color = utils.rgb_to_css(layer.get('color', [150, 150, 150]))
        layer_id = f'{name}-layer'
        toggle_ids = [layer_id]

        if geom_type == 'polygon':
            fill_color = utils.rgb_to_css(layer.get('fill_color', layer.get('color', [150, 150, 150])))
            polygon_layers.append({
                'id': layer_id, 'type': 'fill', 'source': name,
                'layout': {'visibility': visibility},
                'paint': {
                    'fill-color': fill_color,
                    'fill-opacity': layer.get('fill_opacity', 0.4),
                    'fill-outline-color': color,
                }
            })
            symbol_placement = 'point'
        elif geom_type == 'linestring':
            line_layers.append({
                'id': layer_id, 'type': 'line', 'source': name,
                'layout': {'visibility': visibility},
                'paint': {
                    'line-color': color,
                    'line-width': ['coalesce', ['get', 'vector_width'], 2],
                }
            })
            symbol_placement = 'line'
        elif geom_type == 'point':
            point_layers.append({
                'id': layer_id, 'type': 'circle', 'source': name,
                'layout': {'visibility': visibility},
                'paint': {
                    'circle-color': color,
                    'circle-radius': 5,
                    'circle-stroke-width': 1,
                    'circle-stroke-color': '#ffffff',
                }
            })
            symbol_placement = 'point'

        # Add label layer for layers that opt in and don't use sprites
        if layer.get('add_labels') and not layer.get('symbol'):
            label_id = f'{name}-label-layer'
            label_layer = {
                'id': label_id,
                'type': 'symbol',
                'source': name,
                'minzoom': layer.get('label_minzoom', 13),
                'layout': {
                    'visibility': visibility,
                    'symbol-placement': symbol_placement,
                    'text-field': ['get', 'name'],
                    'text-font': ['Open Sans Regular'],
                    'text-size': 12,
                },
                'paint': {
                    'text-color': '#ffffff',
                    'text-halo-color': 'rgba(0,0,0,0.7)',
                    'text-halo-width': 2,
                }
            }
            if 'label_maxzoom' in layer:
                label_layer['maxzoom'] = layer['label_maxzoom']
            # Append label after the geometry layer bucket it belongs to
            if geom_type == 'polygon':
                polygon_layers.append(label_layer)
            elif geom_type == 'linestring':
                line_layers.append(label_layer)
            else:
                point_layers.append(label_layer)
            toggle_ids.append(label_id)

        toggle_list.append({
            'ids': toggle_ids,
            'label': name.replace('_', ' ').title(),
            'visible': visible,
        })

    return sources, polygon_layers + line_layers + point_layers, toggle_list


def _build_toggle_panel_html(toggle_list: list) -> str:
    if not toggle_list:
        return ''
    lines = [
        '<div class="layer-panel">',
        '    <div class="layer-panel-header" onclick="this.parentElement.classList.toggle(\'collapsed\')">',
        '        Layers <span class="layer-panel-arrow">&#x25be;</span>',
        '    </div>',
        '    <div class="layer-panel-list">',
    ]
    for t in toggle_list:
        ids_attr = ','.join(t['ids'])
        checked = ' checked' if t['visible'] else ''
        lines.append(
            f'        <label>'
            f'<input type="checkbox" data-layers="{ids_attr}"{checked}> '
            f'{t["label"]}</label>'
        )
    lines += ['    </div>', '</div>']
    return '\n'.join(lines)


def generate_3d_terrain_html(config: Dict[str, Any]) -> str:
    """
    Generate a 3D terrain HTML page using MapLibre GL JS + PMTiles.

    Requires terrain_rgb_tiles eddy to have been run first to produce
    layers/terrain_rgb_tiles/terrain_rgb_tiles.pmtiles.
    """
    atlas_name = config['name']
    atlas_path = versioning.atlas_path(config)

    layers_dict = {l['name']: l for l in config['dataswale']['layers']}
    terrain_layer_config = layers_dict.get('terrain_rgb_tiles', {})

    if terrain_layer_config.get('cog'):
        s3_bucket = terrain_layer_config.get('cog_s3_bucket', 'scs-atlas-data')
        s3_region = terrain_layer_config.get('cog_s3_region', 'us-east-1')
        terrain_cog_url = (f"https://{s3_bucket}.s3.{s3_region}.amazonaws.com"
                           f"/{atlas_name}/rasters/terrain_rgb_tiles/terrain_rgb_tiles.cog.tif")
        terrain_url_js_line = f"const terrainUrl = 'cog://{terrain_cog_url}';"
    else:
        terrain_rgb_file = atlas_path / "layers" / "terrain_rgb_tiles" / "terrain_rgb_tiles.pmtiles"
        if not terrain_rgb_file.exists():
            raise FileNotFoundError(
                f"terrain_rgb_tiles.pmtiles not found at {terrain_rgb_file} — "
                "run the terrain_rgb_tiles eddy first"
            )
        terrain_rgb_rel_path = "../../layers/terrain_rgb_tiles/terrain_rgb_tiles.pmtiles"
        terrain_url_js_line = (
            f"const terrainUrl = 'pmtiles://' + "
            f"new URL('{terrain_rgb_rel_path}', window.location.href).href;"
        )

    bbox = config['dataswale']['bbox']
    center_lng = (bbox['west'] + bbox['east']) / 2
    center_lat = (bbox['south'] + bbox['north']) / 2

    vector_sources, vector_layers, toggle_list = _build_vector_overlay(config)
    toggle_panel_html = _build_toggle_panel_html(toggle_list)

    style = {
        "version": 8,
        "glyphs": _GLYPHS_URL,
        "sources": {
            "satellite": {
                "type": "raster",
                "tiles": ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
                "tileSize": 256,
                "attribution": "&copy; Esri",
                "maxzoom": 19,
            },
            "terrain": {
                "type": "raster-dem",
                "url": _TERRAIN_URL_PLACEHOLDER,
                "encoding": "mapbox",
                "tileSize": 256,
            },
            **vector_sources,
        },
        "layers": [
            {"id": "satellite", "type": "raster", "source": "satellite",
             "paint": {"raster-opacity": 1.0}},
            *vector_layers,
        ],
        "terrain": {"source": "terrain", "exaggeration": 1.5},
    }
    style_json = json.dumps(style, indent=4).replace(
        f'"{_TERRAIN_URL_PLACEHOLDER}"', 'terrainUrl'
    )

    layer_toggle_js = ""
    if toggle_list:
        layer_toggle_js = """
        map.on('load', () => {
            document.querySelectorAll('.layer-panel-list input[type=checkbox]').forEach(cb => {
                cb.addEventListener('change', e => {
                    const vis = e.target.checked ? 'visible' : 'none';
                    e.target.dataset.layers.split(',').forEach(id => {
                        map.setLayoutProperty(id, 'visibility', vis);
                    });
                });
            });
        });"""

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <title>3D Terrain - {atlas_name.title()}</title>
    <meta charset='utf-8'>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel='stylesheet' href='https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.css' />
    <script src='https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.js'></script>
    <script src='https://unpkg.com/pmtiles@3/dist/pmtiles.js'></script>
    <script src='https://unpkg.com/@geomatico/maplibre-cog-protocol/dist/index.js'></script>
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
        .layer-panel {{
            position: absolute;
            bottom: 10px;
            left: 10px;
            background: rgba(255, 255, 255, 0.9);
            padding: 10px;
            border-radius: 5px;
            z-index: 1000;
            max-width: 200px;
        }}
        .layer-panel-header {{
            cursor: pointer;
            font-weight: bold;
            font-size: 14px;
            margin-bottom: 6px;
            user-select: none;
        }}
        .layer-panel-arrow {{
            display: inline-block;
            transition: transform 0.15s;
        }}
        .layer-panel.collapsed .layer-panel-list {{
            display: none;
        }}
        .layer-panel.collapsed .layer-panel-arrow {{
            transform: rotate(-90deg);
        }}
        .layer-panel-list {{
            max-height: 300px;
            overflow-y: auto;
        }}
        .layer-panel-list label {{
            display: block;
            margin: 4px 0;
            font-size: 13px;
            cursor: pointer;
        }}
        .layer-panel-list input {{
            margin-right: 5px;
            cursor: pointer;
        }}
        .back-link {{
            position: absolute;
            bottom: 10px;
            right: 10px;
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
            <p><em>Drag to pan &middot; Scroll to zoom &middot; Right-click drag to rotate</em></p>
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
                <span id="pitch-value">60&deg;</span>
            </label>
        </div>

        {toggle_panel_html}

        <a href="../../html/admin" class="back-link">&larr; Back</a>
    </div>

    <script>
        const protocol = new pmtiles.Protocol();
        maplibregl.addProtocol('pmtiles', protocol.tile.bind(protocol));
        maplibregl.addProtocol('cog', MaplibreCOGProtocol.cogProtocol);

        {terrain_url_js_line}

        const map = new maplibregl.Map({{
            container: 'map',
            zoom: 14,
            center: [{center_lng}, {center_lat}],
            pitch: 60,
            bearing: 0,
            style: {style_json},
            maxZoom: 18,
            maxPitch: 85
        }});

        map.addControl(new maplibregl.NavigationControl({{
            visualizePitch: true,
            showZoom: true,
            showCompass: true
        }}));

        map.addControl(new maplibregl.TerrainControl({{
            source: 'terrain',
            exaggeration: 1.5
        }}));

        const exaggerationSlider = document.getElementById('exaggeration');
        const exaggerationValue = document.getElementById('exaggeration-value');
        exaggerationSlider.addEventListener('input', (e) => {{
            const value = parseFloat(e.target.value);
            exaggerationValue.textContent = value + 'x';
            map.setTerrain({{source: 'terrain', exaggeration: value}});
        }});

        const pitchSlider = document.getElementById('pitch');
        const pitchValue = document.getElementById('pitch-value');
        pitchSlider.addEventListener('input', (e) => {{
            const value = parseInt(e.target.value);
            pitchValue.textContent = value + '°';
            map.setPitch(value);
        }});

        map.on('pitch', () => {{
            const pitch = map.getPitch();
            pitchSlider.value = pitch;
            pitchValue.textContent = Math.round(pitch) + '°';
        }});
        {layer_toggle_js}
        map.on('load', () => {{
            console.log('3D Terrain map loaded — atlas: {atlas_name}');
            console.log('Terrain PMTiles:', terrainUrl);
        }});

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
    Returns the path to the generated HTML file.
    """
    output_path = versioning.atlas_path(config) / "outlets" / "3dview" / "index.html"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    html_content = generate_3d_terrain_html(config)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)

    print(f"3D terrain view generated: {output_path}")
    return output_path


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        config_path = sys.argv[1]
        config = json.load(open(config_path))
        create_3d_terrain_view(config)
    else:
        print("Usage: python 3dview.py <config_path>")
