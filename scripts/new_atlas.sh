#!/usr/bin/env bash
# new_atlas.sh — Provision a new atlas on the server.
#
# Run this on the server after:
#   1. DNS A record created (CDK: KennedyDns stack from local machine)
#   2. Security group ports opened (CDK: KennedySecurityGroup stack from local machine)
#   3. GeoJSON + assets/layers config files committed to the repo
#
# Usage:
#   ATLAS_NAME=kennedy WEBAPP_PORT=9997 JUPYTER_PORT=8887 bash new_atlas.sh

set -euo pipefail

# --- Configuration ---
ATLAS_NAME="${ATLAS_NAME:?Set ATLAS_NAME}"
WEBAPP_PORT="${WEBAPP_PORT:?Set WEBAPP_PORT}"
JUPYTER_PORT="${JUPYTER_PORT:?Set JUPYTER_PORT}"
DATA_ROOT="${DATA_ROOT:-/root/swales_dev}"
REPO_URL="${REPO_URL:-git@github.com:Salmon-Creek-Systems/stewardship_atlas.git}"
DOMAIN="${DOMAIN:-${ATLAS_NAME}.fireatlas.org}"
ATLAS_DATA_BUCKET="${ATLAS_DATA_BUCKET:-scs-atlas-data}"

ATLAS_DIR="${DATA_ROOT}/${ATLAS_NAME}"
APP_DIR="${ATLAS_DIR}/app"
GEOJSON="${APP_DIR}/configuration/${ATLAS_NAME}.geojson"

echo "==> Provisioning atlas: ${ATLAS_NAME}"
echo "    data root:    ${DATA_ROOT}"
echo "    domain:       ${DOMAIN}"
echo "    webapp port:  ${WEBAPP_PORT}"
echo "    jupyter port: ${JUPYTER_PORT}"
echo ""

# --- 1. Create atlas directory and clone repo into app/ ---
echo "==> Setting up app/ directory..."
mkdir -p "${ATLAS_DIR}"
if [ ! -d "${APP_DIR}/.git" ]; then
    git clone "${REPO_URL}" "${APP_DIR}"
else
    echo "    app/ already exists, pulling latest..."
    git -C "${APP_DIR}" pull
fi

# --- 2. Build the atlas (creates directory structure, config, htpasswds) ---
echo "==> Running build_atlas.py..."
cd "${APP_DIR}/python"
python3 "${APP_DIR}/scripts/build_atlas.py" "${GEOJSON}"

# --- 3. Nginx vhost ---
echo "==> Installing nginx vhost..."
NGINX_CONF="${APP_DIR}/infrastructure/nginx/${DOMAIN}"
if [ -f "${NGINX_CONF}" ]; then
    cp "${NGINX_CONF}" "/etc/nginx/sites-available/${DOMAIN}"
    ln -sf "/etc/nginx/sites-available/${DOMAIN}" "/etc/nginx/sites-enabled/${DOMAIN}"
    echo "    Installed and enabled ${DOMAIN}"
else
    echo "    WARNING: nginx config not found at ${NGINX_CONF} — skipping"
fi

# --- 4. Certbot SSL ---
# Temporarily replace ssl listen with plain http so nginx can reload
# before the cert exists, then certbot will rewrite the ssl directives.
echo "==> Obtaining SSL certificate..."
sed -i 's/listen 443 ssl;/listen 80;/' "/etc/nginx/sites-available/${DOMAIN}"
sed -i '/ssl_certificate\|ssl_certificate_key\|options-ssl-nginx\|ssl_dhparam/s/^/# /' \
    "/etc/nginx/sites-available/${DOMAIN}"
nginx -t && systemctl reload nginx
certbot --nginx -d "${DOMAIN}" --non-interactive --agree-tos -m "gateless@gmail.com"

# --- 5. Start webapp in a named screen session ---
echo "==> Starting webapp on port ${WEBAPP_PORT}..."
cd "${APP_DIR}/python"
screen -dmS "app_${ATLAS_NAME}" bash -c "
    DATASWALE_PATH=${ATLAS_DIR} ATLAS_DATA_BUCKET=${ATLAS_DATA_BUCKET} \
    uvicorn --port ${WEBAPP_PORT} --host 0.0.0.0 webapp:app \
        --reload --log-level trace \
        --ssl-certfile /etc/letsencrypt/live/${DOMAIN}/fullchain.pem \
        --ssl-keyfile  /etc/letsencrypt/live/${DOMAIN}/privkey.pem
"
echo "    Started screen session 'app_${ATLAS_NAME}' (attach: screen -r app_${ATLAS_NAME})"

# --- 6. Start Jupyter in a named screen session ---
echo "==> Starting Jupyter on port ${JUPYTER_PORT}..."
NOTEBOOK_DIR="${ATLAS_DIR}/staging/outlets/notebook"
screen -dmS "jupyter_${ATLAS_NAME}" bash -c "
    jupyter notebook --allow-root --port ${JUPYTER_PORT} \
        --notebook-dir ${NOTEBOOK_DIR}
"
echo "    Started screen session 'jupyter_${ATLAS_NAME}' (attach: screen -r jupyter_${ATLAS_NAME})"

echo ""
echo "==> Done. Atlas ${ATLAS_NAME} is up."
echo "    Webapp:  https://${DOMAIN}:${WEBAPP_PORT}"
echo "    Jupyter: https://${DOMAIN}:${JUPYTER_PORT}"
echo ""
echo "    Attach to sessions:"
echo "      screen -r app_${ATLAS_NAME}"
echo "      screen -r jupyter_${ATLAS_NAME}"
echo ""
echo "    Next: materialize layers from the notebook."
