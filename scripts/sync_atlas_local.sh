#!/bin/bash
# Copy one atlas's read-only outputs from the box for an offline demo.
#
# Usage:
#   scripts/sync_atlas_local.sh <atlas_name> <local_dir> [server]
#
# Example:
#   scripts/sync_atlas_local.sh south_fork_eel ~/atlas_demo
#
# Mirrors the production URL layout so root-relative links keep working:
#   <local_dir>/<atlas>/staging/{outlets,layers}   ← /<atlas>/staging/...
#   <local_dir>/local/{documents,css}              ← /local/...
#
# Next steps: scripts/offline_bundle.py, then scripts/serve_local.py.
# See documents/local_demo_serving.md.

set -e

ATLAS="${1:?Usage: $0 <atlas_name> <local_dir> [server]}"
LOCAL_DIR="${2:?Usage: $0 <atlas_name> <local_dir> [server]}"
SERVER="${3:-${ATLAS_SERVER:-ubuntu@54.241.152.98}}"
SSH_KEY="${ATLAS_SSH_KEY:-$HOME/.ssh/SCSProd.pem}"

REMOTE_BASE="/root/swales_dev/${ATLAS}/staging"
LOCAL_ATLAS="${LOCAL_DIR}/${ATLAS}/staging"

# Root SSH is blocked on the box; read root-owned files via sudo rsync.
RSYNC=(rsync -az --copy-links
       -e "ssh -i ${SSH_KEY}" --rsync-path="sudo rsync")

echo "==> Syncing ${ATLAS} from ${SERVER}:${REMOTE_BASE} → ${LOCAL_ATLAS}"
mkdir -p "${LOCAL_ATLAS}" "${LOCAL_DIR}/local"

echo "--- outlets/ (skipping retired html, work dirs, notebooks)"
"${RSYNC[@]}" --exclude="html/" --exclude="work/" --exclude="notebook/" \
    --exclude=".htpasswd" \
    "${SERVER}:${REMOTE_BASE}/outlets/" "${LOCAL_ATLAS}/outlets/"

echo "--- layers/ (GeoJSON, COGs, sidecars; skipping raw .tiff sources)"
"${RSYNC[@]}" --exclude="work/" \
    --include="*/" --include="*.geojson" --include="*.cog.tif" \
    --include="*.pmtiles" --include="*.json" --exclude="*" \
    "${SERVER}:${REMOTE_BASE}/layers/" "${LOCAL_ATLAS}/layers/"

"${RSYNC[@]}" "${SERVER}:${REMOTE_BASE}/atlas_config.json" "${LOCAL_ATLAS}/"

# staging/local -> /root/data is ~25 GB of shared data; take only what pages link.
echo "--- local/ (documents + css only)"
for d in documents css; do
    "${RSYNC[@]}" "${SERVER}:${REMOTE_BASE}/local/${d}/" "${LOCAL_DIR}/local/${d}/"
done

echo ""
echo "==> Done. Next: python3 scripts/offline_bundle.py ${LOCAL_DIR} ${ATLAS}"
