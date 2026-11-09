# Overlook — the Atlas Progress Report

**Status:** Spike MVP on branch `claude/tender-heisenberg-j42g6i` (2026-11-09), wired
into kennedy only. See §0 for what the spike decided and built.
**Outlet key:** `overlook` (was `gauge` in the first draft; "gauge" below means
the same thing) · **Code:** `python/overlook.py`, `templates/overlook.html`,
`templates/css/overlook.css`, `templates/js/overlook.js`

## 0. Spike decisions (2026-11-09)

**Name.** The outlet is `overlook`. Visitor-facing title is the atlas title;
the page calls itself Overlook only in the browser tab.

**Delta provenance is fixed at the source, not worked around (was G3).**
Deltas exist so that every change to a layer is recorded and can be used
exactly, so every write path now names itself. The heuristics in §3.3 apply
only to legacy files.

- Filenames are `{source}__{YYYYmmdd_HHMMSS}__{action}.geojson`. The source is
  the inlet asset name, or an interactive source from
  `delta_provenance.SOURCES`: `webedit`, `webedit_upload`, `reshape`,
  `console_upload`, `sheet_import`, `gsheet_import`, `email_photo`,
  `web_photo`, `s3_photo_import`, `comment`, `move`, `create`, `api_store`, and
  `api` for an untagged `/delta_upload` client. `assetless` is refused for new
  deltas; old files read as source `unrecorded`.
- Every delta also carries a `provenance` foreign member: `source`, `asset`,
  `action`, `layer`, `feature_count`, `written_at` (UTC) and **`base_version`**,
  the newest published version when the delta was written. That answers
  "what's been added since the last publish" directly: a delta with
  `base_version == V` belongs to the release after V. It is written into the
  data rather than inferred from file times, and published versions keep it
  because they keep the delta tree.
- One entry point, `deltas_geojson.write_delta(config, layer, fc, action, source)`.
  `delta_path_from_layer` now requires `source`. Files written by external tools
  (ogr2ogr, Overpass) get the member added with `stamp_provenance`.
- `/delta_upload` takes a transport field `source` (like `apply`); the web
  editor, reshape page and console upload send theirs.
- Same-second filenames get a `_cNNN` suffix (#180). `apply_deltas` now orders by
  the timestamp field (`delta_sort_key`). With varied sources, a whole-name sort
  would apply them alphabetically by source.
- **Still not recorded as deltas:** `/clear_layer` (wipes the file directly);
  the overwrite in `/import_sheet` (`apply_deltas_overwrite` — the import
  itself *is* a `sheet_import` delta, but the wipe before it isn't); eddy and
  raster outputs, which are regenerated rather than edited. These need a
  `clear`/`replace` action in the delta model (#223), not more naming. The
  overlook shows layers rewritten without deltas as "regenerated".
- Not recorded: *who* made a web edit. The API doesn't see the htpasswd user
  today. `provenance.actor` is the place for it once auth moves into the API
  (Cognito, Phase 4).

**Open photo ingest on a public page (G7): accepted for now.** Anyone can email
a photo and it appears on the overlook. Before the photo address is promoted
widely, go back to the sender allowlist (#61) or add admin approval: a
`reviewed` property set from the console, and `overlook.is_photo` filters on
it. That function is the single place photos are selected, which is the room
left to improve. `sender` is never written to the outlet either way.

**Spike scope vs the plan below:** slices 1–3 are in (masthead, headlines,
change list and map, photos, products), plus tests (`tests/test_overlook.py`,
`tests/test_delta_provenance.py`). Not done: G2 metadata on real atlases (the
page falls back to `name`/`description`), G4–G6, G8 (refresh script + leak
check), roll-out beyond kennedy, and the help page. Pipeline outlets
(`s3_upload`) are left out of the products table (open question 4).

A public, static page per atlas that shows what the atlas holds, what changed in
the latest release, where field photos are coming in from, and the state of every
product the atlas builds. It exists to make the work visible: to the community, so
they see their contributions land and are encouraged to make more, and to funders,
so they see an active, well-run project and what their support produces.

---

## 1. Context

### What the public sees today

`console/public/` is the only public landing point. `outlets.make_swale_html`
generates it alongside the `internal`, `admin` and `technical` variants in a single
`console/` directory. The public variant is a sidebar listing outlets by internal
key (`webmap`, `runbook`) around an iframe of the webmap; the template hides
versions and publish controls for `PUBLIC`. Nothing on it says what the atlas is,
who is behind it, or that anything is happening.

Its About and Contact pages are hardcoded placeholder text in `make_swale_html`
(`info@example.org`, `(555) 123-4567`) on every atlas.

### Why a new outlet rather than a console change

- **The gauge is a data artifact, not an interface.** It's static, specific to a
  version, and computed from the dataswale, which is the original meaning of an
  outlet (roadmap, "Rethinking the Outlet Concept"). The console is an interface
  with role variants and API calls.
- **It avoids the console's security baggage.** `console/` holds four access tiers
  in one directory, which is why `atlas_store.PROTECTED_OUTLET_SUBDIRS` exists
  (kennedy's first publish leaked `console/admin/`). The gauge is one public
  directory with nothing to prune.
- **It doesn't block the console decision.** If the gauge works out,
  `console/public/` can later become a redirect to it. That's a separate project.

### Constraints it must respect

- **Publish computes nothing** (`data_architecture.md`). The gauge is materialized
  before publish, like every outlet, and reports the state as of that version. It
  says so ("as of 2026-11-05").
- **Static-first.** Plain HTML, JSON and GeoJSON with no API calls, so it serves
  identically from the box and from CloudFront.
- **Fail-closed tiering.** `federation.layer_access` treats an undeclared layer as
  `internal`; outlet `access` fails *open*. The gauge reads only layers that
  `atlas_store.is_public(federation.layer_access(layer))` (where `layer` is the layer's config dict) passes, and it writes only
  derived data it has filtered.
- **Uniform asset signature.** `outlet_gauge(config, outlet_name)`, registered in
  `outlets.asset_methods`, so Dagster can wrap it like any other asset.

### Data that already exists

| Source | What the gauge gets from it |
|---|---|
| Layer files (`layers/{name}/{name}.geojson`) | Feature counts, road miles, acres |
| Archived deltas (`deltas/{layer}/work/{asset}__{YYYYmmdd_HHMMSS}__{action}.geojson`) | Every applied change, by layer, action and time. Published versions keep the delta tree. |
| `config['dataswale']['versions']` (`%Y-%m-%d_%H-%M-%S`) | The previous version's timestamp, which bounds "this release" |
| Features with `source == "email"` | Field photos (email ingest *and* `ingest_s3_photos.py`, which both use `email_inlet.build_feature`), with `image_url`, `timestamp` and `name` |
| `config['assets']` where `type == "outlet"` | Every product, its `access` and its declared input layers |
| Outlet directories under `outlets/{name}/` | Build time from file modification times (`shutil.copytree` preserves them on publish) |

**Provenance gap.** The delta filename's first field is the asset name *only* when
an inlet wrote it with a name. Web edits, email photos, spreadsheet matches, the
`/create` boundary import and several inlets (`h3_grid` and others in
`vector_inlets.py`) all write `assetless__…`. v1 classifies with the heuristics in
§3.3; issue G3 fixes this properly.

---

## 2. Architecture

### The outlet

- **Function:** `outlets.outlet_gauge(config, outlet_name)` → returns the outlet
  path. Pure Python plus a template; no QGIS, GDAL or DuckDB required, so it runs
  in the bare local test environment and later in the API Lambda.
- **Template:** `templates/gauge.html`, `templates/css/gauge.css` and
  `templates/js/gauge.js`. Data is injected the same way as the console
  (`initializePage({...})` before `</body>`), with large data in separate files.
- **Shared config:** a `gauge` entry in `shared_outlets_config.json`:
  `fetch_type: "gauge"`, `access: ["public"]`, `interaction: "interface"`, the
  default headline list (§3.2) and limits (§3.4, §3.5).
- **Per-atlas:** add `gauge` to the atlas's assets, and to `cloud.outlets` so it
  publishes. `tests/test_json_configs.py::test_active_atlas_configs_are_current`
  should require both on every active atlas once v1 ships.

### Output layout

```
outlets/gauge/
  index.html            page shell + summary data inline
  gauge.json            the full computed summary (also the hook for a roll-up later)
  data/changes.geojson  change-map features, filtered + property-stripped
  data/photos.geojson   photo points, filtered + property-stripped
```

Context layers on the maps (atlas boundary, optionally roads) are referenced with
`atlas_catalog.layer_data_url`, the same relative form the webmap uses. Name
them in the gauge's `in_layers` so `atlas_store.plan_current_layers` mirrors them,
and so a protected layer is refused rather than published.

The two `data/*.geojson` files are *derived* data written into the outlet itself.
This is the one place the gauge looks like the retired `bake_data` (#177). It's
safe only because the gauge builds them from public layers and strips properties
to an allowlist (§4).

### Build order and "this release"

- The gauge reports on other outlets, so it is **materialized last**, after
  webmap, runbook, 3D view and console, and immediately before publish.
  `atlas_full_refresh.sh` gains a step (G8).
- **"This release"** = archived deltas (in `work/`) whose timestamp is later than
  the most recent published version in `config['dataswale']['versions']`. If there
  is no prior version, it means everything. Pending deltas (not yet applied)
  aren't in the layer, so they aren't counted as changes.
- Because the gauge is built immediately before publish, its numbers describe
  exactly the version that gets published. A gauge built in staging and *not*
  published is a preview of the next release, which is useful for admins.

### Entry points

v1: link the gauge from the public console sidebar ("Progress Report") and link
back to the webmap from the gauge. Making the gauge the atlas's front door
(redirecting the atlas root, or `console/public/`) is a later decision (§7).

---

## 3. v1 sections

Page order, mobile-first and single column, widening to two columns on desktop.
Fonts and colors follow the console (`console_new.css`).

### 3.1 Masthead

Atlas title, logo (`logo_url`), tagline, a short about paragraph, and "Data as of
{version date}" (or "Preview — not yet published" when built in staging).

Needs new atlas metadata properties (G2), all optional, all flowing through
`config.update(props)` like every other `{atlas}.geojson` property:

| Property | Use | Fallback |
|---|---|---|
| `title` | Display name | `name` |
| `tagline` | One line under the title | omitted |
| `about` | Short markdown paragraph | omitted |
| `contact_email` | Get-involved section | section omits contact |
| `photo_email` | Where to send photos | `{name}@fireatlas.org` |

### 3.2 Headline numbers

A row of stat tiles. **A tile is omitted if its layer is missing, isn't public, or
its value is zero.** There are no zero tiles and no placeholders.

The gauge asset config holds a list of headline specs; the shared default covers
common layers, and an atlas can override or extend it:

```json
"headlines": [
  {"label": "Hydrants mapped",    "layer": "hydrants",   "measure": "count"},
  {"label": "Water tanks",        "layer": "watertanks", "measure": "count"},
  {"label": "Culverts",           "layer": "culverts",   "measure": "count"},
  {"label": "Turnouts",           "layer": "turnouts",   "measure": "count"},
  {"label": "Buildings",          "layer": "buildings",  "measure": "count"},
  {"label": "Miles of road",      "layer": "roads",      "measure": "length_mi"},
  {"label": "Acres covered",      "layer": "boundary",   "measure": "area_acres"}
]
```

Measures are `count`, `length_mi` (geodesic) and `area_acres` (geodesic). An
optional `filter` (a property and its values) allows "Treated acres" from a
treatments layer. Three tiles come from the system rather than a layer, each also
omitted at zero:

- **Field photos**: features with `source == "email"` across public layers.
- **Edits this release**: archived deltas since the previous version.
- **Releases published**: `len(versions)`.

Implementation note: confirm the real layer names per active atlas when writing
the default list. The names above are illustrative.

### 3.3 What changed this release

A list grouped by layer, newest first. For example:

> **roads**: 14 reshaped, 3 added · Nov 2–4
> **hydrants**: 6 updated · Nov 3
> **buildings**: rebuilt from Overture (2,140 features) · Nov 1

Per layer: counts by action, a date range, and a source label. v1 source
classification, in order:

1. Delta asset name isn't `assetless` → that inlet's name. If the delta has more
   than `bulk_threshold` features (default 500), it's shown as "rebuilt from
   {source} (N features)" and excluded from the change map (§3.4). This covers
   rebuilds and large re-pulls.
2. `assetless` and every feature has `source == "email"` → "field photos".
3. Otherwise `assetless` → "edits". A large `assetless` delta (an `h3_grid` seed
   or a `/create` boundary import) also falls under `bulk_threshold`.

Only public layers appear. Layers written directly by eddies or raster inlets
leave no deltas. v1 lists them as "regenerated" when the layer file's modification
time is later than the previous version, and puts nothing on the map for them.

### 3.4 Change map

**One** map showing this release's changes in context. Each changed layer gets
its own color from a categorical palette, keyed in a legend that lists the layer,
its color and its counts.

What gets drawn, per action:

| Action | Geometry drawn | Style |
|---|---|---|
| `create` | The delta's features | Layer color, filled/solid |
| `reshape` | The delta's new geometry | Layer color, filled/solid |
| `match` | Resolved: `atlas_id` → the feature's geometry in the current layer file (skipped if gone) | Layer color, filled/solid |
| `annotate` | The drawn polygon (footprint) | Layer color, outline only, dashed |
| `delete` | The drawn polygon (footprint) | Layer color, outline only, dotted |

Readability:

- **Ordering, bottom to top:** basemap → atlas boundary → footprints → changed
  polygons → changed lines → changed points. Small things stay on top of large
  things.
- **Translucency:** polygon fill at about 0.35 opacity with a solid outline, lines
  at full opacity with a white casing, points as circles with a white stroke.
- **Default view:** fit to the bounding box of all drawn changes, falling back to
  the atlas boundary.
- **Pop-up:** layer, action, date, and the feature's `name` if there is one.
- **Basemap:** the webmap's default basemap, muted. Reuse its style source rather
  than defining a new one.

`data/changes.geojson` features carry only `layer`, `action`, `date`, `name` and a
`kind` (`feature` or `footprint`). All other properties are stripped. Bulk deltas
(§3.3) are excluded. A `max_change_features` cap (default 5,000) protects page
weight; when it's hit, the page says the map shows a subset.

### 3.5 Field photos

A second minimap plus a strip of recent photos.

- **Map:** every photo point in public layers (`source == "email"` with an
  `image_url`), at its **exact location** (agreed for v1; see Open Questions).
  Colored by layer, with a pop-up showing the photo, title, date and a "view on
  the map" link (the feature's `webmap_url`).
- **Strip:** the newest `photo_strip_count` (default 12) photos as thumbnails,
  newest first, each opening the same pop-up content.
- `data/photos.geojson` keeps only `name`, `timestamp`, `layer`, `image_url` and
  `webmap_url`. **`sender` is never written**, nor any EXIF or body-derived
  property.
- Images load from `scs-atlas-data`, where `*/media/email_photos/*` is already
  public. The gauge adds no new exposure of the image itself. Full-size originals
  are heavy for a strip; thumbnails are G6.
- **Moderation risk:** anyone can email a photo (`/ingest/email_photo` has no
  sender check), and it lands in a public layer and on this page. See G7 and Open
  Questions before promoting the photo address publicly.

### 3.6 Products and their status

**Every outlet in the atlas's config**, public or not, as a table:

| Column | Source |
|---|---|
| Product | Optional `title` on the outlet asset (G5), falling back to its key |
| Who can see it | From `access`: Public / Members / Admins |
| Built | Newest modification time under `outlets/{name}/`, or "not built" |
| Status | **Current**: no input layer file is newer than the build. **Needs rebuild**: an input is newer. **Built**: no declared inputs, so it can't be judged. |

- **Links only for public outlets that are in `cloud.outlets`.** Protected outlets
  are listed by name and tier with no URL. The page never links into `admin/`,
  `internal/`, `technical/` or `staging/`.
- Inputs come from the outlet's `in_layers` / `in_layer`
  (`atlas_store.outlet_layer_names` has the logic). For console-type outlets with
  no declared inputs, the status is just "built".
- The gauge excludes itself from the table.
- Modification times are a v1 proxy. They're computed at build time and frozen
  into `gauge.json`, so the S3 mirror losing them doesn't matter. A real build
  manifest is G4.

### 3.7 Get involved

Short, plain instructions: how to send a geotagged photo (`photo_email`, with the
one-line GPS check from `help/email_photo_submission.md`), how to comment on a
feature (link to `help/conversations.md`), and `contact_email` if set. If no
contact is configured, the line is omitted.

---

## 4. Privacy and security rules

The gauge is public by design. These rules are what keeps it safe, and each should
have a test.

1. **Layers:** only layers passing `atlas_store.is_public(federation.layer_access(layer))`.
   This applies to headlines, the change list, the change map and photos.
   Undeclared means internal, which means excluded.
2. **Properties:** derived GeoJSON uses a per-file allowlist (§3.4, §3.5). Nothing
   else passes through. `sender` has a specific test.
3. **Links:** no URL to a protected outlet, a role-variant subdirectory, or
   `staging/`. Protected outlets appear as names only.
4. **Leak check:** `atlas_full_refresh.sh` already runs a role-variant leak check
   on publish. Extend it to grep the published `gauge/` for `sender`, `staging/`
   and protected subdirectory paths (G8).
5. **Listing protected outlet names is acceptable.** It reveals what the atlas
   builds, not its contents or the API. (Contrast `console/admin/`, whose HTML
   spells out the mutating API.) Revisit if an outlet name is itself sensitive.

---

## 5. Implementation plan

Each slice is shippable on its own.

1. **Skeleton.** `outlet_gauge`, the shared config entry, the template, the
   masthead (with fallbacks) and the headline tiles. Wire into one atlas
   (kennedy or scvfd), materialize, check locally and on staging.
2. **Changes and products.** Delta scan and classification, the change list, and
   the products table. Pure Python, unit-testable against fixture delta trees.
3. **Maps.** The change map and the photo map/strip, with the derived GeoJSON and
   the property allowlists.
4. **Roll-out.** Add to every active atlas and its `cloud.outlets`, extend the
   config test, add the `atlas_full_refresh.sh` step and leak check, link from
   the public console, and add a help page (`help/progress_report.md`, audience
   `user`, linked from `user_manual.md`, as `tests/test_help_docs.py` requires).

### Tests (`python/tests/test_gauge.py`)

- The delta scanner over a fixture tree: window boundary at the previous version,
  `work/` versus pending, classification rules, `bulk_threshold`.
- `match` resolution via `atlas_id`, including the missing-feature case.
- Headline omission: missing layer, non-public layer, zero value.
- Tier filtering: an undeclared-access layer never appears anywhere in the output.
- Property allowlists: `sender` and arbitrary properties are absent from both
  GeoJSON files.
- Products: no `href` for protected outlets; status logic with synthetic
  modification times.

---

## 6. Issues to file

Labels follow `ISSUES.md`.

| # | Title | Labels | Notes |
|---|---|---|---|
| G1 | Gauge outlet v1 — atlas Progress Report | `P1` `map-ui` `user-facing` `commercial` | Epic; links this doc. Slices 1–4 as checklist. |
| G2 | Public atlas metadata (`title`, `tagline`, `about`, `contact_email`, `photo_email`) and real About/Contact pages | `P1` `config` `user-facing` | The placeholder `example.org` text in `make_swale_html` is live on every atlas today. Feeds the gauge masthead. |
| G3 | Record delta provenance: source kind on every delta | `P2` `data-integrity` `tech-debt` | Web edits, email, sheet import, `/create` and several inlets all write `assetless__`. Either a real asset or source name in the filename (`webedit__`, `email_photo__`, `sheet__`, `create__`) or a sidecar. Bundle with #180 (second-resolution filename collisions), which touches the same naming. |
| G4 | Outlet build manifest (`built_at`, input layers and their versions, code SHA) written by `materialize` | `P2` `reliability` `dagster` | Replaces modification-time staleness. Also useful to the console and Dagster. |
| G5 | Friendly `title` / `description` on outlet assets | `P3` `config` `ux` | Used by the gauge and the public console instead of internal keys. |
| G6 | Thumbnails for ingested photos | `P2` `email-ops` | Generate at ingest next to the original in `scs-atlas-data`, and record `thumb_url` on the feature. |
| G7 | Moderation or gating for publicly displayed field photos | `P1` `security` `email-ops` | The photo address will be promoted on a public page while ingest has no sender check (#61). Options in Open Questions. Decide before G1 roll-out. |
| G8 | `atlas_full_refresh.sh`: materialize gauge last; extend leak check to `gauge/` | `P2` `reliability` `security` | |
| G9 | Asset graph: let an outlet depend on other outlets (gauge after all outlets) | `P3` `dagster` | Edges are ordering-only, so this is a declaration only (for example `after_outlets: "*"`). Until then, ordering lives in the refresh script. |
| G10 | Fix `CLAUDE.md`: `bake_data` is described as current but was removed | `P3` `tech-debt` | `outlets.py` notes it's gone (#177); CLAUDE.md's Phase 2 section still documents it. |

---

## 7. Open questions

1. **Name.** `gauge` in code and "Progress Report" for visitors is the working
   choice. Alternatives considered: Field Report (community-first), Overlook,
   Active Progress Report.
2. **Photo moderation (G7).** Options: (a) display only photos from senders on an
   allowlist; (b) hold field photos out of the gauge until an admin approves them
   (a `reviewed` property set from the console); (c) accept the risk until #61. (b)
   is the most robust and fits the delta model; (a) is cheapest.
3. **Exact photo locations.** Agreed for v1. Revisit for photos taken on private
   property, or if moderation is weak: options are snapping to an H3 cell or
   hiding the points per layer.
4. **Which outlets count as "all".** v1 lists every `type == "outlet"` asset. Do
   pipeline outlets like `s3_upload` assets and `stac` belong in the table, or only
   `interaction` of `interface` / `download`?
5. **Default headlines.** Which layers, across the active atlases, actually make
   the shared default list? Should headlines carry a "+N this release" delta?
6. **Bulk threshold.** Is 500 features the right line between "edits" and "rebuilt
   from source", or should it be per layer?
7. **Front door.** When should the atlas root (and `console/public/`) point at the
   gauge? Likely at the cloud cutover, when the apex flips to CloudFront.
8. **Staging preview.** Should admins get a link to the staging gauge from the
   admin console as a pre-publish check ("this is what the public will see")?
9. **Funder recognition.** Named or logo'd on the page, attributed per project
   later, or both?

---

## 8. Future extensions and improvements

**Cheap follow-ons (next after v1):**
- **Activity over time:** a weekly edits chart split by source, from the same
  delta scan.
- **Featured views:** preset views and regions (which already have `webmap_url`
  share links), the 3D view, and the atlas's tour on salmoncreeksystems.com.
- **Release history:** past versions with dates and a one-line summary each, built
  from each version's own `gauge.json`.

**Bigger ideas:**
- **Projects.** A curated project layer (manual-only, edited in webedit) for
  on-the-ground work like treatments, burn piles, biochar runs and restoration,
  each with status, funder and photo. The gauge summarizes and maps them, and TA
  projects become a filter over an atlas rather than a separate page.
- **Portfolio roll-up.** A site-level page aggregating each atlas's `gauge.json`
  for funders and regional organizations. It pairs naturally with STAC and
  federation v2.
- **Drafted release notes.** Generate a per-atlas update (email or post) from the
  gauge's change summary, a per-atlas counterpart to `documents/release_notes/`.
- **Raster change.** Use STAC checksums to flag changed rasters and eddy outputs,
  and perhaps show before/after thumbnails.
- **Change timeline.** Animate deltas over a release or a season on the change map.
- **Usage.** Public visit counts once CloudFront logs replace the box's GoAccess
  report.
- **Comments stream.** Recent comments on features, anonymized, as a second
  engagement signal.
- **Embeds.** A small widget of headlines and the latest photo that partners and
  funders can embed on their own sites.
- **Freshness.** Scheduled publishes (via Dagster) so the gauge never lags far
  behind the work.
