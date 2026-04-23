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
│   ├── outlets_qgis.py  # QGIS PDF rendering helpers (not the materializer entry point)
│   ├── outlets_qgis_atlas.py  # QGIS materializer entry point — registers asset_methods
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

### Server Safety Rules

**Never silently switch the server to an unrelated or old branch to access a script.** This is what the server-branch rules are protecting against: a prior incident where Claude checked out an old branch to reach a script that wasn't on main, and in doing so silently replaced live service files (`webapp.py` etc.) with stale versions. The five minutes to get the script onto main properly is never more expensive than that risk.

**If a script you need isn't on main, get it onto main first** (cherry-pick or merge) before running it on the server. Don't route around it.

**Temporarily checking out a feature branch for intentional testing is fine**, as long as it's deliberate and you switch back when done. The risk is silent, unintended branch switches — not intentional testing checkouts.

**Long-term goal: a proper staging environment** so testing doesn't require touching the production server at all. Not yet in place — work around it carefully for now.

**Be explicit about S3 trigger scope.** Any write to a watched prefix fires Lambda. Before writing a handler that moves files within the same bucket, check whether the destination prefix is also watched.

### Pre-Deploy Checklist for Lambda/S3 Changes

Before deploying any change to a Lambda handler or S3 trigger configuration, explicitly answer these questions:

1. **What prefixes does this Lambda watch?** (Check the CDK stack notification filter)
2. **Does the handler write to the same bucket?** (copies, moves, quarantine operations)
3. **Does any write destination fall under a watched prefix?** If yes, that write will re-trigger the Lambda — is that intentional?
4. **What happens if the Lambda is triggered on its own output?** Trace the execution path to confirm it terminates.

### Code Conventions

**Shared helpers go in `utils.py`.** When a utility function is needed in both a `python/` module and a `scripts/` script, put it in `utils.py` once and import it everywhere. Scripts use `sys.path.insert(0, str(Path(__file__).parent.parent / "python"))` — see `build_atlas.py`, `generate_attributions.py`, `ingest_s3_photos.py` for the established pattern.

**Additive changes should be additive.** When asked to add a new field or structure, preserve everything that was already there unless explicitly told to remove it.

### Current Workflow

Development happens in two locations:
1. **Local (Claude Code on macOS)**: Edit code, commit to git
2. **Remote server (emacs)**: Pull changes, run/test, sometimes edit directly

**Important**: Most functionality cannot be run locally due to QGIS dependencies and data paths. The deployed server at `/root/swales/` or `/root/swales_dev/` is the primary execution environment.

### Service Operation

Screen session names on the server:
```bash
screen -r app_{atlas}      # webapp
screen -r jupyter_{atlas}  # jupyter
```

Start unified webapp:
```bash
cd /root/swales_dev/app/python
SWALES_ROOT=/root/swales_dev ATLAS_DATA_BUCKET=scs-atlas-data \
uvicorn --port 9000 --host 0.0.0.0 webapp:app --reload \
  --ssl-certfile /etc/letsencrypt/live/fireatlas.org-0001/fullchain.pem \
  --ssl-keyfile /etc/letsencrypt/live/fireatlas.org-0001/privkey.pem
```

**`--reload` has a race condition.** If the service was mid-flight during a `git pull`, `--reload` may not pick up the new code. Hard restart (kill screen session + relaunch) is required to guarantee new code is running.

**Ghostty + screen**: The server doesn't have Ghostty's terminfo. Run `TERM=xterm-256color screen` or add `export TERM=xterm-256color` to `~/.bashrc` on the server.

Start Jupyter:
```bash
cd /root/swales_dev/{atlas}/staging/outlets/notebook
jupyter notebook --config /root/swales_dev/{atlas}/staging/outlets/notebook/jupyter_notebook_config.py \
  --debug --allow-root --port {jupyter_port}
```
The `jupyter_notebook_config.py` must exist with SSL cert paths — Jupyter doesn't support SSL cert paths on the CLI.

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

### Git Workflow

1. Edit locally or on server
2. Commit; push when the server needs to see the changes or at the end of a meaningful working block
3. SSH to server, pull changes
4. Test/run on server

Keep commits focused — the deployed server may have uncommitted local changes.

**Plan lifecycle:** When a plan file is complete, move it to `~/.claude/completed_plans/` rather than deleting it.

## Email Inlet Pipeline

Pipeline: `scvfd@fireatlas.org` → SES → S3 `atlas-ingress/incoming/` → Lambda `atlas-email-photo-handler` → POST `/ingest/email_photo` on webapp → delta written → layer refreshed

### Subject Line Format

`<layer>: <title>` — e.g. `hydrants: New hydrant on Miller Rd`

If no colon present, the whole subject is used as the title and the layer defaults to `processing_sites`. Override the default per-atlas with `email_photo_default_layer` in the atlas GeoJSON.

### Authorized Senders

`admin_emails` in atlas config controls who can submit:
- **Non-empty list** → only those addresses accepted (403 otherwise, no bounce)
- **Empty list `[]`** → open ingest mode (all senders accepted)
- **Missing entirely** → treated as non-empty (safe default, allowlist behavior)

Add new senders to `{atlas}.geojson`, commit, pull on server, rebuild config.

### GPS Requirement

Photos must have GPS EXIF data. If missing, webapp returns 400. Bounce emails are currently **disabled** (see Pending Work). Users currently get no feedback when GPS is missing.

To verify GPS before sending: open photo in iOS Photos → swipe up → if a map thumbnail appears, GPS is present. iOS: Settings → Privacy → Location Services → Camera → While Using.

### SES Status

SES sandbox mode is still active (production access requested 2026-03-17). In sandbox mode, bounces fail with AccessDenied on non-verified recipients.

### Operational Scripts

- `scripts/trace_email.py` — trace recent invocations through SES/S3/Lambda/webapp
- `scripts/reprocess_email.py --list` — show emails stuck in ingress bucket
- `scripts/reprocess_email.py --inspect <key>` — show sender, subject, GPS status
- `scripts/reprocess_email.py <key>` — retrigger Lambda by copying S3 object with metadata change

Issues #70 (combine trace/reprocess) and #71 (unified logging, re-enable bounces) cover planned improvements.

### SES Auto-Registration

`/create_atlas` automatically appends `{slug}@fireatlas.org` to the `atlas-email-inlet` receipt rule set when a new atlas is created. EC2 role needs `ses:DescribeReceiptRuleSet` + `ses:UpdateReceiptRule` (in CDK stack).

### Technical Console Log

`/log/{swalename}` endpoint queries CloudWatch for recent Lambda invocations. Technical console fetches this live on page load. Implemented in `python/atlas_logs.py`.

## AWS Infrastructure

### Accounts and Profiles

- **Atlas account**: 438886543302, profile name `atlas` in `~/.aws/credentials`
- **Personal account**: default profile — has Route 53 for fireatlas.org DNS only; nothing else relevant

### CDK Stack

Stack in `infrastructure/cdk/`. Deploy with:
```bash
cd infrastructure/cdk && npx aws-cdk deploy AtlasEmailPhotoStack --profile atlas
```
(`cdk` is not on PATH — use `npx aws-cdk`.)

Security-sensitive CDK changes (IAM/bucket policy) require `--require-approval broadening` or the deploy will fail non-interactively.

### Lambda Dependency Bundling

No Docker available locally, so CDK `BundlingOptions` doesn't work. Pre-build deps manually:
```bash
pip3 install -r infrastructure/lambda/requirements.txt -t infrastructure/lambda_build/
cp infrastructure/lambda/email_photo_handler.py infrastructure/lambda_build/
```
Then `cdk deploy`. The `lambda_build/` dir is gitignored.

### IAM Summary

EC2 role (`atlas-webapp-ec2-role` / `atlas-webapp-instance-profile`, attached to i-088b520a5a06d51ac us-west-1):
- S3 PutObject on `scs-atlas-data`
- CloudWatch `logs:DescribeLogStreams` + `logs:GetLogEvents` on Lambda log group
- SES `ses:SendEmail` + `ses:SendRawEmail`
- SES `ses:DescribeReceiptRuleSet` + `ses:UpdateReceiptRule`

Lambda role: S3 GetObject + DeleteObject on `atlas-ingress`.

### S3 Buckets

- **`atlas-ingress`**: raw SES emails, 30-day lifecycle, Lambda triggered on OBJECT_CREATED for `incoming/` prefix; quarantine at `quarantine/no-gps/` (not under watched prefix)
- **`scs-atlas-data`**: permanent photo storage; `*/media/email_photos/*` is public-readable; upload uses `ContentType` + `ContentDisposition: inline` so images display in browser

### Route 53

DNS records for fireatlas.org are in the **personal** AWS account, not the atlas account.

## PMTiles and Terrain

PMTiles is the chosen tile format — single file, S3-compatible range requests, no tile server needed, MapLibre native support via `pmtiles://` protocol. Pre-generate on publish, serve static from nginx/S3. No runtime compute.

Two eddies in `eddies.py`:
- `hillshade_tiles` — raster basemap PMTiles
- `terrain_rgb_tiles` — Mapbox terrain-RGB for 3D view

`pip3 install pmtiles` is the only new server dependency (rasterio + gdal2tiles already present).

### Terrain-RGB Encoding

Mapbox format: `height = -10000 + (R*65536 + G*256 + B) * 0.1`

`(0,0,0)` = −10,000m. Any pixel written as zero becomes a 10km-deep pit on the map. Nodata must be handled before encoding. OpenTopography COP30 sometimes has coverage gaps written as exactly `0.0` with `nodata=None` — treat `elevation == 0.0` as nodata for mountainous atlas regions where genuine sea-level elevation won't appear.

### Tile Artifacts and Auto Min-Zoom

`gdal2tiles.py` fills tile area outside the raster extent with zeros (= −10km terrain pits). For a small atlas, a low-zoom tile covers a huge area — mostly zeros.

Fix: `_auto_min_zoom()` raises min_zoom until tiles are at least ~25% covered by the DEM:
- Formula: `ceil(log2(90 / dem_width_degrees))`
- 100-acre atlas → z12; county-scale → z8

Even with correct min_zoom, partial tiles at DEM edges still have zero-fill. `terrain_dem_inlet` in `raster_inlets.py` fetches COP30 for the union bbox of all tiles at min_zoom that cover the atlas — terrain-RGB then perfectly fills the tiles.

### 3D Geometry Source

For 3D terrain geometry (not hillshade), a global tile service beats local PMTiles — our atlas PMTiles sit in correct terrain but the surrounding world has a sharp edge. AWS Open Data terrain (Terrarium encoding, free, global) is seamless. Our own PMTiles are valuable for the 2D hillshade basemap only.

### PMTiles Technical Notes

- PMTiles URL must be absolute for HTTP range requests. Use `new URL(relPath, window.location.href).href` — works from staging, CURRENT, or any published version.
- `pmtiles.writer.Writer.write_tile(tile_id, data)` takes a single tile_id integer. Use `pmtiles.tile.zxy_to_tileid(z, x, y)`.
- `gdal2tiles.py --xyz` generates XYZ tiles (not TMS y-flipped). Use this for PMTiles.
- `gdal2tiles.py` is at `/usr/bin/gdal2tiles.py` on server.
- `3dview.py` can't be imported directly (digit prefix). Use `importlib.import_module('3dview')`.

## Config and Materializer System

### atlas_config.json is Always a Build Artifact

`{atlas}_layers.json`, `{atlas}_assets.json`, and all `shared_*.json` files are **inputs** to `build_atlas.py`. The merged result is `staging/atlas_config.json`, which is the running system's source of truth. **Any change to any of these files requires re-running `build_atlas.py config_only` before the change takes effect.** There is no runtime merge.

### Which QGIS File Does What

Two QGIS outlet files exist — easy to confuse:
- `outlets_qgis.py` — rendering helpers and shared logic; **not** the materializer entry point
- `outlets_qgis_atlas.py` — the materializer entry point; imports `outlets_qgis` as a helper; **registers `asset_methods`**

New materializers must be registered in `outlets_qgis_atlas.asset_methods`.

### Common Failure Modes

- `KeyError: 'fetch_type'` — `atlas_config.json` hasn't been rebuilt after a source config change
- `KeyError: '<fetch_type_name>'` — function registered in wrong module, or missing from `asset_methods`
- Adding a `config_def` in per-atlas assets but forgetting the corresponding entry in `shared_*.json` — fails silently or partially

Adding a new materializer requires: function → registered in `asset_methods` → `config_def` in `shared_*.json` → reference in per-atlas assets JSON → re-run `build_atlas.py config_only`.

## Unified Webapp Gotchas

- **`apiurl` vs `base_url`**: `base_url` includes the path (`fireatlas.org/scvfd`), so `${base_url}:9000` creates malformed URLs. Pass `apiurl` (from `app_url` in config) separately to templates.
- **Stale `atlas_config.json`**: always check the live config on the server — it's stale if `build_atlas.py config_only` hasn't been re-run after source changes.
- **htpasswd cascade**: after any roles change, re-run `build_atlas.py config_only` on server to regenerate htpasswd files.
- **CDK security changes**: need `--require-approval broadening` flag.
- **Certbot chicken-and-egg**: comment out SSL nginx directives before first certbot run.

## QGIS Label Filtering

All options live in the per-atlas `{atlas}_layers.json` and are read by `apply_basic_styling()` in `outlets_qgis.py`.

| Option | Type | Effect |
|---|---|---|
| `add_labels` | bool | Enable labels at all |
| `label_deduplicate` | bool | Only label the lowest-`$id` feature per unique label value |
| `avoid_label_collisions` | bool (default true) | QGIS collision avoidance; false = show all labels |
| `labels_avoid_features` | bool (default false) | Labels treat features as obstacles |
| `max_labels` | int | Hard cap on total labels shown for this layer |
| `label_expression` | string | **Most powerful.** Raw QGIS expression; NULL return suppresses label. Use this first for non-trivial filtering. |
| `label_color` | [r, g, b] | Overrides default label color |
| `qgis_width_scale` | float | Multiplies line width (roads, creeks) |

`label_deduplicate` uses PAL expression: `if($id = minimum($id, group_by:="<field>"), "<field>", NULL)` — aggregate runs across the whole layer. Works well for buildings/addresses; less predictable for per-cell gazetteer context.

**WVFD brittleness warning**: Label collision tuning felt brittle. If label layout regresses, likely culprits: `displayAll`/`avoid_label_collisions` interaction, QGIS version PAL differences, `max_labels` stacking with collision avoidance, aggregate expressions behaving differently when atlas clipping is active.

## Feature Branches and Pending Work

### feature/atlas-create (pushed 2026-04-04, not yet merged or deployed)

`/create` page on fireatlas.org: draw a bbox, enter a name, submit → atlas created with starter layers, materialized (html/webmap/webedit), redirect to public console.

Manual step required on server before deploying nginx changes:
```bash
htpasswd -bc /root/swales_dev/roles/shared.htpasswd admin admin
htpasswd -b  /root/swales_dev/roles/shared.htpasswd internal internal
```

### pmtiles-terrain branch

Not yet merged. SCVFD wired but not tested.

### Pending Issues

| Issue | Area | Status |
|---|---|---|
| #70 | Combine `reprocess_email.py` + `trace_email.py` | Open |
| #71 | Unified logging + re-enable bounce emails | Open |
| #67 | Legend "only rendered" toggle doesn't restore unchecked-layer state | Open |
| #64 | Lambda memory 128MB→512MB CDK deploy | Open |
| #52 | Layer management | Open |
| #53 | Config simplification | Open |
| #43 | Email address configurability | Open |
| #42 | Edit map refresh | Open |
| #41 | Edit map shows all layers | Open |
| #40 | Delta /work subdir | Open |
| #34 | Overture S3 release path auto-update (~monthly) | Open |

### Stuck Emails

Three emails are stuck in the ingress bucket — retrigger after confirming Lambda memory fix (issue #64) is deployed:
- `a0d6bl15`
- `dl2t1n7q`
- `v8a3cpdhj`

Use `scripts/reprocess_email.py <key>` to retrigger.

### Bounce Emails

Currently **disabled** (`_send_bounce` calls commented out in webapp). Users get no feedback when GPS is missing. Issue #71 covers redesign and re-enablement.

### Westport Config Files

- `wvfd_dev_assets.json` is canonical for Westport assets (more complete than `wvfd_assets.json`)
- `wvfd_dev_layers.json` is canonical for Westport layers (`wvfd_layers.json` is missing `roads_candidates` and `private_notes`)

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
- `pmtiles` - PMTiles generation
- `anthropic>=0.25.0` - NL→SQL feature (`POST /sql_generate/{swalename}`)

### QGIS (System Dependency)

QGIS with Python bindings is required for PDF generation (`outlets_qgis.py`). On the server, QGIS was installed via system packages (not pip).

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

gj = json.load(open("my_region.geojson"))

config = atlas.create(
    feature_collection=gj,
    data_root="/root/swales",
    layers_path="configuration/myatlas_layers.json",
    assets_path="configuration/myatlas_assets.json",
    shared_dir=Path("/root/data")
)
```

`atlas.create_config()` does `config.update(props)` for all GeoJSON feature properties — any new field in `{atlas}.geojson` flows into `atlas_config.json` automatically. Edit the GeoJSON, then `build_atlas.py config_only` — never edit `atlas_config.json` directly.

### Materializing Assets

```python
import atlas
import json

config = json.load(open("/root/swales/myatlas/staging/atlas_config.json"))
atlas.materialize(config, "dem")       # inlet
atlas.materialize(config, "gdal_contours")  # eddy
atlas.materialize(config, "webmap")    # outlet
```

**Known asset method keys**:

| Key | What it generates |
|-----|-------------------|
| `html` | All console HTML variants (technical, admin, internal, public) — all four generated together. **Do not use `technical_console`, `admin_console`, etc. — they don't exist.** |
| `webmap` | MapLibre webmap |
| `webmap_private` | Private variant of webmap |
| `webedit` | Web editing interface |
| `runbook` | PDF runbook (non-QGIS) |
| `sqlquery` | SQL query outlet |
| `jupyter_notebook` | Jupyter notebook outlet |
| `config_editor` | Config editor UI |
| `3dview` | 3D terrain view |

**Outlet build order**: `webmap` must come before `html` — the console HTML checks if `outlets/webmap/index.html` exists at generation time.

## Current Atlases

- `wvfd` / `wvfd_dev` — Westport Volunteer Fire Department. Recent paying contract; significant code, config, and data changes that need to be propagated back to SCVFD. Use `wvfd_dev_*` config files as canonical.
- `scvfd` — Original and anchor customer. Personal connection (community VFD). No current funding but important — friendly testers and the reference implementation.
- `MineralKinsey` — Fire safe council
- `kennedy`, `wildwood` — Other customers

Each has corresponding `*_layers.json` and `*_assets.json` in `/configuration`.

## Architecture Notes

**Static-first**: Once generated, outlet files are static and can be viewed offline. The webapp/API is only needed when modifying data or publishing.

**Actual on-disk layout**:
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
```

**Deployment model (current)**: Unified webapp — single uvicorn at `fireatlas.org:9000`, `SWALES_ROOT=/root/swales_dev`, path-based URLs. All atlases served from one process. Per-atlas model retired.

**Future direction**: Scale-to-zero: static data on S3/CloudFront, API/compute only active during edit sessions. Lambda container images or ECS Fargate. Don't make decisions in config/S3/routing work that close this door.

**S3 status**: Partially underway. Files currently served from S3; goal is to make S3 a proper data backend. Key wrinkle: current structure relies on symlinks (`local ->`, `CURRENT ->`); S3 doesn't support symlinks. The `app/` tree belongs in a container image, not S3.

**Logistics eddy**: Exists as a notebook only — not yet a proper eddy in `eddies.py`. Fuel reduction/biochar logistics modeling; commercially interesting.

**Dagster history**: Originally orchestrated with Dagster. The uniform asset method signature and `asset_methods` dict are intentional Dagster scaffolding — preserve this pattern. Old integration code at `Salmon-Creek-Systems/internal`, `python/atlas_dagster.py`. When restored, Dagster coexists with notebooks and the web console — it handles recurring maintenance and dependency-driven orchestration.

## Commands

### Run tests (locally, no QGIS required)
```bash
cd python
python -m pytest tests/            # all tests
python -m pytest tests/test_foo.py # single file
```

### Run end-to-end tests (against live server, read-only)
```bash
cd python
ATLAS_NAME=kennedy \
ATLAS_BASE_URL=https://fireatlas.org \
ATLAS_API_URL=https://fireatlas.org:9000 \
ATLAS_USER=admin ATLAS_PASSWORD=admin \
pytest tests/test_kennedy_e2e.py -v
```
Install deps once: `pip3 install -r requirements-dev.txt --break-system-packages && playwright install chromium`

### Atlas CLI
```bash
python scripts/build_atlas.py --help
```

## Testing

Tests exist in `python/tests/` but haven't been run recently. Strategy going forward:

1. **Priority**: Simple unit tests for core modules first (`utils.py`, `versioning.py`, `deltas_geojson.py`)
2. **Later**: Integration tests, then system-level tests
3. **Framework**: unittest (existing) / pytest (preferred going forward)

## Known Challenges & Gotchas

### Filesystem Complexity

The system relies heavily on symbolic links (`local/` → shared data, `CURRENT/` → active version), directory creation with specific permissions and `.htpasswd` files, and path resolution that differs between local repo and deployed server. When debugging path issues, check `versioning.py`.

### Config File Manipulation

Atlas configs are built dynamically. The actual runtime config (`atlas_config.json`) may look different from the template files. Use `atlas.create_config()` to inspect. Always fetch live config from `https://fireatlas.org/{atlas}/staging/atlas_config.json` — the source files are not what the code runs from.

### QGIS Environment

QGIS Python bindings must be installed at system level (not virtualenv). Requires specific environment variables. `qgis_init()` in `outlets_qgis.py` handles initialization.

### Local vs Remote Paths

Code references paths like `/root/swales/` which don't exist locally. For local development: mock filesystem operations, use test fixtures with relative paths, or work on modules that don't require the full data tree.

### Layer Config: Zoom Visibility

- **`vis: {"minzoom": N}`** — applied to both geometry layer AND label layer
- **`label_minzoom` / `label_maxzoom`** — overrides zoom range on the label/icon layer only

### MapLibre Legend Library (`@watergis/maplibre-gl-legend`)

- "Only rendered" checkbox class: `maplibregl-legend-onlyRendered-checkbox`
- Default position: `top-right` (our code places it at `bottom-left`)
- **Known bug**: unchecked layers reappear when toggling "only rendered" off then on (issue #67)
- Avoid broad CSS selectors on `.maplibregl-ctrl-bottom-left input[type=checkbox]` — hits all layer visibility checkboxes

### Template Copy

`cp -r src dst` when `dst` exists copies `src` INTO `dst`, not over it. Use `shutil.copytree(src, dst, dirs_exist_ok=True)` — already fixed in `outlets.py`.

## Priority Work Areas

1. **Local Development Setup**: Currently can't run much locally. Need better mocking, fixtures, or containerization.
2. **QGIS Deployment**: Installation is manual and fragile. Consider Docker or better orchestration.
3. **Atlas Creation Workflow**: `create()` and `create_config()` in `atlas.py` could be more streamlined.
4. **Testing Infrastructure**: Get unit tests running. Start with `utils.py`, `versioning.py`, then expand.
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
