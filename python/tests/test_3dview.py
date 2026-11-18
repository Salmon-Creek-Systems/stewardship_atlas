"""3D view HTML generation: the Animate Camera controls."""
import importlib
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
dview = importlib.import_module('3dview')


def _config(layers, in_layers=None):
    cfg = {
        'name': 'testatlas',
        'dataswale': {
            'bbox': {'west': -123.8, 'east': -123.7, 'south': 39.9, 'north': 40.0},
            'layers': layers,
        },
        'assets': {},
    }
    if in_layers is not None:
        cfg['assets']['3dview'] = {'in_layers': in_layers}
    return cfg


LAYERS = [
    {'name': 'roads', 'geometry_type': 'linestring'},
    {'name': 'evac_route', 'geometry_type': 'linestring'},
    {'name': 'hydrants', 'geometry_type': 'point'},
    {'name': 'parcels', 'geometry_type': 'polygon'},
]


def _route_urls(html):
    m = re.search(r'const ROUTE_URLS = (\{.*?\});', html)
    return json.loads(m.group(1)) if m else None


class TestCameraRoute(unittest.TestCase):

    def test_dropdown_lists_only_line_layers(self):
        html = dview.generate_3d_terrain_html(_config(LAYERS))
        self.assertIn('Animate Camera', html)
        # Only the dropdown row shows until a layer is chosen.
        self.assertIn('<div id="route-details" hidden>', html)
        options = re.findall(r'<option value="([^"]+)">', html)
        self.assertEqual(options, ['roads', 'evac_route'])
        self.assertEqual(_route_urls(html), {
            'roads': '../../layers/roads/roads.geojson',
            'evac_route': '../../layers/evac_route/evac_route.geojson',
        })

    def test_respects_in_layers(self):
        html = dview.generate_3d_terrain_html(_config(LAYERS, in_layers=['evac_route', 'hydrants']))
        self.assertEqual(_route_urls(html), {'evac_route': '../../layers/evac_route/evac_route.geojson'})

    def test_no_line_layers_no_route_panel(self):
        html = dview.generate_3d_terrain_html(_config([{'name': 'hydrants', 'geometry_type': 'point'}]))
        self.assertNotIn('Animate Camera', html)
        self.assertNotIn('ROUTE_URLS', html)
        # Share view still works without the route code.
        self.assertIn("typeof routeShareState === 'function'", html)

    def test_title_uses_display_name(self):
        cfg = _config(LAYERS)
        cfg['description'] = 'Kennedy Fire & Rescue'
        html = dview.generate_3d_terrain_html(cfg)
        self.assertIn('<h3>Kennedy Fire &amp; Rescue 3D View</h3>', html)
        self.assertIn('<title>Kennedy Fire &amp; Rescue 3D View</title>', html)
        # Every panel gets the web map's collapse toggle.
        self.assertEqual(html.count('class="panel-collapse-btn"'), 2)

    def test_placeholders_filled(self):
        html = dview.generate_3d_terrain_html(_config(LAYERS))
        self.assertNotRegex(html, r'__[A-Z_]+__')
        self.assertIn('routeNumber(routeEls.angle, 45, 0, 85)', html)


if __name__ == '__main__':
    unittest.main()


class TestInitialVisibility(unittest.TestCase):
    """Which layers start checked: visible_layers if given, else the name patterns."""

    def _toggles(self, cfg):
        view = cfg['assets'].get('3dview', {})
        return dview._build_vector_overlay(cfg, view.get('in_layers'), view.get('visible_layers'))[2]

    def test_name_patterns_without_visible_layers(self):
        on = {t['ids'][0] for t in self._toggles(_config(LAYERS)) if t['visible']}
        self.assertEqual(on, {'roads-layer'})

    def test_visible_layers_decides_exactly(self):
        cfg = _config(LAYERS)
        cfg['assets']['3dview'] = {'visible_layers': ['parcels', 'hydrants']}
        on = {t['ids'][0] for t in self._toggles(cfg) if t['visible']}
        self.assertEqual(on, {'parcels-layer', 'hydrants-layer'})  # roads no longer on by pattern

    def test_visible_layers_sets_layout_visibility(self):
        cfg = _config(LAYERS)
        cfg['assets']['3dview'] = {'visible_layers': ['parcels']}
        _, layers, _ = dview._build_vector_overlay(cfg, None, ['parcels'])
        vis = {l['id']: l['layout']['visibility'] for l in layers if l['id'].endswith('-layer')}
        self.assertEqual(vis['parcels-layer'], 'visible')
        self.assertEqual(vis['roads-layer'], 'none')

    def test_empty_visible_layers_starts_all_hidden(self):
        cfg = _config(LAYERS)
        cfg['assets']['3dview'] = {'visible_layers': []}
        self.assertFalse(any(t['visible'] for t in self._toggles(cfg)))

    def test_generated_page_reads_visible_layers(self):
        cfg = _config(LAYERS, in_layers=['roads', 'parcels'])
        cfg['assets']['3dview']['visible_layers'] = ['parcels']
        html = dview.generate_3d_terrain_html(cfg)
        self.assertRegex(html, r'"id": "parcels-layer"[^}]*"visibility": "visible"')
