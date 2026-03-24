# Unified Webapp Migration: SCVFD and WVFD

**Status**: Pending — verify kennedy is fully working first, then execute.

Kennedy is already on the unified deployment and serves as the reference. SCVFD and WVFD are still on the legacy per-atlas model.

---

## Jupyter Decision

**Chosen approach**: Single Jupyter server at `/root/swales_dev/`, one port, serving all atlases. Data isolation is maintained by notebook code (each notebook loads its own `atlas_config.json`) rather than by server-level isolation. The file browser will show all atlas directories — acceptable since notebook access is admin/dev only.

Stop all per-atlas Jupyter instances. Start one:
```bash
cd /root/swales_dev
jupyter notebook --no-browser --port 8888 \
  --certfile /etc/letsencrypt/live/fireatlas.org-0001/fullchain.pem \
  --keyfile /etc/letsencrypt/live/fireatlas.org-0001/privkey.pem \
  --ip 0.0.0.0
```
(Or adapt the existing `jupyter_notebook_config.py` pattern — just move it to `swales_dev/` root.)

**Long-term**: Evaluate JupyterHub for proper per-user workspace isolation, especially if non-admin customers need direct notebook access.

---

## What Changes

### URLs
| Atlas | Old | New |
|-------|-----|-----|
| SCVFD | `https://scvfd.fireatlas.org/staging/outlets/html/public` | `https://fireatlas.org/scvfd/staging/outlets/html/public` |
| WVFD  | `https://westportvfd.fireatlas.org/staging/outlets/html/public` | `https://fireatlas.org/westport/staging/outlets/html/public` |

Per-subdomain 301 redirect blocks preserve existing bookmarks — users land at the new URL after one redirect.

### API port
Old: `https://{atlas}.fireatlas.org:{port}` (per-atlas uvicorn, e.g. 9999 for scvfd)
New: `https://fireatlas.org:9000` (unified uvicorn, all atlases)

This is baked into HTML outlets at generation time — regeneration required after config change.

### Process
Old: one uvicorn process per atlas in its own screen session
New: one shared uvicorn process for all atlases

---

## Migration Steps (repeat per atlas)

### 1. Pull latest code
```bash
cd /root/swales_dev/{atlas}/app   # legacy app dir, for reference only
# OR for the unified app:
cd /root/swales_dev/app
git pull
```

### 2. Update `atlas_config.json`

Edit `/root/swales_dev/{atlas}/staging/atlas_config.json`:

**SCVFD:**
```json
"base_url": "https://fireatlas.org/scvfd",
"app_url":  "https://fireatlas.org:9000"
```

**WVFD (dir is `westport`):**
```json
"base_url": "https://fireatlas.org/westport",
"app_url":  "https://fireatlas.org:9000"
```

### 3. Regenerate URL-embedding outlets

```python
import atlas, json
config = json.load(open('/root/swales_dev/{atlas}/staging/atlas_config.json'))
atlas.materialize(config, 'html')
atlas.materialize(config, 'webmap')
```

Other outlets (pdf, geopackage, notebook) don't embed URLs — no regeneration needed.

### 4. Update nginx

**For SCVFD** — `fireatlas.org` config already has scvfd auth blocks. Just need to:
- Disable the wildcard redirect block (lines 110-125 in `infrastructure/nginx/fireatlas.org`) — no `*.fireatlas.org` cert exists, so it causes SSL errors
- Add per-subdomain redirect block for `scvfd.fireatlas.org` (see below)
- Remove symlink: `rm /etc/nginx/sites-enabled/scvfd.fireatlas.org`
- Confirm `sites-enabled/fireatlas.org` is active

**For WVFD** — add auth blocks and redirect block to `fireatlas.org` config (not yet present):
```nginx
location ~ ^/westport/staging/outlets/html/admin/ {
    auth_basic "Atlas Admin";
    auth_basic_user_file /root/swales_dev/roles/westport/admin.htpasswd;
}
location ~ ^/westport/staging/outlets/html/internal/ {
    auth_basic "Atlas Internal";
    auth_basic_user_file /root/swales_dev/roles/westport/internal.htpasswd;
}
```

**Per-subdomain redirect blocks** (replace the broken wildcard block):
```nginx
server {
    server_name scvfd.fireatlas.org;
    return 301 https://fireatlas.org/scvfd$request_uri;
    listen 443 ssl;
    ssl_certificate /etc/letsencrypt/live/scvfd.fireatlas.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/scvfd.fireatlas.org/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
}
server {
    server_name westportvfd.fireatlas.org;
    return 301 https://fireatlas.org/westport$request_uri;
    listen 443 ssl;
    ssl_certificate /etc/letsencrypt/live/westportvfd.fireatlas.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/westportvfd.fireatlas.org/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
}
```

Reload: `nginx -s reload`

### 5. Stop old per-atlas service
```bash
screen -r app_scvfd   # (or app_westport / app_wvfd)
# Ctrl-C to stop uvicorn
```

### 6. Confirm unified service is running
```bash
screen -r app_unified   # confirm kennedy + migrated atlases are all being served
```

If not running:
```bash
cd /root/swales_dev/app/python
SWALES_ROOT=/root/swales_dev ATLAS_DATA_BUCKET=scs-atlas-data \
uvicorn --port 9000 --host 0.0.0.0 webapp:app \
  --ssl-certfile /etc/letsencrypt/live/fireatlas.org-0001/fullchain.pem \
  --ssl-keyfile /etc/letsencrypt/live/fireatlas.org-0001/privkey.pem
```

---

## Risks / Watch List

- **Auth files**: old scvfd config pointed at `/root/swales/roles/scvfd/` (production path). Unified config uses `/root/swales_dev/roles/scvfd/`. Confirm htpasswd files exist at the `swales_dev` path before switching nginx.
- **Wildcard redirect block**: currently in `fireatlas.org` nginx config but broken (no `*.fireatlas.org` cert). Must be disabled and replaced with per-subdomain blocks before reload, or nginx will serve SSL errors on all subdomains.
- **Single point of failure**: one uvicorn process for all atlases. Consider a systemd service after migration stabilises.
- **No downtime required**: unified process and new nginx config can be in place before stopping old services.

---

## Verification

After each atlas migration:
1. `https://scvfd.fireatlas.org` → 301 redirect → `https://fireatlas.org/scvfd/staging/outlets/html/public` ✓
2. Console loads, webmap loads ✓
3. API calls (delta upload, refresh, publish) succeed via `fireatlas.org:9000` ✓
4. Admin/internal paths still prompt for auth ✓
5. Old per-atlas process no longer running ✓
