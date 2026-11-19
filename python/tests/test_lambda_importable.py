"""The API and jobs Lambdas can import their entry points without QGIS, system
GDAL or Chromium (Phase 4, task 5). Requires the server env (fastapi, dagster).

Each check runs in a fresh interpreter with those packages made unimportable,
which is what the Lambda image is.
"""
import os
import subprocess
import sys
import unittest

import pytest

pytest.importorskip("fastapi")

PY = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

BLOCK = '''
import sys, importlib.abc
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in ('qgis', 'osgeo', 'playwright'):
            raise ImportError(f"{name} is not in the Lambda image")
sys.meta_path.insert(0, Block())
sys.path.insert(0, %r)
''' % PY


def run(code):
    env = dict(os.environ, SWALES_ROOT=os.environ.get('SWALES_ROOT', '/tmp/lambda-import-check'))
    return subprocess.run([sys.executable, '-c', BLOCK + code], capture_output=True, text=True,
                          env=env, timeout=120)


class TestLambdaEntryPoints(unittest.TestCase):
    def check(self, code):
        result = run(code)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])

    def test_the_api_imports(self):
        self.check('import webapp')

    def test_the_jobs_consumer_and_runners_import(self):
        self.check('import atlas_job_consumer, atlas_job_runners, atlas_dagster, versioning')

    def test_a_box_only_materializer_fails_only_when_asked_for(self):
        self.check('''
import atlas
assert 'outlet_runbook_qgis_atlas' in atlas.DEFAULT_MATERIALIZERS
try:
    atlas.DEFAULT_MATERIALIZERS['outlet_runbook_qgis_atlas']
except ImportError:
    pass
else:
    raise SystemExit('expected the QGIS materializer to need qgis')
''')

    def test_a_lazy_materializer_resolves_where_its_library_is(self):
        # Unblocked interpreter: the box. The lazy entry imports and is cached.
        code = ("import sys; sys.path.insert(0, %r); import atlas; "
                "f = atlas.DEFAULT_MATERIALIZERS['outlet_slides']; "
                "assert f is atlas.DEFAULT_MATERIALIZERS['outlet_slides']" % PY)
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                                timeout=120, env=dict(os.environ, SWALES_ROOT='/tmp/x'))
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])


if __name__ == '__main__':
    unittest.main()
