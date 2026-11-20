"""Legend probe: rows (Only rendered on/off) and, per row, whether a click hides every group member.

Run on the box (needs playwright + chromium); pass one or more webmap/webedit URLs:
  python3 scripts/legend_probe.py https://fireatlas.org/grange/staging/outlets/webmap/
Waits 45 s per page for software WebGL. A toggle is "ok" when unchecking hides the whole
group and re-checking restores each member's starting visibility.
"""
import sys, json
from playwright.sync_api import sync_playwright
ROWS = """() => [...document.querySelectorAll('.legend-table input[type=checkbox]')].map(c => c.value)"""
NAMES = """() => [...document.querySelectorAll('.legend-table tr')].map(r => r.innerText.trim())"""
GROUP = """(id) => { const g = (map.getLayer(id).metadata||{}).group;
  const ids = map.getStyle().layers.filter(l => (l.metadata||{}).group === g && g).map(l => l.id);
  return Object.fromEntries((ids.length ? ids : [id]).map(i => [i, map.getLayoutProperty(i,'visibility') || 'visible'])); }"""
out = {}
with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader"])
    for url in sys.argv[1:]:
        pg = b.new_context(viewport={"width": 1400, "height": 900},
                           http_credentials={"username": "admin", "password": "admin"}).new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
        pg.goto(url, wait_until="load", timeout=120000)
        pg.wait_for_timeout(45000)
        r = {"zoom": round(pg.evaluate("() => map.getZoom()"), 2),
             "rows_only_rendered": pg.evaluate(NAMES)}
        r["only_rendered_box"] = pg.evaluate("() => { const el = document.querySelector('.maplibregl-legend-onlyRendered-checkbox'); if (!el) return 'absent'; const was = el.checked; if (was) el.click(); return was ? 'was on' : 'was off'; }")
        pg.wait_for_timeout(2000)
        r["rows_all"] = pg.evaluate(NAMES)
        r["targets"] = pg.evaluate(ROWS)
        toggles = {}
        for tid in r["targets"]:
            box = pg.locator(f'.legend-table input[value="{tid}"]').first
            start = pg.evaluate(GROUP, tid)
            box.evaluate("el => el.click()"); pg.wait_for_timeout(1500)
            off = pg.evaluate(GROUP, tid)
            box.evaluate("el => el.click()"); pg.wait_for_timeout(1500)
            on = pg.evaluate(GROUP, tid)
            toggles[tid] = "ok" if all(v == "none" for v in off.values()) and on == start else {"start": start, "off": off, "on": on}
        r["toggles"] = toggles
        r["pageerrors"] = errs
        out[url.split("/outlets/")[0].rsplit("/", 2)[-2] + " " + url.split("/outlets/")[1]] = r
    b.close()
print(json.dumps(out, indent=1))
