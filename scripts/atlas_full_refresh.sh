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
#   ATLAS_API_URL   webapp API base  (default https://fireatlas.org:9000)
#   ATLAS_CDN_URL   CloudFront base for the post-publish check
#                   (default https://next.fireatlas.org; set empty to skip)
#
# Run from anywhere; paths resolve relative to the repo.

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

# The webapp's publish status is a single module-level global, not per-atlas,
# so two overlapping publishes would report each other's progress.
if curl -sS --max-time 15 "$API_URL/publish-status?swale=$ATLAS" \
     | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin)["publishing"] else 1)' 2>/dev/null; then
  die "a publish is already running (status is process-wide, not per-atlas) — wait for it"
fi

step "1/5  rebuilding config from $ATLAS.geojson"
python3 "$REPO/scripts/build_atlas.py" config_only "$CONFIG"

# refresh is synchronous and returns JSON; a non-success status is fatal here
# because everything after it would build on a stale asset.
refresh() {
  local asset="$1"
  curl -sS --max-time 900 "$API_URL/refresh?swale=$ATLAS&asset=$asset" \
    | python3 -c "
import json,sys
try: r = json.load(sys.stdin)
except Exception: print('unparseable response'); sys.exit(1)
print('   ' + str(r.get('message', r))[:200])
sys.exit(0 if r.get('status') == 'success' else 1)
" || die "refresh of '$asset' failed"
}

# webmap before html: the console HTML checks whether the webmap exists at
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

step "4/5  materializing console html"
refresh html

step "5/5  publishing"
curl -sS --max-time 60 "$API_URL/publish?swale=$ATLAS" >/dev/null || die "publish request failed"

printf '   waiting'
for _ in $(seq 1 240); do          # 240 * 5s = 20 min
  sleep 5
  printf '.'
  if ! curl -sS --max-time 15 "$API_URL/publish-status?swale=$ATLAS" \
       | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin)["publishing"] else 1)' 2>/dev/null; then
    break
  fi
done
echo

curl -sS --max-time 15 "$API_URL/publish-status?swale=$ATLAS" | python3 -c "
import json,sys
r=json.load(sys.stdin)
if r.get('publishing'): print('   still publishing after 20min — check the server'); sys.exit(1)
print(f\"   finished at {r.get('finished_at')}\")
for entry in (r.get('log') or [])[-6:]:
    print('   ' + str(entry)[:220])
" || die "publish did not finish"

if [ -n "$CDN_URL" ] && [ -n "$CLOUD_OUTLETS" ]; then
  step "checking the published copy"
  ok=1
  for outlet in ${CLOUD_OUTLETS//,/ }; do
    # `html` and `console` generate all four role variants into subdirectories
    # and have no root index of their own, so the public entry point is the
    # public/ variant. Checking the outlet root would always 403.
    case "$outlet" in
      html|console) entry="$outlet/public" ;;
      *)            entry="$outlet" ;;
    esac
    code="$(curl -s -o /dev/null -w '%{http_code}' "$CDN_URL/$ATLAS/current/outlets/$entry/")"
    printf '   %-20s %s\n' "$entry" "$code"
    [ "$code" = "200" ] || ok=0
  done
  # Role variants live inside html/ and console/ and must never be public.
  for variant in admin internal technical; do
    code="$(curl -s -o /dev/null -w '%{http_code}' "$CDN_URL/$ATLAS/current/outlets/html/$variant/")"
    [ "$code" = "200" ] && { printf '   \033[31mLEAK: html/%s is public\033[0m\n' "$variant"; ok=0; }
  done

  # Every layer URL the published webmap actually emits, fetched. This is the
  # check that matters: each earlier slice shipped a URL that was individually
  # plausible and pointed at nothing, because the outlet half and the data half
  # were only ever verified apart. Reading the URLs out of the served HTML and
  # resolving them against the page's own location tests the join the browser
  # will actually perform.
  layer_urls="$(curl -s --max-time 30 "$CDN_URL/$ATLAS/current/outlets/webmap/" \
    | python3 -c "
import json, re, sys
from urllib.parse import urljoin
html = sys.stdin.read()
m = re.search(r'const MAP_CONFIG = (\{.*?\});\s*\n', html, re.S)
if not m:
    sys.exit(0)
base = '$CDN_URL/$ATLAS/current/outlets/webmap/'
seen = set()
for src in (json.loads(m.group(1)).get('style', {}).get('sources') or {}).values():
    for key in ('data', 'url'):
        val = src.get(key)
        if isinstance(val, str) and val.startswith('../'):
            seen.add(urljoin(base, val))
print('\n'.join(sorted(seen)))
" 2>/dev/null)"

  if [ -n "$layer_urls" ]; then
    n=0; bad=0
    for u in $layer_urls; do
      n=$((n+1))
      code="$(curl -s -o /dev/null -w '%{http_code}' "$u")"
      [ "$code" = "200" ] || { printf '   \033[31mBROKEN: %s -> %s\033[0m\n' "${u#$CDN_URL/}" "$code"; bad=1; }
    done
    [ "$bad" -eq 0 ] && printf '   %-20s %d/%d resolve\n' "layer sources" "$n" "$n" || ok=0
  else
    printf '   \033[33mnote: could not read layer URLs from the published webmap\033[0m\n'
  fi

  # No protected layer may be readable under the public prefix. The mirror is
  # tier-filtered fail-closed, so this asserts that filter held in practice.
  protected="$(python3 -c "
import json
cfg = json.load(open('$CONFIG'))
for l in (cfg.get('dataswale') or {}).get('layers') or []:
    access = l.get('access')
    if access and 'public' not in access and 'shareable' not in access:
        print(l['name'])
" 2>/dev/null)"
  for layer in $protected; do
    code="$(curl -s -o /dev/null -w '%{http_code}' "$CDN_URL/$ATLAS/current/layers/$layer/$layer.geojson")"
    [ "$code" = "200" ] && { printf '   \033[31mLEAK: protected layer %s is public\033[0m\n' "$layer"; ok=0; }
  done

  [ "$ok" -eq 1 ] && echo "   public outlets served, layers resolve, nothing protected exposed" \
                  || die "published copy did not verify"
fi

printf '\n\033[1mdone: %s\033[0m\n' "$ATLAS"
