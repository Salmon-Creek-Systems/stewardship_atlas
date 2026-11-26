# Handoff: stewardship_atlas-20261125-1824-auth-on-staging

- Repo / branch / HEAD: `sa_cloud` worktree, `feature/cloud-phase3-compute`, `8bf8367` + this wrapup commit. Pushed. Only untracked: the stray `infrastructure/lambda_build` symlink (leave until deploying is done).
- Session: 2026-11-19 → 11-25, Phase 4 task 6 (auth) built, deployed to staging, verified; main merged in.

## Where things stand
- **Task 6 is live on staging and verified** (`AtlasCloud-staging`, `https://d1y2f6bmf8ds3m.cloudfront.net`). Pool `us-east-1_hSMgD4w7T`, client `mgqmihfa1vlroc7cqoknaj1fm`, signing key `K306E67OB99DAD`.
  - Logged-out private page → our 403 login page (CloudFront *does* use the custom page for signed-cookie failures). Missing public key → 404. API → 401 `login_required` / `forbidden`.
  - Per-role checks all pass (table in the conversation; recipe: `scripts/cognito_login.sh USER PASS`, then `curl -b jar_USER.txt …`).
  - Users: `scvfd` (scvfd-admin), `admin` (westport-admin), `internal` (westport-internal), Scot's Google user `google_113731324078271222942` (scs-admin). Passwords are Scot's; not recorded here.
- **API origin changed:** open function URL (auth NONE) + `x-atlas-origin-token` header from CloudFront (`atlas/origin-token-{env}`), checked by `webapp_auth.check_origin`. OAC was dropped because it needs `x-amz-content-sha256` on every POST (all POSTs 403'd).
- **Google sign-in works** with a new Web application OAuth client (`…l3lbo62r…`); the old one was a Desktop client.
- **Branches:** main has the embed-mode branch merged (`d1f5441`); main merged into the cloud branch (`21037ec`, fixups `8bf8367`); box suite = 144 failures, same set as before the merge. `offline-demo` deleted, kept as tag `archive/offline-demo`.
- Prod: nothing deployed since task 6. Box prod checkout (`/root/swales_dev/app`) is at `5ce5fa7`, behind main by #188 + embed mode — Scot pulls when he wants.

## Next step
Phase 4 **task 7** (see `documents/cloud_native_plan.md` §4e): CloudFront rewrites for legacy paths, move `/create` etc. off the box, repoint the email Lambda to `/api/ingest/email_photo` with the service token header, console/webedit `app_url` → same-origin `/api`, `/files` to S3 (#244), log-out link (#243). Bring a short plan with relaxed-assumption options first.

## Open questions for Scot
1. **Task 7 scope/order** — confirm the list above and whether #243/#244 go in it.
2. **Box prod pull** — pull main on the box now (#188 fix, embed mode) or leave it.

## Decisions this session
- `scs-admin` = admin on every atlas, including future ones; one all-atlas CloudFront cookie set at `Path=/` (Scot: default unless he says otherwise).
- Webedit always requires a login; no public editing.
- API origin: open function URL + origin token (Scot chose B over browser body-hashing or API Gateway).
- Scot uses Google sign-in only; his password account was deleted.
- Callback shows Cognito's `error_description` instead of masking it as `bad_login_state`.
- Embed-mode branch merged to main; offline-demo closed (archived as a tag).

## Issues filed / relevant
- #243 log-out link · #244 `/files` reads box paths on Lambda · #245 delete orphaned old Cognito pools
- #236 revert OIDC branch trust at cutover · #234 duplicate-job collapsing (offer again) · #235 Scot's

## Gotchas
- **A Cognito IdP secret change needs `update-identity-provider`;** a redeploy doesn't re-read the dynamic reference. Shape-check secrets Scot stores before deploying (Google: `GOCSPX-` + 28 chars).
- Every 403 from anywhere becomes the HTML login page — a broken origin looks like "please log in". Check `x-cache`/Lambda logs before assuming auth.
- zsh doesn't word-split `$A` — use `AWS_PROFILE`/`AWS_DEFAULT_REGION` env vars, not a flags variable.
- Scratchpad gets cleared between days; the login script now lives in `scripts/cognito_login.sh`.
- Laptop Python is 3.9: enforcement tests only run on the box (`/root/swales_phase3`).

## Verify state on pickup
```bash
git -C ~/projects/work/scs/sa_cloud log --oneline -3; git -C ~/projects/work/scs/sa_cloud rev-list --count HEAD..origin/main   # expect 0
curl -s https://d1y2f6bmf8ds3m.cloudfront.net/api/me          # {"error":"login_required"}
curl -s -o /dev/null -w '%{http_code}\n' https://d1y2f6bmf8ds3m.cloudfront.net/scvfd/staging/atlas.geojson   # 403
scripts/cognito_login.sh scvfd '<password>' / && curl -s -b jar_scvfd.txt https://d1y2f6bmf8ds3m.cloudfront.net/api/me
```
