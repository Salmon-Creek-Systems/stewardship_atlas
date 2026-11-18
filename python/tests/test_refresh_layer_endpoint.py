"""
Tests for the issue #131 webapp wiring:

- POST /delta_upload/{swale}: apply flag (T3) — store-and-refresh by
  default, store-only when apply=False.
- /refresh_layer, /materialize_asset and /publish became jobs (Phase 4):
  tests/test_job_endpoints.py.

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

# /refresh_layer, /materialize_asset and /publish are jobs now: tests/test_job_endpoints.py
