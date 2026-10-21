"""
Test the starter bundles — the templates every /create atlas is born from.

The invariant that matters: a starter may only be allowed to publish once its
public layers say so explicitly. `atlas.py` reads a missing `access` as public,
so an undeclared layer in a published outlet is fail-open; and a layer declared
non-public but referenced by a public outlet routes to the private bucket,
leaving a dead layer on the public map (the shape of kennedy's lidar_basemap).

Adding a `cloud` block to a starter is therefore gated by this test: give one an
allowlist without declaring its layers and the suite fails.

Pure JSON — no markdown, no boto3, no atlas imports. Runs anywhere.
"""
import glob
import json
import os
import unittest
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[2] / 'configuration'

STARTERS = sorted(glob.glob(str(CONFIG / '*_starter.json')))
SHARED_LAYERS = json.loads((CONFIG / 'shared_layers_config.json').read_text())
SHARED_OUTLETS = json.loads((CONFIG / 'shared_outlets_config.json').read_text())


def load(path):
    return json.loads(Path(path).read_text())


def layer_access(starter, name):
    """Declared access for one layer, or None if it says nothing."""
    layer = starter['layers'][name]
    if layer.get('access'):
        return layer['access']
    shared = SHARED_LAYERS.get(layer.get('layer_def', name), {})
    return shared.get('access')


def outlet_access(asset):
    """Declared access for one outlet, or None — which atlas.py reads as public."""
    return asset.get('access')


def allowlist(starter):
    return (starter.get('properties', {}).get('cloud') or {}).get('outlets') or []


class TestEveryStarter(unittest.TestCase):
    def test_allowlisted_names_are_real_outlets(self):
        for path in STARTERS:
            starter = load(path)
            outlets = {n for n, a in starter['assets'].items() if a.get('type') == 'outlet'}
            for name in allowlist(starter):
                with self.subTest(starter=os.path.basename(path), outlet=name):
                    self.assertIn(name, outlets)

    def test_allowlisted_outlets_declare_their_access(self):
        for path in STARTERS:
            starter = load(path)
            for name in allowlist(starter):
                with self.subTest(starter=os.path.basename(path), outlet=name):
                    self.assertIsNotNone(outlet_access(starter['assets'][name]),
                                         f"{name} publishes but declares no access")

    def test_published_public_maps_only_carry_declared_public_layers(self):
        """The gate. A layer on a published public map must say it is public."""
        for path in STARTERS:
            starter = load(path)
            for name in allowlist(starter):
                asset = starter['assets'][name]
                if 'public' not in (outlet_access(asset) or ['public']):
                    continue
                for layer in asset.get('in_layers') or []:
                    with self.subTest(starter=os.path.basename(path),
                                      outlet=name, layer=layer):
                        access = layer_access(starter, layer)
                        self.assertIsNotNone(
                            access, f"{layer} is undeclared and would publish as public")
                        self.assertIn(
                            'public', access,
                            f"{layer} is {access} but sits on the public {name}")

    def test_pdf_outlets_are_never_allowlisted(self):
        """GeoPDF outlets are large, QGIS-only, and admin-tier output."""
        for path in STARTERS:
            starter = load(path)
            for name in allowlist(starter):
                shared = SHARED_OUTLETS.get(starter['assets'][name].get('config_def'), {})
                with self.subTest(starter=os.path.basename(path), outlet=name):
                    self.assertNotEqual(shared.get('data_type'), 'geopdf')


class TestFieldTripStarter(unittest.TestCase):
    def setUp(self):
        self.starter = load(CONFIG / 'fieldtrip_starter.json')

    def test_regions_stays_admin(self):
        # Regions drive runbook pages; they are not public map furniture.
        self.assertEqual(layer_access(self.starter, 'regions'), ['admin'])

    def test_regions_is_not_on_the_webmap(self):
        self.assertNotIn('regions', self.starter['assets']['webmap']['in_layers'])

    def test_runbook_points_at_a_layer_that_exists(self):
        runbook = self.starter['assets']['qgis_runbook']
        self.assertIn(runbook['regions_layer'], self.starter['layers'])

    def test_every_layer_declares_access(self):
        for name in self.starter['layers']:
            with self.subTest(layer=name):
                self.assertIsNotNone(layer_access(self.starter, name))


if __name__ == '__main__':
    unittest.main()
