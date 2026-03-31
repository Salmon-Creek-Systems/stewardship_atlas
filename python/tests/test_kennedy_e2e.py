"""
End-to-end read-only tests for a live atlas.

These tests run against the live server and make no writes.
They exercise the public-facing webmap, protected console pages,
layer GeoJSON endpoints, and the log API.

Layer names are derived at runtime from the atlas's atlas_config.json,
so no edits are needed when layers change.

Environment variables:
  ATLAS_NAME        Atlas to test (default: kennedy)
  ATLAS_BASE_URL    Base URL for static files (default: https://fireatlas.org)
  ATLAS_API_URL     Base URL for the webapp API (default: https://fireatlas.org:9000)
  ATLAS_USER        Basic auth username for protected pages
  ATLAS_PASSWORD    Basic auth password for protected pages

Run:
  cd python
  ATLAS_BASE_URL=https://fireatlas.org \
  ATLAS_API_URL=https://fireatlas.org:9000 \
  ATLAS_USER=... ATLAS_PASSWORD=... \
  pytest tests/test_kennedy_e2e.py -v
"""
import os

import pytest
import requests
from playwright.sync_api import Page, expect

ATLAS = os.environ.get("ATLAS_NAME", "kennedy")
BASE_URL = os.environ.get("ATLAS_BASE_URL", "https://fireatlas.org")
API_URL = os.environ.get("ATLAS_API_URL", "https://fireatlas.org:9000")
USER = os.environ.get("ATLAS_USER", "")
PASSWORD = os.environ.get("ATLAS_PASSWORD", "")

STAGING = f"{BASE_URL}/{ATLAS}/staging"
CONSOLE_PAGES = ["admin", "internal"]

RASTER_TYPES = {"raster", "documents"}


def _fetch_vector_layers():
    """Fetch atlas_config.json and return the vector layers listed in the webmap's in_layers."""
    try:
        r = requests.get(f"{STAGING}/atlas_config.json", timeout=10)
        r.raise_for_status()
        config = r.json()
        layers_by_name = {l["name"]: l for l in config["dataswale"]["layers"]}
        webmap_in_layers = config["assets"]["webmap"]["in_layers"]
        return [
            name for name in webmap_in_layers
            if layers_by_name.get(name, {}).get("geometry_type") not in RASTER_TYPES
        ]
    except Exception as e:
        pytest.exit(f"Could not fetch atlas_config.json from {STAGING}: {e}")


VECTOR_LAYERS = _fetch_vector_layers()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def auth_context(browser):
    """Browser context with basic auth credentials pre-configured."""
    ctx = browser.new_context(
        http_credentials={"username": USER, "password": PASSWORD},
    )
    yield ctx
    ctx.close()


@pytest.fixture
def auth_page(auth_context):
    page = auth_context.new_page()
    yield page
    page.close()


@pytest.fixture
def public_page(browser):
    ctx = browser.new_context()
    page = ctx.new_page()
    yield page
    page.close()
    ctx.close()


# ---------------------------------------------------------------------------
# Webmap (public)
# ---------------------------------------------------------------------------

class TestWebmap:
    def test_webmap_loads(self, public_page: Page):
        """Webmap index.html returns 200 and contains a MapLibre canvas."""
        public_page.goto(f"{STAGING}/outlets/webmap/index.html")
        canvas = public_page.locator("#map canvas")
        expect(canvas).to_be_visible(timeout=15_000)

    def test_webmap_no_js_errors(self, public_page: Page):
        """No uncaught JS errors while loading the webmap."""
        errors = []
        public_page.on("pageerror", lambda e: errors.append(str(e)))
        public_page.goto(f"{STAGING}/outlets/webmap/index.html")
        public_page.wait_for_load_state("networkidle", timeout=20_000)
        assert errors == [], f"JS errors on webmap load: {errors}"


# ---------------------------------------------------------------------------
# Console pages (auth-protected)
# ---------------------------------------------------------------------------

class TestConsolePages:
    @pytest.mark.parametrize("console", CONSOLE_PAGES)
    def test_console_loads(self, auth_page: Page, console: str):
        """Each console page loads and initializePage sets the atlas name."""
        url = f"{STAGING}/outlets/html/{console}/index.html"
        auth_page.goto(url)
        swale_name = auth_page.locator("#swale-name")
        expect(swale_name).to_have_text(ATLAS, timeout=10_000)

    @pytest.mark.parametrize("console", CONSOLE_PAGES)
    def test_console_no_js_errors(self, auth_page: Page, console: str):
        errors = []
        auth_page.on("pageerror", lambda e: errors.append(str(e)))
        auth_page.goto(f"{STAGING}/outlets/html/{console}/index.html")
        auth_page.wait_for_load_state("networkidle", timeout=15_000)
        assert errors == [], f"JS errors on {console} console: {errors}"


# ---------------------------------------------------------------------------
# Layer GeoJSON endpoints (public)
# ---------------------------------------------------------------------------

class TestLayerEndpoints:
    @pytest.mark.parametrize("layer", VECTOR_LAYERS)
    def test_layer_geojson_returns_200(self, layer: str):
        url = f"{STAGING}/layers/{layer}/{layer}.geojson"
        r = requests.get(url, timeout=10)
        assert r.status_code == 200, f"{layer} GeoJSON returned {r.status_code}"

    @pytest.mark.parametrize("layer", VECTOR_LAYERS)
    def test_layer_geojson_is_valid(self, layer: str):
        url = f"{STAGING}/layers/{layer}/{layer}.geojson"
        r = requests.get(url, timeout=10)
        data = r.json()
        assert data.get("type") == "FeatureCollection", f"{layer}: unexpected GeoJSON type"
        assert "features" in data, f"{layer}: missing 'features' key"


# ---------------------------------------------------------------------------
# Log API
# ---------------------------------------------------------------------------

class TestLogApi:
    def test_log_endpoint_returns_200(self):
        r = requests.get(f"{API_URL}/log/{ATLAS}", timeout=10)
        assert r.status_code == 200

    def test_log_endpoint_shape(self):
        r = requests.get(f"{API_URL}/log/{ATLAS}", timeout=10)
        data = r.json()
        assert "useCases" in data, f"Unexpected log response shape: {list(data.keys())}"
        assert isinstance(data["useCases"], list)
