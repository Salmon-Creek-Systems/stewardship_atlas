# ABI Demo: Biochar Suitability Tool

Working demonstration for the ABI/USDA ARS proposal. Lives on the `feature/abi-biochar` branch.

## What It Does

A single-page tool where a user clicks a SSURGO soil polygon on the map, sees key soil
properties in a sidebar, selects an amendment goal, and gets a ranked list of biochar
products with predicted ΔpH, application rate, and suitability score.

The pH-liming path (raise_ph) is fully implemented using a simplified Phillips 2020 algorithm.
The other three goals (water retention, carbon sequestration, P/K provision) are wired up in
the UI but return a "coming in production" placeholder — they're shown to demonstrate the
intended scope to reviewers.

## Architecture

### Outlet

`biochar_webedit` is a custom outlet registered in `outlets.py` → `outlet_biochar_webedit()`.
It uses `templates/biochar_webedit.html` — a bespoke single-page app, not derived from the
standard webmap/console templates.

The map style is built as a Python dict in `outlet_biochar_webedit()` (not in the template),
following the established pattern for avoiding `{z}/{x}/{y}` conflicts in f-strings.

### Data Layers

| Asset name | Layer written to | Source | Notes |
|---|---|---|---|
| `seed_ssurgo` | `ssurgo_polk_or` | S3 `abi_demo/seed/ssurgo_polk_or.geojson` | SSURGO mapunit polygons, Polk County OR |
| `seed_biochar` | `seed_biochar` | S3 `abi_demo/seed/biochar_properties_pnw.geojson` | Biochar product properties |

### API

`/api/biochar/suitability` (POST) is handled by `python/biochar_routes.py`, which is included
as a FastAPI router in `webapp.py`. It loads the biochar properties from the local layer file,
calls `dst_match_point()` in `eddies.py`, and returns ranked results.

### Suitability Algorithm

`dst_match_point()` in `eddies.py`: simplified Phillips 2020 pH-liming path.

```
liming_potential = 0.7 × norm(biochar_pH) + 0.3 × norm(ash_content)
predicted_ΔpH   = k × liming_potential × rate   (k = 0.1667)
rate             = clamped to [3, 12] t/ac per CPS-336
```

Cross-checked against PNW Atlas R reference for 3 test cases.

### Tile Serving

The SSURGO layer is served as vector PMTiles (not GeoJSON like other atlases).
Tippecanoe generates `ssurgo_polk_or.pmtiles` in the staging layers directory;
the page loads it via the MapLibre pmtiles protocol.

Tippecanoe note: use the **Felt fork** (`github.com/felt/tippecanoe`), not the archived
Mapbox version. The Mapbox version writes MBTiles even with a `.pmtiles` extension.
If using the Mapbox version, convert after: `pmtiles convert input.mbtiles output.pmtiles maxzoom`.
The Felt build may OOM on a low-RAM server with `make -j` — use `make -j2` or `make`.

## Oddities (differences from other atlases)

**`s3_geojson` inlet ignores `out_layer`.**
The `s3_geojson` inlet writes to `layers/{asset_name}/` using the asset name, not the
`out_layer` value in config. So `seed_biochar` (asset name) ends up at
`layers/seed_biochar/seed_biochar.geojson`, not `layers/biochar_properties_pnw/`.
This is inconsistent with how other inlets (e.g. `h3_grid_inlet`) work, and the `out_layer`
field in the abi_demo config is misleading. The fix is either to update `s3_geojson` to
honor `out_layer`, or accept the current behavior and use the asset name everywhere.

**`biochar_routes.py` has a mismatched hardcoded layer name.**
The route looks for `layers/biochar_properties_pnw/biochar_properties_pnw.geojson` but the
file actually lives at `layers/seed_biochar/seed_biochar.geojson` (see above). This needs
a one-line fix before the API will work on a freshly materialized atlas.

**No `atlas_config.json`-driven layer definitions.**
The SSURGO layer source and all map layers are hardcoded in `outlet_biochar_webedit()` in
`outlets.py`, not driven by layer config. Adding or changing a layer requires editing Python
code, not just config.

**PMTiles requires manual tippecanoe run.**
Unlike other layers which are fully materializable via `atlas.materialize()`, the SSURGO
PMTiles file must be generated manually with tippecanoe and placed in the layers directory.
There is no eddy or inlet for this yet.

**Single-file GeoJSON config.**
Uses `configuration/abi_demo.geojson` (single-file format). Layers and assets are embedded
in the GeoJSON properties — consistent with the new format introduced in issue #100, but
the abi_demo config is the only one that uses `biochar_webedit` and `biochar_routes`.

## Next Steps

- **Fix `biochar_routes.py` layer name**: change `biochar_properties_pnw` → `seed_biochar`
  (one line in `_load_biochar_records()`).
- **Seed biochar DB with real data**: `abi_demo/seed/biochar_properties_pnw.geojson` on S3
  currently has placeholder data. Replace with real PNW biochar product properties.
- **Implement remaining goals**: water retention, carbon sequestration, P/K provision
  (all show as "coming in production" in the UI).
- **Automate tile generation**: wrap tippecanoe in an eddy or script so PMTiles can be
  regenerated without a manual command. Longer-term: Lambda container (see roadmap).
- **Fix `s3_geojson` `out_layer` handling**: either honor `out_layer` consistently, or
  remove the field from the config to avoid confusion.
