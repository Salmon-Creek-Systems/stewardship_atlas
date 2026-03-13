#!/bin/bash
# Sync an atlas from the production server to a local directory for offline/demo use.
#
# Usage:
#   scripts/sync_atlas_local.sh <atlas_name> <local_dir> [server]
#
# Examples:
#   scripts/sync_atlas_local.sh scvfd ~/atlas_demo
#   scripts/sync_atlas_local.sh scvfd ~/atlas_demo root@myserver.example.com
#
# Default server is read from ATLAS_SERVER env var, then falls back to
# root@fireatlas.org. Override on the command line as the third argument.

set -e

ATLAS="${1:?Usage: $0 <atlas_name> <local_dir> [server]}"
LOCAL_DIR="${2:?Usage: $0 <atlas_name> <local_dir> [server]}"
SERVER="${3:-${ATLAS_SERVER:-root@fireatlas.org}}"

REMOTE_BASE="/root/swales_dev/${ATLAS}/staging"
LOCAL_ATLAS="${LOCAL_DIR}/${ATLAS}"

echo "==> Syncing ${ATLAS} from ${SERVER}:${REMOTE_BASE}"
echo "    → ${LOCAL_ATLAS}"
echo ""

mkdir -p "${LOCAL_ATLAS}/staging"

# Sync outlets — all generated outputs (webmap, PDFs, HTML, PMTiles)
echo "--- outlets/"
rsync -az --info=progress2 --copy-links \
    "${SERVER}:${REMOTE_BASE}/outlets/" \
    "${LOCAL_ATLAS}/staging/outlets/"

# Sync layers — GeoJSON files only (skip large rasters)
echo "--- layers/ (GeoJSON only)"
rsync -az --info=progress2 --copy-links \
    --include="*/" \
    --include="*.geojson" \
    --exclude="*" \
    "${SERVER}:${REMOTE_BASE}/layers/" \
    "${LOCAL_ATLAS}/staging/layers/"

# Sync atlas config
echo "--- atlas_config.json"
rsync -az --copy-links \
    "${SERVER}:${REMOTE_BASE}/atlas_config.json" \
    "${LOCAL_ATLAS}/staging/"

# Sync local/ (CSS, logo, documents) — resolve symlink on the remote side
echo "--- local/ (css, logo, docs)"
rsync -az --info=progress2 --copy-links \
    "${SERVER}:${REMOTE_BASE}/local/" \
    "${LOCAL_ATLAS}/staging/local/"

echo ""
echo "==> Done. Start the local server with:"
echo "    python scripts/serve_local.py --data-dir ${LOCAL_DIR} --atlas ${ATLAS}"
