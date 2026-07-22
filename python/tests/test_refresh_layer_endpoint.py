"""
Tests for the issue #131 webapp wiring:

- POST /delta_upload/{swale}: apply flag (T3) — store-and-refresh by
  default, store-only when apply=False.
- GET /refresh_layer + /refresh-layer-status (T5): background Dagster
  refresh with single-flight guard.
- GET /publish (T4): pure snapshot — no outlet materialization.

Mocks out config load, delta writes, layer refresh, and atlas_dagster so
these run without server infrastructure. Requires fastapi (server env).

The open() fake only intercepts paths under /fake/ and passes everything
else to the real open — a blanket intercept breaks unrelated file reads
(botocore data files, dagster's alembic.ini) elsewhere in the process.
"""
import builtins
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

# webapp reads DATASWALE_PATH at import time — set it before importing.
# Imported once here, outside any patch, so the real import chain
# (boto3 etc.) reads its own files normally.
os.environ.setdefault("DATASWALE_PATH", "/fake/swales")
import webapp


FAKE_CONFIG = {
    "name": "testatlas",
    "dataswale": {
        "layers": [
            {"name": "roads", "geometry_type": "linestring"},
            {"name": "culverts", "geometry_type": "point"},
        ]
    },
    "assets": {"webmap": {"type": "outlet"}},
}

DELTA_PAYLOAD = {
    "data": {
        "layer": "roads",
        "action": "create",
        "type": "FeatureCollection",
        "features": [],
    }
}

FAKE_DELTA_PATH = "/fake/deltas/roads/web__20260715__create.geojson"

_real_open = builtins.open


class KeepOpenStringIO(io.StringIO):
    """StringIO whose contents survive the with-block that wrote them."""
    def close(self):
        pass


class WebappTestCase(unittest.TestCase):

    def setUp(self):
        self.written = {}

        def fake_open(path, *args, **kwargs):
            if not str(path).startswith('/fake/'):
                return _real_open(path, *args, **kwargs)
            mode = args[0] if args else kwargs.get('mode', 'r')
            if 'w' in mode:
                buf = KeepOpenStringIO()
                self.written[str(path)] = buf
                return buf
            return io.StringIO(json.dumps(FAKE_CONFIG))

        for p in [
            patch("builtins.open", fake_open),
            patch("webapp.SWALES_ROOT", "/fake/swales"),
            patch("deltas_geojson.delta_path_from_layer", return_value=FAKE_DELTA_PATH),
            patch("dataswale_geojson.refresh_vector_layer", return_value="/fake/layer.geojson"),
        ]:
            p.start()
            self.addCleanup(p.stop)  # runs even if setUp fails later — no leaks

        # atlas_dagster is imported lazily inside the endpoint; make the
        # import resolve to a mock whether or not dagster is installed.
        self.mock_atlas_dagster = MagicMock()
        self.mock_atlas_dagster.refresh_layer.return_value = MagicMock(success=True)
        self._prior_atlas_dagster = sys.modules.get('atlas_dagster')
        sys.modules['atlas_dagster'] = self.mock_atlas_dagster
        self.addCleanup(self._restore_atlas_dagster)

        # Reset single-flight state between tests.
        webapp.refresh_layer_status.update(
            running=False, swale=None, layer=None, mode=None, cascade=None,
            started_at=None, finished_at=None, success=None, error=None)
        webapp.publish_status.update(
            publishing=False, started_at=None, finished_at=None)
        webapp.materialize_status.update(
            running=False, swale=None, asset=None,
            started_at=None, finished_at=None, success=None, error=None)
        self.client = TestClient(webapp.app, raise_server_exceptions=False)

    def _restore_atlas_dagster(self):
        if self._prior_atlas_dagster is not None:
            sys.modules['atlas_dagster'] = self._prior_atlas_dagster
        else:
            sys.modules.pop('atlas_dagster', None)


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
        stored = json.loads(self.written[FAKE_DELTA_PATH].getvalue())
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
        webapp.refresh_layer_status["running"] = True
        webapp.refresh_layer_status["layer"] = "culverts"
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


class TestMaterializeAssetEndpoint(WebappTestCase):

    def setUp(self):
        super().setUp()
        self.mock_atlas_dagster.materialize_asset.return_value = MagicMock(success=True)

    def test_build_runs_and_records_status(self):
        resp = self.client.get("/materialize_asset?swale=testatlas&asset=webmap")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "started")

        self.mock_atlas_dagster.materialize_asset.assert_called_once()
        args = self.mock_atlas_dagster.materialize_asset.call_args[0]
        self.assertEqual(args[1], "webmap")

        status = self.client.get("/materialize-asset-status").json()
        self.assertFalse(status["running"])
        self.assertTrue(status["success"])

    def test_unknown_asset_rejected(self):
        resp = self.client.get("/materialize_asset?swale=testatlas&asset=nope")
        self.assertEqual(resp.status_code, 400)
        self.mock_atlas_dagster.materialize_asset.assert_not_called()

    def test_single_flight_guard(self):
        webapp.materialize_status["running"] = True
        webapp.materialize_status["asset"] = "runbook"
        resp = self.client.get("/materialize_asset?swale=testatlas&asset=webmap")
        self.assertEqual(resp.json()["status"], "already_running")
        self.assertEqual(resp.json()["asset"], "runbook")
        self.mock_atlas_dagster.materialize_asset.assert_not_called()


class TestPublishIsPureSnapshot(WebappTestCase):

    def test_publish_snapshots_without_materializing(self):
        with patch("atlas.materialize") as mock_mat, \
             patch("versioning.publish_new_version",
                   return_value="/fake/swales/testatlas/2026-07-16") as mock_pub:
            resp = self.client.get("/publish?swale=testatlas")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["status"], "success")
            # Background task ran: snapshot taken, nothing materialized (C8).
            mock_pub.assert_called_once()
            mock_mat.assert_not_called()

        status = self.client.get("/publish-status?swale=testatlas").json()
        self.assertFalse(status.get("publishing"))
