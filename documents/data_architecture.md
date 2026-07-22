# Data Architecture

How we think about data in the Stewardship Atlas: layers, assets, deltas,
refresh, and versions — the conceptual model, independent of servers, APIs,
and deployment. For those, see `atlas_technical_architecture.md`.

This document describes the model settled in issue #131. Where the code has
not yet caught up, a **Transition** note says so.

## The dataswale

The dataswale is the store of record: one directory tree per atlas, holding
each layer's data as files (GeoJSON for vector layers, GeoTIFF/COG for
raster). Everything else in the system either feeds the dataswale or is
derived from it. Data flows one way:

```
INLETS  →  LAYERS  →  (EDDIES →)  OUTLETS
```

Disk is the single source of truth. No component holds authoritative state
in memory or passes data to another component except through files in the
dataswale. This is what keeps every execution surface — notebooks, the
webapp, Dagster — interchangeable: they all call the same functions against
the same files, in any order.

## Assets: the uniform unit of work

An **asset** is a named, materializable step: a function that reads state
from the dataswale (and/or the outside world) and writes state back to it.
`atlas.materialize(config, asset_name)` runs one asset. Every asset has the
same signature and the same contract, which is what lets a dependency graph
orchestrate them uniformly.

Assets come in three flavors, distinguished only by where they sit in the
flow — not by any structural difference:

- **Inlets** bring external data in (OSM, Overture, S3 files, spreadsheets,
  photos). *Vector inlets do not write layers.* They write **deltas** —
  pending inputs (see below). Raster inlets write their layer directly.
- **Eddies** transform layers into other layers (DEM → contours, roads →
  mileage, points → H3 aggregation). An eddy writes its output layer
  directly.
- **Outlets** export layers into products (webmap, PDF runbook, GeoPackage,
  consoles). Outlets never write layers.

## Layers and the `layer_*` assets

A **layer** is a single-typed dataset in the dataswale. Layers are *data*,
not workers — so a layer as such does not appear in the asset graph. What
appears instead is an asset that produces it.

For delta-fed vector layers, that producer is a synthesized asset named
`layer_{name}` (e.g. `layer_roads`). Its job is **canonization**: gather the
layer's accumulated pending deltas, apply them in order, and write the
canonical layer file into the dataswale. This is a deliberate design
decision: *turning deltas into layer data is itself work, so it is itself an
asset* — uniform with every other asset, orchestrated the same way. Nothing
else writes a delta-fed layer.

So the roads pipeline is really:

```
roads_osm (inlet, writes deltas) ─┐
                                  ├→ layer_roads (applies deltas, writes roads.geojson) → mileage eddy → webmap
roads_overture (inlet, deltas) ───┘
```

The `layer_*` asset is also the fan-in point: multiple inlets plus manual
edits can all feed one layer, and canonization happens once, after all of
them, avoiding intermediate-state races.

Two kinds of layers have **no** `layer_*` asset, because they are not
delta-fed: eddy-produced layers and raster layers are written directly by
their producing asset, which owns them outright. Manual-only layers
(hand-drawn, no inlet at all) **do** get a `layer_*` asset — they are pure
delta consumers.

## Deltas: pending inputs, not mutations

A **delta** is a batch of proposed changes to one layer: `create` (new
features), `annotate` (spatial property update), `match` (id-based property
update), `delete` (remove features intersecting a polygon). Deltas come from
inlets, web editing, photo ingest, spreadsheet import — every source of
change writes a delta rather than touching the layer.

A delta does not change anything by itself. Deltas **accumulate** in a
pending queue per layer until a refresh consumes them. Once applied, a delta
is moved into that layer's `work/` archive — never deleted. The archive is
the layer's edit history.

A layer is therefore fully defined as: *its sources (inlets), plus its
accumulated deltas, materialized at refresh time.*

## Refresh: the only mutation

Exactly one operation mutates a delta-fed layer: **refresh** (materializing
its `layer_*` asset). Refresh has two modes:

- **Update** (default): apply pending deltas on top of the current layer
  file. Cheap; no source re-pull. This is the everyday path.
- **Rebuild**: re-run the layer's direct inlets to get a fresh base, then
  apply pending deltas onto an *empty* layer. Expensive, and — important —
  archived deltas in `work/` are **not** replayed: a rebuild reflects the
  fresh source pull plus whatever is pending, nothing more. Manual edits
  already consumed into the archive drop out of the layer (they remain on
  disk, untouched). To restore archived edits after a rebuild, move their
  files from `work/` back into the pending queue and update-refresh.
  Automated archive replay is a possible future extension; it has real
  subtleties (prior inlet pulls are themselves archived deltas and must not
  be duplicated; `match` deltas target `atlas_id`s that a fresh pull
  regenerates).

Update and rebuild can diverge. Rebuild is the canonical, reconciling
operation; update is the fast path.

Mode is a **runtime parameter, not layer configuration**. The only per-layer
distinction is structural and derived from the graph: a manual-only layer
has no inlet, so it has nothing to rebuild — update and rebuild are the same
operation, and rebuild is coerced to update rather than allowed to empty the
layer.

### Cascade

Refreshing a layer re-derives what is **downstream** of it — dependent
eddies, then outlets — so the products people look at stay consistent with
the data. It never re-runs anything upstream (an expensive source is not
re-pulled because something below it changed; rebuild re-runs *direct*
inlets only). Cascade is a property of the refresh action, not of the
assets: a no-cascade refresh exists for development velocity, so a slow PDF
outlet is not rebuilt on every upstream tweak.

### Auto-apply

Whether a delta is applied immediately is a property of **how it was
created, not which layer it lands on**. Interactive sources (web editing,
photo ingest) default to immediate apply — which is just a cheap
update-refresh of the target layer with *no cascade*, giving instant
feedback on the editing surface while deferring the outlet cascade to an
explicit refresh or publish. Bulk sources default to accumulate.

## Versions and publish

`staging` is the one editable copy of the dataswale. A **version** is a
frozen, timestamped snapshot of staging; `CURRENT` points at the latest one.

**Publish is a pure snapshot. It computes nothing.** No inlet runs, no eddy
runs, no outlet materializes at publish time. The guiding invariant: *what
the user saw in staging is exactly what gets published.* Running computation
at publish time would break that — the published version could differ from
what was reviewed.

The corollary: outlets are kept current by refresh cascades *before*
publish, not by publish itself. For expensive, non-real-time outlets (PDF
runbooks, gazetteer products) that may have been skipped via no-cascade
refreshes during a work session, the answer to "when do these get built?"
is: **materialize them explicitly, then publish.** A full-cascade refresh —
or directly materializing the outlet assets — is the pre-publish hygiene
step. Rebuild-before-publish for canonical layers is likewise reasonable
hygiene, not an enforced rule.

Published versions retain the full delta tree — pending queue and `work/`
archive — for provenance and so that restoring staging from a version
(`reset_staging`) restores the layer's edit history too. Deltas are never
auto-deleted.

The `versioned_outlets` config list has exactly one meaning: which outlets
to **copy into** the published snapshot (default: all). It is not a build
list, not an ordering (the asset graph encodes ordering), and not a cascade
scope.

> **Transition**: as of mid-2026 the publish endpoint still materializes the
> `versioned_outlets` list before snapshotting. Issue #131 removes this,
> making publish the pure snapshot described here.

## The asset graph

The full atlas — inlets, `layer_*` canonizations, eddies, outlets — forms a
dependency DAG, built automatically from the atlas config (`atlas_dagster.py`)
and orchestrated by Dagster. Two properties of the graph matter more than
the tooling:

- **Edges are ordering-only.** No data passes between assets through the
  orchestrator; every asset reads its inputs from the dataswale when it
  runs. Any single asset can therefore be materialized in isolation, from
  any surface, against whatever is on disk.
- **Dagster is additive, not authoritative.** Its run history records what
  *it* did; the dataswale records what is *true*. A notebook materialization
  is exactly as legitimate as a Dagster run — the graph exists to automate
  ordering and cascades, not to gatekeep execution.
