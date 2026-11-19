"""Lambda-safe materializers (Phase 4, task 5).

The API and jobs Lambdas have no QGIS, no system GDAL and no Chromium. Two
rules keep that safe, and both are checked here from source (ast), so the
test runs where those libraries are absent too:

1. ``atlas.LAZY_MATERIALIZER_MODULES`` lists exactly the fetch types each lazy
   module registers in its ``asset_methods`` — the table cannot drift.
2. Every asset whose materializer needs a box-only library — a lazy module, or
   a function that imports osgeo/qgis/playwright — resolves to
   ``runs_on: box``, in the shared templates and in every active atlas. That
   is what stops ``atlas_jobs.route`` from sending it to a Lambda.
"""
import ast
import json
import sys
import unittest
from pathlib import Path

PY = Path(__file__).parent.parent
CONFIG = PY.parent / 'configuration'
sys.path.insert(0, str(PY))
import config_checks  # noqa: E402

BOX_ONLY_IMPORTS = ('osgeo', 'qgis', 'playwright')
EAGER_MODULES = ('outlets', 'eddies', 'vector_inlets', 'raster_inlets')


def tree(module):
    return ast.parse((PY / f'{module}.py').read_text())


def literal_assignment(module, name):
    for node in tree(module).body:
        if isinstance(node, ast.Assign) and any(getattr(t, 'id', None) == name for t in node.targets):
            return node.value
    raise LookupError(f'{module}.{name}')


def asset_methods(module):
    """{fetch_type: function name} from a module's ``asset_methods`` dict literal."""
    value = literal_assignment(module, 'asset_methods')
    out = {}
    for k, v in zip(value.keys, value.values):
        if isinstance(k, ast.Constant):
            out[k.value] = getattr(v, 'id', None) or getattr(v, 'attr', None)
    return out


def imports_box_only(node):
    for n in ast.walk(node):
        names = []
        if isinstance(n, ast.Import):
            names = [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module:
            names = [n.module]
        if any(x.split('.')[0] in BOX_ONLY_IMPORTS for x in names):
            return True
    return False


def lazy_table():
    return ast.literal_eval(literal_assignment('atlas', 'LAZY_MATERIALIZER_MODULES'))


def box_only_fetch_types():
    found = set(lazy_table())
    for module in EAGER_MODULES:
        t = tree(module)
        top_level = any(imports_box_only(n) for n in t.body
                        if isinstance(n, (ast.Import, ast.ImportFrom)))
        assert not top_level, f"{module} imports a box-only library at top level"
        functions = {n.name: n for n in t.body if isinstance(n, ast.FunctionDef)}
        for fetch_type, fn in asset_methods(module).items():
            if fn in functions and imports_box_only(functions[fn]):
                found.add(fetch_type)
    return found


def load(name):
    return json.loads((CONFIG / name).read_text())


def shared_templates():
    out = {}
    for kind in ('inlets', 'eddies', 'outlets'):
        out.update(load(f'shared_{kind}_config.json'))
    return out


class TestLazyTable(unittest.TestCase):
    def test_matches_each_modules_asset_methods(self):
        table = lazy_table()
        for module in set(table.values()):
            self.assertEqual({k for k, m in table.items() if m == module},
                             set(asset_methods(module)), module)

    def test_eager_modules_import_nothing_box_only_at_top_level(self):
        box_only_fetch_types()        # asserts as it goes

    def test_gdal_eddies_are_found(self):
        self.assertTrue({'derived_hillshade', 'gdal_contours', 'outlet_runbook_qgis_atlas',
                         'outlet_slides'} <= box_only_fetch_types())


class TestBoxOnlyAssetsRunOnTheBox(unittest.TestCase):
    def setUp(self):
        self.box_only = box_only_fetch_types()
        self.templates = shared_templates()

    def test_shared_templates(self):
        wrong = [name for name, t in self.templates.items()
                 if t.get('fetch_type') in self.box_only and t.get('runs_on') != 'box']
        self.assertEqual(wrong, [])

    def test_active_atlases(self):
        wrong = []
        for atlas in config_checks.ACTIVE_ATLASES:
            path = CONFIG / f'{atlas}.geojson'
            if not path.exists():
                continue
            props = json.loads(path.read_text())['features'][0]['properties']
            for name, asset in (props.get('assets') or {}).items():
                template = self.templates.get(asset.get('config_def')) or {}
                fetch_type = asset.get('fetch_type') or template.get('fetch_type')
                runs_on = asset.get('runs_on') or template.get('runs_on') or 'lambda'
                if fetch_type in self.box_only and runs_on != 'box':
                    wrong.append(f'{atlas}:{name}')
        self.assertEqual(wrong, [])


if __name__ == '__main__':
    unittest.main()
