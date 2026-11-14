"""Slides outlet: screenshot a list of pre-configured map views as PNGs.

Each view is loaded in headless Chromium with `chrome=0` (no panels or controls)
and captured once the page sets `window.atlasMapReady` — the first `idle` after
the webmap's or 3D view's own setup has finished. The pages are rendered exactly
as a visitor would see the share link, so no styling is duplicated here.

Needs `playwright` and its Chromium on the box:
    pip3 install playwright --break-system-packages
    playwright install --with-deps chromium

Pages are fetched over HTTP(S), so they must be materialized with the
chrome=0/atlasMapReady hooks (any webmap or 3dview built after this outlet was
added). An older page never signals ready and its slide fails with a timeout.
"""
import json
import logging

import slides
import versioning

logger = logging.getLogger(__name__)

READY_TIMEOUT_MS = 90_000
# Raster tiles fade in over ~300 ms after idle; give them time to finish.
SETTLE_MS = 800
SCREENSHOT_TIMEOUT_MS = 180_000
# WebGL in headless Chromium on a GPU-less box: software rendering.
CHROMIUM_ARGS = ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist']


def outlet_slides(config, outlet_name):
    from playwright.sync_api import sync_playwright  # heavy; only the renderer needs it

    asset = config['assets'][outlet_name]
    # Shared-def keys sit under 'config' in the merged asset; per-atlas keys at the top.
    outlet_config = {**asset.get('config', {}), **asset}
    views = outlet_config.get('views')
    if outlet_config.get('tour'):
        views = _fetch_tour_views(outlet_config['tour'])
    plan = slides.plan_slides(views, config.get('base_url', ''))
    opts = slides.render_options(outlet_config)

    out_dir = versioning.atlas_path(config, "outlets") / outlet_name
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob('*.png'):
        old.unlink()  # a removed or renamed view must not leave a stale slide behind

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=CHROMIUM_ARGS)
        try:
            context = browser.new_context(
                viewport={'width': opts['width'], 'height': opts['height']},
                device_scale_factor=opts['scale'])
            for slide in plan:
                results.append(_render_one(context, slide, out_dir))
        finally:
            browser.close()

    manifest = {'size': [opts['width'], opts['height']], 'scale': opts['scale'], 'slides': results}
    with open(out_dir / 'slides.json', 'w') as f:
        json.dump(manifest, f, indent=2)

    failed = [r for r in results if r['status'] != 'ok']
    logger.info(f"slides: {len(results) - len(failed)}/{len(results)} rendered to {out_dir}")
    if failed:
        raise RuntimeError(f"{len(failed)} slide(s) failed: " +
                           "; ".join(f"{r['file']}: {r['error']}" for r in failed))
    return out_dir


def _fetch_tour_views(name):
    import requests
    url = slides.TOUR_RAW_URL.format(name=name)
    resp = requests.get(url, timeout=30)
    if resp.status_code != 200:
        raise ValueError(f"slides: tour {name!r} not found at {url} (HTTP {resp.status_code})")
    views = slides.tour_views(resp.text)
    logger.info(f"slides: {len(views)} views from tour {url}")
    return views


def _render_one(context, slide, out_dir):
    entry = {k: slide[k] for k in ('title', 'file', 'url', 'kind', 'notes')}
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    try:
        page.goto(slide['render_url'], wait_until='load', timeout=READY_TIMEOUT_MS)
        page.wait_for_function('window.atlasMapReady === true', timeout=READY_TIMEOUT_MS)
        page.wait_for_timeout(SETTLE_MS)
        # Software WebGL makes a 3D screenshot itself take a minute or more.
        page.screenshot(path=str(out_dir / slide['file']), timeout=SCREENSHOT_TIMEOUT_MS)
        entry['status'] = 'ok'
    except Exception as e:
        entry['status'] = 'error'
        entry['error'] = f"{type(e).__name__}: {e}".splitlines()[0]
        if errors:
            entry['error'] += f" (page errors: {errors[0]})"
        logger.warning(f"slides: {slide['file']} failed: {entry['error']}")
    finally:
        page.close()
    if errors:
        entry['page_errors'] = errors
    return entry


asset_methods = {
    'outlet_slides': outlet_slides,
}
