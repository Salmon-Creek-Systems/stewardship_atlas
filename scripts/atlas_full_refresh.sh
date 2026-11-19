#!/usr/bin/env bash
#
# Rebuild one atlas's config, re-materialize its webmap and console, and
# publish — the per-atlas cutover sequence from the Phase 2 read-path
# migration (documents/cloud_native_plan.md).
#
#   ./scripts/atlas_full_refresh.sh <atlas>          # pauses to check staging
#   ./scripts/atlas_full_refresh.sh -y <atlas>       # no pause
#
# Env:
#   ATLAS_API_URL         webapp API base  (default https://fireatlas.org:9000)
#   ATLAS_CDN_URL         CloudFront base for the post-publish check
#                         (default https://next.fireatlas.org; set empty to skip)
#   ATLAS_WORKSPACE_ROOT  local directory to hydrate into — required, because
#                         the config rebuild step now runs inside a session
#                         (#159) rather than writing to a local swales tree.
#
# Run from anywhere; paths resolve relative to the repo. AWS credentials are
# needed for the config rebuild, which locks the atlas and writes back to S3.

set -euo pipefail

API_URL="${ATLAS_API_URL:-https://fireatlas.org:9000}"
CDN_URL="${ATLAS_CDN_URL-https://next.fireatlas.org}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ASSUME_YES=0

usage() { echo "usage: $(basename "$0") [-y] <atlas>" >&2; exit 2; }

while [ $# -gt 0 ]; do
  case "$1" in
    -y|--yes) ASSUME_YES=1; shift ;;
    -h|--help) usage ;;
    -*) echo "unknown option: $1" >&2; usage ;;
    *) break ;;
  esac
done
[ $# -eq 1 ] || usage
ATLAS="$1"

CONFIG="$REPO/configuration/$ATLAS.geojson"
[ -f "$CONFIG" ] || { echo "no config at $CONFIG" >&2; exit 1; }

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[31mFAILED: %s\033[0m\n' "$*" >&2; exit 1; }

# Read a value out of the source config. The generated atlas_config.json is
# the runtime truth, but it lives under a data root this script has no
# business guessing at, and these fields pass through unchanged.
cfg() {
  python3 -c "
import json,sys
p=json.load(open('$CONFIG'))['features'][0]['properties']
v=p
for k in '$1'.split('.'):
    v = (v or {}).get(k) if isinstance(v, dict) else None
print('' if v is None else (','.join(v) if isinstance(v,list) else v))
"
}

BASE_URL="$(cfg base_url)"
CLOUD_OUTLETS="$(cfg cloud.outlets)"

echo "atlas       : $ATLAS"
echo "api         : $API_URL"
echo "staging     : $BASE_URL/staging/outlets/webmap/"
# There is no `cloud.enabled` any more — S3 is the storage backend, not an
# opt-in. `cloud.outlets` is the mandatory allowlist, and an atlas without one
# publishes no outlets at all, which is a misconfiguration rather than a mode.
if [ -n "$CLOUD_OUTLETS" ]; then
  echo "publishes   : $CLOUD_OUTLETS  ->  S3/CloudFront"
else
  echo "publishes   : NOTHING — no cloud.outlets allowlist in $ATLAS.geojson"
fi

step "1/5  rebuilding config from $ATLAS.geojson"
python3 "$REPO/scripts/build_atlas.py" config_only "$CONFIG"

# refresh is a job: submit, then poll its status. A failure is fatal here,
# because everything after it would build on a stale asset.
refresh() {
  local asset="$1" job state
  job=$(curl -sS --max-time 60 "$API_URL/refresh?swale=$ATLAS&asset=$asset" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])') \
    || die "refresh of '$asset' was not accepted"
  for _ in $(seq 1 180); do          # 180 * 5s = 15 min
    state=$(curl -sS --max-time 15 "$API_URL/job-status?swale=$ATLAS&id=$job" \
      | python3 -c 'import json,sys; print(json.load(sys.stdin).get("state", "unknown"))' 2>/dev/null)
    case "$state" in queued|running) sleep 5 ;; *) break ;; esac
  done
  [ "$state" = "succeeded" ] || die "refresh of '$asset' ended $state (job $job)"
  echo "   $asset: succeeded (job $job)"
}

# webmap before console: the console checks whether the webmap exists at
# generation time, so the order is load-bearing.
step "2/5  materializing webmap"
refresh webmap

if [ "$ASSUME_YES" -eq 0 ]; then
  step "3/5  check staging before publishing"
  echo "   $BASE_URL/staging/outlets/webmap/"
  echo
  echo "   Re-materializing with bake_data rewrites the live staging webmap,"
  echo "   and this is the last point before it becomes a published version."
  printf '   publish? [y/N] '
  read -r reply </dev/tty
  case "$reply" in [yY]*) ;; *) echo "   stopped; nothing published."; exit 0 ;; esac
else
  step "3/5  skipping staging check (-y)"
fi

step "4/5  materializing console"
refresh console

step "5/5  publishing"
JOB_ID=$(curl -sS --max-time 60 "$API_URL/publish?swale=$ATLAS" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])') \
  || die "publish request failed"
echo "   job $JOB_ID"

# Publish is a job: poll its status object until it finishes.
job_state() {
  curl -sS --max-time 15 "$API_URL/job-status?swale=$ATLAS&id=$JOB_ID" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("state", "unknown"))' 2>/dev/null
}
printf '   waiting'
for _ in $(seq 1 240); do          # 240 * 5s = 20 min
  sleep 5
  printf '.'
  case "$(job_state)" in queued|running) ;; *) break ;; esac
done
echo

curl -sS --max-time 15 "$API_URL/job-status?swale=$ATLAS&id=$JOB_ID" | python3 -c "
import json,sys
r=json.load(sys.stdin)
if r.get('state') in ('queued', 'running'): print('   still publishing after 20min — check the jobs'); sys.exit(1)
if r.get('state') != 'succeeded': print('   publish failed: ' + str(r.get('error'))[:600]); sys.exit(1)
print(f\"   finished at {r.get('finished_at')}: {str(r.get('result'))[:200]}\")
" || die "publish did not finish"

if [ -n "$CDN_URL" ] && [ -n "$CLOUD_OUTLETS" ]; then
  step "checking the published copy"
  # One implementation of this check, shared with staging_rehearsal.sh. It
  # verifies the outlets serve, no role variant is public, every layer URL the
  # published webmap actually emits resolves, and no protected layer is
  # readable.
  ATLAS_CDN_URL="$CDN_URL" python3 "$REPO/scripts/verify_published.py" "$ATLAS" \
    || die "published copy did not verify"
fi

printf '\n\033[1mdone: %s\033[0m\n' "$ATLAS"
