# Deployment Learnings

This is a living document capturing observations, pain points, and higher-level patterns noticed during atlas deployments (SCVFD migration and kennedy). Discrete tasks are filed as GitHub issues (linked below); this doc captures the connective tissue — the recurring friction, the systemic causes, and the things we said "we should fix this eventually" but didn't immediately act on.

---

## High-Level Observations

### The per-atlas code copy model is both the right call for now and the root of most pain

Each atlas runs a separate `app/` git checkout, its own uvicorn process on a dedicated port, and its own Let's Encrypt cert. This evolved from an earlier shared-service model that had cross-site header issues. The current model is cleaner in one important way — during active development, different atlases can run different git branches, which turned out to be genuinely useful (e.g. testing `email-photo-inlet` on SCVFD before merging). But it creates significant friction: deploying a code change to multiple atlases means pulling on each server individually, any shared config changes must be applied and re-materialized per atlas, and there's no single place to see the running state of the fleet.

The correct long-term model is a container image for code plus per-atlas data on S3, routed by a wildcard cert and a single nginx reverse proxy. That's essentially the original shared-service idea done correctly, with actual isolation coming from the data layer and routing, not from duplicated code. Don't make decisions in config reform or S3 backend projects that close this door.

### Shared config has no propagation mechanism, and this compounds over time

After any change to the shared JSON config files (`shared_*.json`), every deployed atlas must re-run `build_atlas.py config_only` to regenerate its `atlas_config.json`. There is no tooling to do this across all atlases at once (Issue #35). This is currently a purely manual process that relies on the operator remembering which atlases exist and which might be affected. As the number of atlases grows and config files multiply, this will become a meaningful operational burden. The real fix is config reform (roadmap project 4), but a short-term script to push a shared config update across all deployed atlases would already help.

### fetch_type names and asset_methods keys are an invisible contract that breaks silently

The config files identify assets by a `fetch_type` string, and `asset_methods` dicts in the Python modules use that string as the key to look up the materializer function. These two strings must match exactly, but there's no enforcement — if they don't match, the asset simply fails to materialize with an opaque error. The kennedy deployment hit this twice: `fetch_url` in shared config didn't match `url_raster` in `raster_inlets.asset_methods`, and `contours` didn't match `gdal_contours` in `eddies.asset_methods`. Aliases were added as short-term fixes. The longer-term answer is an audit (Issue #33) and possibly a startup check that validates all config `fetch_type` values against the registered asset_methods keys.

### Two htpasswd systems coexist and can silently diverge

The system uses htpasswd for authentication, but there are actually two separate mechanisms: per-directory `.htpasswd` files created by `atlas.create()`, and a roles-based system under `infrastructure/htpasswd/roles/{atlas}_roles.json` that nginx reads directly. During the SCVFD migration, the nginx config was still pointing at the old `/root/swales/roles/` path instead of `/root/swales_dev/roles/`. The kennedy deploy had the same issue. This is a latent correctness problem — it's easy to have the wrong paths in nginx while everything appears to work until someone tries to access a protected resource from outside. An auth audit is listed as a short-term action in the roadmap for this reason.

### The delta apply step is a silent dependency that causes confusing failures

After running an inlet that writes delta files, a separate `refresh_vector_layer()` call is required before those deltas are reflected in the queryable layer data. This step is not obviously implied by running the inlet, and missing it causes the next pipeline step to see stale data. During the SCVFD migration, this bit us in the gazetteer workflow — `gazetteer_regions` wasn't visible because the delta apply step hadn't been run after `gazetteer_grid`. This is a good candidate for Dagster to solve properly (an outlet depending on a layer would automatically require the refresh), but in the meantime it should be documented clearly in any provisioning runbook.

### Env vars and SSL cert paths are implicit startup requirements with no guardrails

Starting a webapp requires setting `DATASWALE_PATH` before uvicorn starts — if it's missing, the app launches but behaves incorrectly in ways that aren't immediately obvious. Similarly, Jupyter doesn't support SSL cert paths via CLI flags, so each atlas needs a `jupyter_notebook_config.py` in its notebook outlet directory with the SSL paths hardcoded. If that file is missing or has stale cert paths (e.g. after cert renewal), Jupyter fails in a way that requires knowing this implicit requirement. The `new_atlas.sh` provisioning script addresses some of this, but the dependency on externally-configured cert files remains fragile. Process management via systemd (rather than screen sessions) would be a natural place to encode env vars and validate them on startup.

### The Overture Maps release path needs periodic manual updates

Overture data is fetched via a versioned S3 path (`s3://overture-maps-data/.../2026-02-18.0/...`). This version string is hardcoded in shared config and goes stale approximately monthly when Overture publishes a new release. During the kennedy deploy, two different stale Overture versions were found across different configs. This is Issue #34, with auto-resolution planned.

---

## Deployment Pain Points

### Certbot chicken-and-egg with nginx

The first time nginx is configured for an atlas, the SSL cert doesn't exist yet, but nginx won't start without valid cert paths. The workaround is to comment out the SSL directives from the nginx config, run certbot to issue the cert, then re-enable the SSL directives and reload nginx. This works but is not documented and easy to get wrong. The `new_atlas.sh` script should codify this sequence explicitly — or consider certbot's `--nginx` plugin mode which handles the directive rewrite automatically.

### Per-atlas port and Jupyter SSL config (Issue #28)

Each atlas requires a manually chosen port for uvicorn and a separate port for Jupyter. Jupyter requires a `jupyter_notebook_config.py` in the notebook outlet directory with SSL cert paths. There's no registry of which ports are in use; tracking this is currently tribal knowledge. Adding a new atlas means choosing ports that don't conflict, updating nginx, and generating the Jupyter config — all of which are manual steps that the `new_atlas.sh` script should eventually absorb.

### Config rebuild must follow shared config changes

As noted above: any change to `shared_*.json` requires re-running `build_atlas.py config_only` on every atlas before those changes take effect. This is easy to forget. The `new_atlas.sh` script runs this as part of initial provisioning, but there's no equivalent tooling for propagating subsequent updates. This is Issue #35.

### WVFD-specific filenames hardcoded in shared config

`shared_inlets_config.json` contains `westport_turnouts.geojson` and `westport_dem_2m_hillshade.tiff` — filenames specific to the WVFD atlas. These will cause failures for any other atlas that tries to use the turnouts or DEM inlets and doesn't have files by those names. This hasn't caused breakage yet on kennedy because those inlets haven't been run, but it's a latent bug waiting to happen as more atlases are provisioned. Fix should come during config reform.

### Help docs path was relative and broke on deployment

The `outlets.py` help documentation path was hardcoded relative to the script's working directory. This worked in the original single-codebase layout but broke with the per-atlas `app/` structure. Fixed during kennedy by switching to `versioning.atlas_path(config, version='app')`. This is representative of a broader pattern: paths written assuming a specific working directory are a recurring source of breakage when the code moves to a different layout.

### Running as root

All services currently run as root on the server. This is acknowledged bad practice and a security concern. It should be fixed alongside any process management improvements (systemd units would naturally run as a service user).

### Screen sessions as process management

Webapp and Jupyter are both run in named `screen` sessions manually. There's no auto-start on boot, no restart on failure, no log management, and no easy way to see which atlases have running services. This is fragile — a server reboot requires manually restarting services for each atlas. This belongs on the roadmap as a process management upgrade (systemd units, supervisor, or similar).

---

## Related GitHub Issues

### Config

- **#33 — Audit fetch_type names in shared configs against asset_methods keys**: Systematic check that every `fetch_type` value in shared config has a corresponding entry in `asset_methods` dicts. Should also add a startup validation check.
- **#34 — Overture Maps release version is hardcoded in shared config**: Overture publishes new data releases monthly and the version string in shared config goes stale. Planned to auto-resolve by querying the latest available release at run time.
- **#35 — No tooling to propagate shared config changes to deployed atlases**: After changing `shared_*.json`, every atlas must re-run `build_atlas.py config_only` manually. Needs a script or workflow to push shared config updates across all deployed atlases.

### Infrastructure

- **#10 — Scripted atlas provisioning from GeoJSON + config**: Capture the full provisioning sequence (directory structure, nginx config, cert, service startup, initial materialization) in a repeatable script. The `new_atlas.sh` work is the direct follow-on from the SCVFD and kennedy deployments.
- **#28 — Server spinup: per-atlas port/instance and Jupyter SSL config pain points**: Documents the port selection and Jupyter SSL config friction; scope is to reduce the manual steps and tribal knowledge required to start a new atlas.
- **#11 — AWS infrastructure for email photo inlet (SES + S3 + Lambda)**: CDK stack for the email ingest pipeline; partially deployed for SCVFD.

### Pipeline

- **#30 — Raster inlets: no clear delta semantics or pre-processing pipeline**: Raster inlets currently lack the delta queue and apply pattern that vector inlets use. The kennedy deploy exposed this — raster inlet signature didn't match the expected pattern, requiring a fix.
- **#15 — Layer config and testing for email photo inlet**: Config and test coverage for the new email photo inlet; surface area for regression as the inlet matures.

### UI / Console

- **#29 — Config editor: saving edits does not work**: The in-browser config editor does not successfully persist changes. Broken across atlases, not deployment-specific.
- **#21 — Broken/garbled characters in HTML generated from help markdown**: Help doc rendering issue visible across atlases; likely an encoding or markdown library issue introduced when the path was fixed.
- **#32 — Webmap: configurable default zoom level per atlas**: Atlases covering different geographic scales need different default zoom levels; currently there's no per-atlas config for this.
- **#23 — Edit UI: show feature match count in selection feedback**: UX improvement for the feature edit flow; easy to confuse "no match" for "match not shown."

### Delete Feature (tracked as a group)

- **#24 — Add delete action to apply_deltas() in deltas_geojson.py**: Core delta type needed to support feature deletion.
- **#25 — Add Delete button to edit UI (annotate page)**: Surface the delete action in the web console.
- **#26 — Rename 'annotate' to 'Edit' in outlets.py generated HTML**: Terminology cleanup that should accompany the delete feature.
- **#27 — Delete feature: tests**: Test coverage for the new delete delta type.

---

## Resolved During Deployment

These bugs were found and fixed during the kennedy deployment (2026-03-11) and are already in main. They do not need future work, but are good candidates for regression tests.

- **Help docs path was relative** — `outlets.py` used a relative path for help docs; fixed to use `versioning.atlas_path(config, version='app')`.
- **`fetch_url` / `url_raster` key mismatch** — `fetch_url` in shared config didn't match `url_raster` in `raster_inlets.asset_methods`; fixed by adding an alias.
- **`contours` / `gdal_contours` key mismatch** — `contours` in shared config didn't match `gdal_contours` in `eddies.asset_methods`; fixed by adding an alias.
- **Raster inlets missing `delta_queue` default** — raster inlet functions lacked the `delta_queue=DELTA_QUEUE` default arg that vector inlets had; fixed to match pattern.
- **`apply_deltas()` not sorting delta files** — deltas were applied in filesystem order rather than by filename (timestamp), causing incorrect apply order; fixed.
- **`setup_role_htpasswds()` not running in config_only mode** — htpasswd setup was skipped during `config_only` builds, leaving auth unconfigured; fixed.
- **Stale Overture release versions** — two different stale Overture S3 release paths were hardcoded in configs; updated to `2026-02-18.0`.
- **nginx config pointing at wrong htpasswd path** — kennedy nginx config referenced `/root/swales/roles/` instead of `/root/swales_dev/roles/`; fixed on server.

Earlier bugs resolved during the SCVFD migration (already captured in MEMORY.md):

- `build_atlas.py` `config_only` defaulted to `True` from a GeoJSON property rather than the CLI arg.
- `derived_hillshade` fetch_type didn't match the `asset_methods` key.
- `atlas.create()` wasn't creating the `CURRENT` symlink.
- Notebook outlet had a hardcoded absolute path to the old codebase location.
