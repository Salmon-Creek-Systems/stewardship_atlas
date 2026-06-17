"""
Validates that all JSON config files parse cleanly and key structural
invariants hold. Runs locally without any server deps.
"""
import json
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
CONFIG_DIR = REPO_ROOT / 'configuration'


class TestJsonConfigs(unittest.TestCase):
    """All config JSON files must parse without error."""

    def _load(self, path):
        with open(path) as f:
            return json.load(f)

    def test_shared_eddies_config(self):
        data = self._load(CONFIG_DIR / 'shared_eddies_config.json')
        # Every entry must have a fetch_type
        for name, entry in data.items():
            self.assertIn('fetch_type', entry, f"{name} missing fetch_type")

    def test_shared_outlets_config(self):
        data = self._load(CONFIG_DIR / 'shared_outlets_config.json')
        for name, entry in data.items():
            # fetch_type may be at top level or nested under 'config' (legacy inconsistency)
            has_ft = 'fetch_type' in entry or 'fetch_type' in entry.get('config', {})
            self.assertTrue(has_ft, f"outlets config '{name}' missing fetch_type")

    def test_shared_inlets_config(self):
        self._load(CONFIG_DIR / 'shared_inlets_config.json')

    def test_shared_layers_config(self):
        data = self._load(CONFIG_DIR / 'shared_layers_config.json')
        for name, layer in data.items():
            self.assertIn('geometry_type', layer, f"layer '{name}' missing geometry_type")

    def test_starter_layers(self):
        data = self._load(CONFIG_DIR / 'starter_layers.json')
        names = [l['name'] for l in data]
        self.assertIn('roads', names)
        self.assertIn('burns', names)
        self.assertIn('processing_sites', names)

    def test_starter_assets_webmap_layers_exist(self):
        """Every layer in starter webmap in_layers must be defined in starter_layers."""
        assets = self._load(CONFIG_DIR / 'starter_assets.json')
        layers = self._load(CONFIG_DIR / 'starter_layers.json')
        layer_names = {l['name'] for l in layers}
        webmap_in_layers = assets['webmap']['in_layers']
        for lname in webmap_in_layers:
            self.assertIn(lname, layer_names,
                f"webmap in_layers references '{lname}' not in starter_layers.json")

    def test_mineral_kinsey_assets_webmap_layers_exist(self):
        """MineralKinsey webmap in_layers must not reference undefined layers."""
        assets = self._load(CONFIG_DIR / 'MineralKinsey_assets.json')
        layers_path = CONFIG_DIR / 'MineralKinsey_layers.json'
        if not layers_path.exists():
            self.skipTest("MineralKinsey_layers.json not found")
        layers = self._load(layers_path)
        layer_names = {l['name'] if isinstance(l, dict) else l for l in layers}
        webmap_in_layers = assets['webmap']['in_layers']
        # We only check that referenced layers are in shared_layers_config or atlas layers
        shared_layers = self._load(CONFIG_DIR / 'shared_layers_config.json')
        all_known = layer_names | set(shared_layers.keys())
        for lname in webmap_in_layers:
            self.assertIn(lname, all_known,
                f"MineralKinsey webmap in_layers references '{lname}' not in any layer config")

    def test_treatments_file(self):
        data = self._load(CONFIG_DIR / 'treatments' / 'default_treatments.json')
        required_fields = ['production_rate', 'biomass_rate', 'biochar_rate',
                           'risk_reduction_rate', 'cost_rate']
        for treatment_name, treatment in data.items():
            if treatment_name.startswith('_'):
                continue  # skip metadata keys like _notes
            for field in required_fields:
                self.assertIn(field, treatment,
                    f"treatment '{treatment_name}' missing '{field}'")

    def test_landfire_fuel_loads(self):
        data = self._load(CONFIG_DIR / 'landfire_evc_fuel_loads.json')
        self.assertIn('_default', data)


CORE_OUTLETS = {'html', 'webmap', 'webedit', 'notebook', '3dview'}


class TestStarterBundles(unittest.TestCase):
    """Single-file starter bundles used by the /create dropdown.

    Each configuration/{key}_starter.json must be a self-contained bundle with
    label/description metadata, layers (dict keyed by name) and assets (dict),
    internally consistent layer references, and the core outlets.
    """

    def _load(self, path):
        with open(path) as f:
            return json.load(f)

    def _starter_files(self):
        files = sorted(CONFIG_DIR.glob('*_starter.json'))
        self.assertTrue(files, "no *_starter.json bundles found")
        return files

    def test_starters_present(self):
        keys = {p.name[:-len('_starter.json')] for p in self._starter_files()}
        for expected in ('simple', 'nature', 'vfd', 'biochar', 'foresthealth'):
            self.assertIn(expected, keys, f"missing starter bundle '{expected}'")

    def test_starter_structure_and_references(self):
        shared_layers = set(self._load(CONFIG_DIR / 'shared_layers_config.json').keys())
        for path in self._starter_files():
            with self.subTest(starter=path.name):
                data = self._load(path)
                self.assertIsInstance(data.get('label'), str, "missing label")
                self.assertIsInstance(data.get('description'), str, "missing description")
                self.assertIsInstance(data.get('layers'), dict, "layers must be a dict")
                self.assertIsInstance(data.get('assets'), dict, "assets must be a dict")

                layer_names = set(data['layers'])
                assets = data['assets']

                # layer_def references must resolve against shared_layers_config
                for lname, ldef in data['layers'].items():
                    if 'layer_def' in ldef:
                        self.assertIn(ldef['layer_def'], shared_layers,
                            f"layer '{lname}' layer_def '{ldef['layer_def']}' not in shared_layers_config")
                    else:
                        self.assertIn('geometry_type', ldef,
                            f"layer '{lname}' has neither layer_def nor geometry_type")

                # Every inlet/eddy out_layer and outlet in_layers must be defined
                for aname, a in assets.items():
                    out_layer = a.get('out_layer')
                    if out_layer is not None:
                        self.assertIn(out_layer, layer_names,
                            f"asset '{aname}' out_layer '{out_layer}' not a defined layer")
                    for lname in a.get('in_layers', []):
                        self.assertIn(lname, layer_names,
                            f"asset '{aname}' in_layers references undefined layer '{lname}'")

                # Core outlets present
                outlet_names = {n for n, a in assets.items() if a.get('type') == 'outlet'}
                self.assertTrue(CORE_OUTLETS.issubset(outlet_names),
                    f"missing core outlets: {CORE_OUTLETS - outlet_names}")


if __name__ == '__main__':
    unittest.main()
