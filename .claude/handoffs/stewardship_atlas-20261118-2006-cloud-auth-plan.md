# Handoff: stewardship_atlas-20261118-2006-cloud-auth-plan

- Repo / branch / HEAD: `sa_cloud` worktree (`/Users/scotfree/projects/work/scs/sa_cloud`), branch `feature/cloud-phase3-compute`, pushed. Main checkout `stewardship_atlas` is on `main` (untouched). Stray untracked symlink `infrastructure/lambda_build` in sa_cloud (from a CDK deploy; harmless, delete when done deploying).
- Session: 2026-11-17..19. Cloud migration: tasks 3, 4, 5 built and verified on staging; task 6 (auth) planned and approved, **not started**.

## Where things stand
- **Task 3 done:** `open_session(inputs=config -> prefixes|None)` two-pass hydrate, wired into all light write sessions. Differential check green incl. `layer:` targets.
- **Task 4 done:** jobs (`python/atlas_jobs.py`, `atlas_job_runners.py`, `atlas_job_consumer.py`). S3 status `{atlas}/jobs/{id}.json`, routing by `runs_on`, box worker `scripts/atlas_worker.py` + systemd (`atlas-worker-staging` running on the box, started, not enabled). Verified: scvfd runbook job on box.
- **Task 5 done:** API + jobs Lambda image (OSGeo GDAL base, Mangum, `lambda_env`, lazy registry). `/refresh`, add/edit/delete layer are jobs. Deployed to **staging** via CI from the branch. Verified: `/api/job-status`; kennedy webmap job (21 s) and kennedy publish job (17 s) on the jobs Lambda. Cold start 2.7–4.2 s, warm ~0.4 s.
- Active atlases now **13** (`config_checks.ACTIVE_ATLASES`): dropped mather, isf_920, laytonville, cahto_creek_ranch.
- Full record: `documents/cloud_native_plan.md` Phase 4 (4b jobs + task 5 notes; 4e task 6 plan). Branch `CLAUDE.md` has a "Cloud Migration Decisions" section.

## Next step: build task 6 (plan approved 2026-11-19)
1. **Cognito (CDK `atlas_cloud_stack.py`):** replace both pools (both have 0 users): `sign_in_aliases(username=True)`, email optional, relaxed password policy (min length ~8, no class rules), **new domain prefix** (the retained old pool keeps `scs-atlas-{env}`). Groups `{atlas}-admin`/`{atlas}-internal` from `config_checks.ACTIVE_ATLASES` (replace the stale `ATLASES` list in `infrastructure/cdk/app.py`; import from `python/`, stdlib only). Staging callback host via CI: `-c callback_host=d1y2f6bmf8ds3m.cloudfront.net`. Keep Google IdP.
2. **Signing key:** generate RSA-2048 locally with `openssl`, store the private key in Secrets Manager (e.g. `atlas/cloudfront-signing-key-{env}`), never print it. Commit the public key PEM (`infrastructure/cdk/keys/`). CDK: `cloudfront.PublicKey` + `KeyGroup`.
3. **CloudFront:** second origin = private bucket via OAC, behavior `/*/staging/*` with `trusted_key_groups=[kg]`, caching disabled. A viewer-request function on it: append `index.html` to directory URLs, and 302 to `/api/auth/login?next=<uri>` when the `CloudFront-Policy` cookie is missing or its `DateLessThan` has passed. Signature is still CloudFront's check. Don't use distribution-wide custom error responses: the public bucket's missing keys are 403s too.
4. **`python/atlas_auth.py` (pure, testable):**
   - `roles(groups, atlas)`; admin ⊇ internal; any group = private visibility.
   - `verify_id_token(token, jwks, issuer, client_id, now)` (PyJWT[crypto]).
   - `cloudfront_cookies(atlas, host, key_pair_id, private_key_pem, expires)`: custom policy `https://{host}/{atlas}/staging/*`, RSA-SHA1 PKCS1v15, CloudFront base64 (`+`→`-`, `=`→`_`, `/`→`~`), cookie `Path=/{atlas}/`, Secure, HttpOnly, 7 days.
   - `ROUTE_ROLES` keyed by FastAPI route path; **unlisted = admin**.
   - Add `cryptography` + `PyJWT` pins to `infrastructure/api/requirements.txt`.
5. **Webapp:**
   - `/auth/login` (redirect to the hosted UI), `/auth/callback` (exchange the code, set the ID-token cookie plus one CloudFront cookie set per atlas in the user's groups), `/auth/logout`, `/me`.
   - A global FastAPI dependency enforcing `ROUTE_ROLES`. Atlas from path params (`swalename`/`swale`), the `swale` query param, or the body (`atlas_name`).
   - Service token header for the email Lambda (secret in Secrets Manager).
   - `ATLAS_AUTH=off` only for tests: `tests/test_job_endpoints.py` etc. must set it.
   - Console: on a 401 from `/api/*`, go to `/api/auth/login?next=`.
6. **Routes:**
   - *Public:* reads, `/job-status`, `/sql_query`, `/select_features`, comments, web-photo ingest.
   - *Internal:* `delta_upload`, `store`, gsheet import/export, console photo ingest.
   - *Admin:* publish, refresh, materialize_asset, refresh_layer, add/edit/delete/copy/clear layer, `save_config`, reset-staging, set-current-version, everything unlisted.
7. **Accounts** (after the deploy): `scvfd` → scvfd-admin; `admin` → westport-admin; `internal` → westport-internal; Scot's SCS account → every `{atlas}-admin`. Use `admin-create-user` plus `admin-set-user-password --permanent` with passwords **Scot supplies**. No email needed.
8. **Tests:** role logic; cookie policy text and signature verified with the public key; token verification with a test key; every route either in the table or admin. Then on staging: private pages with and without a login, and API calls by role.
9. **Deploys:** the CDK changes touch IAM, so Scot deploys `AtlasCloud-staging` from a TTY with `-c env_name=staging -c atlas_domain= -c certificate_arn=` (plus `callback_host`). Or deploy via CI after reading `cdk diff` together.

After task 6: task 7 (CloudFront rewrites `CURRENT`→`current`, `html`→`console`, dated versions; move `/create`, `/identify_as_scs`, `/usage/`; repoint the email Lambda; console `app_url`→same-origin `/api`), then task 8 (13-atlas rehearsal with a fresh seed, rebuild configs so they carry `runs_on`, cutover runbook, cutover, revert OIDC #236).

## Open questions for Scot
1. **Passwords:** Scot to supply the current passwords for `scvfd`, westport `admin`, westport `internal`, and choose his own account's username and sign-in (username or Google).
2. **Rescue `submit_job.sh`?** It submits a job to a staging queue with the aws CLI. Unanswered; still in the old session's scratchpad, which may be gone. Recreate it from the description if needed.

## Decisions this session (with reasons)
- **One cutover, 13 atlases.** Four were dropped (never published).
- **SQS + jobs, not Dagster's daemon:** Lambda freezes background threads, and Dagster's run queue needs an always-on host. Dagster stays the in-process engine. S3 job status is "the easy version", swappable behind `read_status`/`write_status`.
- **GDAL base image** over AWS Python (Scot chose a): light paths shell out to `ogr2ogr`, `gdal_translate` and `gdalwarp`.
- **Slow synchronous endpoints are jobs:** the 30 s API and CloudFront limits.
- **Auth (Scot's reframing):** one private visibility tier per atlas; roles are capabilities; `admin` aliased to `internal`, retired organically. **Don't merge the consoles** before cutover. Customer accounts only for scvfd and westport; westport keeps the `admin`/`internal` usernames. Sessions last 7 days.
- **Cascade inputs union:** Scot will write it (#235). Duplicate-job collapsing is deferred (#234); offer it to him again.

## Issues filed / relevant
- #233 Rewrite pre-session tests (`test_refresh_layer_endpoint` delta-upload; `test_eddies` GDAL)
- #234 Collapse duplicate queued jobs
- #235 Hydrate a cascade's inputs from its Dagster selection (Scot writes)
- #236 Revert the OIDC branch trust at cutover (P1)
- Not filed (Scot chose): kennedy runbook renders 0 pages on the September seed (0/1 region reprojected); a 0-page runbook counts as succeeded; console merge; `admin` retirement.

## Gotchas
- **Staging CDK deploys need all three flags,** or staging grabs `next.fireatlas.org` (409, rolls back). Read the whole `cdk diff`.
- **The `lambda_build` symlink must be absolute:** `ln -sfn /Users/scotfree/projects/work/scs/stewardship_atlas/infrastructure/lambda_build infrastructure/lambda_build`.
- **Lambda rejects OCI image indexes:** CI builds with `--provenance=false --sbom=false`.
- **CloudFront forwards `/api/...`:** `api_handler.strip_prefix` removes it.
- **Box test suite:** 153 failures that predate this work; compare failure *sets*, not counts. Local runs shift with missing packages; the box is authoritative.
- **S3 configs predate `runs_on`:** until they're rebuilt (`config_only`), every asset routes to Lambda.
- **The staging API is unauthenticated until task 6.**
- **The box has no `ANTHROPIC_API_KEY`,** so `sql_generate` is off. The Anthropic secret is optional.

## Verify state on pickup
```
cd /Users/scotfree/projects/work/scs/sa_cloud && git status -sb && git log --oneline -3
curl -s -o /dev/null -w "%{http_code}\n" "https://d1y2f6bmf8ds3m.cloudfront.net/api/job-status?swale=scvfd&id=20261119-001057-484a19ce"   # 200
aws cloudformation describe-stacks --stack-name AtlasCloud-staging --region us-east-1 --profile atlas --query 'Stacks[0].StackStatus'   # UPDATE_COMPLETE
ssh -i ~/.ssh/SCSProd.pem ubuntu@54.241.152.98 'systemctl is-active atlas-worker-staging'   # active
```
