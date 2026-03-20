# Deploy: Unified Service (one-time)

Run once when setting up the unified multi-atlas service on a new server.

## 1. SSL cert for apex domain

```bash
certbot certonly --manual --preferred-challenges dns -d "*.fireatlas.org" -d "fireatlas.org"
```

Certbot will give you two TXT challenge values. In Route 53 (personal AWS account), add both as values on a single `_acme-challenge.fireatlas.org` TXT record. Wait for propagation, then press Enter.

Cert lands at `/etc/letsencrypt/live/fireatlas.org/`.

## 2. Clone unified-webapp branch as shared app

```bash
git clone git@github.com:Salmon-Creek-Systems/stewardship_atlas.git /root/swales_dev/app
cd /root/swales_dev/app
git checkout unified-webapp
```

This is the single shared code checkout for all atlases. Do not create per-atlas copies.

## 3. Place background image

Confirm `bearbutte1.jpeg` is at `/root/swales_dev/bearbutte1.jpeg` (the nginx root). Copy or symlink if needed.

## 4. Enable nginx config

```bash
ln -s /root/swales_dev/app/infrastructure/nginx/fireatlas.org /etc/nginx/sites-enabled/fireatlas.org
nginx -t && nginx -s reload
```

## 5. Start unified webapp service

```bash
screen -S app_unified
cd /root/swales_dev/app/python
SWALES_ROOT=/root/swales_dev ATLAS_DATA_BUCKET=scs-atlas-data \
uvicorn --port 9000 --host 0.0.0.0 webapp:app \
  --ssl-certfile /etc/letsencrypt/live/fireatlas.org/fullchain.pem \
  --ssl-keyfile /etc/letsencrypt/live/fireatlas.org/privkey.pem
```

## 6. Verify

- `https://fireatlas.org/` shows the landing page
- `https://fireatlas.org:9000/status` returns `{"status": "success", ...}`
