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
        for expected in ('simple', 'nature', 'vfd', 'biochar', 'foresthealth', 'fieldtrip'):
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

    def test_starter_config_defs_resolve(self):
        """Every config_def must exist in the shared configs.

        atlas.create_config does all_configs[asset['config_def']], so a typo
        here is a KeyError at atlas-creation time rather than a config error.
        """
        all_configs = set()
        for fname in ('shared_inlets_config.json', 'shared_eddies_config.json',
                      'shared_outlets_config.json'):
            all_configs |= set(self._load(CONFIG_DIR / fname).keys())

        for path in self._starter_files():
            with self.subTest(starter=path.name):
                for aname, a in self._load(path)['assets'].items():
                    if 'config_def' in a:
                        self.assertIn(a['config_def'], all_configs,
                            f"asset '{aname}' config_def '{a['config_def']}' "
                            f"not in any shared config")

    def test_fieldtrip_road_tiers_partition_by_class(self):
        """The three Field Trip road tiers must partition road classes exactly.

        Each tier is the same overture_roads inlet narrowed by an
        alterations.filter rule. An overlap would draw a road twice at two
        widths; a gap would silently drop it from the map.
        """
        data = self._load(CONFIG_DIR / 'fieldtrip_starter.json')
        tiers = {n: a for n, a in data['assets'].items()
                 if n.startswith('public_roads_')}
        self.assertEqual(len(tiers), 3, "expected three road tier inlets")

        # Overture transportation segment class vocabulary, plus None for
        # features that carry no class at all.
        vocabulary = [
            'motorway', 'trunk', 'primary', 'secondary', 'tertiary',
            'unclassified', 'residential', 'living_street', 'service',
            'pedestrian', 'footway', 'path', 'track', 'steps', 'cycleway',
            'bridleway', 'unknown', None,
        ]

        def matches(rule, value):
            op, _field, values = rule
            return value in values if op == 'require' else value not in values

        for klass in vocabulary:
            hits = [a['out_layer'] for a in tiers.values()
                    if all(matches(r, klass) for r in a['alterations']['filter'])]
            self.assertEqual(len(hits), 1,
                f"class {klass!r} lands in {len(hits)} tiers ({hits}), expected exactly 1")

    def _resolved_asset(self, asset, shared):
        """Merge a starter asset over its config_def template.

        Mirrors atlas.create_config: the shared template is the base, then every
        per-asset key overwrites it. Note the overwrite is shallow — a per-asset
        'alterations' REPLACES the template's block rather than merging into it.
        """
        merged = dict(shared.get(asset['config_def'], {})) if 'config_def' in asset else {}
        for key, value in asset.items():
            if key != 'config':
                merged[key] = value
        return merged

    def test_starter_vector_width_layers_get_a_width(self):
        """A linestring layer with vector_width needs its features stamped.

        The webmap paints line-width as ["get", "vector_width"] (outlets.py),
        reading the value off each feature — not off the layer config. If the
        producing inlet contributes no alterations.vector_width, every line in
        the layer renders with a null width.

        Checked against the RESOLVED asset, since most starters inherit the
        width from their config_def template rather than setting it inline.
        """
        shared = {}
        for fname in ('shared_inlets_config.json', 'shared_eddies_config.json',
                      'shared_outlets_config.json'):
            shared.update(self._load(CONFIG_DIR / fname))

        for path in self._starter_files():
            data = self._load(path)
            for lname, ldef in data['layers'].items():
                if not ldef.get('vector_width'):
                    continue
                producers = [a for a in data['assets'].values()
                             if a.get('out_layer') == lname and a.get('type') == 'inlet']
                if not producers:
                    continue  # hand-entry layer; width comes from editable_columns
                for a in producers:
                    with self.subTest(starter=path.name, layer=lname):
                        resolved = self._resolved_asset(a, shared)
                        self.assertIn('vector_width', resolved.get('alterations', {}),
                            f"layer '{lname}' uses vector_width but its inlet "
                            f"contributes no alterations.vector_width")

    def test_fieldtrip_road_tiers_zoom_thresholds_ascend(self):
        """Road tier minzooms must ascend and be distinct, so the fade works.

        Note the thresholds are deliberately high enough that a county-scale
        atlas (which fitBounds opens around z11) starts out showing only the
        primary tier. That was reviewed against ft3 and kept on purpose — do
        not "fix" it by lowering them without asking.
        """
        layers = self._load(CONFIG_DIR / 'fieldtrip_starter.json')['layers']
        tiers = ['roads_primary', 'roads_secondary', 'roads_tertiary']
        minzooms = [layers[t]['vis']['minzoom'] for t in tiers]

        self.assertEqual(minzooms, sorted(minzooms),
            f"tier minzooms must ascend primary->tertiary, got {minzooms}")
        self.assertEqual(len(set(minzooms)), 3,
            f"tiers need distinct minzooms to actually fade, got {minzooms}")
        for t in tiers:
            self.assertGreater(layers[t]['vis']['maxzoom'], max(minzooms),
                f"{t} maxzoom must exceed every tier minzoom")

    def test_starter_paint_zoom_expressions_are_legal(self):
        """["zoom"] may only be the input to a top-level step/interpolate.

        From the MapLibre style spec: "in layout or paint properties, ["zoom"]
        may appear only as the input to an outer interpolate or step
        expression". So ["case", [">=", ["zoom"], ...]] silently breaks the
        style. Guards every paint block in every starter.
        """
        def zoom_positions(node, path='', top=True):
            """Yield the path of every ["zoom"] that is NOT a legal top-level input."""
            if not isinstance(node, list) or not node:
                return
            if node[0] == 'zoom':
                if not top:
                    yield path
                return
            legal_input = node[0] in ('step', 'interpolate', 'interpolate-hcl',
                                      'interpolate-lab')
            for i, child in enumerate(node[1:], start=1):
                # only the operator's input slot may be a bare ["zoom"]
                is_input = legal_input and (i == 1 if node[0] == 'step' else i == 2)
                yield from zoom_positions(child, f'{path}[{i}]', top=is_input)

        for path in self._starter_files():
            data = self._load(path)
            for lname, ldef in data['layers'].items():
                for prop, expr in (ldef.get('paint') or {}).items():
                    with self.subTest(starter=path.name, layer=lname, prop=prop):
                        bad = list(zoom_positions(expr, top=False))
                        self.assertEqual(bad, [],
                            f"{lname}.{prop}: ['zoom'] used outside a top-level "
                            f"step/interpolate input at {bad}")

    def test_fieldtrip_creeks_zoom_bands(self):
        """Creeks fade in by tier within a single layer, mirroring the roads tiers.

        vector_width doubles as the tier (nhd_creeks: perennial 3, ephemeral 2,
        intermittent/artificial 1), so this checks the step expression actually
        gates those tiers at the intended zooms rather than just parsing.
        """
        creeks = self._load(CONFIG_DIR / 'fieldtrip_starter.json')['layers']['creeks']
        expr = creeks['paint']['line-opacity']

        def ev(node, zoom, props):
            if not isinstance(node, list):
                return node
            op = node[0]
            if op == 'zoom':
                return zoom
            if op == 'get':
                return props[node[1]]
            if op == '>=':
                return ev(node[1], zoom, props) >= ev(node[2], zoom, props)
            if op == 'case':
                return (ev(node[2], zoom, props) if ev(node[1], zoom, props)
                        else ev(node[3], zoom, props))
            if op == 'step':
                value, out = ev(node[1], zoom, props), node[2]
                for stop, result in zip(node[3::2], node[4::2]):
                    if value >= stop:
                        out = result
                return ev(out, zoom, props)
            raise AssertionError(f"unhandled op {op!r}")

        # (zoom, tier) -> visible?   tier 3 perennial, 2 ephemeral, 1 intermittent
        cases = {
            (11, 3): 1, (11, 2): 0, (11, 1): 0,   # zoomed out: perennial only
            (13, 3): 1, (13, 2): 1, (13, 1): 0,   # + ephemeral
            (16, 3): 1, (16, 2): 1, (16, 1): 1,   # everything
        }
        for (zoom, tier), expected in cases.items():
            with self.subTest(zoom=zoom, tier=tier):
                self.assertEqual(ev(expr, zoom, {'vector_width': tier}), expected)

        # and nothing at all below the layer's own floor
        self.assertEqual(creeks['vis']['minzoom'], 10)

    def test_fieldtrip_inat_is_filtered(self):
        """Field Trip narrows iNaturalist to recent research-grade observations.

        On ft3's bbox the unfiltered bbox holds 15419 observations; research
        grade cuts it to 7096 and the 2024 start to ~3474. The end of the
        window must stay null: the inlet hands time_range straight to d1/d2 and
        requests drops a None param, so null means "to now" — a literal end date
        would quietly stop including new observations.
        """
        inat = self._load(CONFIG_DIR / 'fieldtrip_starter.json')['assets']['inat_obs']
        self.assertEqual(inat['quality_grade'], 'research')
        start, end = inat['time_range']
        self.assertRegex(start, r'^\d{4}-\d{2}-\d{2}$', "time_range needs a real start date")
        self.assertIsNone(end, "time_range end must be null so the window stays open-ended")

    def test_fieldtrip_threatened_layer(self):
        """The threatened layer keeps the quality bar but drops the recency window.

        460 research-grade threatened observations all-time is already a small
        layer; applying the general layer's 2024 window would cut it to ~211 and
        hide long-standing records of exactly the species worth flagging.
        """
        data = self._load(CONFIG_DIR / 'fieldtrip_starter.json')
        layer = data['layers']['inaturalist_threatened']
        inlet = data['assets']['inat_threatened']

        self.assertEqual(inlet['out_layer'], 'inaturalist_threatened')
        self.assertEqual(inlet['quality_grade'], 'research')
        self.assertEqual(inlet['api_params'], {'threatened': True})
        self.assertNotIn('time_range', inlet, "threatened layer must not be date-limited")

        # same icon as the general layer, different colour
        general = data['layers']['inaturalist']
        self.assertEqual(layer['symbol'], general['symbol'])
        self.assertNotEqual(layer['color'], general['color'])

        self.assertIn('inaturalist_threatened', data['assets']['webmap']['in_layers'])

    def test_starter_alterations_override_keeps_canonicalize(self):
        """An inline alterations override must not drop the template's canonicalize.

        Per-asset overrides replace the whole alterations dict rather than
        deep-merging, so an inline block that omits a canonicalize the template
        provided silently breaks labels (overture_roads maps primary_name ->
        name, which is where road labels come from).
        """
        shared = self._load(CONFIG_DIR / 'shared_inlets_config.json')
        for path in self._starter_files():
            for aname, a in self._load(path)['assets'].items():
                if 'alterations' not in a or 'config_def' not in a:
                    continue
                template = shared.get(a['config_def'], {}).get('alterations', {})
                if 'canonicalize' not in template:
                    continue
                with self.subTest(starter=path.name, asset=aname):
                    self.assertIn('canonicalize', a['alterations'],
                        f"asset '{aname}' overrides alterations but drops the "
                        f"canonicalize from '{a['config_def']}' — labels will break")


if __name__ == '__main__':
    unittest.main()
