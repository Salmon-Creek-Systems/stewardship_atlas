# Handoff: stewardship_atlas-20261128-0001-task7-rehearsed

- Repo / branch / HEAD: `sa_cloud` worktree, `feature/cloud-phase3-compute`, HEAD = the wrapup commit after `d706362`. Pushed. Untracked `infrastructure/lambda_build` symlink (leave it).
- Session: 2026-11-25..28. Phase 4 task 7 (traffic-flip prep) built, deployed to staging and rehearsed end to end.

## Where things stand
- **Task 7 is done; all of it is on staging** (API image `d706362`, viewer function, site assets, box worker restarted on HEAD):
  1. Feature links point at `current/`, and pages link relatively (`88bb8be`).
  2. Every published version is served at `{atlas}/{version}/` (`29178ba`).
  3. The CloudFront viewer function replaces nginx's URL rules, including `/create` and `/usage` (`969dadb`, `faf57ab`).
  4. `app_url=/api` (`cd79b72`).
  5. Email Lambda: service token, and photos by S3 key (`b6175e7`). Deployed to prod but still posting to the box.
  6. `/create` is a box job with progress (`c7cd1d8`).
  7. `/files` retired (#244), and a log-in/log-out link on the console (#243) (`c4d1f6c`, `eeac7b8`).
  8. Usage report from CloudFront logs (`e393d3b`).
  9. No API redirects, and a descriptive Overpass User-Agent (`d706362`).
- **The staging rehearsal passed:**
  - kennedy: config push, webmap and console rebuilt on Lambda, then published (`2026-11-28_01-22-04`)
  - the version URL is served, and the console uses `API_URL="/api"`
  - stored feature links have the `current/` form
  - email by reference works (4.7 MB photo)
  - `rehearsal1` created as a job, and `rehearsal2` via the page (Scot); SES untouched; both deleted afterwards
  - Scot checked `/usage` and log-out in his browser
- Box suite: 138 failures, identical to the baseline (`/tmp/b.txt` on the box). Node: 18/18 (`node --test infrastructure/cdk/functions/*.test.mjs`).
- **Box config outside git:**
  - `/etc/atlas-worker-staging.env` now has `ATLAS_LOGS_BUCKET` and `HOME=/root` (backup `/root/atlas-worker-staging.env.bak-20261128`)
  - `/etc/cron.d/fireatlas-usage-cloud` is installed
- **Staging leftovers** (fine to keep): kennedy version `2026-11-28_01-22-04`; one test photo in kennedy `processing_sites`.

## Next step
Start the **cutover sequence**: `documents/cloud_native_plan.md` §4e, "Cutover sequence (outline)". Bring Scot a concrete runbook first: the order, what each step touches, and the rollback for each. Items it must include:
- the prod CDK deploy (log bucket and logging come with it)
- the email Lambda flip in `infrastructure/cdk/app.py` (`webapp_url` → prod `/api`, `image_by_reference=True`)
- the prod worker env file (`HOME`, `ATLAS_LOGS_BUCKET`) and the cloud usage cron
- republishing site assets
- re-materializing every atlas's console and webmap
- **checking an OSM inlet on prod**: the Overpass fix arrives with the merge
- reverting the OIDC branch trust (#236)
- closing #243 and #244 at merge

## Open questions for Scot
1. **Cutover runbook:** review the order and rollback before anything prod-facing runs.
2. **Usage report login (#248):** fix before cutover, or after?

## Decisions this session
- Staging is login-only, and old QR codes landing on the login page until cutover is fine.
- Every version is served at its own URL (older data stays browsable); old box versions are not backfilled.
- Link rule: stored `webmap_url` points at `current/`, pages link relatively at materialize time, and nothing is rewritten in the browser or at publish (Scot rejected both). Exports are #246.
- `/create` runs on the box as a queue job, a step toward moving it off the box later. SES registration happens only on prod with a logged-in creator; anonymous creates may be allowed later.
- Usage report runs from a box cron for now, served at `/api/usage/` for `scs-admin`. Raw logs kept 30 days (Scot).
- Log-in/log-out link on the **console only**, not the webmap (Scot).
- Overpass fix **waits for cutover**: it's on the branch, and Scot asked that it be checked then. The prod box's OSM inlets fail until the merge.
- Prod email Lambda keeps posting to the box until cutover; no separate staging email path.

## Issues filed / relevant
#243 log-in link, #244 /files (both fixed on the branch; close at merge), #246 version-exact `webmap_url` in exports, #247 API request logging, #248 logged-out `/usage` returns JSON, #236 OIDC trust revert.

## Gotchas
- **`deploy-cloud.yml` runs `cdk deploy --require-approval never`.** Read the `cdk diff` with Scot before dispatching it whenever IAM changes.
- **The console outlet renders `templates/console_new.html`;** `console.html` is the retired html console. Check which template a materializer uses before editing one.
- **Behind the function URL a request's trailing slash arrives stripped,** and any redirect the API builds points at the Lambda's own host (`not_via_cloudfront`). Never add trailing-slash routes or redirects.
- **The systemd worker had no `HOME`,** and DuckDB inlets fail without it. The box worker also needs a restart after a pull; it had run Nov 19 code for days.
- **Staging jobs without a login:** `scripts/submit_job.py --env staging …`. Email test with no SES: `scripts/rehearse_email_photo.py`. Never stage test mail under `incoming/`, which triggers the prod Lambda.
- **`.venv` here is a symlink to the main checkout's `.venv`;** boto3 and requests are now installed in it.

## Verify state on pickup
```
git -C ~/projects/work/scs/sa_cloud log --oneline -3
gh run list --workflow deploy-cloud.yml --limit 1
curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" https://d1y2f6bmf8ds3m.cloudfront.net/usage   # 302 -> /api/usage/index.html
curl -s -o /dev/null -w "%{http_code}\n" https://d1y2f6bmf8ds3m.cloudfront.net/create                  # 200
ssh -i ~/.ssh/SCSProd.pem ubuntu@54.241.152.98 'sudo systemctl is-active atlas-worker-staging; sudo git -C /root/swales_phase3 log --oneline -1'
```
