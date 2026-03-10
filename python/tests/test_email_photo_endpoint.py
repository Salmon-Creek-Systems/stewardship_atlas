"""
Tests for POST /ingest/email_photo webapp endpoint.

Mocks out S3, delta writes, and layer refresh so these run locally
without any server infrastructure.
"""
import base64
import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# webapp reads DATASWALE_PATH at import time — set it before importing
os.environ.setdefault("DATASWALE_PATH", "/fake/swales")

from fastapi.testclient import TestClient


# --- Minimal fake atlas config -------------------------------------------------

FAKE_CONFIG = {
    "name": "scvfd",
    "admin_emails": ["crew@scvfd.org"],
    "dataswale": {
        "layers": [
            {"name": "poi"},
            {"name": "hydrants"},
        ]
    },
}

VALID_GPS = {"lat": 38.123, "lon": -122.456, "altitude": 142.0, "make": "Apple"}

VALID_PAYLOAD = {
    "subject": "poi | Locked gate",
    "sender": "crew@scvfd.org",
    "atlas_name": "scvfd",
    "image_data": base64.b64encode(b"fake-image-bytes").decode(),
    "filename": "IMG_001.jpg",
    "received_at": "2024-03-09T14:32:00Z",
}


def make_client():
    """Import webapp and return a TestClient with all external calls patched."""
    from webapp import app
    return TestClient(app, raise_server_exceptions=False)


class TestEmailPhotoEndpoint(unittest.TestCase):

    def setUp(self):
        self.config_patcher = patch("builtins.open", self._fake_open)
        self.config_patcher.start()

        self.swales_patcher = patch("webapp.SWALES_ROOT", "/fake/swales")
        self.swales_patcher.start()

        self.extract_gps_patcher = patch("email_inlet.extract_gps", return_value=VALID_GPS)
        self.extract_gps_patcher.start()

        self.delta_patcher = patch("deltas_geojson.add_deltas_from_features", return_value=["/fake/delta.geojson"])
        self.delta_patcher.start()

        self.refresh_patcher = patch("dataswale_geojson.refresh_vector_layer", return_value=None)
        self.refresh_patcher.start()

        self.s3_patcher = patch("boto3.client")
        mock_boto = self.s3_patcher.start()
        self.mock_s3 = MagicMock()
        mock_boto.return_value = self.mock_s3

        self.env_patcher = patch.dict(os.environ, {"ATLAS_DATA_BUCKET": "atlas-data-test"})
        self.env_patcher.start()

        self.client = make_client()

    def tearDown(self):
        self.config_patcher.stop()
        self.swales_patcher.stop()
        self.extract_gps_patcher.stop()
        self.delta_patcher.stop()
        self.refresh_patcher.stop()
        self.s3_patcher.stop()
        self.env_patcher.stop()

    def _fake_open(self, path, *args, **kwargs):
        """Return fake atlas config JSON for any config path."""
        content = json.dumps(FAKE_CONFIG)
        return io.StringIO(content)

    # --- Happy path ---

    def test_happy_path_returns_ok(self):
        resp = self.client.post("/ingest/email_photo", json=VALID_PAYLOAD)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["layer"], "poi")
        self.assertAlmostEqual(data["lat"], 38.123)
        self.assertAlmostEqual(data["lon"], -122.456)

    def test_image_uploaded_to_s3(self):
        self.client.post("/ingest/email_photo", json=VALID_PAYLOAD)
        self.mock_s3.put_object.assert_called_once()
        call_kwargs = self.mock_s3.put_object.call_args[1]
        self.assertEqual(call_kwargs["Bucket"], "atlas-data-test")
        self.assertIn("scvfd/media/email_photos/", call_kwargs["Key"])
        self.assertIn("IMG_001.jpg", call_kwargs["Key"])

    def test_delta_written(self):
        with patch("deltas_geojson.add_deltas_from_features") as mock_delta:
            mock_delta.return_value = ["/fake/delta.geojson"]
            self.client.post("/ingest/email_photo", json=VALID_PAYLOAD)
            mock_delta.assert_called_once()
            _, kwargs = mock_delta.call_args[0], mock_delta.call_args[1]
            # layer_name kwarg should be "poi"
            self.assertIn("poi", str(mock_delta.call_args))

    def test_layer_refreshed_after_delta(self):
        with patch("dataswale_geojson.refresh_vector_layer") as mock_refresh:
            self.client.post("/ingest/email_photo", json=VALID_PAYLOAD)
            mock_refresh.assert_called_once()

    # --- Sender validation ---

    def test_unknown_sender_rejected(self):
        payload = {**VALID_PAYLOAD, "sender": "stranger@example.com"}
        resp = self.client.post("/ingest/email_photo", json=payload)
        self.assertEqual(resp.status_code, 403)

    def test_sender_case_insensitive(self):
        payload = {**VALID_PAYLOAD, "sender": "CREW@SCVFD.ORG"}
        resp = self.client.post("/ingest/email_photo", json=payload)
        self.assertEqual(resp.status_code, 200)

    def test_sender_with_display_name(self):
        # "Display Name <addr@domain>" format from email clients
        payload = {**VALID_PAYLOAD, "sender": "Crew Member <crew@scvfd.org>"}
        resp = self.client.post("/ingest/email_photo", json=payload)
        self.assertEqual(resp.status_code, 200)

    # --- Layer validation ---

    def test_unknown_layer_rejected(self):
        payload = {**VALID_PAYLOAD, "subject": "nonexistent_layer | Title"}
        resp = self.client.post("/ingest/email_photo", json=payload)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Unknown layer", resp.json()["detail"])

    def test_valid_non_default_layer(self):
        payload = {**VALID_PAYLOAD, "subject": "hydrants | New hydrant"}
        resp = self.client.post("/ingest/email_photo", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["layer"], "hydrants")

    # --- GPS validation ---

    def test_no_gps_rejected(self):
        with patch("email_inlet.extract_gps", return_value=None):
            resp = self.client.post("/ingest/email_photo", json=VALID_PAYLOAD)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("GPS", resp.json()["detail"])

    # --- Missing env var ---

    def test_missing_bucket_env_var(self):
        with patch.dict(os.environ, {}, clear=True):
            # Remove ATLAS_DATA_BUCKET
            env = {k: v for k, v in os.environ.items() if k != "ATLAS_DATA_BUCKET"}
            with patch.dict(os.environ, env, clear=True):
                resp = self.client.post("/ingest/email_photo", json=VALID_PAYLOAD)
        self.assertEqual(resp.status_code, 500)
        self.assertIn("ATLAS_DATA_BUCKET", resp.json()["detail"])


if __name__ == "__main__":
    unittest.main()
