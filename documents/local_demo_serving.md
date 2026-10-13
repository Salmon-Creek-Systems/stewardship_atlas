# Local / Offline Atlas Demo

## Purpose

Run one atlas on a laptop with **no network**, for a live demo: webmap, 3D view, console, runbook PDFs, and the edit pages (drawing works; submitting shows "Offline demo: changes are not saved"). Nothing is written anywhere.

This is a deliberately minimal stopgap. The long-term version is "offline as an outlet artifact" (see `roadmap.md`).

## Steps

```bash
scripts/sync_atlas_local.sh south_fork_eel ~/atlas_demo     # ~5 min, ~370 MB
python3 scripts/offline_bundle.py ~/atlas_demo south_fork_eel   # ~4 min, ~520 MB more
python3 scripts/serve_local.py ~/atlas_demo
```

Open `http://localhost:8080/south_fork_eel/staging/outlets/console/public/` (the server prints the webmap and 3D URLs too). Then **turn wifi off and click through everything** — that is the real test.

All three are stdlib Python / rsync; no installs needed.

## What each script does

- **`sync_atlas_local.sh`** — rsyncs one atlas's `staging/` from the box as `ubuntu@` with `sudo rsync` (root SSH is blocked). Takes outlets (minus the retired `html/`, `work/`, notebooks), layer GeoJSON + `*.cog.tif` + sidecars (not raw `.tiff` sources), and only `documents/` and `css/` from `local/` — the `local` symlink is 25 GB of shared data. Lays files out like production (`/<atlas>/staging/…`, `/local/…`) so root-relative links work.
- **`offline_bundle.py`** — downloads into `~/atlas_demo/_offline/`: the unpkg JS libraries the pages use, Open Sans glyphs (Latin ranges), the atlas's own COGs on `scs-atlas-data`, and AWS terrarium tiles z8–15 over the atlas extent +30%. Then rewrites the *copy*: box URLs → `localhost`, the API → `localhost/_api/`, externals → `_offline/`, the 3D view's Esri drape → the local basemap COG. Injects an "OFFLINE DEMO" banner and an alert on any attempted change. Ends by listing every external host still referenced.
- **`serve_local.py`** — static server rooted at the demo dir, with HTTP Range support (the COG/PMTiles readers need it). `/_api/*` and any non-GET return 503 `{"error": "offline demo"}`.

## Known gaps (acceptable for a demo)

- Alternate basemaps (Esri, USGS, OpenTopo) are blank; the default LIDAR hillshade works.
- Rasters borrowed from another atlas (SFE's three `fhe_*` layers) 404. Download them and add a rewrite if needed.
- Photo popups load images from S3 — broken offline.
- Google Fonts fall back to system fonts.
- Read-only API features (SQL query, publish status, logs, annotate selection preview) fail quietly.
- Glyphs cover Latin only; other characters in labels won't render.

## Gotchas

- **The port is baked in.** URLs are absolute (MapLibre sprite/glyph URLs must be), so `serve_local.py --port` must match `offline_bundle.py --port`. To change it: re-run the sync (rsync restores the original pages), then the bundle.
- **Bundle edits the copy in place.** Re-running it is safe (idempotent, downloads are skipped), but a fresh sync is the clean reset.
- The data is a snapshot of `staging/` at sync time — re-sync before the demo if the atlas changed.
