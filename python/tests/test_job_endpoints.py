"""The job endpoints: /refresh_layer, /materialize_asset, /publish, /refresh
(box assets), and /job-status. Requires fastapi (server env).

The config session and the queue clients are faked; what is checked is that a
request becomes the right job, on the right queue, with a status to poll.
"""
import json
import os
import sys
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.dirname(__file__))

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

os.environ.setdefault("SWALES_ROOT", "/fake/swales")
import webapp  # noqa: E402
from fake_s3 import FakeS3  # noqa: E402

CONFIG = {
    "name": "testatlas",
    "dataswale": {"layers": [{"name": "roads", "geometry_type": "linestring"},
                             {"name": "dem", "geometry_type": "raster"}]},
    "assets": {
        "webmap": {"type": "outlet", "config": {"fetch_type": "webmap"}},
        "runbook": {"type": "outlet", "config": {"fetch_type": "runbook", "runs_on": "box"}},
        "dem": {"type": "inlet", "config": {"out_layer": "dem", "runs_on": "box"}},
    },
}
URLS = {'lambda': 'q-jobs', 'box': 'q-render'}


class FakeSqs:
    def __init__(self):
        self.sent = []

    def send_message(self, QueueUrl, MessageBody):
        self.sent.append((QueueUrl, json.loads(MessageBody)))


class JobEndpointTest(unittest.TestCase):
    def setUp(self):
        self.s3, self.sqs = FakeS3(), FakeSqs()

        @contextmanager
        def config_session(swale, purpose):
            yield SimpleNamespace(config=json.loads(json.dumps(CONFIG)))

        for p in (patch.object(webapp, '_config_session', config_session),
                  patch.object(webapp, '_job_clients',
                               lambda: (self.s3, self.sqs, 'privb', URLS))):
            p.start()
            self.addCleanup(p.stop)
        self.client = TestClient(webapp.app, raise_server_exceptions=False)

    def queue_of(self, resp):
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body['status'], 'started')
        [(url, msg)] = self.sqs.sent
        self.assertEqual(msg, {'atlas': 'testatlas', 'id': body['job_id']})
        return url, body

    def status(self, job_id):
        return self.client.get(f"/job-status?swale=testatlas&id={job_id}").json()


class TestRefreshLayer(JobEndpointTest):
    def test_a_cascade_goes_to_the_box(self):
        url, body = self.queue_of(self.client.get("/refresh_layer?swale=testatlas&layer=roads"))
        self.assertEqual(url, 'q-render')
        job = self.status(body['job_id'])
        self.assertEqual(job['state'], 'queued')
        self.assertEqual(job['params'], {'layer': 'roads', 'mode': 'update', 'cascade': True})

    def test_a_plain_update_runs_on_lambda(self):
        url, _ = self.queue_of(self.client.get(
            "/refresh_layer?swale=testatlas&layer=roads&cascade=false"))
        self.assertEqual(url, 'q-jobs')

    def test_rebuilding_a_heavy_inlets_layer_goes_to_the_box(self):
        url, _ = self.queue_of(self.client.get(
            "/refresh_layer?swale=testatlas&layer=dem&mode=rebuild&cascade=false"))
        self.assertEqual(url, 'q-render')

    def test_bad_requests_queue_nothing(self):
        self.assertEqual(self.client.get(
            "/refresh_layer?swale=testatlas&layer=roads&mode=sideways").status_code, 400)
        self.assertEqual(self.client.get(
            "/refresh_layer?swale=testatlas&layer=nope").status_code, 400)
        self.assertEqual(self.sqs.sent, [])


class TestMaterializeAndRefresh(JobEndpointTest):
    def test_materialize_routes_by_runs_on(self):
        url, _ = self.queue_of(self.client.get("/materialize_asset?swale=testatlas&asset=runbook"))
        self.assertEqual(url, 'q-render')

    def test_unknown_asset(self):
        self.assertEqual(self.client.get(
            "/materialize_asset?swale=testatlas&asset=nope").status_code, 400)

    def test_refresh_of_a_box_asset_is_queued(self):
        url, body = self.queue_of(self.client.get("/refresh?swale=testatlas&asset=runbook"))
        self.assertEqual((url, body['target']), ('q-render', 'box'))


class TestPublishAndStatus(JobEndpointTest):
    def test_publish_is_a_lambda_job(self):
        url, body = self.queue_of(self.client.get("/publish?swale=testatlas"))
        self.assertEqual(url, 'q-jobs')
        self.assertTrue(body['publishing'])
        self.assertEqual(self.status(body['job_id'])['kind'], 'publish')

    def test_unknown_job(self):
        self.assertEqual(self.client.get("/job-status?swale=testatlas&id=nope").status_code, 404)


if __name__ == '__main__':
    unittest.main()
