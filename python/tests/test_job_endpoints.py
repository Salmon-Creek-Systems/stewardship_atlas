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


class TestLayerChanges(JobEndpointTest):
    def test_refresh_of_a_light_asset_is_a_lambda_job_too(self):
        url, body = self.queue_of(self.client.get("/refresh?swale=testatlas&asset=webmap"))
        self.assertEqual((url, body['target']), ('q-jobs', 'lambda'))

    def test_edit_layer_queues_its_changes(self):
        url, body = self.queue_of(self.client.post("/edit_layer/testatlas/roads",
                                                   json={'color': '#ff0000'}))
        self.assertEqual(url, 'q-jobs')
        self.assertEqual(self.status(body['job_id'])['params'],
                         {'layer': 'roads', 'changes': {'color': '#ff0000'}})

    def test_edit_of_an_unknown_layer_is_refused(self):
        self.assertEqual(self.client.post("/edit_layer/testatlas/nope", json={}).status_code, 400)
        self.assertEqual(self.sqs.sent, [])

    def test_add_an_empty_layer(self):
        url, body = self.queue_of(self.client.post(
            "/add_layer/testatlas/hydrants", json={'geometry': 'point', 'source': 'empty'}))
        params = self.status(body['job_id'])['params']
        self.assertEqual((params['layer'], params['source'], params['s3_key']),
                         ('hydrants', 'empty', None))

    def test_delete_needs_the_name_typed_back(self):
        plan = {'blockers': [], 'assets_removed': []}
        with patch('atlas.delete_layer', return_value=plan) as dl:
            self.assertEqual(self.client.post("/delete_layer/testatlas/roads",
                                              json={'confirm': 'nope'}).status_code, 400)
            self.assertEqual(self.client.post("/delete_layer/testatlas/roads",
                                              json={'dry_run': True}).json()['plan'], plan)
            self.assertEqual(self.sqs.sent, [])
            url, _ = self.queue_of(self.client.post("/delete_layer/testatlas/roads",
                                                    json={'confirm': 'roads'}))
        self.assertEqual(url, 'q-jobs')
        self.assertTrue(all(c.kwargs.get('dry_run') for c in dl.call_args_list))

    def test_a_blocked_delete_queues_nothing(self):
        with patch('atlas.delete_layer', return_value={'blockers': ['used by runbook']}):
            self.assertEqual(self.client.post("/delete_layer/testatlas/roads",
                                              json={'confirm': 'roads'}).status_code, 400)
        self.assertEqual(self.sqs.sent, [])


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
