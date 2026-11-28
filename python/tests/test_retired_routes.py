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


class TestNoSlashRedirects(unittest.TestCase):
    """A redirect from the API is absolute to the function URL's host, which
    strands a browser outside CloudFront; a wrong slash must not redirect."""

    def test_a_wrong_slash_is_a_404_not_a_redirect(self):
        from fastapi.testclient import TestClient
        client = TestClient(webapp.app)
        for path in ('/job-status/', '/usage/', '/create-starters/'):
            r = client.get(path, follow_redirects=False)
            self.assertNotIn(r.status_code, (301, 302, 307, 308), path)


if __name__ == '__main__':
    unittest.main()
