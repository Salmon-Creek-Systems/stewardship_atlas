# Claude Code Handoff — Federation v1

**Keyed to:** `federation-overview.md` v0.1. If the overview changes on human review, re-sync this doc before building.
**Goal:** Ship the minimal end-to-end federation path: a source atlas publishes selected layers as a static STAC catalog; a consumer atlas pulls them into its dataswale at build time.

---

## 0. Architecture primer (read first)

Stewardship Atlas uses a **Dataswale / Inlet / Eddy / Outlet** model:

- **Dataswale** — versioned GeoJSON datastore; layers organized by type and access level.
- **Inlet** — brings external data in.
- **Eddy** — transforms/derives.
- **Outlet** — materializes published/derived views from the dataswale.

Federation reuses these. **Federation is just an inlet on the consumer**, fed by a **STAC outlet on the source**. No new "federation engine," no special dataswale operation. Read `federation-overview.md` §2–§4 before starting.

> The config/schema sketches below are **illustrative**. Adapt field names and placement to the repo's actual layer-config and inlet/outlet conventions. Where this doc and existing conventions disagree, existing conventions win — note the divergence rather than silently reshaping the codebase.

---

## 1. Scope

### In scope (build this)
1. A `shareable` declaration on source layer config, with optional property masking.
2. A **STAC outlet** that materializes a static STAC catalog for shareable layers.
3. A **federation inlet** that consumes another atlas's static STAC catalog and lands a layer in the dataswale.

### Out of scope (do NOT build — see overview §7)
- STAC **API** / dynamic query server (OGC API – Features, item-search, CQL).
- Read endpoint in front of the static files.
- Atlas discovery / overlap registry.
- Per-record / per-feature filtering (v1 is whole-layer + property mask).
- Exploding vector features into individual STAC Items. **Layer = one Collection + a GeoJSON asset.**

---

## 2. Task 1 — `shareable` on source layer config

Add an optional `shareable` block to layer config. Absent ⇒ not federatable (safe default).

Illustrative shape:

```yaml
layers:
  water_tanks:
    # ...existing layer config...
    shareable:
      enabled: true
      # Optional property control. Choose ONE of allow / block.
      # Omit both ⇒ publish all properties.
      properties:
        block: [gate_combination, owner_private_notes]
      # Optional published metadata surfaced into the STAC Collection.
      license: "CC-BY-4.0"
      title: "Water tanks"
      description: "Static water sources for suppression planning."
```

Requirements:
- **Masking is source-side and applied before publish.** Blocked properties must be stripped from the GeoJSON that the STAC outlet references. The withheld value must never appear in any published artifact.
- `allow` and `block` are mutually exclusive; if both present, fail the build with a clear error.
- Validate `shareable` config when present; unknown sub-fields tolerated (forward-compatible), per the project's permissive-schema convention.

**Acceptance:** a layer with `block: [gate_combination]` produces published GeoJSON in which no feature carries `gate_combination`, while `geometry` and other properties remain.

---

## 3. Task 2 — STAC outlet (source side)

A new outlet that materializes a **static STAC catalog** from the set of shareable layers in the current build. Pure serialization over data the build already produces; regenerated each build cycle. Output is flat JSON files written into published output, intended to be served over HTTPS/S3.

Produce:

- **One `catalog.json` per atlas** — top-level index linking to each shareable layer's collection.
- **One `collection.json` per shareable layer** — layer-level metadata + an asset link to that layer's published (masked) GeoJSON.

Use STAC `stac_version` `"1.1.0"`. Follow static-catalog best practices for file naming (`catalog.json` / `collection.json`) and link structure; prefer self/absolute links if the published location is known at build time.

### `catalog.json` (illustrative)
```json
{
  "stac_version": "1.1.0",
  "type": "Catalog",
  "id": "vfd-community-atlas",
  "description": "Shareable layers published by the VFD community atlas.",
  "links": [
    { "rel": "self", "href": "https://.../catalog.json" },
    { "rel": "child", "href": "./water_tanks/collection.json" }
  ]
}
```

### `collection.json` for a vector layer (illustrative)
```json
{
  "stac_version": "1.1.0",
  "type": "Collection",
  "id": "water_tanks",
  "description": "Static water sources for suppression planning.",
  "license": "CC-BY-4.0",
  "extent": {
    "spatial": { "bbox": [[-123.1, 38.4, -122.9, 38.6]] },
    "temporal": { "interval": [[null, null]] }
  },
  "links": [
    { "rel": "self", "href": "https://.../water_tanks/collection.json" },
    { "rel": "parent", "href": "../catalog.json" },
    { "rel": "root", "href": "../catalog.json" }
  ],
  "assets": {
    "data": {
      "href": "https://.../water_tanks/water_tanks.geojson",
      "type": "application/geo+json",
      "roles": ["data"],
      "title": "Water tanks (GeoJSON)"
    }
  }
}
```

Requirements:
- `extent.spatial.bbox` comes from the layer/atlas bounding box already available at build time.
- The asset `href` points at the **masked** published GeoJSON from Task 1.
- Temporal extent may be open (`[[null, null]]`) for v1; wire up real intervals only if trivially available.
- Optionally embed a published-version identifier in the collection (a property or a STAC version-extension field) so consumers can record provenance; if not embedded, the inlet records the fetch timestamp + source build id instead.

**Acceptance:** running the outlet on an atlas with two shareable layers yields one valid `catalog.json` with two `child` links and two valid `collection.json` files, each pointing at its masked GeoJSON. Validate against a STAC validator.

---

## 4. Task 3 — Federation inlet (consumer side)

A new inlet type that pulls a published layer from another atlas's static STAC catalog into the dataswale.

Illustrative config:
```yaml
inlets:
  federated_water_tanks:
    type: federation
    source_catalog: "https://.../vfd-community-atlas/catalog.json"
    collection_id: "water_tanks"
    bbox: null            # optional [w, s, e, n]; null ⇒ no spatial filter
    target_layer: "neighbor_water_tanks"
```

Behavior:
1. Fetch `source_catalog` (`catalog.json`).
2. Resolve the child collection by `collection_id`; fetch its `collection.json`.
3. Read the `data` asset href; fetch the GeoJSON.
4. If `bbox` is set, filter features to the bbox.
5. Write features into `target_layer` in the dataswale.
6. **Record provenance**: source catalog URL, collection id, the source version identifier (from the collection if present, else fetch timestamp + any source build id), and the consumer build version — into the delta/audit trail.

Requirements:
- Pull happens **at build time** (ingest), consistent with the upstream-federation model. No runtime dependency on the source after ingest.
- Network/parse failures fail the inlet with a clear, actionable error (which URL, which step). Decide per project convention whether a failed federation inlet fails the whole build or degrades gracefully — default to failing loudly for v1.
- Do **not** attempt to re-derive or unmask data; consume exactly what's published.
- The consumer's own layer definition over `target_layer` is separate and not this inlet's concern.

**Acceptance:** given a reachable source catalog, a consumer build pulls the masked GeoJSON into `target_layer`, the provenance entry is written, and a bbox filter (when set) correctly excludes out-of-box features.

---

## 5. End-to-end acceptance scenario

Set up a **source** atlas with three layers:
- `water_tanks` — `shareable.enabled: true`, no mask.
- `gates` — `shareable.enabled: true`, `block: [gate_combination]`.
- `culverts` — no `shareable` block.

Set up a **consumer** atlas with federation inlets for `water_tanks` and `gates` (none for `culverts`).

Expected after both build:
1. Source STAC catalog lists `water_tanks` and `gates` collections **only** (no `culverts`).
2. Consumer dataswale contains `water_tanks` features with full properties.
3. Consumer dataswale contains `gates` features with location intact and **no** `gate_combination` on any feature.
4. No culvert data anywhere in the consumer.
5. Provenance entries exist for both federated pulls.

---

## 6. Documented next steps (leave stubs/notes, don't build)

- **Read endpoint** in front of the static catalog for sources lacking raw file access.
- **Full STAC API** (OGC API – Features) for queryable discovery; future home of the atlas-overlap registry.
- **Atlas discovery / registry**; bounding-box overlap search across known/self-hosted atlases.
- **Per-record filtering**; share/exclude individual features.
- **Consumer-side layer defaulting** from the source's published layer config on first fetch.

Where reasonable, leave a short `# FUTURE:` comment at the seams (inlet transport, outlet endpoint) so these extensions slot in as variants rather than rewrites.
