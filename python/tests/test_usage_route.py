"""GET /api/usage/{page}: the usage report from the private bucket, scs-admin
only (server env: imports webapp)."""
import os
import sys
import unittest
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.dirname(__file__))
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

os.environ.setdefault("SWALES_ROOT", "/fake/swales")
import atlas_auth  # noqa: E402
import webapp  # noqa: E402
from fake_s3 import FakeS3  # noqa: E402


class TestUsageRoute(unittest.TestCase):
    def setUp(self):
        self.s3 = FakeS3()
        for p in (patch('atlas_store._s3', lambda: self.s3),
                  patch.dict(os.environ, {'ATLAS_PRIVATE_BUCKET': 'privb'})):
            p.start()
            self.addCleanup(p.stop)
        self.client = TestClient(webapp.app, raise_server_exceptions=False)

    def test_index_and_report(self):
        self.s3.put_object(Bucket='privb', Key='usage/index.html', Body=b'<p>index</p>')
        self.s3.put_object(Bucket='privb', Key='usage/report.html', Body=b'<p>report</p>')
        r = self.client.get('/usage/')
        self.assertEqual((r.status_code, r.text), (200, '<p>index</p>'))
        self.assertTrue(r.headers['content-type'].startswith('text/html'))
        self.assertEqual(r.headers['cache-control'], 'no-store')
        self.assertEqual(self.client.get('/usage/report.html').text, '<p>report</p>')

    def test_nothing_else_in_the_bucket_is_served(self):
        self.s3.put_object(Bucket='privb', Key='usage/logs/x.gz', Body=b'secret')
        for path in ('/usage/logs', '/usage/x.gz', '/usage/..%2Fkennedy'):
            self.assertEqual(self.client.get(path).status_code, 404, path)

    def test_before_the_first_run(self):
        self.assertEqual(self.client.get('/usage/').status_code, 404)

    def test_only_scs_admin(self):
        for path in ('/usage/', '/usage/{page}'):
            required = atlas_auth.required_role(path)
            self.assertIsNone(atlas_auth.authorize(required, None, [atlas_auth.SCS_ADMIN_GROUP]))
            self.assertIsNotNone(atlas_auth.authorize(required, None, ['kennedy-admin']))
            self.assertIsNotNone(atlas_auth.authorize(required, None, None))


if __name__ == '__main__':
    unittest.main()
