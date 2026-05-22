#!/usr/bin/env python3
"""
One-time pull of SSURGO mapunit polygon geometries + soil properties for
Polk County, OR from NRCS Soil Data Access (SDA).

Writes: configuration/seed_data/ssurgo_polk_or.geojson

Usage (run on server where geopandas is available):
    python scripts/pull_ssurgo_polk_or.py
    python scripts/pull_ssurgo_polk_or.py --output /path/to/output.geojson
"""

import sys
import json
import time
import argparse
import requests
from pathlib import Path

try:
    import geopandas as gpd
except ImportError:
    print("ERROR: geopandas required — pip3 install geopandas")
    sys.exit(1)

SDA_TABULAR_URL = 'https://sdmdataaccess.nrcs.usda.gov/tabular/post.rest'
# WGS84 geographic WFS endpoint
SDA_WFS_URL = 'https://sdmdataaccess.nrcs.usda.gov/Spatial/SDMWGS84Geographic.wfs'

# Polk County, OR — generous bbox, clipped to actual survey polygons by SDA
BBOX = {'west': -123.60, 'south': 44.78, 'east': -123.00, 'north': 45.20}


def sda_tabular(sql: str, timeout: int = 60) -> list:
    body = {'query': sql, 'format': 'json+columnname'}
    resp = requests.post(SDA_TABULAR_URL, json=body, timeout=timeout)
    if not resp.ok:
        raise Exception(f"SDA tabular {resp.status_code}: {resp.text[:500]}")
    return resp.json().get('Table', [])


def fetch_polygons() -> 'gpd.GeoDataFrame':
    """Fetch MapunitPoly features via SDA WFS for Polk County bbox.

    Uses direct HTTP rather than GDAL WFS driver to avoid silent failures.
    Tries lon,lat bbox order (WFS 1.0 convention) first; falls back to
    lat,lon with explicit CRS (WFS 1.1 EPSG:4326 spec).
    """
    import io, tempfile

    attempts = [
        # lon,lat order — widely accepted despite spec
        (f"{BBOX['west']},{BBOX['south']},{BBOX['east']},{BBOX['north']}",
         "1.1.0", "lon,lat order"),
        # lat,lon with explicit CRS — strict WFS 1.1 EPSG:4326
        (f"{BBOX['south']},{BBOX['west']},{BBOX['north']},{BBOX['east']},"
         f"urn:ogc:def:crs:EPSG::4326", "1.1.0", "lat,lon + CRS"),
        # WFS 1.0.0 lon,lat (older but broadly supported)
        (f"{BBOX['west']},{BBOX['south']},{BBOX['east']},{BBOX['north']}",
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

        print(f"  {len(gdf)} polygons parsed")
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
        sql = f"""
            SELECT co.mukey, co.compname, co.drainagecl,
                   ch.ph1to1h2o_r, ch.om_r, ch.cec7_r,
                   ch.sandtotal_r, ch.silttotal_r, ch.claytotal_r
            FROM component co
            INNER JOIN chorizon ch ON co.cokey = ch.cokey
            WHERE co.mukey IN ({mukey_list})
            AND co.majcompflag = 'Yes'
            AND ch.hzdept_r = (SELECT MIN(h2.hzdept_r) FROM chorizon h2 WHERE h2.cokey = co.cokey)
            ORDER BY co.mukey, co.comppct_r DESC
        """
        rows = sda_tabular(sql, timeout=60)
        if len(rows) > 1:
            headers = rows[0]
            seen = set()
            for row in rows[1:]:
                row_dict = dict(zip(headers, row))
                mk = row_dict.get('mukey')
                if mk and mk not in seen:
                    seen.add(mk)
                    props_by_mukey[mk] = row_dict
        time.sleep(0.3)
    return props_by_mukey


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default=None)
    parser.add_argument('--wfs-cache', default='/tmp/ssurgo_polk_or_wfs.gpkg',
                        help='Path to cache the raw WFS polygons (avoids re-downloading 50MB)')
    args = parser.parse_args()

    repo_root = Path(__file__).parent.parent
    out_path = Path(args.output) if args.output else (
        repo_root / 'configuration' / 'seed_data' / 'ssurgo_polk_or.geojson'
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cache_path = Path(args.wfs_cache)
    if cache_path.exists():
        print(f"Loading WFS polygons from cache: {cache_path}")
        gdf = gpd.read_file(cache_path)
        print(f"  {len(gdf)} polygons loaded")
    else:
        gdf = fetch_polygons()
        if gdf.empty:
            print("ERROR: no polygons returned from WFS")
            sys.exit(1)
        print(f"Saving WFS polygons to cache: {cache_path}")
        gdf.to_file(cache_path, driver='GPKG')
        print("  Cached.")
    if gdf.empty:
        print("ERROR: no polygons returned from WFS")
        sys.exit(1)

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

    features = []
    matched = 0
    for _, row in gdf.iterrows():
        mukey = str(row.get('mukey', '') or '')
        geom = row.geometry.__geo_interface__ if row.geometry and not row.geometry.is_empty else None

        props = {'mukey': mukey}
        soil = props_by_mukey.get(mukey, {})
        if soil:
            matched += 1
            props['soil_series'] = soil.get('compname')
            props['drainage_class'] = soil.get('drainagecl')
            props['soil_ph'] = soil.get('ph1to1h2o_r')
            props['soil_om'] = soil.get('om_r')
            props['soil_cec'] = soil.get('cec7_r')
            props['soil_sand'] = soil.get('sandtotal_r')
            props['soil_silt'] = soil.get('silttotal_r')
            props['soil_clay'] = soil.get('claytotal_r')

        features.append({'type': 'Feature', 'geometry': geom, 'properties': props})

    fc = {'type': 'FeatureCollection', 'features': features}
    with open(out_path, 'w') as f:
        json.dump(fc, f)

    ph_vals = [f['properties']['soil_ph'] for f in features if f['properties'].get('soil_ph') is not None]
    print(f"\nWrote {len(features)} polygons ({matched} with soil data) → {out_path}")
    if ph_vals:
        print(f"pH range: {min(ph_vals):.1f} – {max(ph_vals):.1f} across {len(ph_vals)} polygons")


if __name__ == '__main__':
    main()
