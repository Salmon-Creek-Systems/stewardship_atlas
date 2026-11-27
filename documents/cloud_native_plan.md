# Cloud-Native Migration Plan

**Status:** Draft / agreed direction · **Started:** 2026-08-10

A phased plan to move Stewardship Atlas from a single internet-facing EC2 box to a
serverless, cloud-native architecture: static reads on S3/CloudFront, API and light
compute on Lambda, QGIS rendering as an async worker (on a box for now, containerized
later). Ordered by dependency, not calendar. Each phase stands alone and banks value;
none closes a door.

---

## Why (the incident that motivated this)

An August 2026 outage plus a security review revealed that the outage, the exposed
`.git`, the unauthenticated API, and the fake auth were **one architectural root**, not
four bugs: everything runs fused on one internet-facing box that **serves live from a git
working tree**.

| Symptom | Root cause |
|---|---|
| An OOM took down *serving*, not just the render | Static serving + heavy compute share one host's RAM |
| `.git` was downloadable by scanner bots | nginx docroot **is** a live checkout, not built artifacts |
| Unauthenticated, internet-facing mutating API on `:9000` | API + static + compute all on one host, all public |
| Prod == `swales_dev`, `--reload`, no memory history | No separation of prod/staging; running code is an editable checkout |

The fix is not "patch four things" — it is to **stop fusing three workloads on one public
box**:

1. **Read path** — serving already-generated outlets/data. Static, cacheable, zero compute.
2. **Mutating/compute API** — edit, delta, refresh, publish, materialize. Occasional, needs auth, should be private.
3. **Heavy batch compute** — QGIS renders, terrain tiles, COG. Bursty, memory-hungry.

The static-first design is the key asset: the read path can leave the box entirely, which
is where most of the security / cost / stability win lives.

---

## Target architecture

- **Reads:** generated outlets/data in **S3**, served by **CloudFront** (public, cached, no server).
- **API + light compute:** **Lambda** (container image — geo deps are heavy). Scale-to-zero.
  Submit → spinner → poll UX absorbs cold starts. Handles config, deltas, non-QGIS eddies
  (COG/tiles/pmtiles), publish-snapshot, webmap/html/console generation.
- **QGIS renders:** a **small private box** as an async SQS worker for now, with a clean
  path to lift into a Lambda container later.
- **Auth:** **Cognito with Google SSO** — passwordless. Group-based authorization,
  fail-closed. **CloudFront is the single front door**; public prefixes served static,
  protected prefixes gated (Lambda-proxy first, edge-auth later).
- **Data tiering:** private raw dataswale (IAM-only) / public outlets (open) / protected
  outlets (Cognito-gated). The outlet's existing `access` field drives its destination
  prefix. Layer-level granularity.
- **Deploy:** one **CDK** stack, single source of truth. **GitHub Actions → ECR →
  `cdk deploy`** (container builds in CI; local Docker available as fallback). Per-atlas
  cutover behind a temp hostname, apex flipped last.

### Access model (Cognito + Google)

- **Enforcement is binary and fixed in infra:** a resource is public (CloudFront→S3) or
  authenticated (gated). Two paths, that's all infra knows.
- **Authorization is Cognito groups** — data, not code. Groups per atlas *and* role:
  `scvfd-admin`, `scvfd-internal`, `wvfd-admin`, … The gated path reads the JWT `groups`
  claim and intersects it with the outlet's required `access`.
- **Google is the login only.** Identity from Google; authorization stays in Cognito groups.
  A federated user lands with **no groups → no access** (fail-closed). Onboarding /
  role change = a group-membership edit, **no deploy**. Optional Cognito Lambda trigger can
  restrict logins to an email domain/allowlist.
- **Net:** no passwords to store, leak, or rotate. Deletes the htpasswd problem entirely.

### Why not serve everything through Lambda

Public reads are the high-volume path (tiles, geojson, PMTiles/COG range requests, JS/CSS,
PDFs). Routing those through Lambda pays an invocation per asset, eats cold-start latency,
fights the 6 MB response limit on big binaries, and throws away the static-first property
that lets serving survive a render OOM. The unifier is **CloudFront** (one front door,
per-prefix rules), not Lambda. Tiers collapse at the **policy** layer (Cognito groups), not
the serving layer.

---

## Phases

Effort sizes are rough magnitude (S/M/L), not dates.

### Phase 0 — Toolbench cleanup & acute safety · *S, now*
*Theme: get the workshop in order. Close the one live exposure while we're in here.*

- **0a. Fix laptop `aws`/`cdk`** — resolve the pkgsrc-env/PATH gap that hides `aws`. Unblocks all CDK work.
- **0b. TTY/terminal fixes** — restore the lost Ghostty config (missing `~/.config/ghostty/config`, plus the stray misnamed empty file); fix scroll breaking when SSH'd to the server (likely `TERM`/terminfo or screen mouse-mode; `TERM=xterm-256color` is the thread).
- **0c. Acute safety (rides along, still urgent)** — close `:9000` + Jupyter at the security group; single-flight + `limit_req` on heavy endpoints; swapfile. The open API is live *now* and the migration is weeks out, so this can't wait.
- **0d. Repo hygiene** — `git rm` committed htpasswd + `.gitignore`. Password mechanism is being replaced by Google SSO anyway.

**Deliverable:** safe, stable box; working local deploy tooling.

### Phase 1 — CDK stack skeleton + CI/CD spine · *M*
The cloud-native substrate, deployable from CI, before app logic moves.

- **1a. Single CDK stack** — buckets (`private-data`, `public-outlets`), CloudFront + Origin Access Control, ACM cert (us-east-1), Cognito pool with Google IdP + groups, SQS queue, API Lambda placeholder, IAM. Temp hostname in Route 53 for validation.
- **1b. GitHub Actions → ECR → `cdk deploy`** — container image built in CI. Delivers the long-wanted GitHub-Actions deploy and permanently kills the live-checkout / `.git` / `--reload` class (prod becomes an immutable image).

**Deliverable:** substrate exists and self-deploys; nothing customer-facing changes yet.

### Phase 2 — Read path to S3 + CloudFront · *M, first visible win*
A strict subset of the end state; banks security/burn/stability early.

- **2a. Outlet-write to S3 by tier** — `publish` mirrors a version's public outlets to S3. *(`python/atlas_store.py`, hooked from `versioning.publish_new_version`.)*
- **2b. Public outlets bake their own data** — the webmap copies the layers it uses into its own `data/` dir instead of referencing `../../layers/`, so raw data never has to be public (fail-closed).
- **2c. CloudFront front door** — public served static. Cut over **kennedy** on the temp hostname, validate, then per-atlas.
- **2d. Flip traffic** to CloudFront once validated. **Deferred into Phase 4 — see below.**

**Deliverable:** every atlas's public outlets are published to S3 and served by CloudFront, verified. Traffic still goes to the box.

#### Status: 2a–2c DONE (2026-08-21)

All eight atlases cut over and verified — kennedy, layt1, gilhamhike, napachar, king_range, fhe, scvfd, westport. 563 objects in `scs-atlas-outlets-prod`. Allowlists exactly respected, role variants 403 everywhere, no stale `../../layers/` references.

`scripts/atlas_full_refresh.sh <atlas>` runs the per-atlas sequence and verifies the published copy, including a role-variant leak check on every publish.

#### 2d is deferred into Phase 4, deliberately

2d was written assuming Phase 2 would move *all* reads. We narrowed it to the public tier (protected outlets stay on the box until the API moves), and that quietly pushed 2d's remainder into Phase 4's dependency set. Recording it so it doesn't later read as skipped work.

**2d's stated deliverable is unreachable as things stand.** "Box goes private (reads leave it)" cannot hold while protected outlets and the `:9000` API still live there — a CloudFront origin pointing back at the box requires the box to stay reachable. That outcome needs Phase 4, not more Phase 2 effort.

What 2d would require, and what survives:

| Piece | Survives Phase 4/6? |
|---|---|
| Second CloudFront origin → the box, for protected outlets and the API | **No** — deleted at Phase 4. The bulk of the work |
| Path-rewrite for legacy URL shapes (`/staging/outlets/…`, `/CURRENT/outlets/…`) | Yes — but Phase 3 sets what it rewrites *to* |
| DNS flip | Yes, and trivial whenever done |

Doing 2d before Phase 3 means building the dual-origin config that Phase 4 deletes, and building the path-rewrite against a URL scheme Phase 3 is about to change (`current/` likely becomes a pointer resolved at the edge). After Phase 4, 2d collapses to a DNS change plus one rewrite function built against a settled scheme.

**Cost of waiting:** the published copies sit unread (245 MB, pennies), and the "a render can't take down the site" win is not yet banked because no traffic uses them. Nothing structural is lost.

#### Scope decisions taken when Phase 2 started (2026-08-17)

- **Public tier only.** Protected (admin/internal/technical) outlets stay on the box behind nginx until Phase 4. The admin consoles are mostly calls to the API, which doesn't move until then — building a Cognito proxy into the placeholder Lambda now would be throwaway work. *SSO-gated protected outlets move from the Phase 2 deliverable to Phase 4.*
- **Published versions only.** `staging/` stays on the box as the working copy; CloudFront serves published content. "Static reads are published reads."
- **No versioned prefixes in S3 yet.** Publish writes straight to `{atlas}/current/`, so URLs stay static and no edge resolver is needed. Version *history* already lives on the box. Phase 3 revisits this — once S3 is the source of truth, a pointer object plus a CloudFront KeyValueStore lookup is the better shape.
- **The local `CURRENT ->` symlink is untouched.** Replacing it would break nginx serving and `reset_staging()` on a production box for no Phase 2 benefit. The S3 side gets an equivalent `{atlas}/current.json` pointer object instead — which is the "two implementations of one interface" idea in miniature.
- **Publish never fails on a failed push.** `publish_public_outlets` logs and returns an error dict. The box is still serving, so the worst case is a stale CloudFront copy.

#### The public-by-default hazard

`access` is optional and absent means public (`atlas.py` does `.get('access', ['public'])`). That default predates anything being genuinely world-readable, and it sweeps in outlets that must not be: **`sqldb` builds an `atlas.db` containing every layer**, including ones only an admin-only webmap references. `stac` and `spreadsheet_export` default the same way.

So tier alone does not decide what gets published. `cloud.outlets` is an explicit allowlist and is the recommended way to cut an atlas over; publishing an outlet on the strength of a *missing* `access` field logs a warning. The allowlist can only ever narrow — a name in it that resolves to a protected outlet is still refused.

#### A second granularity problem: role variants inside one outlet

Tier-per-outlet is not fine-grained enough either. `html` and `console` build **all four** role variants into one directory, so the outlet reads as public via its `public/` variant while the same directory carries the admin console. kennedy's first publish exposed `html/{admin,internal,technical}/` and `console/admin/` on CloudFront with no auth — no *new* capability, since the `:9000` API is already open, but the `html/` variants had been htpasswd-gated on the box and a discoverable admin UI lowers the effort to abuse it.

`atlas_store.PROTECTED_OUTLET_SUBDIRS` prunes those subdirectories. Because publish mirrors rather than merely uploads, a re-publish deletes already-leaked keys on its own.

#### Per-atlas config

```json
"cloud": {
  "enabled": true,
  "outlets_bucket": "scs-atlas-outlets-prod",
  "distribution_id": "E1A5S5MB0K3FZG",
  "outlets": ["webmap", "console", "html", "3dview", "runbook"]
}
```

Absent or `enabled: false` → publish behaves exactly as before. That flag is the per-atlas cutover switch. `bake_data: true` on a webmap asset turns on 2b for that outlet.

### Phase 3 — S3 data-layer refactor · *L, the long pole*
Makes the *source* data S3-native so compute can be stateless. The real engineering.

**Status: the code is complete and rehearsed on the staging substrate (2026-09-15); nothing is in production.** Branch `feature/cloud-phase3-compute`, issue #159.

- **3a. Read path to S3 + CloudFront** — DONE, merged to `main` `ac03d8a` (2026-09-10). Publish mirrors a version's public outlets and the layers they reference to `{atlas}/current/`, so one built artifact resolves `../../layers/…` against the local tree on the box and against S3 on CloudFront. `cloud.enabled` is gone; S3 is unconditional and `cloud.outlets` is a required, fail-closed allowlist.
- **3b. S3-native compute, via sessions** — a *session* is: take a per-atlas S3 lease (conditional PUT) → hydrate a local workspace laid out exactly like `{data_root}/{atlas}/staging/` → point `config['data_root']` at it → run the **unchanged** materializer → hash-diff write-back → release. A Dagster cascade is one session, never one per asset.
- **3c. Publish becomes a STAC version.** No `shutil.copytree`, no local version directory, no `CURRENT` symlink. A version is a Catalog naming immutable, version-stamped objects; outlets are Items alongside layers, carrying their entry point as an asset and `derived_from` links to the layer Items they were built from. Unchanged layers and outlets are *referenced*, not re-stored.

**Deliverable:** no local-disk/symlink dependence; S3 is source of truth. Box still runs the API, now statelessly — the precondition for Lambda.

#### The correction that matters: QGIS never needs `/vsis3/`

This section previously said "S3-native materialize path for inlets/eddies/outlets (QGIS excepted — reads via GDAL `/vsis3/`)". That framing is wrong, and dropping it is what made Phase 3 tractable.

Compute is never given an S3 path. The session hydrates a real directory and every materializer — QGIS, gdal2tiles, rasterio, geopandas, `shutil` — keeps opening real files, unchanged. The boundary moved to the *edges* of a run rather than into the middle of it, which is also why the "do not build a `Path` lookalike" warning below stopped being a live hazard: nothing hands a lookalike to anything.

The same correction applies to **Phase 5**: an SQS render worker does not read layers via `/vsis3/` either, it opens a session like everything else.

Reading S3 directly is still worth doing eventually, but as an *optimization* a materializer opts into (COG via `/vsis3/`, GeoParquet via DuckDB httpfs — `overture_duckdb` is the precedent), letting the session skip hydrating those inputs. The wins concentrate in big rasters and shared sources; most layers are too small to benefit. That is issue #174, not Phase 3.

#### What was actually built

| module | role |
|---|---|
| `atlas_lock.py` | per-atlas lease via S3 conditional PUT; TTL, heartbeat renewal, expired-lease takeover |
| `atlas_workspace.py` | hydrate/write-back planners and executors; sha256 manifest; the delta archive is never hydrated and never deleted |
| `atlas_session.py` | `open_session()` — lock, hydrate, run, write back, release; read-only sessions take no lease |
| `atlas_shared.py` | shared `/root/data` files fetched on demand into a per-workspace cache; `staging/local` symlinked to it |
| `atlas_seed.py` + `scripts/seed_atlas.py` | the one-time move of the box's staging trees into S3 |
| `atlas_catalog.py` / `federation.py` | the version Catalog: layers and outlets as Items, reuse by checksum, history read from S3 |

**Failure semantics.** The body raises → nothing is written back, and the next hydrate resets the workspace. The write-back fails part-way → a marker file means the next session on that workspace finishes it *before* hydrating, and uploads go before deletes, so an interruption can leave a consumed delta still pending but never lost. The lease is lost → the final renew raises and nothing is written back. Side effects outside the workspace (`s3_upload` outlets, COG pushes, SES) are not rolled back.

**A successful run that deleted things is obeyed.** That is the one failure mode worth stating plainly: write-back cannot tell a deliberate removal from a mistaken one, so a wipe reaches S3. Recovery is the private bucket's object versioning — which is why its noncurrent retention is a year, and why `scripts/session_race.py` proves the recovery against the real bucket rather than assuming it.

**Interactive edits do not queue.** `delta_upload` writes the delta straight to S3 without the lock and applies it only if the atlas is free; otherwise it stays pending, which the delta system already supports. A running session leaves it alone, because write-back only deletes what its own manifest vouches for.

#### Rehearsal

`scripts/staging_rehearsal.sh <atlas>` runs the whole cutover path against the staging substrate: seed → config rebuild → materialize → publish → verify through CloudFront. Since Phase 3 it reads the box read-only and mutates nothing there. `scripts/session_race.py` covers the parts a unit test structurally cannot — genuine lease contention across threads, heartbeat renewal past TTL, wipe and recovery, a full publish against the real object store.

Green on `kennedy`, `scvfd`, `westport`, `fhe` (2026-09-15).

#### Still true, and still load-bearing

**Do not build a `Path` lookalike.** Third-party libraries (rasterio, QGIS, geopandas, shutil) call `os.fspath()` on whatever they are handed and then open a real file; partial emulation fails silently inside a dependency. The workspace exists so this never has to be attempted.

**Do not convert the 142 `versioning.atlas_path()` call sites.** `outlets.py` has 58 and `eddies.py` 27, all reaching around `dataswale_geojson` straight to the filesystem. They keep working untouched because `config['data_root']` is what the session repoints — one assignment moves every one of them.

**What breaks under S3**, in order of how much it hurts: call granularity (a free `.exists()` becomes a network round trip — the failure mode is *correct but unusably slow*), mutation semantics (no rename, no append, no symlinks), and read-modify-write concurrency. The workspace answers the first two by not being S3; the lease answers the third.

#### Not done

- **mineralkinsey cannot go through this pipeline** — still the two-file config format (`MineralKinsey_*.json`), so there is no single-file source for `build_atlas.py` to read. Needs #100 first.
- ~~**#181 — site-wide `/local/` assets have no cloud home.**~~ **Done.** The console stylesheets, help pages, manuals, about/contact and the default logo are built from the repo by `python/site_assets.py` and published to the `local/` prefix at the root of the outlets bucket by `scripts/publish_site_assets.py` — which the distribution's default behaviour already serves at `/`, so the baked `/local/...` URLs resolve with no CloudFront change. **Run that script once at cutover**; an atlas materialize deliberately no longer regenerates them. What is left is cosmetic: the prefix name (#182) and the unsprited-icon fallback (#183).
- Six atlases have no `cloud.outlets` allowlist and would publish nothing; `abi_demo` has an empty one deliberately.

### Phase 4 — API → Lambda, and the one cutover · *L*

**Plan changed 2026-11-17.** Phases 3 and 4 land in production together, in **one cutover**: all active atlases at once, about a day of downtime, no per-atlas strangler. Phase 3 is not deployed on its own, because once S3 holds the data the box's nginx has no local `staging/` or `CURRENT/` to serve, and the only alternative was a throwaway nginx-serves-the-workspace step. The big parts of Phases 5–6 (containerized QGIS, retiring the box) come after the cutover; the box keeps QGIS, privately.

**Deliverable:** fireatlas.org served by CloudFront; the API on Lambda; S3 the only source of truth; the box running only queued heavy jobs, with no public exposure.

#### 4a. The API as a Lambda container image

FastAPI in the `atlas-api-{env}` image (Lambda Web Adapter), behind the existing CloudFront → function URL path. `infrastructure/api/Dockerfile` copies `python/`, `templates/` and the shared `configuration/` files; CI builds it as today. Every request that touches atlas data already runs in a session (Phase 3), so the handler needs no new storage code — the workspace root becomes `/tmp`.

#### 4b. Which jobs run where

Lambda has no lasting disk, a 15-minute limit and 10 GB of `/tmp`. So jobs split in two:

- **On Lambda:** delta apply, config rebuild, config edits, webmap/webedit/console/3dview/overlook materialize, publish, versions/rollback, SQL query, sheet import/export, email/web photo ingest.
- **Queued to the box:** QGIS outlets (runbook, game_kit board), slides (headless Chromium), and any inlet or eddy that is slow or large (DEM, hillshade, terrain, lidar, Overture, H3 at fine resolution). This is a minimal Phase 5a — the existing `atlas-render-{env}` SQS queue, a worker on the box that opens a session like any other compute, and a status object the console polls. Which assets go to the queue is a per-asset flag, not a hardcoded list.
- **Jobs, as built (2026-11-18):** on Lambda a background task never runs, because the container freezes once the response goes out. So *everything* that answers "started" and gets polled is a job: publish, `/refresh_layer`, `/materialize_asset`, and `/refresh` of a box asset.
  - `python/atlas_jobs.py` keeps each job's status at `{atlas}/jobs/{id}.json` in the private bucket (queued → running → succeeded|failed). `route()` reads the asset's `runs_on` (`box` is set on 29 shared templates: QGIS, slides, game_kit, DEM/terrain/hillshade, Overture, LANDFIRE…). Box assets and cascades go to `atlas-render-{env}`; everything else, publish included, goes to `atlas-jobs-{env}`.
  - Consumers live in `python/atlas_job_consumer.py`: a jobs Lambda (same image, created once task 5 ships it) and the box worker (`scripts/atlas_worker.py`, systemd). The box takes one job at a time, keeps the message invisible while it runs, waits up to 10 min for the lease, and does a full hydrate. A redelivered finished job is skipped; a busy atlas retries after 60 s.
  - `/job-status?swale=&id=` replaced the three in-memory status endpoints, and the console polls it by job id.
  - Dagster is unchanged: a job's runner calls it in-process. SQS stands in for the Dagster daemon, which would need an always-on host.
  - Verified on staging: a scvfd runbook job went queued → running → succeeded on the box worker, its PDFs were written back to staging, and the staging catalog was updated. Kennedy's runbook makes 0 pages on the September seed even run by hand (0 of 1 region reprojected; hydrants/lpss/ponds missing). That is a data or runbook problem to look at with the fresh seed, not the job path.
  - Collapsing duplicate jobs (repeat clicks, a refresh covered by a queued cascade) is deferred.
  - Cutover: rebuild every config (`config_only`) so the built configs carry `runs_on`; until then everything routes to Lambda.
- **API and jobs Lambdas, as built (2026-11-19, task 5).**
  - The image uses the OSGeo GDAL base (`ghcr.io/osgeo/gdal:ubuntu-small-3.9.3`), chosen over the AWS Python base because light paths shell out to `ogr2ogr`, `gdal_translate` and `gdalwarp`. It adds `awslambdaric` and pip pins matching the box, and installs DuckDB's spatial extension at build. CI builds it from the repo root without attestations (Lambda rejects an OCI index) and smoke-imports its entry points before pushing.
  - Two handlers share the image: `api_handler` (Mangum, strips CloudFront's `/api` prefix) and `jobs_handler`. At cold start, `lambda_env.prepare()` gives a writable `HOME` under `/tmp`, the DuckDB extension, and the gspread service account from Secrets Manager (`atlas/gspread-service-account`). A missing secret turns off only its feature.
  - QGIS, game_kit and slides materializers load lazily (`atlas.LAZY_MATERIALIZER_MODULES`). `test_lazy_materializers` checks that every asset needing a box-only library resolves to `runs_on: box`.
  - `/refresh`, `add_layer`, `edit_layer` and `delete_layer` are jobs, so none can outlast the API's 30 s timeout.
  - Verified on staging: `/api/job-status` through CloudFront; a kennedy webmap job on the jobs Lambda took 21 s, cold start included; a kennedy publish job took 17 s, with webmap, console and 3D view returning 200 from `current/`.
  - Cold start, after making eddies, geopandas, pandas and nbformat load lazily (webapp now imports 1,093 modules instead of 2,082): 2.7–4.2 s per cold request (init 2.0–2.9 s), versus 5.7–7.7 s before. The first request after a new image takes about 18 s (it was 68 s), and warm requests about 0.4 s. If that is still too slow in use, add memory or provisioned concurrency (~$20/month at 2 GB).
  - **The staging API is unauthenticated until task 6 is deployed** (Cognito + signed cookies): anyone who finds the CloudFront URL can write to staging.
  - The OIDC trust allows `feature/cloud-phase3-compute` (staging only; the workflow refuses prod off main). **Revert it at cutover.**
- **Staging CDK deploys need all three flags:** `cdk deploy AtlasCloud-staging -c env_name=staging -c atlas_domain= -c certificate_arn=`. `cdk.json` pins the prod domain and cert, so without them staging tries to claim `next.fireatlas.org` (a 409 that rolls back, 2026-11-18).

#### 4c. Declared inputs: hydrate only what a job reads

A full staging copy is fine on the box, where the workspace cache persists and only changed files download. On Lambda every cold start would be a full copy (westport ~800 MB). So each asset declares what it reads, and the session hydrates only that plus `atlas_config.json`:

- Most of it already exists: `in_layer`, `in_layers`, and an outlet's `in_layers`.
- Undeclared reads found so far: the console checks the webmap exists; `sqldb` reads every layer; inlets read `local/`; delta apply reads the layer's own `deltas/`.
- **Verified, not trusted** — built 2026-11-18 as `python/asset_inputs.py` plus `scripts/check_declared_inputs.py`, a *differential* check: each asset is materialized in a full and in a declared-only workspace (read-only sessions) and every file written must match. That catches reads by GDAL/QGIS, which a Python audit hook cannot see; the hook only names missing files Python opened. Green for webmap/webedit/3dview/console on kennedy, scvfd, westport, fhe. It found the console's undeclared `layers/attributions/` read and a real `atlas_shared` bug (the shared-file memo was per process, not per cache, so a second or recreated workspace lost every sprite — a warm Lambda with a cleared `/tmp` would have hit it).
- **Wired (2026-11-18):** `open_session(inputs=config -> prefixes | None)` hydrates the config, works out the prefixes from it, then hydrates those, all under the lease and against one listing, so the job runs on the config its inputs were planned from. The following use it:
  - delta apply, photo ingest, comment/store, gsheet import/export, clear/copy layer: the layer and its deltas;
  - `/refresh` and `/materialize_asset`: `asset_inputs`;
  - `/refresh_layer` without cascade: in rebuild mode, also its inlets' inputs;
  - add/edit layer: the layer plus the outlets they re-materialize. `add_layer` touches `sqldb`, so in practice it gets a full hydrate.

  A cascade, `delete_layer`, `save_config` and read sessions hydrate everything. Narrowing a cascade to its Dagster selection is left for later; Scot will write it. The differential check also takes `layer:<name>` for a delta apply; it seeds `uuid4` so that features minted without an `atlas_id` match across the two runs. Green for `roads` and `regions` on the four rehearsal atlases.
- Write-back (done, `75f2bf6`): a partial hydrate records every object it did not download as `{etag, hydrated: false}`. A write over one is an `If-Match` overwrite; its absence is never a delete, so a partial hydrate cannot read as a wipe.

#### 4c′. Publish promotes a staging STAC catalog (built 2026-11-18)

Publish used to walk a hydrated staging directory and hash every file, because staging had no index. Now it has one, in the same format and place as the version catalogs: `{atlas}/catalog/staging/` in the outlets bucket, one Item per layer and per outlet directory, one asset per file with `file:checksum`, size and S3 ETag; hrefs are the private staging objects. It is not a child of the root catalog (whose children are versions) and is written `no-cache`.

- **Written by** session write-back only (under the lock). The files about to change are recorded in the write-back marker first, so a resumed write-back still catalogues uploads that landed before the crash.
- **Built by** the seed (`staging_catalog.rebuild`, from the listing and each object's `sha256` metadata). **Any out-of-band change to staging in S3 — a versioning restore, direct puts — must end with a rebuild.**
- **Publish** reads it (`staging_tree.CatalogTree`), reuses by checksum, and server-side copies changed objects to the version archive and the public mirror. It hydrates only the config, so it runs on Lambda. It refuses an empty staging catalog when the config declares layers, which would otherwise publish an empty version and prune the public site.
- Rehearsed green on four atlases; `session_race.py` 13/13 against real S3.

Direct S3 reads (#174) stay a later optimization, worth it only for large inputs (COGs via `/vsis3/`, Overture via DuckDB httpfs).

#### 4d. Atlas configs live in S3

Each atlas's source `{atlas}.geojson` lives at `staging/atlas.geojson`, seeded from `configuration/` and versioned by the private bucket. The repo copies of the active atlases are deleted at cutover so there is one copy. `shared_*.json` and starters stay in git — they are code and ship in the image.

- `scripts/atlas_config.py pull <atlas>` copies it out through a read-only session; `push <atlas>` takes the lock, prints a diff against S3, validates, writes it back, appends a line to a change log in the atlas's staging, and optionally runs a `config_only` rebuild.
- Built: `python/config_checks.py` holds the rules; `tests/test_json_configs.py`, `atlas_config.py push` and every console layer action (`atlas.write_source_geojson`) call it, and a console edit that would *introduce* a problem is refused.
- This closes #195 (console edits never committed).

#### 4e. Traffic flip (inherited from 2d)

- **Public outlets:** on CloudFront under `{atlas}/current/`, and (task 7, 2026-11-25) every publish is also served at `{atlas}/{version}/`, the box's URL shape. A version's prefix is written once by server-side copy (`atlas_store.serve_version_copy`) and never pruned. Versions published before this are not backfilled (Scot: we can lose old versions). `current/` stays a copy, not a KeyValueStore pointer, for now. A CloudFront Function rewrites the legacy shapes: `/{atlas}/CURRENT/outlets/…` → `/{atlas}/current/outlets/…` and `/{atlas}/outlets/html/…` → `console`.
- **Staging is login-only on the cloud** (Scot, 2026-11-25: staging is unpublished work). Old staging links and QR codes land on the login page, which is accepted.
- **Feature links (task 7):** stored `webmap_url`s name `{base_url}/current/outlets/webmap/?…`, for readers outside any page (exports, sheets). Pages never follow the stored path: the runbook index and the Regions dropdown write relative links (`map_style.page_view_href`) at materialize, so a page in a version opens that version's webmap. No URL is rewritten in the browser or at publish, because rewriting layer files at publish would defeat layer reuse and make staging differ from its version. Exports carrying a stored link point at `current/` (#246). The webmap's QR code is baked at materialize, so it names `current/`.
- **Protected and staging pages: task 6 (built 2026-11-19; on staging 2026-11-21, verified).** Signed cookies can't separate two tiers of one atlas: the paths nest and the cookie names are fixed. Scot's simplification: **data visibility has one private tier per atlas; roles are capabilities.**
  - **Visibility:** an access list with `internal` or `admin` and no `public` means *private*. `admin` is an alias for `internal` on the data side; configs keep both words, and `admin` is retired organically. On the box, only the `console/admin|internal` pages were ever gated; all staging data was public by URL. So this loses nothing real.
  - **Cookies:** standard CloudFront signed cookies, one set per atlas the user belongs to, each with `Path=/{atlas}/` and a policy for `https://{host}/{atlas}/staging/*`, served by a `/*/staging/*` behavior on the private bucket with a key group. The private key is in Secrets Manager (`atlas/cloudfront-signing-key-{env}`); the public half is committed as `infrastructure/cdk/keys/cloudfront-{env}.pem`.
  - **Sessions last 1 day**, which is Cognito's cap on an ID token. The ID token itself is the API's session cookie (`atlas_id_token`, `Path=/api`), and the CloudFront cookies expire with it, so there's no refresh-token handling. Scot: 7 days was never a requirement.
  - **Logged out on a private page → the 403 page, not a redirect.** CloudFront seems to check signed cookies before any viewer function runs, so a function can't redirect. The distribution answers *every* 403 with `/local/auth/login-required.html`, whose button goes to `/api/auth/login?next=<this page>`. Scot preferred this over Lambda@Edge or a CloudFront Function checking a token of our own. Making a 403 mean only "log in":
    - both buckets grant CloudFront `ListBucket`, so a missing object is a 404;
    - the API never returns 403. Every auth failure is a 401 with `{"error": "login_required"}` or `{"error": "forbidden", "required": …}`. The second is strictly a 403, which is the one compromise. The console and webedit go to the login page on the first and show a message on the second.
    - The private bucket's `GetObject` grant is narrowed to `*/staging/*`.
  - **The API origin is an open function URL plus an origin-token header, not OAC** (deployed to staging 2026-11-21). OAC needs a body hash (`x-amz-content-sha256`) from every POST client; since the API checks auth itself, OAC's only job was "CloudFront is the only way in", which the header (`atlas/origin-token-{env}`, checked by `webapp_auth.check_origin`) does. Verified on staging: logins for all four accounts, private pages per atlas, every role refusal, and the direct URL refused.
  - **Capabilities:** the Cognito groups `{atlas}-internal` and `{atlas}-admin`, with admin ⊇ internal. Either group grants that atlas's private cookie. The API checks the ID token and gives every route a required role (`atlas_auth.ROUTE_ROLES`, enforced by `webapp_auth.enforce` as a global dependency).
    - *Public:* reads, `/job-status`, `/sql_query`, `/select_features`, comments, web-photo ingest, the biochar lookups, and `/create`, `/create-preview` and `/create-starters` (`/create-status` was retired with the in-memory status; a create is polled through `/job-status`).
    - *Internal:* `delta_upload`, `move_features`, `store`, gsheet import/export, and `sql_generate` (it spends Anthropic credit).
    - *Admin:* everything else, listed or not (fail closed).
    - **`scs-admin` is admin on every atlas** (Scot, 2026-11-20: the default unless he says otherwise), including atlases created later, and is the only group allowed routes that name no atlas (`/create_atlas`, `/files`, `/dereference_url`). It gets one CloudFront cookie set at `Path=/` for `/*/staging/*` instead of one per atlas.
    - **Webedit always needs a login** (Scot, 2026-11-20): `delta_upload` stays internal; there will be no anonymous editing.
    - The email Lambda sends a service token (`x-atlas-service-token`, from `atlas/service-token-{env}`) to `/ingest/email_photo`.
    - The test `test_every_listed_route_exists` fails if the table names a route that no longer exists.
  - **The Cognito pools were replaced** (both had 0 users): sign-in by username with email optional, an 8-character password minimum with no character classes, no account recovery (an admin resets passwords), and a new domain prefix `scs-atlas-auth-{env}`. The old pools are retained, empty. Google's OAuth client needs the new `…/oauth2/idpresponse` redirect URI before Google sign-in works.
  - **Google sign-in uses a Web application OAuth client** (`732932899752-l3lbo62r…`, 2026-11-25). The old client in `cdk.json` was a Desktop app client, which has no redirect URIs, so Cognito's Google sign-in had never worked. Its secret is `atlas/google-oauth-client-secret` (`GOCSPX-` + 28 characters); a secret change must be pushed with `update-identity-provider`, since a deploy doesn't re-read it.
  - **Staging status (2026-11-25):** verified with `scripts/cognito_login.sh` and Scot's Google login. Follow-ups: log-out link (#243), `/files` still reads box paths (#244), delete the old pools (#245).
  - **Consoles are not merged** (Scot, 2026-11-19): both variants are still generated and both are served under the private cookie. An internal user who opens the admin console gets buttons the API refuses. Merging them comes after cutover.
  - **Accounts** (Scot, 2026-11-19): `scvfd` (scvfd admin), `admin` and `internal` (westport admin/internal; the names stay because no other atlas has customer logins), and Scot's SCS account, in `scs-admin` only. Scot signs in with **Google** (his existing account; a `google_…` user put in `scs-admin` after his first sign-in), not a password; the staging password account was deleted 2026-11-25. Prod repeats this at cutover. Other atlases have no customer logins. Scot supplies the current passwords, set as permanent: no forced change and no email needed. Cognito usernames are unique across the pool, so a future customer gets prefixed names (`{atlas}-admin`).
- **Auth:** Cognito user pool (exists). For cutover, the existing htpasswd accounts (westport, scvfd, …) become Cognito users with the same usernames in each atlas's `{atlas}-admin`/`{atlas}-internal` group and new passwords — htpasswd hashes cannot be migrated. Google SSO stays available for per-person accounts later.
- **Box-hosted pages that move:** `/create`, `/usage/`, `/identify_as_scs`, help pages under `/local/`. The usage report reads nginx logs, so it needs CloudFront logs instead.
  - **`/create` (done, 2026-11-27):**
    - **Page:** a site asset at `local/create/index.html`, which the viewer function serves at `/create`. It calls `/api`.
    - **Job:** `/create_atlas` checks the request and submits a `create_atlas` job, always to the box queue (`atlas_create.py`). The page polls `/job-status` and shows the job's `progress` lines; every job runner now gets `report(message)`.
    - **Email address:** SES registration runs only on prod with a logged-in creator, so an anonymous create (allowed later, perhaps) leaves it off.
    - **Off the box later:** the job is queue-based, so moving it to a container (Lambda image or Fargate) changes only who consumes the queue.
  - **`/identify_as_scs` and the landing page (done):** the viewer function.
- **Inbound integrations to repoint:** the email-photo Lambda posts to the box's `:9000`.
  - **Ready (2026-11-27):** it sends `x-atlas-service-token` (read from `atlas/service-token-{env}` at runtime). With `IMAGE_BY_REFERENCE=1`, it stages the photo in `atlas-ingress/images/` and sends the key instead of the bytes, because a function URL accepts at most 6 MB.
  - **At cutover:** in `infrastructure/cdk/app.py`, set `webapp_url` to the prod distribution's `/api` and `image_by_reference=True`.
- **DNS last.** Then the box's security group closes to the public.

#### Cutover sequence (outline)

1. Re-rehearse every active atlas on the staging substrate, Lambda path included.
2. Freeze edits; final seed of every active atlas (`config_checks.ACTIVE_ATLASES`; abandoned ones are not seeded — the EBS snapshot keeps them). The seed builds each staging catalog. **Then** push each repo config: `atlas_config.py push <atlas> configuration/<atlas>.geojson` — after the seed, which would otherwise copy the box's stale `staging/atlas.geojson` back (the stale copies were moved to `/root/stale_atlas_geojson_20261117/` on 2026-11-17).
3. Publish each atlas; publish site assets.
4. Deploy the API image and worker; flip DNS; verify every atlas through `verify_published.py`.
5. Close the box to the public.

#### Known gaps at cutover

- **Jupyter** reads the box's staging directly, so it stops seeing current data. Deferred since Phase 3; rethink after cutover.
- ~~Delta `base_version` and overlook's "since the last release" read stale config versions~~ — fixed `9126576`: they read the S3 catalog.

### Phase 5 — QGIS as an async SQS worker · *M*
- **5a.** Lambda enqueues render jobs `{atlas, asset, version}` to SQS; a worker on the box consumes them, **opens a session** like any other compute (not `/vsis3/` — see Phase 3), writes PDFs to S3, updates a status object the client polls (matches submit→spinner→poll).
- **5b.** Box shrinks to a minimal private worker; optionally scale-from-zero on queue depth.

**Deliverable:** QGIS fully decoupled; box has no public exposure and no other job.

A minimal 5a (queue + box worker) ships with the Phase 4 cutover; what is left here is shrinking the box and scale-from-zero.

### Phase 6 — Containerize QGIS, retire the box · *L, finish line*
- **6a.** Bake QGIS into a Lambda container image (10 GB ceiling fits renders; each invocation gets its own memory, so stacking can never OOM a shared box again). Worker becomes a Lambda on the same queue.
- **6b.** Retire the EC2 entirely.

**Deliverable:** no servers; burn ≈ pay-per-use.

---

## Cross-cutting (true throughout)

- **Cost curve:** idle burn drops at Phase 2 (reads off box), again at 4 (API off box), to ~zero at 6.
- **Staging for free:** the CDK stack can deploy a second instance (separate buckets/prefixes) — a real staging env at last, retiring "prod == `swales_dev` with `--reload`".
- **Local dev improves:** the API container runs locally against a dev S3 prefix — more runnable locally than today. Weave tests in as each module is touched.
- ~~**Strangler throughout:**~~ abandoned 2026-09-09 and again 2026-11-17: one cutover for every atlas.

## Pause points

The plan is clean to **pause** after **Phase 2** (safe, cheap, stable) or after **Phase 4**
(serverless API, QGIS on a box) — both are resting points, not half-states.

Note that pausing after Phase 2 now means the box is still serving all traffic, since 2d
moved to Phase 4. It is a safe resting point, but the security/burn/stability wins are
banked at **Phase 4**, not here.

## Deferred — post-migration verification checklist

Do **not** touch now; much of it dissolves once reads move to CloudFront and the box goes
private. Revisit after Phase 2:

- DNS records for `fireatlas.org` (Route 53, **personal** AWS account) — reconcile with CloudFront.
- TLS certs — the apex + wildcard certbot setup vs. the new ACM/CloudFront cert.
- nginx config cruft — the stale/broken `auth_basic_user_file` blocks (`/var/www/html/...`, `swales` vs `swales_dev`, double slashes), legacy per-atlas configs, `autoindex on`.

## Open items / next actions

1. **Phase 0 is the immediate work.** Start with laptop `aws`/`cdk` (0a) since it gates the CDK stack.
2. **Merge the incident fixes to `main`.** The nginx dotfile-deny (`.git` fix) and the `DAGSTER_HOME` start-script fix currently live only on `feature/concat-runbook-pdf`; fold them to `main` so they aren't lost when that branch merges.
3. **File tracking issues** — a P0 for Phase 0 safety, then an epic per phase, with triage labels.

## Decision log

- **2026-11-19: 13 atlases migrate.** mather, isf_920, laytonville and cahto_creek_ranch were dropped from `config_checks.ACTIVE_ATLASES` (none was ever published; the EBS snapshots keep them), joining the atlases abandoned on 2026-11-17.
- **2026-11-17: one cutover for Phases 3 and 4; Phases 5–6 after.** Atlas configs move to S3 (`staging/atlas.geojson`). Lambda hydrates only declared inputs; slow, large and QGIS jobs queue to the box. Existing htpasswd accounts become Cognito users with new passwords. Post-migration: storage format (GeoParquet), STAC as the read path, and the delta rethink (#223, federation v2) go together.

- **CloudFront is the single front door, not Lambda** — preserves static-first (cost, perf, OOM-independence). Public static; protected gated per-prefix.
- **Cognito + Google SSO, group-based authz** — passwordless (deletes the htpasswd/leak/rotation problem); enforcement binary, roles expressed as data (groups); fail-closed by default.
- **QGIS stays on a box initially** — heavy system dependency; decouple via SQS now, containerize into a Lambda image later. Clear runway, no forced march.
- **Read-path-to-S3 before the data refactor** — cheap, reversible, a strict subset of the end state; banks the security/burn win weeks before the long-pole refactor lands.
- **No throwaway "private box" step** — the existing box is hardened and its responsibilities *wither* (reads → S3, API → Lambda) until only QGIS remains. We effectively go straight to serverless.
- **All-CDK, CI-built container images** — one source of truth; immutable artifacts end the live-checkout antipattern and the whole `.git`-exposure class.
- **2d deferred into Phase 4 rather than done at the end of Phase 2** — its larger half (a CloudFront origin pointing back at the box) is deleted by Phase 4, and its durable half (the path-rewrite) targets a URL scheme Phase 3 is about to change. Narrowing Phase 2 to the public tier is what created this dependency; it is recorded rather than silently skipped.
- **Compute is never handed an S3 path** — a session hydrates a real directory and every materializer keeps opening real files unchanged. This replaced "S3-native materialize path, QGIS excepted via `/vsis3/`", and it is what made Phase 3 tractable: the boundary is at the edges of a run rather than in the middle of it, so none of the 142 `atlas_path()` call sites had to move. Direct S3 reads survive as a per-materializer *optimization* (#174), not as the model.
- **A version is a STAC Catalog, not a copy of staging** — outlets are Items alongside layers, and an unchanged layer or outlet is referenced at its existing immutable key rather than re-stored. The `copytree` this replaced is why westport's ten versions cost 6 GB of mostly identical bytes. The corollary is that publish cannot fail safe *and* write the catalog first: objects, then the documents naming them, then the pointer.
- **The storage seam is built where a phase forces it, not up front** — `atlas_store.py` splits into a pure half (tiers, keys, upload plans; unit-testable with no AWS) and an S3 half, so a different backend reimplements the second against the same plan objects. That is the "multiple implementations of one interface" idea the dataswale was designed around, applied narrowly. It is deliberately *not* a general filesystem abstraction: Phase 3 generalizes it with real usage to point at. Note the existing seam is routinely bypassed — `outlets.py` has 58 direct `versioning.atlas_path()` calls and `eddies.py` 27, all reaching around `dataswale_geojson` straight to a concrete `pathlib.Path`. That return type is the load-bearing leak.
