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
        self._load(CONFIG_DIR / 'shared_outlets_config.json')

    def test_shared_inlets_config(self):
        self._load(CONFIG_DIR / 'shared_inlets_config.json')

    def test_default_layers(self):
        data = self._load(CONFIG_DIR / 'default_layers.json')
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
        # We only check that referenced layers are in shared default_layers or atlas layers
        default_layers = self._load(CONFIG_DIR / 'default_layers.json')
        all_known = layer_names | set(default_layers.keys())
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


if __name__ == '__main__':
    unittest.main()
