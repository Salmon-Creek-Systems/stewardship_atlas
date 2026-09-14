#!/usr/bin/env bash
#
# Rehearse the S3 cutover for one atlas against the *staging* substrate.
#
#   ./scripts/staging_rehearsal.sh kennedy
#
# Run this from a git worktree of the branch under test, on the box, because
# that is the only machine with the data to seed from. What isolates the
# rehearsal is the set of buckets it writes to, not the machine it runs on.
#
# Since Step 3 (#159) this **no longer mutates the live box**. Compute happens
# in a session workspace hydrated from the staging bucket; the box's disk is
# read once, to seed, and never written. The previous version of this script
# rebuilt the config in place, re-materialized into /root/swales_dev/{atlas}/
# staging and moved the CURRENT symlink — all real changes to a live atlas,
# accepted only because publish could not be done any other way.
#
# Two things it still deliberately does NOT do:
#
#   * It does not touch the live checkout at /root/swales_dev/app. Switching
#     that branch would swap webapp.py and friends under the running service —
#     the incident the server-safety rules exist to prevent. Use a worktree.
#   * It does not publish through the API. `/publish` runs inside the webapp
#     process, which carries the *production* environment, so the S3 write
#     would go to the production buckets no matter what this shell exports.
#     Publish is therefore invoked in-process here.

set -euo pipefail

ATLAS="${1:-}"
[ -n "$ATLAS" ] || { echo "usage: $(basename "$0") <atlas>" >&2; exit 2; }

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# --- the staging substrate -------------------------------------------------
export ATLAS_OUTLETS_BUCKET="${ATLAS_OUTLETS_BUCKET:-scs-atlas-outlets-staging}"
export ATLAS_PRIVATE_BUCKET="${ATLAS_PRIVATE_BUCKET:-scs-atlas-private-staging}"
export ATLAS_PUBLIC_BASE_URL="${ATLAS_PUBLIC_BASE_URL:-https://d1y2f6bmf8ds3m.cloudfront.net}"
export ATLAS_DISTRIBUTION_ID="${ATLAS_DISTRIBUTION_ID:-E15MW3URKTZ7Q1}"
export SWALES_ROOT="${SWALES_ROOT:-/root/swales_dev}"
export ATLAS_SHARED_DIR="${ATLAS_SHARED_DIR:-/root/data}"
export ATLAS_WORKSPACE_ROOT="${ATLAS_WORKSPACE_ROOT:-/root/atlas_workspace_staging}"

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[31mFAILED: %s\033[0m\n' "$*" >&2; exit 1; }

# Refuse to run against production buckets. This script exists to rehearse;
# if the environment has drifted back to prod the whole point is lost, and
# the failure would be silent — everything succeeds, against the wrong data.
case "$ATLAS_OUTLETS_BUCKET$ATLAS_PRIVATE_BUCKET" in
  *prod*) die "environment points at production buckets ($ATLAS_OUTLETS_BUCKET / $ATLAS_PRIVATE_BUCKET)" ;;
esac

# The workspace must not be the live data root. They have the same shape —
# {root}/{atlas}/staging — so a workspace pointed at /root/swales_dev would
# hydrate the staging bucket straight over the live atlas.
case "$ATLAS_WORKSPACE_ROOT" in
  "$SWALES_ROOT"|"$SWALES_ROOT"/*) die "ATLAS_WORKSPACE_ROOT ($ATLAS_WORKSPACE_ROOT) is inside SWALES_ROOT — a hydrate would overwrite the live atlas" ;;
esac

# Refuse to rehearse stale code. A worktree that silently did not pull is
# indistinguishable in the output from one that did, right up until the
# verification fails for a reason that was already fixed — which is exactly
# how the first kennedy re-run was spent.
BRANCH="$(cd "$REPO" && git rev-parse --abbrev-ref HEAD)"
git -C "$REPO" fetch -q origin "$BRANCH" 2>/dev/null || true
LOCAL="$(git -C "$REPO" rev-parse HEAD)"
REMOTE="$(git -C "$REPO" rev-parse "origin/$BRANCH" 2>/dev/null || echo "$LOCAL")"
if [ "$LOCAL" != "$REMOTE" ]; then
  behind="$(git -C "$REPO" rev-list --count "$LOCAL..$REMOTE" 2>/dev/null || echo '?')"
  die "worktree is $behind commit(s) behind origin/$BRANCH.
     local  $LOCAL
     origin $REMOTE
     run:   git -C $REPO pull --ff-only"
fi
if [ -n "$(git -C "$REPO" status --porcelain)" ]; then
  printf '\033[33mwarning: worktree has uncommitted changes\033[0m\n'
fi

echo "atlas     : $ATLAS"
echo "code      : $REPO  ($BRANCH @ $(cd "$REPO" && git rev-parse --short HEAD))"
echo "seed from : $SWALES_ROOT  (read-only)"
echo "workspace : $ATLAS_WORKSPACE_ROOT"
echo "outlets   : $ATLAS_OUTLETS_BUCKET"
echo "private   : $ATLAS_PRIVATE_BUCKET"
echo "cdn       : $ATLAS_PUBLIC_BASE_URL"

step "1/5  seeding the staging bucket from the box (skipped if already there)"
python3 "$REPO/scripts/seed_atlas.py" "$ATLAS" --force

step "2/5  rebuilding config (in a session)"
python3 "$REPO/scripts/build_atlas.py" config_only "$ATLAS"

step "3/5  materializing webmap + console (one session)"
PYTHONPATH="$REPO/python" python3 - "$ATLAS" <<'PY'
import logging, sys
logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
import atlas, atlas_session

name = sys.argv[1]
# One session for both: a cascade is one hydrate and one write-back, never one
# session per asset. webmap before html — the console checks for
# outlets/webmap/index.html at generation time, so the reverse order silently
# produces a console with no map link.
with atlas_session.open_session(name, purpose='rehearsal materialize') as session:
    for asset in ('webmap', 'html'):
        if asset in session.config.get('assets', {}):
            print(f"  materializing {asset}")
            atlas.materialize(session.config, asset)
PY

step "4/5  publishing (in-process, staging buckets)"
PYTHONPATH="$REPO/python" python3 - "$ATLAS" <<'PY'
import logging, sys
logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
import atlas_session, versioning

name = sys.argv[1]
with atlas_session.open_session(name, purpose='rehearsal publish') as session:
    result = versioning.publish_new_version(session.config)
print(f"published: {result['version']}")
print(f"  layers  : {len(result['catalog']['written_layers'])} written, "
      f"{len(result['catalog']['reused_layers'])} reused")
print(f"  outlets : {len(result['catalog']['written_outlets'])} written, "
      f"{len(result['catalog']['reused_outlets'])} reused")
PY

step "5/5  verifying the published copy"
ATLAS_CDN_URL="$ATLAS_PUBLIC_BASE_URL" \
  python3 "$REPO/scripts/verify_published.py" "$ATLAS" \
  || die "verification failed"

printf '\n\033[1mrehearsal complete: %s\033[0m\n' "$ATLAS"
printf 'race and wipe checks are separate: scripts/session_race.py\n'
