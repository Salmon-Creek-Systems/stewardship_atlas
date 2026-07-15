"""
Tests for the issue #131 webapp wiring:

- POST /delta_upload/{swale}: apply flag (T3) — store-and-refresh by
  default, store-only when apply=False.
- GET /refresh_layer + /refresh-layer-status (T5): background Dagster
  refresh with single-flight guard.

Mocks out config load, delta writes, layer refresh, and atlas_dagster so
these run without server infrastructure. Requires fastapi (server env).
"""
import io
import json
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient


FAKE_CONFIG = {
    "name": "testatlas",
    "dataswale": {
        "layers": [
            {"name": "roads", "geometry_type": "linestring"},
            {"name": "culverts", "geometry_type": "point"},
        ]
    },
}

DELTA_PAYLOAD = {
    "data": {
        "layer": "roads",
        "action": "create",
        "type": "FeatureCollection",
        "features": [],
    }
}


class KeepOpenStringIO(io.StringIO):
    """StringIO whose contents survive the with-block that wrote them."""
    def close(self):
        pass


class WebappTestCase(unittest.TestCase):

    def setUp(self):
        self.written = {}

        def fake_open(path, *args, **kwargs):
            if 'w' in (args[0] if args else kwargs.get('mode', 'r')):
                buf = KeepOpenStringIO()
                self.written[str(path)] = buf
                return buf
            return io.StringIO(json.dumps(FAKE_CONFIG))

        self.patchers = [
            patch("builtins.open", fake_open),
            patch("webapp.SWALES_ROOT", "/fake/swales"),
            patch("deltas_geojson.delta_path_from_layer",
                  return_value="/fake/deltas/roads/web__20260715__create.geojson"),
            patch("dataswale_geojson.refresh_vector_layer", return_value="/fake/layer.geojson"),
        ]
        for p in self.patchers:
            p.start()

        # atlas_dagster is imported lazily inside the endpoint; make the
        # import resolve to a mock whether or not dagster is installed.
        self.mock_atlas_dagster = MagicMock()
        self.mock_atlas_dagster.refresh_layer.return_value = MagicMock(success=True)
        sys.modules['atlas_dagster'] = self.mock_atlas_dagster

        import webapp
        self.webapp = webapp
        # Reset single-flight state between tests.
        webapp.refresh_layer_status.update(
            running=False, swale=None, layer=None, mode=None, cascade=None,
            started_at=None, finished_at=None, success=None, error=None)
        self.client = TestClient(webapp.app, raise_server_exceptions=False)

    def tearDown(self):
        for p in self.patchers:
            p.stop()
        del sys.modules['atlas_dagster']


class TestDeltaUploadApplyFlag(WebappTestCase):

    def test_default_applies_refresh(self):
        import dataswale_geojson
        resp = self.client.post("/delta_upload/testatlas", json=DELTA_PAYLOAD)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["applied"])
        dataswale_geojson.refresh_vector_layer.assert_called_once()
        args = dataswale_geojson.refresh_vector_layer.call_args[0]
        self.assertEqual(args[1], "roads")

    def test_apply_false_stores_only(self):
        import dataswale_geojson
        payload = {"data": dict(DELTA_PAYLOAD["data"], apply=False)}
        resp = self.client.post("/delta_upload/testatlas", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["applied"])
        dataswale_geojson.refresh_vector_layer.assert_not_called()

    def test_apply_flag_not_persisted_in_delta(self):
        payload = {"data": dict(DELTA_PAYLOAD["data"], apply=False)}
        self.client.post("/delta_upload/testatlas", json=payload)
        stored = json.loads(
            self.written["/fake/deltas/roads/web__20260715__create.geojson"].getvalue())
        self.assertNotIn("apply", stored)
        self.assertEqual(stored["layer"], "roads")


class TestRefreshLayerEndpoint(WebappTestCase):

    def test_update_refresh_runs_and_records_status(self):
        resp = self.client.get("/refresh_layer?swale=testatlas&layer=roads")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "started")

        # TestClient runs background tasks before returning.
        self.mock_atlas_dagster.refresh_layer.assert_called_once()
        _, kwargs = self.mock_atlas_dagster.refresh_layer.call_args
        self.assertEqual(kwargs["mode"], "update")
        self.assertTrue(kwargs["cascade"])

        status = self.client.get("/refresh-layer-status").json()
        self.assertFalse(status["running"])
        self.assertTrue(status["success"])
        self.assertIsNone(status["error"])
        self.assertIsNotNone(status["finished_at"])

    def test_rebuild_no_cascade_params_forwarded(self):
        self.client.get("/refresh_layer?swale=testatlas&layer=roads&mode=rebuild&cascade=false")
        _, kwargs = self.mock_atlas_dagster.refresh_layer.call_args
        self.assertEqual(kwargs["mode"], "rebuild")
        self.assertFalse(kwargs["cascade"])

    def test_unknown_mode_rejected(self):
        resp = self.client.get("/refresh_layer?swale=testatlas&layer=roads&mode=sideways")
        self.assertEqual(resp.status_code, 400)
        self.mock_atlas_dagster.refresh_layer.assert_not_called()

    def test_unknown_layer_rejected(self):
        resp = self.client.get("/refresh_layer?swale=testatlas&layer=nope")
        self.assertEqual(resp.status_code, 400)
        self.mock_atlas_dagster.refresh_layer.assert_not_called()

    def test_single_flight_guard(self):
        self.webapp.refresh_layer_status["running"] = True
        self.webapp.refresh_layer_status["layer"] = "culverts"
        resp = self.client.get("/refresh_layer?swale=testatlas&layer=roads")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "already_running")
        self.assertEqual(resp.json()["layer"], "culverts")
        self.mock_atlas_dagster.refresh_layer.assert_not_called()

    def test_failure_recorded_in_status(self):
        self.mock_atlas_dagster.refresh_layer.side_effect = ValueError("boom")
        self.client.get("/refresh_layer?swale=testatlas&layer=roads")
        status = self.client.get("/refresh-layer-status").json()
        self.assertFalse(status["running"])
        self.assertFalse(status["success"])
        self.assertIn("boom", status["error"])
