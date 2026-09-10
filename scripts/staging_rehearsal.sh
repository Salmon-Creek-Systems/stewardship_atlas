#!/usr/bin/env bash
#
# Rehearse the S3 cutover for one atlas against the *staging* substrate.
#
#   ./scripts/staging_rehearsal.sh kennedy
#
# Run this from a git worktree of the branch under test, on the box, because
# that is the only machine with the data. What isolates the rehearsal is the
# set of buckets it writes to, not the machine it runs on.
#
# Two things this deliberately does NOT do:
#
#   * It does not touch the live checkout at /root/swales_dev/app. Switching
#     that branch would swap webapp.py and friends under the running service —
#     the incident the server-safety rules exist to prevent. Use a worktree.
#   * It does not publish through the API. `/publish` runs inside the webapp
#     process, which carries the *production* environment, so the S3 write
#     would go to the production buckets no matter what this shell exports.
#     Publish is therefore invoked in-process here.
#
# What it DOES mutate on the live box, because publish cannot be done
# otherwise: it rebuilds the atlas config, re-materializes the webmap and
# console into staging/, creates a real new version snapshot, and moves the
# CURRENT symlink. Those are ordinary operations, but they are real.

set -euo pipefail

ATLAS="${1:-}"
[ -n "$ATLAS" ] || { echo "usage: $(basename "$0") <atlas>" >&2; exit 2; }

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$REPO/configuration/$ATLAS.geojson"
[ -f "$CONFIG" ] || { echo "no config at $CONFIG" >&2; exit 1; }

# --- the staging substrate -------------------------------------------------
export ATLAS_OUTLETS_BUCKET="${ATLAS_OUTLETS_BUCKET:-scs-atlas-outlets-staging}"
export ATLAS_PRIVATE_BUCKET="${ATLAS_PRIVATE_BUCKET:-scs-atlas-private-staging}"
export ATLAS_PUBLIC_BASE_URL="${ATLAS_PUBLIC_BASE_URL:-https://d1y2f6bmf8ds3m.cloudfront.net}"
export ATLAS_DISTRIBUTION_ID="${ATLAS_DISTRIBUTION_ID:-E15MW3URKTZ7Q1}"
export SWALES_ROOT="${SWALES_ROOT:-/root/swales_dev}"

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[31mFAILED: %s\033[0m\n' "$*" >&2; exit 1; }

# Refuse to run against production buckets. This script exists to rehearse;
# if the environment has drifted back to prod the whole point is lost, and
# the failure would be silent — everything succeeds, against the wrong data.
case "$ATLAS_OUTLETS_BUCKET$ATLAS_PRIVATE_BUCKET" in
  *prod*) die "environment points at production buckets ($ATLAS_OUTLETS_BUCKET / $ATLAS_PRIVATE_BUCKET)" ;;
esac

echo "atlas    : $ATLAS"
echo "code     : $REPO  ($(cd "$REPO" && git rev-parse --abbrev-ref HEAD) @ $(cd "$REPO" && git rev-parse --short HEAD))"
echo "data     : $SWALES_ROOT"
echo "outlets  : $ATLAS_OUTLETS_BUCKET"
echo "private  : $ATLAS_PRIVATE_BUCKET"
echo "cdn      : $ATLAS_PUBLIC_BASE_URL"

printf '\nThis creates a real version and moves CURRENT on the live box. Continue? [y/N] '
read -r reply
[ "$reply" = "y" ] || { echo "aborted"; exit 1; }

step "1/4  rebuilding config"
python3 "$REPO/scripts/build_atlas.py" config_only "$CONFIG"

step "2/4  materializing webmap + console"
PYTHONPATH="$REPO/python" python3 - "$ATLAS" <<'PY'
import json, os, sys
import atlas, versioning
name = sys.argv[1]
cfg_path = os.path.join(os.environ['SWALES_ROOT'], name, 'staging', 'atlas_config.json')
config = json.load(open(cfg_path))
# webmap before html: the console checks for outlets/webmap/index.html at
# generation time, so the reverse order silently produces a console with no
# map link.
for asset in ('webmap', 'html'):
    if asset in config.get('assets', {}):
        print(f"  materializing {asset}")
        atlas.materialize(config, asset)
PY

step "3/4  publishing (in-process, staging buckets)"
PYTHONPATH="$REPO/python" python3 - "$ATLAS" <<'PY'
import json, os, sys, logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
import versioning
name = sys.argv[1]
cfg_path = os.path.join(os.environ['SWALES_ROOT'], name, 'staging', 'atlas_config.json')
config = json.load(open(cfg_path))
path = versioning.publish_new_version(config)
print(f"published: {path}")
PY

step "4/4  verifying the published copy"
ATLAS_CDN_URL="$ATLAS_PUBLIC_BASE_URL" \
  python3 "$REPO/scripts/verify_published.py" "$ATLAS" \
  || die "verification failed"

printf '\n\033[1mrehearsal complete: %s\033[0m\n' "$ATLAS"
