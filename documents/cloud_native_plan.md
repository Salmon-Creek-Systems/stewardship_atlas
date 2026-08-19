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
- **2d. Flip the apex** to CloudFront once validated; **box goes private** (reads leave it).

**Deliverable:** public serving is off the box; a render can't take down the site; burn starts dropping.

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

- **3a. Storage abstraction for source data** — `versioning.py` / `dataswale_geojson.py` read/write layers, deltas, versions from S3 (symlink→prefix: `CURRENT`→`current/` pointer, `local`→shared prefix). DuckDB via httpfs.
- **3b. S3-native materialize path** for inlets/eddies/outlets (QGIS excepted — reads via GDAL `/vsis3/`).

**Deliverable:** no local-disk/symlink dependence; S3 is source of truth. Box still runs the API, now statelessly — the precondition for Lambda.

### Phase 4 — API → Lambda · *M–L*
- **4a.** FastAPI as a Lambda container image (Lambda Web Adapter or Mangum), behind API Gateway/Function URL with a Cognito authorizer on writes.
- **4b.** Move all non-QGIS compute into it (inlets, eddies, COG/tiles/pmtiles, webmap/html/console, publish-snapshot).
- **4c.** Retire the monolith API on the box.

**Deliverable:** scale-to-zero API. Box now runs **only** QGIS.

### Phase 5 — QGIS as an async SQS worker · *M*
- **5a.** Lambda enqueues render jobs `{atlas, asset, version}` to SQS; a worker on the box consumes them, reads layers via `/vsis3/`, writes PDFs to S3, updates a status object the client polls (matches submit→spinner→poll).
- **5b.** Box shrinks to a minimal private worker; optionally scale-from-zero on queue depth.

**Deliverable:** QGIS fully decoupled; box has no public exposure and no other job.

### Phase 6 — Containerize QGIS, retire the box · *L, finish line*
- **6a.** Bake QGIS into a Lambda container image (10 GB ceiling fits renders; each invocation gets its own memory, so stacking can never OOM a shared box again). Worker becomes a Lambda on the same queue.
- **6b.** Retire the EC2 entirely.

**Deliverable:** no servers; burn ≈ pay-per-use.

---

## Cross-cutting (true throughout)

- **Cost curve:** idle burn drops at Phase 2 (reads off box), again at 4 (API off box), to ~zero at 6.
- **Staging for free:** the CDK stack can deploy a second instance (separate buckets/prefixes) — a real staging env at last, retiring "prod == `swales_dev` with `--reload`".
- **Local dev improves:** the API container runs locally against a dev S3 prefix — more runnable locally than today. Weave tests in as each module is touched.
- **Strangler throughout:** old box serves un-migrated atlases; per-atlas cutover; temp hostname until the apex flip.

## Pause points

The plan is clean to **pause** after **Phase 2** (safe, cheap, stable) or after **Phase 4**
(serverless API, QGIS on a box) — both are resting points, not half-states.

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

- **CloudFront is the single front door, not Lambda** — preserves static-first (cost, perf, OOM-independence). Public static; protected gated per-prefix.
- **Cognito + Google SSO, group-based authz** — passwordless (deletes the htpasswd/leak/rotation problem); enforcement binary, roles expressed as data (groups); fail-closed by default.
- **QGIS stays on a box initially** — heavy system dependency; decouple via SQS now, containerize into a Lambda image later. Clear runway, no forced march.
- **Read-path-to-S3 before the data refactor** — cheap, reversible, a strict subset of the end state; banks the security/burn win weeks before the long-pole refactor lands.
- **No throwaway "private box" step** — the existing box is hardened and its responsibilities *wither* (reads → S3, API → Lambda) until only QGIS remains. We effectively go straight to serverless.
- **All-CDK, CI-built container images** — one source of truth; immutable artifacts end the live-checkout antipattern and the whole `.git`-exposure class.
- **The storage seam is built where a phase forces it, not up front** — `atlas_store.py` splits into a pure half (tiers, keys, upload plans; unit-testable with no AWS) and an S3 half, so a different backend reimplements the second against the same plan objects. That is the "multiple implementations of one interface" idea the dataswale was designed around, applied narrowly. It is deliberately *not* a general filesystem abstraction: Phase 3 generalizes it with real usage to point at. Note the existing seam is routinely bypassed — `outlets.py` has 58 direct `versioning.atlas_path()` calls and `eddies.py` 27, all reaching around `dataswale_geojson` straight to a concrete `pathlib.Path`. That return type is the load-bearing leak.
