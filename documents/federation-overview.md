# Stewardship Atlas — Federation Overview

**Status:** Draft v0.1 — for human review
**Scope of this doc:** Conceptual design and v1 surface. Implementation detail lives in the companion Claude Code handoff doc.

---

## 1. The problem

Overlapping atlases should be able to share data in a curated, privacy-respecting, volume-respecting way.

**Motivating use case.** A volunteer fire department (VFD) runs an atlas covering a whole community. Individual landowners run atlases for their own properties inside that community. Some property data is of direct interest to the VFD and small in volume — a new water tank, say — and should show up on the fire department's map automatically. Other data is high-detail, high-volume, and irrelevant at the community scale — a property's full culvert inventory — and should not federate. The landowner needs systematic control over which of their data flows up.

A second, finer-grained need: even within a shared feature, some properties should be withheld. We want to federate *that a gate exists at this location* without federating *the gate's combination*.

---

## 2. The core insight: federation is just an inlet

The key realization is that federation is **not** a special operation on the dataswale. Mechanically, the consumer atlas treats a federated source like any other external data source (Overture, OpenStreetMap, a public agency layer). It pulls the data in at **ingest time**, materializes it into its own dataswale, and from then on everything — eddies, derived assets, outlets — runs against locally held data exactly as normal.

This is a deliberate departure from the classic federated-query model, where the consumer reaches out to the source every time the data is needed (at outlet/usage time). We pull **upstream, at build time**, because:

- We need the data locally to run calculations and build derived assets.
- We don't want per-view latency or a hard runtime dependency on the source being online.
- It fits the existing build model, where versioned assets are recalculated when a new version is generated.

So: **defining a federation between two atlases is defining an inlet** on the consumer that knows how to fetch a published layer from the source.

---

## 3. The v1 surface — three small changes

Everything below reuses existing concepts. We are not building a new protocol or data format.

### 3.1 A `shareable` flag on the source layer config

The source atlas owns and generates the layer normally (via its own inlet, eddies, or just people drawing it through web edit — that part is unrelated to federation). The only addition is a flag on the layer config saying **"this layer may be federated."**

To support property-level masking, this is not strictly a boolean. It carries an optional **property allowlist or blocklist**, so the source can publish the gate's location while withholding the combination. Masking is applied **source-side**: the withheld property is never written into the published output, so it never crosses the wire. Control stays genuinely with the source.

### 3.2 A STAC outlet on the source atlas

The source publishes its shareable layers as a **static STAC catalog** — flat JSON files, no query server:

- one `catalog.json` per atlas, indexing the shareable layers;
- one `collection.json` per shareable layer (id, spatial/temporal extent, license, providers, and any layer metadata the source chooses to publish);
- asset links pointing at the already-published GeoJSON for each layer.

This is modeled as an **outlet**, not as dataswale content: the catalog is a derived, materialized view of "what's shareable and where it lives," regenerated as part of the normal build cycle. The files are written into published output and served over HTTPS/S3.

A useful side effect: the STAC Collection **is** the "published layer descriptor" we'd otherwise have had to invent. Federation advertising and STAC support land in the same object.

### 3.3 A federation inlet on the consumer atlas

A new inlet type whose config points at a source atlas + layer. It reads the source's `catalog.json`, resolves the target collection by id, follows the asset link, and pulls the GeoJSON into the consumer's dataswale. It accepts an **optional bbox filter** (unused for the single-property case, wanted by a large-area consumer).

The consumer then defines its own layer over that inlet — its own styling, its own exposed properties — like any other layer. No federation-specific rules needed downstream; a layer whose definition doesn't match the inlet's output breaks the same way any mismatched layer would, federation or not.

---

## 4. Data flow, end to end

1. **Source build.** Source atlas generates its layers as usual. Shareable layers, with masks applied, are written to published GeoJSON.
2. **STAC outlet.** Source materializes `catalog.json` + `collection.json` files referencing that GeoJSON. Served over HTTPS/S3.
3. **Consumer build.** Consumer's federation inlet reads the source catalog, resolves the collection, pulls the (current, masked) GeoJSON, applies any bbox filter, lands it in the dataswale. The pulled source version is recorded in the provenance/delta trail.
4. **Downstream.** Eddies, derived assets, and outlets on the consumer run against locally held data, identically to any other layer.

---

## 5. Standards posture — and why this is a safe bet

We are deliberately building **on top of** existing standards rather than inventing federation from scratch, and rather than asking anyone to adopt something new.

- **Static STAC** (the SpatioTemporal Asset Catalog static-catalog profile) is the publishing format. It is, by design, minimal: a STAC Item is a GeoJSON Feature plus a few foreign members, and a static catalog is just linked JSON files on a web server — no moving parts. For vector layers we use the **layer-as-Collection-with-a-GeoJSON-asset** pattern; we do **not** explode individual features into Items.
- This mirrors the approach taken by the OGC **Federated Marine Spatial Data Infrastructure (FMSDI)** pilots, which build federation/governance on top of OGC API standards rather than replacing them. Our story is "modeled on the same pattern FMSDI uses," which makes it an easier sell and signals we're not doing anything exotic.
- Our one intentional divergence from the marine work: we federate **upstream at ingest** rather than via live query. The transport is the same; only the timing differs.

Because the inlet just consumes a published catalog, it can equally consume a future STAC-API endpoint or any OGC API – Features endpoint without a redesign.

---

## 6. Proposed defaults (please redline)

These are baked into the v1 design as defaults; flag any you'd change:

- **Masking is source-side.** Withheld properties are never written to published output.
- **Pull-latest-at-build, with provenance.** The inlet pulls the source's current published version at build time and records which version it pulled.
- **Optional bbox on the inlet.** Off by default; available for large-area consumers.

---

## 7. Explicitly deferred (documented, not built)

- **Read endpoint.** A thin dynamic server in front of the static files, for sources where raw file access isn't guaranteed. *Next step.*
- **Full STAC API.** Queryable, OGC API – Features–conformant search. This is the heavier build and is also the natural home for the discovery problem below. *Later step.*
- **Atlas discovery / registry.** "Which atlases overlap my area of operations?" For now this is handled by configuration — we hand-write who federates with whom — or at most a simple bounding-box scan over known atlas configs. Self-hosted atlases we don't control make this a genuinely separate, upstream problem. Out of scope for v1.
- **Per-record / per-feature filtering.** v1 shares whole layers (with property masking). "Share all water tanks except this one" or "share only this one culvert" is a later refinement.
- **Consumer-side defaulting from the source's published layer config.** Later, the first fetch could grab the source's published layer config as a starting default for the consumer's layer, then let the consumer override. For now the consumer defines its own layer.

---

## 8. Open questions

- Exact placement of federation config in the Atlas config: attached to individual layer/asset configs, a global federation block, or a mix. Expectation is this resolves naturally during implementation and a mix is acceptable.
- Catalog layout conventions (namespacing, file naming) — follow STAC static-catalog best practices unless they conflict with existing dataswale/output conventions.
