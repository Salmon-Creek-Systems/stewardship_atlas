"""Routes retired in Phase 4 stay gone (server env: imports webapp)."""
import os
import sys
import unittest

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
pytest.importorskip("fastapi")
os.environ.setdefault("SWALES_ROOT", "/fake/swales")
import webapp  # noqa: E402


class TestRetiredRoutes(unittest.TestCase):
    def test_gone(self):
        paths = {getattr(r, 'path', None) for r in webapp.app.routes}
        # /files listed a box directory nothing wrote to (#244); /create-status
        # read an in-memory dict, replaced by the create_atlas job's status.
        for path in ('/files', '/create-status'):
            self.assertNotIn(path, paths)
        self.assertIn('/create_atlas', paths)


if __name__ == '__main__':
    unittest.main()
