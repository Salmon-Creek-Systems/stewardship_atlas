# Local / Offline Atlas Serving

## Purpose

Run a single atlas locally for demos, field use, or development without access to the production server. Provides read-only access to all pre-generated outputs: webmap, runbook, gazetteer, and the admin console.

## What works in read-only mode

| Feature | Works offline? | Notes |
|---|---|---|
| Webmap | ✓ | Vector layers and PMTiles load from local files |
| Runbook (PDFs + index) | ✓ | Fully static |
| Gazetteer (PDFs + index) | ✓ | Fully static |
| Admin console (view layers, versions) | ✓ | HTML is static; data is baked in |
| 3D view (PMTiles terrain) | ✓ | If PMTiles are in the outlet dir |
| Base map imagery (satellite, street) | ⚠ | Loads from external tile service — needs internet |
| Delta upload / layer editing | ✗ | Requires server + QGIS |
| Publish / rollback | ✗ | Requires server |
| Refresh / materialize assets | ✗ | Requires server + QGIS |
| SQL query | ✗ | Requires server + DuckDB |
| Location sharing | Possible | One small read-only endpoint; see below |

Write operations return a clear "read-only mode" response rather than silently failing.

## Setup

### 1. Sync atlas files from the server

```bash
scripts/sync_atlas_local.sh scvfd ~/atlas_demo
```

This rsyncs only what's needed for serving:
- `staging/outlets/` — all generated outputs
- `staging/layers/` — GeoJSON layers (needed by webmap)
- `staging/atlas_config.json`
- `staging/local/` (resolving the symlink) — CSS, logo, documents

Skips: `staging/deltas/`, raster source files, large DEMs.

Estimated size: a few hundred MB depending on atlas (PDFs are the bulk).

### 2. Start the local server

```bash
python scripts/serve_local.py --data-dir ~/atlas_demo --atlas scvfd --port 8080
```

Open: `http://localhost:8080/staging/outlets/html/admin/`

### 3. Verify

- Webmap loads and layers are visible
- Gazetteer and runbook index pages open
- Admin console shows layer list (action buttons will show "read-only" if clicked)

---

## How it works

`serve_local.py` is a small FastAPI app (same dependency as the production webapp) that:

1. **Serves static files** from the synced directory tree, matching the same URL paths the production server uses (`/staging/`, `/local/`).

2. **Rewrites the console HTML** — the admin console has `baseurl` and `atlasappport` baked in pointing at production. The server intercepts requests to `*/html/admin/` and rewrites those values to `localhost:{port}` before sending.

3. **Stubs read-only API calls** — returns live data from local files for:
   - `/status` → reads `atlas_config.json`
   - `/publish-status` → returns idle/read-only status

4. **Returns 503 for write operations** with a JSON body `{"error": "read-only mode"}` — publish, delta upload, refresh, rollback, reset-staging.

---

## Known limitations

**Base map tiles**: the webmap base layer (satellite imagery, street map) loads from an external tile service and won't work without internet. The vector data layers and PMTiles terrain/hillshade load from local files and will work. Consider switching the base map style to a simple blank or minimal offline-capable style for offline demos.

**Symlinks**: the production `staging/local/` is a symlink. The sync script resolves it with `rsync --copy-links`. On macOS this works fine.

**URL rewriting is fragile**: the console HTML rewrite assumes the structure of the generated HTML. If `outlet_html` changes how it embeds `baseurl`/`atlasappport`, the rewrite regex needs updating.

**atlas_config.json must be current**: the console reads config from the baked-in HTML (generated at outlet time), not from a live API call. If the atlas was last published a while ago, the displayed data reflects that state.

---

## Relation to containerization

This local server is essentially a preview of what a container-based deployment looks like:

| Layer | Now | Container target |
|---|---|---|
| Code | Git checkout per atlas (`app/`) | Single shared image |
| Data | Server filesystem + S3 partial | S3 backend (layers + outlets) |
| Serving | uvicorn + full webapp | Lightweight static server + minimal read API |
| Compute | QGIS on EC2, always on | Lambda/Fargate, triggered only for publish |

The local demo mode makes the static-data boundary concrete: **everything in `outlets/` is truly static** and can be served from S3/CloudFront with zero compute. The only things that need compute are the write endpoints (delta upload, materialize, publish).

A container for the write-path API would only need Python + the non-QGIS dependencies for most operations; QGIS-specific operations (PDF generation) could be a separate container or Lambda layer triggered by publish.

The `serve_local.py` script is a useful artifact beyond just demos — it's a testbed for the minimal API surface that a production static-serving setup would need to stub or implement via Lambda.

---

## Location sharing (optional)

The production webapp has a `/location` endpoint for real-time crew location sharing. For demos this is a nice-to-have — if the demo involves multiple devices showing locations on the webmap.

This could be added to `serve_local.py` as a small in-memory store (no persistence needed for a demo). One endpoint to store, one to retrieve. This is a few lines of FastAPI code and no additional dependencies.
