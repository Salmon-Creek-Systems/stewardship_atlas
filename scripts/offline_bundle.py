#!/usr/bin/env python3
"""
Make a synced atlas copy work with no network, for a read-only demo.

    scripts/sync_atlas_local.sh south_fork_eel ~/atlas_demo
    python3 scripts/offline_bundle.py ~/atlas_demo south_fork_eel
    python3 scripts/serve_local.py ~/atlas_demo

Rewrites the *copy* in place (never the repo or the box). Re-running the sync
restores the original pages, so to change the port: sync, then bundle again.

What it does:
  1. Downloads the external files the pages load (JS libraries, label glyphs,
     the atlas's own S3 COGs, 3D terrain tiles for the atlas extent) into
     <demo_dir>/_offline/.
  2. Rewrites URLs: the box and the API go to localhost (API under /_api/,
     which serve_local.py refuses), externals go to their _offline/ copies.
  3. Drapes the 3D view on the local basemap COG instead of Esri imagery.
  4. Injects a banner + an "offline demo" alert on any attempted change.
  5. Prints any http(s) URL still left, so gaps are visible before the demo.

Stdlib only — the local Python is bare.
"""

import argparse
import json
import math
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PAGE_SUFFIXES = {".html", ".js", ".css"}
GLYPH_RANGES = ["0-255", "256-511", "8192-8447"]  # Latin, Latin-ext, punctuation
TERRAIN_ZOOMS = range(8, 16)                      # terrarium source maxzoom is 15
TERRAIN_MARGIN = 0.3                              # pitched views see past the bbox
TERRARIUM = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
ESRI_IMAGERY = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"

OFFLINE_SNIPPET = """
<!-- offline demo (scripts/offline_bundle.py) -->
<style>#offline-demo-banner{position:fixed;top:0;left:50%;transform:translateX(-50%);
z-index:100000;background:#b45309;color:#fff;font:600 12px sans-serif;padding:2px 10px;
border-radius:0 0 6px 6px;pointer-events:none}</style>
<script>
(function () {
  var MSG = 'Offline demo: changes are not saved.';
  function isChange(url, method) {
    return String(url).indexOf('/_api/') !== -1 &&
           String(method || 'GET').toUpperCase() !== 'GET' &&
           String(url).indexOf('/select_features/') === -1;
  }
  var open = XMLHttpRequest.prototype.open, send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url) {
    this._offlineChange = isChange(url, method);
    return open.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function () {
    if (this._offlineChange) alert(MSG);
    return send.apply(this, arguments);
  };
  var origFetch = window.fetch;
  window.fetch = function (input, init) {
    var url = typeof input === 'string' ? input : (input && input.url);
    if (isChange(url, init && init.method)) alert(MSG);
    return origFetch.apply(this, arguments);
  };
  document.addEventListener('DOMContentLoaded', function () {
    var b = document.createElement('div');
    b.id = 'offline-demo-banner';
    b.textContent = 'OFFLINE DEMO \\u2014 read only';
    document.body.appendChild(b);
  });
})();
</script>
"""


def download(url, dest):
    """Fetch url to dest unless already present. Returns True on success."""
    if dest.exists() and dest.stat().st_size > 0:
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "atlas-offline-bundle"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            dest.write_bytes(resp.read())
        return True
    except Exception as exc:  # report and carry on; the leftover-URL check catches gaps
        print(f"   ! {url}: {exc}")
        return False


def page_files(root):
    return [p for p in root.rglob("*") if p.is_file() and p.suffix in PAGE_SUFFIXES]


def atlas_bbox(layers_dir):
    """Union bbox of every vector layer (the H3 grids cover the atlas extent)."""
    xs, ys = [], []

    def walk(coords):
        if coords and isinstance(coords[0], (int, float)):
            xs.append(coords[0])
            ys.append(coords[1])
        else:
            for c in coords:
                walk(c)

    for path in layers_dir.glob("*/*.geojson"):
        for feat in json.loads(path.read_text()).get("features", []):
            geom = feat.get("geometry") or {}
            walk(geom.get("coordinates") or [])
    return min(xs), min(ys), max(xs), max(ys)


def tile_xy(lon, lat, z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def fetch_terrain(bbox, out_dir):
    w, s, e, n = bbox
    dx, dy = (e - w) * TERRAIN_MARGIN, (n - s) * TERRAIN_MARGIN
    w, s, e, n = w - dx, s - dy, e + dx, n + dy
    tiles = []
    for z in TERRAIN_ZOOMS:
        x0, y0 = tile_xy(w, n, z)
        x1, y1 = tile_xy(e, s, z)
        tiles += [(z, x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]
    print(f"   {len(tiles)} terrain tiles, z{TERRAIN_ZOOMS[0]}-{TERRAIN_ZOOMS[-1]}", flush=True)
    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(lambda t: download(TERRARIUM.format(z=t[0], x=t[1], y=t[2]),
                                         out_dir / str(t[0]) / str(t[1]) / f"{t[2]}.png"),
                      tiles))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("demo_dir", type=Path)
    ap.add_argument("atlas")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()

    demo = args.demo_dir.expanduser().resolve()
    staging = demo / args.atlas / "staging"
    outlets, layers = staging / "outlets", staging / "layers"
    offline = demo / "_offline"
    local = f"http://localhost:{args.port}"
    if not outlets.is_dir():
        sys.exit(f"No outlets at {outlets} — run scripts/sync_atlas_local.sh first.")

    pages = page_files(outlets) + page_files(demo / "local")
    texts = {p: p.read_text(errors="surrogateescape") for p in pages}
    every_url = set()
    for t in texts.values():
        every_url |= set(re.findall(r"https?://[^\"'`\s)<>#]+", t))

    print("==> JS libraries")
    for url in sorted(u for u in every_url if re.match(r"https://(www\.)?unpkg\.com/", u)):
        path = re.sub(r"https://(www\.)?unpkg\.com/", "", url)
        download(url, offline / "unpkg" / path)

    print("==> Label glyphs")
    stacks = set()
    for t in texts.values():
        stacks |= set(re.findall(r'"text-font":\s*\[\s*"([^"]+)"', t))
    for stack in sorted(stacks):
        for rng in GLYPH_RANGES:
            download(f"https://fonts.undpgeohub.org/fonts/{urllib.request.quote(stack)}/{rng}.pbf",
                     offline / "fonts" / stack / f"{rng}.pbf")

    print("==> Atlas COGs on S3")
    s3_prefix = "https://scs-atlas-data.s3.us-east-1.amazonaws.com/"
    for url in sorted(u for u in every_url if u.startswith(s3_prefix) and u.endswith(".tif")):
        download(url, offline / "s3" / url[len(s3_prefix):])

    print("==> 3D terrain tiles")
    fetch_terrain(atlas_bbox(layers), offline / "terrarium")

    basemap_cog = layers / "basemap" / "basemap.cog.tif"
    replacements = [
        ("https://fireatlas.org:9000", f"{local}/_api"),
        ("https://fireatlas.org/", f"{local}/"),
        ("https://www.unpkg.com/", f"{local}/_offline/unpkg/"),
        ("https://unpkg.com/", f"{local}/_offline/unpkg/"),
        ("https://fonts.undpgeohub.org/fonts/", f"{local}/_offline/fonts/"),
        (s3_prefix, f"{local}/_offline/s3/"),
        ("https://s3.amazonaws.com/elevation-tiles-prod/terrarium/", f"{local}/_offline/terrarium/"),
    ]
    drape = None
    if basemap_cog.exists():
        cog_url = f"cog://{local}/{args.atlas}/staging/layers/basemap/basemap.cog.tif"
        drape = (re.compile(r'"tiles":\s*\[\s*"' + re.escape(ESRI_IMAGERY) + r'"\s*\]'),
                 f'"url": "{cog_url}"')

    print("==> Rewriting pages")
    changed = 0
    for path, text in texts.items():
        new = text
        if drape and path.parent.name == "3dview":
            new = drape[0].sub(drape[1], new)  # before the generic rewrites
        for old, rep in replacements:
            new = new.replace(old, rep)
        if path.suffix == ".html" and "offline demo (scripts/offline_bundle.py)" not in new:
            new = re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + OFFLINE_SNIPPET, new, count=1)
        if new != text:
            path.write_text(new, errors="surrogateescape")
            changed += 1
    print(f"   {changed} files rewritten")

    print("==> External URLs still referenced (these will not load offline)")
    left = {}
    for path in page_files(outlets) + page_files(demo / "local"):
        for url in re.findall(r"https?://[^\"'`\s)<>]+", path.read_text(errors="surrogateescape")):
            if not url.startswith(local):
                host = re.sub(r"^(https?://[^/]+).*", r"\1", url)
                left[host] = left.get(host, 0) + 1
    for host, n in sorted(left.items(), key=lambda kv: -kv[1]):
        print(f"   {n:5d}  {host}")

    print(f"\n==> Done. Serve with: python3 scripts/serve_local.py {demo} --port {args.port}")
    print(f"    then open {local}/{args.atlas}/staging/outlets/console/public/")


if __name__ == "__main__":
    main()
