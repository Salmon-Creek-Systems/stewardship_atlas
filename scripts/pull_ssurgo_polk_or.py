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
    resp.raise_for_status()
    return resp.json().get('Table', [])


def fetch_polygons() -> 'gpd.GeoDataFrame':
    """Fetch MapunitPoly features via SDA WFS for Polk County bbox."""
    # WFS 1.1.0 with EPSG:4326: axis order is lat,lon (minY,minX,maxY,maxX)
    bbox_str = f"{BBOX['south']},{BBOX['west']},{BBOX['north']},{BBOX['east']},urn:ogc:def:crs:EPSG::4326"
    wfs_params = (
        f"SERVICE=WFS&VERSION=1.1.0&REQUEST=GetFeature"
        f"&TYPENAME=MapunitPoly&BBOX={bbox_str}"
    )
    url = f"WFS:{SDA_WFS_URL}?{wfs_params}"
    print(f"Fetching polygons from SDA WFS (Polk County bbox)...")
    gdf = gpd.read_file(url)
    # Reproject to WGS84 if needed
    if gdf.crs and gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)
    print(f"  {len(gdf)} polygons returned")
    return gdf


def fetch_soil_properties(mukeys: list) -> dict:
    """Batch-fetch dominant topsoil properties for a list of mukeys."""
    props_by_mukey = {}
    chunk_size = 200
    total_chunks = (len(mukeys) + chunk_size - 1) // chunk_size
    for i in range(0, len(mukeys), chunk_size):
        chunk = mukeys[i:i + chunk_size]
        chunk_num = i // chunk_size + 1
        print(f"  Tabular query chunk {chunk_num}/{total_chunks} ({len(chunk)} mukeys)...")
        mukey_list = ','.join(f"'{m}'" for m in chunk)
        sql = f"""
            SELECT co.mukey, co.compname, co.drainagecl,
                   ch.ph1to1h2o_r, ch.om_r, ch.cec7_r,
                   ch.sandtotal_r, ch.silttotal_r, ch.claytotal_r,
                   ch.texture
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
    args = parser.parse_args()

    repo_root = Path(__file__).parent.parent
    out_path = Path(args.output) if args.output else (
        repo_root / 'configuration' / 'seed_data' / 'ssurgo_polk_or.geojson'
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    gdf = fetch_polygons()
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
            props['soil_texture'] = soil.get('texture')

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
