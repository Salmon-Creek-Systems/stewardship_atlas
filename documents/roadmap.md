# Stewardship Atlas — Strategic Roadmap

A living document. Projects are roughly sequenced to unblock later work, with fun/interesting work mixed in throughout.

---

## Current Priorities

Two high-level priorities are driving sequencing right now:

**1. Something visible and cool** — most infrastructure work is invisible to customers. Before the next engagement or demo, add at least one feature that a non-technical person would notice and find compelling. Candidates in priority order:
- **Geo-tagged photo ingest** — fastest path, self-contained, immediately useful for field crews
- **Logistics eddy** — more technically interesting, stronger commercial story, more design work required
- **3D view** — `3dview.py` exists, worth checking how close it is to demo-ready
- **LiDAR/point cloud** — visually impressive but depends on S3 backend first

**2. Easier atlas generation and refresh** — provisioning a new atlas or propagating changes is too manual. Close the gap between "I have a GeoJSON and config files" and "running atlas." Candidates:
- **Scripted provisioning** (Issue #10) — near-term, contained, direct follow-on from SCVFD migration
- **Config reform** — root cause of propagation pain; unblocks everything else
- **Domain/routing** — wildcard cert + single nginx proxy; new atlas = DNS entry
- **S3 backend** — enables scale-to-zero and federation

Infrastructure work (testing, auth audit, process management, schema) supports both priorities but isn't directly visible. Weave it in rather than doing it as a block.

---

## Guiding Principles

- Per-customer deployments are a feature (safety isolation), but the manual process around them is the problem
- Static-first architecture is worth preserving — outlets generate static files, the webapp is only needed during data modification
- SCVFD is our reference customer for new features — friendly testers, personal connection, real use cases
- Foundation work now: just wrapped a WVFD contract, good window for infra improvements before next engagement
- **Code cleanup is a practice, not a project**: simplify module-by-module as we touch things, not as a broad sweep. Don't do it before tests are in place (stages 1-3). Use `/simplify` at the end of working sessions to catch anything worth tidying before committing.

---

## Immediate

### 1. Testing — stages 1 and 2
Assess the state of the existing test suite and get it green.

**Stage 1**: Run existing tests, find out what passes and what doesn't. Fix failures. This is a discovery exercise as much as a fix — we don't know the current state.

**Stage 2**: Wire passing tests into a GitHub Actions CI run so future changes are automatically validated.

### 2. ~~Propagate WVFD contract changes to SCVFD~~ ✅ DONE
SCVFD migrated to new deployment with rough feature parity. Not exhaustively tested but all major paths working: html console, webmap, vector edits, QGIS runbook, gazetteer (redesigned as inlet + qgis_runbook outlet), version publishing. Several bugs fixed in the process — see MEMORY.md for details.

Remaining known gaps:
- nginx htpasswd paths still pointing at old `/root/swales/roles/` — needs fixing on server
- WVFD-specific filenames hardcoded in `shared_inlets_config.json` (`westport_turnouts.geojson`, `westport_dem_2m_hillshade.tiff`)
- `claudelab` branch not yet PRed to main

### 3. Testing — stage 3
Extend test coverage to features added during the WVFD contract. The SCVFD migration surfaced several good test candidates — see MEMORY.md bugs list. No new test methodology — just fill the gaps using existing patterns.

---

## Foundational (do first — unblocks everything else)

### 4. Configuration reform + atlas provisioning
The current template-copy-merge system makes new atlas creation painful and change propagation manual. Goal: single-click (or single-command) atlas provisioning from minimal inputs.

Scope:
- Audit the current new-atlas process (informed by project 1)
- Simplify/formalize config inheritance so changes to shared templates propagate cleanly
- Clean up subdomain structure and per-customer deployment
- Define what "minimum viable inputs" for a new atlas looks like

**Unblocks**: faster customer onboarding, federation (which requires clean per-atlas boundaries), easier maintenance.

#### Design tension: build artifact vs. direct editing

`atlas_config.json` is documented as a build artifact (CLAUDE.md), but the "edit source → run build_atlas.py config_only → forget and get confused" workflow is persistent friction. Two clean directions worth deciding between:

**Option A — fully lean into build model.** Source files (GeoJSON + layers.json + assets.json + shared_*.json) are the authoritative truth. `atlas_config.json` is always generated — either triggered automatically on source file change, or rebuilt inline whenever config is read. The shared-template inheritance system pays off at multi-atlas scale. Resolves the footgun by eliminating the manual rebuild step entirely.

**Option B — go back to direct editing.** Source files are for initial creation only. After that, `atlas_config.json` is the truth and changes are made through Python mutation functions (e.g. `atlas.rename_layer()`) rather than hand-editing source files and rebuilding. No build step means no footgun. The `rename_layer()` utility (2026-04-23) is a prototype of this pattern — it operated directly on the config rather than going through the build pipeline.

**Current instinct**: Option B is closer to the original design intent and is more tractable at current scale (handful of atlases, changes are intentional). Option A makes more sense if configs are being programmatically generated or diff'd across many atlases. The two approaches are not mutually exclusive — the mutation-function API works regardless of whether source files exist.

**What this implies for tooling**: If Option B, invest in a richer set of `atlas.*` mutation functions (set_layer_color, add_asset, etc.) so `atlas_config.json` is never hand-edited. Source files become reference-only and the build step recedes. If Option A, invest in auto-rebuild and source file validation (config linter that checks all `in_layers` references resolve, etc.).

#### Agreed direction (2026-04-23 discussion)

**Option B is the target.** Shared templates (`shared_*.json`) stay in the stewardship_atlas repo — they're genuinely code, and the template system is valuable precisely so you don't have to redefine USGS topo, OSM roads, etc. every time. The creation-time build step earns its keep.

**Per-atlas configs don't belong in this repo.** They crept in because there was a lot to type and no better home, but they're deployment configuration, not code. As shared templates get richer and per-atlas files get lighter (just thin overrides), this becomes more viable. Long-term they belong either in a separate deployment config repo or stored alongside the atlas data. They should never have lived in stewardship_atlas.

**`rename_layer()` is a prototype of a "meta tools" class.** Tools that manage running instance configuration — rename_layer, add_layer, remove_layer, set_layer_color, etc. — should operate on `atlas_config.json` directly. The awkwardness of `rename_layer()` also modifying git-tracked source files (and requiring a commit back from the server) points toward the same answer: once per-atlas configs are out of the repo, these tools only touch data, never source.

**The ideal flow**: shared templates (in git) + thin per-atlas overrides (stored as data) → `atlas_config.json` generated at creation time and then edited directly via mutation functions. No ongoing build step. The complexity of inheritance is front-loaded to creation, where it's wanted.

**Current workaround**: run rename on server, commit updated per-atlas configs from there, then regenerate `atlas_config.json`. Pragmatic until per-atlas configs move out of the repo.

#### Inconsistent container types across config files

The config file family mixes lists and dicts with no clear rule: `{atlas}_layers.json` and `shared_layers_config.json` are lists of dicts; `{atlas}_assets.json` and `shared_inlets/eddies/outlets_config.json` are dicts of dicts. Code that operates across config files (e.g. `replace_layer_references()`) has to defensively check `isinstance(data, dict)` to avoid crashing on list-format files. The distinction isn't semantically meaningful — layers are keyed by `name` field rather than dict key purely for historical reasons. A uniform format (all dicts keyed by name, or all lists) would make generic config tooling much cleaner.

### 5. Domain and routing architecture

Consolidate the current per-atlas nginx/SSL/port setup into a simpler, more provisionable model.

**Direction**:
- Wildcard cert (`*.fireatlas.org`) + single nginx reverse proxy routing by subdomain — new atlas = new subdomain, no new cert or nginx instance
- Single multi-tenant webapp process routing by subdomain/host header, replacing per-atlas webapp instances
- Static outlet files (HTML console, webmaps) served from S3/CloudFront — published atlases have zero server footprint until someone edits data
- API layer becomes optional/on-demand: only needs to be running during active editing sessions

**Why this unblocks transient atlases**: With this architecture, spinning up a new atlas is creating an S3 prefix, a DNS entry, and a config record — no new server, cert, or process. Cheap enough for demos and evaluation instances. Aspirational: lightweight enough to let visitors generate their own.

**Relationship to other projects**: Overlaps significantly with config reform (4) and S3 backend (below). Worth scoping these three together — decisions in one affect the others.

**Open question**: Support for customer-hosted deployments (someone running their own instance) is a non-goal for now but worth not designing out.

### 6. S3 as a first-class data backend
Currently S3 is used as file storage that gets pointed at. Goal: make S3 a proper backend in the atlas data access layer — either via s3fs mounting or native path abstraction in `versioning.py` / `dataswale_geojson.py`.

Scope:
- Evaluate s3fs mount vs. native S3 support in data access layer
- Ensure large datasets (point clouds, rasters) are stored and accessed efficiently
- Enable Parquet format for cloud-native computation

**Unblocks**: LiDAR/point cloud support, reduced infrastructure cost, federation data sharing.

---

## Feature Work (mix in alongside foundational projects)

### 7. 3D Terrain View — polish and next steps

**Current state** (branch `pmtiles-terrain`, wired for kennedy + SCVFD): Working 3D terrain view using MapLibre GL JS with terrain-RGB PMTiles. Accessible via "3D" button on webmap and in Maps section of all consoles. Key engineering learnings captured in MEMORY.md.

**Next steps (in priority order):**

**a. Switch terrain source to a global tile service**
We generate our own terrain-RGB tiles from COP30 data. This works but has a hard edge where our atlas ends — the atlas sits in correct terrain but the surrounding world drops to sea level at the tile boundary. A global tile service (AWS Open Data Terrarium tiles, free + public domain) gives seamless terrain at any zoom with no edges:
```
https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png
```
Our own terrain-RGB PMTiles and the `terrain_dem` inlet work is still valuable for the hillshade-as-basemap use case (2D webmap), but for the 3D geometry source a global tile wins. Low-effort swap in `3dview.py`.

**b. UI integration**
The 3dview currently has its own bespoke styling. Needs:
- Top bar consistent with the console (IBM Plex Sans, same grey, logo + atlas name)
- Control panel styled like the webmap's floating panel (right side, same cards)
- Home button back to admin console
- Font matching rest of interface

**c. Atlas layer data overlay**
Add our vector layers (roads, hydrants, structures, etc.) on top of the 3D terrain. MapLibre supports adding GeoJSON sources and symbol/line/fill-extrusion layers on top of a terrain surface. Hydrants and structures as 3D symbols on terrain would be compelling for fire departments. Moderate effort — reuse the existing webmap layer config.

**d. Satellite opacity**
The ESRI satellite basemap drape is slightly too transparent. Easy CSS/paint property tweak in `3dview.py`.

### 8. Geo-tagged photo ingest
New inlet type: ingest geo-tagged photos as a data source (GPS EXIF → point features, with photo attachments). Self-contained addition to `vector_inlets.py`.

Relatively low scope — good to slot between heavier projects.

### 8. Logistics eddy (notebook → proper eddy)
The fuel reduction / biochar logistics model exists as a notebook. Productize it as a proper eddy in `eddies.py`.

Commercially interesting and technically fun — good morale project. Candidate for SCVFD demonstration once stable.

---

## Medium-Term

### 9. LiDAR / point cloud support
New data type: 3D point cloud data from LiDAR and photogrammetry (LAZ/LAS or processed Parquet). Used for identifying water bodies, roads, terrain analysis.

Depends on S3 backend being solid (project 3). This is the path to getting the interesting applied math work done on real datasets.

### 10. Testing — stages 4 and 5
**Stage 4**: Mock-based tests for external services and cloud functionality (S3, OSM/Overture inlets, Google Sheets). Fits naturally alongside the S3 backend work (project 5).

**Stage 5**: A way for Claude to see outlet interfaces and outputs directly — both for verification during development and for richer testing. Likely involves serving static output locally and inspecting it, or screenshot-based review. Scope TBD; worth discussing before designing.

---

## Strategic / Longer-Term

### 11. Federation
Allow multiple landowners to each maintain their own atlas while sharing controlled subsets of data with regional organizations and state/federal agencies. Data ownership stays with the originating atlas; recipients get read access to shared layers.

This is an architectural direction more than a single project. Requires:
- Clean per-atlas config boundaries (project 2)
- S3 as shared-accessible storage (project 3)
- A data governance model (what can be shared, by whom, with what controls)
- Likely: a lightweight API or manifest format for cross-atlas layer references

Worth doing design thinking on early even while implementation is distant.

---

### PDF Generation: Unify Per-Region and Combined Output

Currently the system generates per-region individual PDFs and a combined PDF as separate operations. The cleaner model would be: always generate per-region PDFs first, then combine them into a single file — one code path, one pass. Right now generating the whole thing as one big PDF separately from the individual pages is redundant and clunky.

Also: `generate_pdf` flag in `outlet_runbook_qgis_atlas` currently skips all PDF generation. If we want the gazetteer to generate individual pages but not a combined PDF (or vice versa), finer-grained control is needed.

---

### Spreadsheet Import/Export Type Fidelity

Export sends raw Python values to gspread; Google Sheets auto-interprets types, causing silent corruption (e.g. string IDs like APNs displayed as scientific notation). Import uses `get_all_records()` which guesses types with no schema — `"001"` comes back as `1`, `None` comes back as `""`.

**Quick wins (can pull out early)**:
- Prefix string columns with `'` on export to force Sheets to treat them as text, preventing scientific notation on large IDs
- Normalize `""` back to `None` on import for fields that were null

**Longer term**: a per-layer type schema that survives the round-trip. Could be column header annotations, a sidecar JSON, or layer config. Should be invisible to non-technical users.

**Geometry round-trip**: geometry is serialized to a JSON string column on export and parsed back on import. Certain geometry formats cause problems — believed to be a non-standard binary or alternate encoding from an external source rather than a geometry type issue (e.g. WKB instead of GeoJSON text). Exact cases not yet catalogued. Be cautious with geometry columns from external sources; capture specific failures when seen.

Risk is moderate — mainly affects ID fields (APNs, address numbers) and edge-case geometry types. Simple point/line/polygon features are generally fine.

---

### Layer Schema

The system has several implicit schemas — notably the `editable_fields` config which defines what fields exist and are user-editable, and plays a schema-like role in the console and spreadsheet interfaces. But there's no single authoritative schema per layer, which creates friction in type fidelity, validation, SQL queries, and import/export.

**This needs a design conversation before implementation.** Questions to resolve:
- Where does schema live? In `*_layers.json` per layer, as a sidecar file, or centrally?
- What does it cover? Field names, types, nullability, display hints, editability?
- How does it relate to `editable_fields` — replace it, extend it, or unify?
- Does it get enforced at write time, read time, or both?

**Timing**: medium term, but have the design conversation soon — decisions in config reform (project 4) and spreadsheet type fidelity should not foreclose good schema options.

---

### Code/Data Separation (`app/` directory)

Currently the code repo lives at `{atlas_root}/app/` alongside the data, and `versioning.atlas_path(config, version='app')` is used throughout `outlets.py` to reach templates, the python path, etc. This is a leaky abstraction — `app` is not a version, and the path function is being abused to reach it.

This resolves naturally when we move to the container image model (roadmap project 5/6): code lives in the image, not in the atlas directory. At that point `version='app'` calls get replaced with a proper `code_root` or equivalent. Don't invest in cleaning this up before that move — just be aware it's there.

---

### Python Module Reorganization

The `python/` directory has grown organically and some responsibilities are misplaced. Notable issues:
- `gsheet_export` lives in `outlets.py` but is more naturally a paired counterpart to `import_sheet` in `vector_inlets.py` — or both belong in a dedicated `gsheet.py`
- Likely other cases where inlet/outlet/utility code has drifted into the wrong file

This should be a deliberate pass once the codebase is better tested (don't reorganize without test coverage). Scope: audit module responsibilities, identify misplaced functions, propose a clean layout before touching anything.

---

## Not Yet Scoped

- Deployment automation (CI/CD for server updates)
- Docker / containerized QGIS for reproducible PDF generation
- Advanced ML/analytics on atlas data (natural territory for applied math work once LiDAR is in)
- **Process management**: replace screen sessions for Jupyter and webapp with proper process management (systemd units, supervisor, or similar). Each atlas currently requires manually starting processes in screen which is fragile and hard to monitor. Should include: auto-start on boot, restart on failure, per-atlas process isolation, log management. Also: everything currently runs as root, which is bad practice and should be fixed alongside process management. **Jupyter near-term**: single server at `swales_dev/` root (one port, all atlases visible, data isolation enforced by notebook code). **Jupyter long-term**: JupyterHub for proper per-user workspace isolation if non-admin customers need direct notebook access.

### Rethinking the Outlet Concept

Outlets were originally pure data artifacts — static, read-only products of the pipeline. They've evolved into three distinct categories that probably deserve different treatment:

1. **Data artifacts**: webmap tiles, GeoPackage exports, PDFs — genuinely static, version-appropriate, belong in the outlet directory
2. **Interfaces**: HTML console, webedit, notebook — not artifacts, they're UIs. Questionable whether they belong in the version structure at all.
3. **Feedback channels**: webedit and notebook both accept input that flows back into the atlas as deltas — making them inlets as much as outlets. The water metaphor breaks down here.

The current model forces all three into the same structure and location. Worth a design conversation about whether to formalize these distinctions — possibly a separate `interfaces/` concept alongside inlets/eddies/outlets, or just acknowledging that some outlets are bidirectional. No action now, but don't design config reform or S3 backend in ways that make this harder to untangle later.

---

### Notebook / Outlet Versioning Tension

Notebooks generated as outlets currently live inside the version directory structure (e.g. `staging/outlets/notebook/`), but notebooks don't really belong there conceptually. Three distinct things have different version-relationship needs:

- **Webapp / API**: must live outside versions — it *switches between* versions, so it can't be inside one
- **Generated notebooks** (outlet): useful as a pre-loaded starting point for sophisticated users, but ideally you'd want to open a notebook and work across versions or compare between them — being locked inside a version conflicts with that
- **Hand-crafted notebooks** (exploratory): definitely shouldn't be in a version, more like code artifacts

**History**: notebooks have lived in three places:
1. `app/notebooks/` — part of the code repo, not version-structured, but wrong when code moves to a container
2. `{atlas_root}/notebooks/` — outside both app and version directories, which made reasonable sense
3. Current: `staging/outlets/notebook/` — inside a version, generated as an outlet

**Open question**: the generated outlet notebook is genuinely useful as a bootstrapped starting point. But maybe the right model is: generate it into `{atlas_root}/notebooks/` (or similar) rather than into the version tree, so it's accessible regardless of which version is active. This also survives the code-to-container move better than `app/notebooks/`.

### Shared Config Contamination
WVFD-specific filenames (`westport_turnouts.geojson`, `westport_dem_2m_hillshade.tiff`) are hardcoded in `shared_inlets_config.json`. Shared config should have no atlas-specific values. Fix as part of config reform (project 4) or earlier if it causes problems.

### Delta Apply Step UX
After running an inlet that writes deltas, a separate `refresh_vector_layer()` call is required before the layer is queryable. This is easy to miss and caused confusion during the gazetteer workflow. Consider: should inlets automatically trigger a refresh, or should the workflow be documented more clearly? Related to Dagster dependency tracking.

---

### Authentication & Authorization (short term: audit; long term: SSO)

**Short term — audit existing access control** (should be relatively soon — important and mostly a matter of checking): Verify that the existing htpasswd-based scheme actually gates the right things. Sensitive layer files and outlet interfaces should not be accessible without credentials. Don't assume it's correct just because it's in place.

**Longer term — real user system** (can wait; no multi-role need yet): Google OAuth SSO is the natural fit — fire department staff already have Google accounts. Roles (admin, editor, viewer, public) control both interface access and layer/outlet visibility.

**Open questions for later**:
- How does auth interact with static file serving? S3/CloudFront doesn't do htpasswd.
- Federation complicates this: a user trusted by atlas A may need scoped read access to atlas B's shared layers.

## North Star: Dagster Orchestration

The atlas was originally built with Dagster handling pipeline orchestration and data dependency tracking. It fell away during rapid feature development but is a long-term goal to restore.

**Why it fits**: inlets, eddies, and outlets are software-defined assets with natural dependencies. Dagster would know that a stale DEM inlet means the contours eddy and webmap outlet need to re-run. Right now that dependency tracking lives nowhere — it's manual.

**Key architectural detail**: the uniform method signature on all asset functions, and the `asset_methods` dict pattern, are intentional Dagster scaffolding. Preserve this. Don't make changes that break the ability to wrap these as Dagster assets.

**Historical note**: there was working code that read the atlas config and dynamically built the entire Dagster DAG from it. This code was "cleaned up" at some point but should be recoverable. Find it before building anything new.

**Sequencing**: this belongs after config reform and S3 backend are stable. Orchestrating a messy pipeline just makes the mess run on a schedule. Notebook stays for exploration; webapp stays for user-triggered one-offs; Dagster handles recurring maintenance and dependency-driven refreshes.

**Scale-to-zero synergy**: Dagster can trigger on schedule or event and spin down when idle — fits the compute-on-demand north star well.

## North Star: Scale-to-Zero Architecture

The long-term cost and scalability goal: static data in S3, compute only active when needed. Since atlases are read 99% of the time and edited rarely, there's no reason to pay for always-on servers.

**Target shape**:
- Static outlet files (HTML console, webmaps) served from S3/CloudFront — zero compute cost at rest
- API/webapp layer dormant until an edit session begins
- QGIS PDF generation as on-demand compute (Lambda container image or Fargate task)

**Implementation options**:
- **Lambda container images** (up to 10GB): avoids the 250MB package limit that makes QGIS on Lambda tricky with zip deployments. Cold start latency is the main question for QGIS initialization.
- **ECS Fargate scale-to-zero**: containers that spin up on demand. "How does static HTML wake the API" is solvable — a lightweight always-on endpoint (cheap Lambda) whose only job is to wake the real service, then the client retries.

**Key insight**: editing is rare enough that even 30 seconds of cold start is probably acceptable to users. That changes the tradeoff — optimize for cost at rest, not for instant API response.

This is a natural outcome of projects 4 (config reform), 5 (domain/routing), and 6 (S3 backend) converging. Don't design those projects in ways that make this harder to reach.
