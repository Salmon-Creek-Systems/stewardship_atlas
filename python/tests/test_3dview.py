"""3D view HTML generation: the Animate Camera Route controls."""
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
        self.assertIn('Animate Camera Route', html)
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
        self.assertNotIn('Animate Camera Route', html)
        self.assertNotIn('ROUTE_URLS', html)
        # Share view still works without the route code.
        self.assertIn("typeof routeShareState === 'function'", html)

    def test_placeholders_filled(self):
        html = dview.generate_3d_terrain_html(_config(LAYERS))
        self.assertNotRegex(html, r'__[A-Z_]+__')
        self.assertIn('routeNumber(routeEls.angle, 45, 0, 85)', html)


if __name__ == '__main__':
    unittest.main()
