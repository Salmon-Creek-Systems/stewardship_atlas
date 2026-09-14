#!/bin/bash
# Sync an atlas from S3 to a local directory for offline/demo use.
#
# Usage:
#   scripts/sync_atlas_local.sh <atlas_name> <local_dir>
#
# Examples:
#   scripts/sync_atlas_local.sh scvfd ~/atlas_demo
#
# Pulls from the atlas's staging prefix in the private bucket, which is the
# source of truth (#159). This used to rsync /root/swales_dev/{atlas}/staging
# off the box — a copy of one machine's disk, which stops being the truth at
# the cutover and is already not where compute reads from.
#
# Env:
#   ATLAS_PRIVATE_BUCKET  default scs-atlas-private-prod
#   AWS_PROFILE           default atlas

set -euo pipefail

ATLAS="${1:?Usage: $0 <atlas_name> <local_dir>}"
LOCAL_DIR="${2:?Usage: $0 <atlas_name> <local_dir>}"
BUCKET="${ATLAS_PRIVATE_BUCKET:-scs-atlas-private-prod}"
export AWS_PROFILE="${AWS_PROFILE:-atlas}"

REMOTE="s3://${BUCKET}/${ATLAS}/staging"
LOCAL_ATLAS="${LOCAL_DIR}/${ATLAS}"

echo "==> Syncing ${ATLAS} from ${REMOTE}"
echo "    → ${LOCAL_ATLAS}"
echo ""

mkdir -p "${LOCAL_ATLAS}/staging"

# Outlets — all generated outputs (webmap, PDFs, HTML, PMTiles).
echo "--- outlets/"
aws s3 sync "${REMOTE}/outlets/" "${LOCAL_ATLAS}/staging/outlets/" --no-progress

# Layers — GeoJSON only, skipping the large rasters.
echo "--- layers/ (GeoJSON only)"
aws s3 sync "${REMOTE}/layers/" "${LOCAL_ATLAS}/staging/layers/" \
    --exclude "*" --include "*.geojson" --no-progress

# The config.
echo "--- atlas_config.json"
aws s3 cp "${REMOTE}/atlas_config.json" "${LOCAL_ATLAS}/staging/" --no-progress

# Shared web assets (css, logo, docs). These live under the `shared/` prefix
# rather than inside the atlas — `local/` is a symlink on the box and a
# symlink into the session's shared cache in a workspace, and S3 has neither.
echo "--- local/ (css, logo, docs)"
aws s3 sync "s3://${BUCKET}/shared/" "${LOCAL_ATLAS}/staging/local/" --no-progress

echo ""
echo "==> Done. Start the local server with:"
echo "    python scripts/serve_local.py --data-dir ${LOCAL_DIR} --atlas ${ATLAS}"
