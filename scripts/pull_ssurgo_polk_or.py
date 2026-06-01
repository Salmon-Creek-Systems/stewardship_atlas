#!/usr/bin/env python3
"""
Fetch SSURGO mapunit polygon geometries + soil properties from NRCS Soil Data
Access (SDA) for a configurable bbox.

Default bbox: Polk County, OR

Writes: configuration/seed_data/ssurgo_polk_or.geojson (or --output path)

Usage:
    python scripts/pull_ssurgo_polk_or.py
    python scripts/pull_ssurgo_polk_or.py --south 45.0 --north 45.1 --west -123.3 --east -123.1 --verbose
    python scripts/pull_ssurgo_polk_or.py --output /path/to/output.geojson
"""

import sys
import json
import time
import argparse
import requests
from pathlib import Path
from shapely.ops import transform as shapely_transform

try:
    import geopandas as gpd
except ImportError:
    print("ERROR: geopandas required — pip3 install geopandas")
    sys.exit(1)

SDA_TABULAR_URL = 'https://sdmdataaccess.nrcs.usda.gov/tabular/post.rest'
# WGS84 geographic WFS endpoint
SDA_WFS_URL = 'https://sdmdataaccess.nrcs.usda.gov/Spatial/SDMWGS84Geographic.wfs'

VERBOSE = False  # set from --verbose in main()


def _log(*args):
    if VERBOSE:
        print(*args)


def sda_tabular(sql: str, timeout: int = 60) -> list:
    _log(f"\n[SQL]\n{sql.strip()}")
    body = {'query': sql, 'format': 'json+columnname'}
    resp = requests.post(SDA_TABULAR_URL, json=body, timeout=timeout)
    if not resp.ok:
        raise Exception(f"SDA tabular {resp.status_code}: {resp.text[:500]}")
    table = resp.json().get('Table', [])
    if VERBOSE:
        n_data = len(table) - 1 if len(table) > 1 else 0
        print(f"[RESPONSE] {n_data} data rows")
        for row in table[:6]:   # header + up to 5 data rows
            print(f"  {row}")
        if len(table) > 6:
            print(f"  ... ({len(table) - 6} more rows not shown)")
    return table


def fetch_polygons(bbox: dict) -> 'gpd.GeoDataFrame':
    """Fetch MapunitPoly features via SDA WFS for the given bbox.

    Tries lon,lat bbox order first; falls back to lat,lon + explicit CRS
    and WFS 1.0.0.
    """
    import io, tempfile

    attempts = [
        # lon,lat order — widely accepted despite spec
        (f"{bbox['west']},{bbox['south']},{bbox['east']},{bbox['north']}",
         "1.1.0", "lon,lat order"),
        # lat,lon with explicit CRS — strict WFS 1.1 EPSG:4326
        (f"{bbox['south']},{bbox['west']},{bbox['north']},{bbox['east']},"
         f"urn:ogc:def:crs:EPSG::4326", "1.1.0", "lat,lon + CRS"),
        # WFS 1.0.0 lon,lat (older but broadly supported)
        (f"{bbox['west']},{bbox['south']},{bbox['east']},{bbox['north']}",
         "1.0.0", "WFS 1.0 lon,lat"),
    ]

    for bbox_str, version, label in attempts:
        url = (
            f"{SDA_WFS_URL}?SERVICE=WFS&VERSION={version}&REQUEST=GetFeature"
            f"&TYPENAME=MapunitPoly&BBOX={bbox_str}"
        )
        print(f"Trying WFS ({label}): ...BBOX={bbox_str[:40]}")
        try:
            resp = requests.get(url, timeout=180)
            resp.raise_for_status()
        except Exception as e:
            print(f"  HTTP error: {e}")
            continue

        nbytes = len(resp.content)
        print(f"  Response: {nbytes:,} bytes")
        if nbytes < 200:
            print(f"  Response body: {resp.text[:300]}")
            continue

        try:
            with tempfile.NamedTemporaryFile(suffix='.gml', delete=False) as tmp:
                tmp.write(resp.content)
                tmp_path = tmp.name
            gdf = gpd.read_file(tmp_path)
        except Exception as e:
            print(f"  Parse error: {e}")
            # Try reading as GeoJSON directly (some WFS endpoints return it)
            try:
                gdf = gpd.read_file(io.BytesIO(resp.content))
            except Exception as e2:
                print(f"  GeoJSON parse also failed: {e2}")
                continue

        if gdf.crs and gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(epsg=4326)

        print(f"  {len(gdf)} polygons parsed (succeeded: {label})")
        if len(gdf) > 0:
            return gdf
        # 0 features — try next bbox ordering

    raise Exception(
        "All WFS attempts returned 0 features.\n"
        "Try fetching manually:\n"
        f"  curl '{SDA_WFS_URL}?SERVICE=WFS&VERSION=1.1.0&REQUEST=GetCapabilities' | head -100\n"
        "to verify the endpoint and feature type names."
    )


def fetch_soil_properties(mukeys: list) -> dict:
    """Batch-fetch dominant topsoil properties for a list of mukeys."""
    props_by_mukey = {}
    chunk_size = 50
    total_chunks = (len(mukeys) + chunk_size - 1) // chunk_size
    for i in range(0, len(mukeys), chunk_size):
        chunk = mukeys[i:i + chunk_size]
        chunk_num = i // chunk_size + 1
        print(f"  Tabular query chunk {chunk_num}/{total_chunks} ({len(chunk)} mukeys)...")
        mukey_list = ','.join(f"'{m}'" for m in chunk)
        # co.comppct_r and ch.hzdept_r included so verbose logging can show them;
        # co.cokey added as tiebreaker when comppct_r is equal.
        sql = f"""
            SELECT co.mukey, co.compname, co.drainagecl,
                   ch.ph1to1h2o_r, ch.om_r, ch.cec7_r,
                   ch.sandtotal_r, ch.silttotal_r, ch.claytotal_r,
                   co.comppct_r, ch.hzdept_r
            FROM component co
            INNER JOIN chorizon ch ON co.cokey = ch.cokey
            WHERE co.mukey IN ({mukey_list})
            AND co.majcompflag = 'Yes'
            AND ch.hzdept_r = (SELECT MIN(h2.hzdept_r) FROM chorizon h2 WHERE h2.cokey = co.cokey)
            ORDER BY co.mukey, co.comppct_r DESC, co.cokey ASC
        """
        rows = sda_tabular(sql, timeout=60)

        chunk_set = set(chunk)
        matched_mukeys = set()

        if len(rows) > 1:
            headers = rows[0]
            # Collect all rows per mukey before deduplicating so we can detect tiebreaks
            all_rows_by_mukey: dict = {}
            seen: set = set()
            for row in rows[1:]:
                row_dict = dict(zip(headers, row))
                mk = row_dict.get('mukey')
                if not mk:
                    continue
                all_rows_by_mukey.setdefault(mk, []).append(row_dict)
                if mk not in seen:
                    seen.add(mk)
                    props_by_mukey[mk] = row_dict
                    matched_mukeys.add(mk)

            if VERBOSE:
                for mk, mk_rows in all_rows_by_mukey.items():
                    top = mk_rows[0]
                    _log(f"  mukey={mk} compname={top.get('compname')!r} "
                         f"comppct_r={top.get('comppct_r')} hzdept_r={top.get('hzdept_r')} "
                         f"ph={top.get('ph1to1h2o_r')}")
                    # Flag tied components — show ALL options so you can identify the discrepancy
                    if len(mk_rows) >= 2:
                        pct0 = mk_rows[0].get('comppct_r')
                        pct1 = mk_rows[1].get('comppct_r')
                        if pct0 == pct1:
                            print(f"  [TIEBREAK] mukey={mk}: {len(mk_rows)} major components "
                                  f"share comppct_r={pct0} — all options:")
                            for idx, r in enumerate(mk_rows):
                                marker = "SELECTED" if idx == 0 else "alt"
                                print(f"    [{marker}] compname={r.get('compname')!r} "
                                      f"ph={r.get('ph1to1h2o_r')} om={r.get('om_r')} "
                                      f"sand={r.get('sandtotal_r')} drainagecl={r.get('drainagecl')!r}")

        missing = chunk_set - matched_mukeys
        if missing:
            print(
                f"  [WARN] {len(missing)} mukey(s) in chunk got no result row "
                f"(NULL hzdept_r or no major component data): {sorted(missing)}"
            )

        time.sleep(0.3)
    return props_by_mukey


def _cache_path_for_bbox(bbox: dict) -> Path:
    """Derive a /tmp cache filename from bbox so different bboxes don't share a cache."""
    def fmt(v):
        sign = 'm' if v < 0 else 'p'
        return f"{sign}{abs(v):.2f}".replace('.', '_')
    s, w, n, e = fmt(bbox['south']), fmt(bbox['west']), fmt(bbox['north']), fmt(bbox['east'])
    return Path(f"/tmp/ssurgo_wfs_{s}_{w}_{n}_{e}.gpkg")


def main():
    global VERBOSE

    parser = argparse.ArgumentParser(
        description='Fetch SSURGO mapunit polygons + soil properties from NRCS SDA.'
    )
    parser.add_argument('--north', type=float, default=45.20,
                        help='Bbox north latitude  (default: Polk County OR)')
    parser.add_argument('--south', type=float, default=44.78,
                        help='Bbox south latitude')
    parser.add_argument('--east',  type=float, default=-123.00,
                        help='Bbox east longitude')
    parser.add_argument('--west',  type=float, default=-123.60,
                        help='Bbox west longitude')
    parser.add_argument('--output', default=None,
                        help='Output GeoJSON path (default: configuration/seed_data/ssurgo_polk_or.geojson)')
    parser.add_argument('--wfs-cache', default=None,
                        help='Path for the WFS polygon cache GeoPackage (auto-derived from bbox if omitted)')
    parser.add_argument('--verbose', action='store_true',
                        help='Print SQL queries, raw SDA responses, and per-mukey match details')
    args = parser.parse_args()

    VERBOSE = args.verbose

    bbox = {
        'west':  args.west,
        'south': args.south,
        'east':  args.east,
        'north': args.north,
    }

    repo_root = Path(__file__).parent.parent
    out_path = Path(args.output) if args.output else (
        repo_root / 'configuration' / 'seed_data' / 'ssurgo_polk_or.geojson'
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cache_path = Path(args.wfs_cache) if args.wfs_cache else _cache_path_for_bbox(bbox)
    print(f"WFS cache: {cache_path}")

    if cache_path.exists():
        print(f"Loading WFS polygons from cache...")
        gdf = gpd.read_file(cache_path)
        print(f"  {len(gdf)} polygons loaded")
    else:
        gdf = fetch_polygons(bbox)
        if gdf.empty:
            print("ERROR: no polygons returned from WFS")
            sys.exit(1)
        print(f"Saving WFS polygons to cache: {cache_path}")
        gdf.to_file(cache_path, driver='GPKG')
        print("  Cached.")

    if gdf.empty:
        print("ERROR: no polygons returned from WFS")
        sys.exit(1)

    # NRCS WFS returns EPSG:4326 in lat,lon axis order (official spec).
    # Detect by checking that west Oregon longitude (~-123) is actually stored as x.
    bounds = gdf.geometry.total_bounds  # [minx, miny, maxx, maxy]
    if bounds[0] > 0:
        print(f"Detected lat,lon axis order (minx={bounds[0]:.3f}); swapping to lon,lat...")
        gdf.geometry = gdf.geometry.apply(
            lambda geom: shapely_transform(lambda x, y: (y, x), geom)
        )
    else:
        _log(f"Axis order looks correct (minx={bounds[0]:.3f}); no swap needed.")

    # Normalize mukey column name
    mukey_col = next((c for c in gdf.columns if c.lower() == 'mukey'), None)
    if not mukey_col:
        print(f"ERROR: no mukey column. Got: {list(gdf.columns)}")
        sys.exit(1)
    if mukey_col != 'mukey':
        gdf = gdf.rename(columns={mukey_col: 'mukey'})

    mukeys = [str(m) for m in gdf['mukey'].dropna().unique().tolist()]
    print(f"Unique mukeys: {len(mukeys)}")

    print("Fetching soil properties from SDA tabular...")
    props_by_mukey = fetch_soil_properties(mukeys)
    print(f"Properties matched: {len(props_by_mukey)}/{len(mukeys)} mukeys")

    unmatched = set(mukeys) - set(props_by_mukey.keys())
    if unmatched:
        print(f"[WARN] {len(unmatched)} mukeys with no soil data in final output: {sorted(unmatched)}")

    features = []
    matched = 0
    for _, row in gdf.iterrows():
        mukey = str(row.get('mukey', '') or '')
        geom = row.geometry.__geo_interface__ if row.geometry and not row.geometry.is_empty else None

        props = {'mukey': mukey}
        soil = props_by_mukey.get(mukey, {})
        if soil:
            matched += 1
            props['soil_series']    = soil.get('compname')
            props['drainage_class'] = soil.get('drainagecl')
            props['soil_ph']        = soil.get('ph1to1h2o_r')
            props['soil_om']        = soil.get('om_r')
            props['soil_cec']       = soil.get('cec7_r')
            props['soil_sand']      = soil.get('sandtotal_r')
            props['soil_silt']      = soil.get('silttotal_r')
            props['soil_clay']      = soil.get('claytotal_r')

        features.append({'type': 'Feature', 'geometry': geom, 'properties': props})

    fc = {'type': 'FeatureCollection', 'features': features}
    with open(out_path, 'w') as f:
        json.dump(fc, f)

    ph_vals = [float(f['properties']['soil_ph']) for f in features
               if f['properties'].get('soil_ph') is not None]
    print(f"\nWrote {len(features)} polygons ({matched} with soil data) → {out_path}")
    if ph_vals:
        print(f"pH range: {min(ph_vals):.1f} – {max(ph_vals):.1f} across {len(ph_vals)} polygons")


if __name__ == '__main__':
    main()
