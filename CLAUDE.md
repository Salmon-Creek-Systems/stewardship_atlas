# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Stewardship Atlas

A geospatial data management system for creating and maintaining fire safety atlases. Each atlas serves a different customer (fire departments, fire safe councils) with customized layers, styling, and outputs.

## Quick Links to Documentation

- **Developer's Guide**: `documents/developers_guide.md` - Configuration, layers, assets, Jupyter workflows
- **Technical Architecture**: `documents/atlas_technical_architecture.md` - Core concepts and implementation details
- **Data Interaction Guide**: `documents/data_interaction_guide.md` - All the ways to view/edit data
- **QGIS Outlets**: `documents/qgis_outlets.md` - PDF generation with QGIS
- **Roadmap**: `documents/roadmap.md` - Strategic project roadmap

## Core Architecture Concepts

The system uses a water/flow metaphor:

| Concept | Description |
|---------|-------------|
| **Dataswale** | Core data store (GeoJSON files organized by layer) |
| **Layer** | A single-typed dataset (points, lines, polygons, or raster) |
| **Delta** | A batch of changes to apply to a layer |
| **Inlet** | Imports external data into layers (OSM, Overture, local files) |
| **Eddy** | Transforms one layer into another (e.g., DEM → contours) |
| **Outlet** | Exports layers to products (webmap, PDF runbook, GeoPackage) |
| **Version** | A snapshot of the dataswale; `staging` is the working copy |

Data flows: `INLETS → LAYERS → (EDDIES →) OUTLETS`

### Asset Materialization

`atlas.materialize(config, asset_name)` routes by `fetch_type` (from the resolved config) to a materializer function. The registry is built at startup by merging `asset_methods` dicts from all modules:

```python
DEFAULT_MATERIALIZERS = (
    outlets.asset_methods | eddies.asset_methods |
    vector_inlets.asset_methods | raster_inlets.asset_methods |
    outlets_qgis_atlas.asset_methods
)
```

**Config resolution**: Each asset in `{atlas}_assets.json` has a `config_def` field that references a template in `shared_*.json`. The shared template is loaded first, then per-asset overrides are applied. The merged result lives in `config['assets'][name]` at runtime. Use `atlas.create_config()` to inspect the merged result.

### Deltas

Delta files are stored at `deltas/{layer_name}/{asset_name}__{timestamp}__{action}.geojson`. Two actions:
- `"create"` — append new features
- `"annotate"` — update properties on existing features

Deltas are applied in timestamp order by `deltas_geojson.apply_deltas()`.

### Versioning

`staging/` is the only editable version. Published versions are immutable snapshots created by `versioning.publish_new_version()`, which copies `staging/` to a timestamped directory and updates the `CURRENT` symlink.

## Project Structure

```
stewardship_atlas/
├── python/              # Core modules
│   ├── atlas.py         # Atlas creation and materialization
│   ├── webapp.py        # FastAPI REST API
│   ├── outlets.py       # Output generators (webmap, HTML, etc.)
│   ├── outlets_qgis.py  # QGIS-based PDF generation
│   ├── vector_inlets.py # Vector data importers
│   ├── raster_inlets.py # Raster data importers
│   ├── eddies.py        # Data transformers
│   ├── dataswale_geojson.py  # Layer read/write operations
│   ├── versioning.py    # Version management and paths
│   └── utils.py         # Shared utilities
├── configuration/       # Atlas-specific configs
│   ├── shared_*.json    # Shared layer/asset/inlet/outlet definitions
│   ├── {atlas}_layers.json   # Per-atlas layer definitions
│   └── {atlas}_assets.json   # Per-atlas asset definitions
├── scripts/             # Build and utility scripts
├── infrastructure/      # AWS CDK deployment
├── documents/           # Documentation
├── notebooks/           # Jupyter notebooks for development
└── templates/           # HTML/JS templates for outlets
```

## Development Environment

### Current Workflow

Development happens in two locations:
1. **Local (Cursor on macOS)**: Edit code, commit to git
2. **Remote server (emacs)**: Pull changes, run/test, sometimes edit directly

**Important**: Most functionality cannot be run locally due to QGIS dependencies and data paths. The deployed server at `/root/swales/` or `/root/swales_dev/` is the primary execution environment.

### Server Paths

On the deployed server:
- Data root: `/root/swales/` (production) or `/root/swales_dev/` (development)
- Shared data: `/root/data/`
- Each atlas lives at: `{data_root}/{atlas_name}/`
- Versions: `{atlas}/staging/`, `{atlas}/CURRENT/` (symlink), `{atlas}/{timestamp}/`

### Web API Key Endpoints

| Endpoint | Purpose |
|----------|---------|
| `POST /delta_upload/{swalename}` | Store a delta; triggers layer refresh |
| `GET /refresh?swale={name}&asset={name}` | Re-materialize a single asset |
| `GET /publish?swale={name}` | Async publish — materializes versioned outlets, creates snapshot, updates `CURRENT` |
| `GET /publish-status?swale={name}` | Poll publish job progress |
| `POST /save_config/{swalename}` | Persist config changes |
| `POST /import_sheet`, `GET /export_gsheet` | Google Sheets import/export |
| `POST /sql_query` | DuckDB query across layers |

### Starting the Webapp

```bash
/usr/bin/python3 /usr/local/bin/uvicorn --port 9000 --host 0.0.0.0 webapp:app \
  --reload --log-level trace \
  --ssl-certfile /etc/letsencrypt/live/{domain}/fullchain.pem \
  --ssl-keyfile /etc/letsencrypt/live/{domain}/privkey.pem
```

## Key Dependencies

### Python (`python/requirements.txt`)

- `geojson`, `shapely` - Geometry handling
- `duckdb` - Spatial queries and joins
- `geopandas` - GeoDataFrame operations
- `requests` - HTTP fetching
- `overpass` - OpenStreetMap queries
- `Pillow` - Image processing (sprites)
- `mercantile` - Tile calculations
- `pystac-client`, `planetary-computer` - Raster data sources

### QGIS (System Dependency)

QGIS with Python bindings is required for PDF generation (`outlets_qgis.py`). On the server, QGIS was installed via system packages (not pip). This is a known pain point—getting PyQGIS working requires careful environment setup.

Key QGIS notes:
- Set `QT_QPA_PLATFORM=offscreen` for headless rendering
- Expect segfaults on exit in offscreen mode (PDFs are complete before this)
- QGIS renders directly from full datasets (no intermediate extraction needed)

### Infrastructure (`infrastructure/requirements.txt`)

- `aws-cdk-lib` - AWS CDK for deployment

## Creating/Configuring Atlases

Atlases are created by combining:
1. A GeoJSON FeatureCollection defining the region and metadata
2. Layer definitions from `configuration/{atlas}_layers.json`
3. Asset definitions from `configuration/{atlas}_assets.json`
4. Shared configs from `configuration/shared_*.json`

### Creating a New Atlas

```python
import atlas
import json

# Load the region GeoJSON (defines bbox and properties like name, logo, admin_emails)
gj = json.load(open("my_region.geojson"))

# Create the atlas
config = atlas.create(
    feature_collection=gj,
    data_root="/root/swales",
    layers_path="configuration/myatlas_layers.json",
    assets_path="configuration/myatlas_assets.json",
    shared_dir=Path("/root/data")
)
```

### Materializing Assets

```python
import atlas
import json

config = json.load(open("/root/swales/myatlas/staging/atlas_config.json"))

# Run an inlet to fetch data
atlas.materialize(config, "dem")

# Run an eddy to transform data
atlas.materialize(config, "gdal_contours")

# Generate an outlet
atlas.materialize(config, "webmap")
```

**Known asset method keys** (from `outlets.asset_methods` in `python/outlets.py`):

| Key | What it generates |
|-----|-------------------|
| `html` | All console HTML variants: technical, admin, internal, public — all four are generated together by `outlet_html`. To regenerate after template changes, run `atlas.materialize(config, "html")`. **Do not try `technical_console`, `admin_console`, etc. — they don't exist.** |
| `webmap` | MapLibre webmap |
| `webmap_private` | Private variant of webmap |
| `webedit` | Web editing interface |
| `runbook` | PDF runbook (non-QGIS) |
| `sqlquery` | SQL query outlet |
| `jupyter_notebook` | Jupyter notebook outlet |
| `config_editor` | Config editor UI |
| `3dview` | 3D terrain view |

## Current Atlases

- `wvfd` / `wvfd_dev` — Westport Volunteer Fire Department. Recent paying contract; significant code, config, and data changes were made here that need to be propagated back to SCVFD.
- `scvfd` — Original and anchor customer. Personal connection (community VFD where the team grew up). No current funding but important — friendly testers and the target for interesting new features. Treat as the reference implementation.
- `MineralKinsey` — Fire safe council
- `kennedy`, `wildwood` — Other customers

Each has corresponding `*_layers.json` and `*_assets.json` in `/configuration`.

## Architecture Notes

**Static-first**: Once generated, outlet files are static and can be viewed offline. The webapp/API is only needed when modifying data or publishing new versions.

**Outlet types customers use directly**:
- `html` — the interactive console customers use day-to-day
- webmap outlets — live data interaction
- `notebook` — advanced code-based interaction

**Actual on-disk layout** (not fully visible in the Python code):
```
/data/swales/
  {atlas}/
    app/              ← git checkout of repo (code + shared config)
    CURRENT -> ...    ← symlink to active published version (atlas root level)
    staging/          ← only editable version
      atlas_config.json
      local -> ...    ← symlink to shared local data
      deltas/, layers/, outlets/
    2026-02-11/       ← immutable published snapshot
      atlas_config.json
      (same subtree as staging)
```

**Deployment history**: Started with shared code + shared service + per-atlas subdomains (cross-site headaches). Moved to fully self-contained per-atlas with own `app/` copy and own service/port (eliminates cross-site but creates propagation pain). Future direction: container image for code, S3 for data, nginx/DNS handles routing — essentially the original model done right.

**S3 status**: Partially underway. Currently the system points at files served from S3; the goal is to make S3 a proper backend in the data access layer. Key wrinkle: current structure relies on symlinks (`local ->`, `CURRENT ->`); S3 doesn't support symlinks natively. The `app/` tree should not go to S3 — it belongs in a container image.

**Logistics eddy**: Exists as a notebook only — not yet a proper eddy in `eddies.py`. Fuel reduction / biochar logistics modeling; commercially interesting.

**Dagster history**: The system was originally orchestrated with Dagster. The uniform asset method signature and `asset_methods` dict are intentional Dagster scaffolding — preserve this pattern. Old integration code exists in the private repo `Salmon-Creek-Systems/internal`, `python/atlas_dagster.py` (check `__init__.py` there too). When Dagster is restored it will coexist with notebooks (exploration/advanced users) and the web console (user-triggered actions) — it handles recurring maintenance and dependency-driven pipeline orchestration.

## Commands

### Run tests (locally, no QGIS required)
```bash
cd python
python -m pytest tests/            # all tests
python -m pytest tests/test_foo.py # single file
```
Tests are unittest-based. Most can run locally without QGIS or server data paths.

### Run end-to-end tests (against live server, read-only)
```bash
cd python
ATLAS_BASE_URL=https://fireatlas.org \
ATLAS_API_URL=https://fireatlas.org:9000 \
ATLAS_USER=... ATLAS_PASSWORD=... \
pytest tests/test_kennedy_e2e.py -v
```
E2e tests use Playwright + requests. Install deps once:
```bash
pip3 install -r requirements-dev.txt --break-system-packages
playwright install chromium
```

### Atlas CLI
```bash
python scripts/build_atlas.py --help
```

## Testing

Tests exist in `python/tests/` but haven't been run recently. The testing strategy going forward:

1. **Priority**: Simple unit tests for core modules first (`utils.py`, `versioning.py`, `deltas_geojson.py`)
2. **Later**: Integration tests, then system-level tests
3. **Framework**: unittest (existing) / pytest (preferred going forward)

## Known Challenges & Gotchas

### Filesystem Complexity

The system relies heavily on:
- Symbolic links (`local/` → shared data, `CURRENT/` → active version)
- Directory creation with specific permissions and `.htpasswd` files
- Path resolution that differs between local repo and deployed server

When debugging path issues, check `versioning.py` for path construction logic.

### Config File Manipulation

Atlas configs are built dynamically from multiple sources. The actual runtime config (`atlas_config.json`) may look different from the template files. Use `atlas.create_config()` to see what the merged config looks like.

### QGIS Environment

QGIS Python bindings are finicky:
- Must be installed at system level (not virtualenv)
- Requires specific environment variables
- The `qgis_init()` function in `outlets_qgis.py` handles initialization

### Local vs Remote Paths

Code references paths like `/root/swales/` which don't exist locally. For local development, you may need to:
- Mock filesystem operations
- Use test fixtures with relative paths
- Work on modules that don't require the full data tree

## Priority Work Areas

These are areas identified for improvement:

1. **Local Development Setup**: Currently can't run much locally. Need better mocking, fixtures, or containerization to enable local development/testing.

2. **QGIS Deployment**: The QGIS installation is manual and fragile. Consider Docker containers or better orchestration tooling.

3. **Atlas Creation Workflow**: The `create()` and `create_config()` functions in `atlas.py` could be improved. Look at making atlas bootstrapping more streamlined.

4. **Testing Infrastructure**: Get unit tests running again. Start with core utilities (`utils.py`, `versioning.py`), then expand to inlets/outlets.

5. **Documentation**: Keep `documents/` up to date as the system evolves.

## Common Operations

### Refresh a layer from deltas
```python
import dataswale_geojson
import deltas_geojson as deltas

dataswale_geojson.refresh_vector_layer(config, 'roads', deltas.apply_deltas)
```

### Read layer data
```python
fc = dataswale_geojson.layer_as_featurecollection(config, 'hydrants')
```

### Generate webmap
```python
atlas.materialize(config, 'webmap')
```

### Publish a version
```python
import versioning
versioning.publish(config)  # Creates timestamped version, updates CURRENT symlink
```

## Git Workflow

1. Edit locally in Cursor
2. Commit and push to origin
3. SSH to server, pull changes
4. Test/run on server
5. (Or: edit on server with emacs, commit, pull locally)

Keep commits focused—the deployed server may have uncommitted local changes.
