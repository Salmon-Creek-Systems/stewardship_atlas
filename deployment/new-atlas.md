# Deploy: New Atlas

Run for each new atlas. Assumes the unified service is already running (see `unified-service.md`).

Replace `{atlas}` throughout with the atlas name (e.g. `scvfd`, `kennedy`).

## 1. Create config files

In `configuration/` within the shared checkout (`/root/swales_dev/app/configuration/`):

**`{atlas}.geojson`** — region boundary and properties:
```json
{
  "type": "FeatureCollection",
  "features": [{
    "type": "Feature",
    "geometry": { "type": "Polygon", "coordinates": [[[lon,lat], ...]] },
    "properties": {
      "name": "{atlas}",
      "data_root": "/root/swales_dev",
      "shared_dir": "/root/data",
      "assets_path": "/root/swales_dev/app/configuration/{atlas}_assets.json",
      "layers_path": "/root/swales_dev/app/configuration/{atlas}_layers.json",
      "admin_emails": ["admin@example.com"],
      "base_url": "https://fireatlas.org/{atlas}",
      "app_url": "https://fireatlas.org:9000",
      "port": 9000,
      "versioned_outlets": ["html", "webmap"],
      "logo_url": "https://scs-atlas-data.s3.amazonaws.com/{atlas}/logo.png"
    }
  }]
}
```

`base_url` and `app_url` match the defaults so can be omitted, but explicit is clearer.

**`{atlas}_layers.json`** — copy `kennedy_layers.json` as a starting template.

**`{atlas}_assets.json`** — copy `kennedy_assets.json` as a starting template.

Commit and push, then pull on the server.

## 2. Run build script

```bash
cd /root/swales_dev/app
python scripts/build_atlas.py configuration/{atlas}.geojson
```

Creates:
```
/root/swales_dev/{atlas}/
  app/     ->  /root/swales_dev/app   (symlink to shared code)
  local/   ->  /root/data             (symlink to shared data)
  CURRENT/ ->  staging                (symlink)
  staging/
    atlas_config.json
    layers/, deltas/, outlets/
  roles/{atlas}/
    admin.htpasswd, internal.htpasswd
```

Also materializes `notebook` and `html` outlets.

## 3. Add nginx auth blocks

Edit `/root/swales_dev/app/infrastructure/nginx/fireatlas.org`, add inside the main `server` block:

```nginx
location ~ ^/{atlas}/staging/outlets/html/admin/ {
    auth_basic "Atlas Admin";
    auth_basic_user_file /root/swales_dev/roles/{atlas}/admin.htpasswd;
}
location ~ ^/{atlas}/staging/outlets/html/internal/ {
    auth_basic "Atlas Internal";
    auth_basic_user_file /root/swales_dev/roles/{atlas}/internal.htpasswd;
}
```

```bash
nginx -s reload
```

## 4. Materialize data

Trigger inlets and outlets via the API. Adjust asset names to match what's defined in `{atlas}_assets.json`:

```bash
curl "https://fireatlas.org:9000/refresh?swale={atlas}&asset=dem"
curl "https://fireatlas.org:9000/refresh?swale={atlas}&asset=derived_hillshade"
curl "https://fireatlas.org:9000/refresh?swale={atlas}&asset=webmap"
curl "https://fireatlas.org:9000/refresh?swale={atlas}&asset=html"
```

## 5. Verify

- `https://fireatlas.org/{atlas}/` redirects to public console
- Webmap renders
- Browser network tab: delta upload POSTs to `https://fireatlas.org:9000/delta_upload/{atlas}`
- SQL query POSTs to `https://fireatlas.org:9000/sql_query/{atlas}`

## Notes

- No new port, SSL cert, DNS record, or service needed per atlas.
- nginx auth blocks are the only per-atlas server change required.
- WVFD-specific filenames leak into some shared inlet templates — if an inlet fails
  to find a file, add an explicit `inpath_template` override in `{atlas}_assets.json`.
