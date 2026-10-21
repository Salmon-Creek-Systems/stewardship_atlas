"""
DuckDB side of the game kit: the spatial filtering, in SQL.

  road_parts()  clip roads to the atlas, mark which are in a kept road
                category, and which pass near something worth reaching
                (safe locations, helispots, landmarks, enough residences);
  edge_exits()  points where main roads cross the atlas edge, minus any
                already in the safe_locations layer — the game_safe_exits
                inlet's seed points.

Graph work (connecting, pruning, spaces) is game_board.py. Features go in as
GeoJSON dicts and come back the same way, so callers and tests never touch
files. Distances are metres in the atlas's UTM zone.
"""

import json

import duckdb

import game_kit as gk

NEAR_M = 75                # a road "serves" a target this close to it
EDGE_INSET_M = 10          # crossings are taken on a boundary this far inside the atlas,
                           # so roads clipped exactly to the edge still count
EXIT_MERGE_M = 300         # crossings this close (or same road, same side) are one exit


def utm_epsg(bbox):
    lon = (bbox["west"] + bbox["east"]) / 2
    lat = (bbox["north"] + bbox["south"]) / 2
    zone = int((lon + 180) // 6) + 1
    return f"EPSG:{326 if lat >= 0 else 327}{zone:02d}"


def _connect():
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    return con


def _load(con, table, features, cls_field=None):
    """A table (id, cls, geom) from GeoJSON features; id is the feature's index."""
    con.execute(f"CREATE TABLE {table} (id INTEGER, cls VARCHAR, geom GEOMETRY)")
    rows = []
    for i, f in enumerate(features):
        if not f.get("geometry"):
            continue
        value = (f.get("properties") or {}).get(cls_field) if cls_field else None
        rows.append((i, None if value is None else str(value), json.dumps(f["geometry"])))
    if rows:
        con.executemany(f"INSERT INTO {table} VALUES (?, ?, ST_GeomFromGeoJSON(?))", rows)


def _metric(expr, epsg):
    return f"ST_Transform({expr}, 'EPSG:4326', '{epsg}', true)"


def road_parts(roads, bbox, road_filter=None, targets=None, near_m=NEAR_M):
    """Roads clipped to the atlas as parts: [{'rid', 'coords', 'by_class', 'serves'}].

    road_filter: {'field': property, 'keep': [values]}; None keeps every road.
    targets: {kind: (features, min_count)} — a part serves `kind` when at least
    min_count of its features lie within near_m (polygons by centroid).
    """
    con = _connect()
    epsg = utm_epsg(bbox)
    _load(con, "roads", roads, (road_filter or {}).get("field"))
    con.execute(f"""
        CREATE TABLE parts AS
        SELECT row_number() OVER () - 1 AS pid, rid, cls, geom, {_metric('geom', epsg)} AS m
        FROM (
            SELECT r.id AS rid, r.cls,
                   UNNEST(ST_Dump(ST_Intersection(r.geom, ST_MakeEnvelope(?, ?, ?, ?)))).geom AS geom
            FROM roads r
            WHERE ST_Intersects(r.geom, ST_MakeEnvelope(?, ?, ?, ?))
        )
        WHERE ST_GeometryType(geom)::VARCHAR = 'LINESTRING'
    """, [bbox["west"], bbox["south"], bbox["east"], bbox["north"]] * 2)

    con.execute("CREATE TABLE targets (kind VARCHAR, id INTEGER, cls VARCHAR, geom GEOMETRY)")
    minimum = {}
    for kind, (features, min_count) in (targets or {}).items():
        minimum[kind] = min_count
        _load(con, "t", features)
        con.execute("INSERT INTO targets SELECT ?, id, cls, ST_Centroid(geom) FROM t", [kind])
        con.execute("DROP TABLE t")

    served = con.execute(f"""
        SELECT p.pid, t.kind, count(*) AS n
        FROM parts p JOIN targets t ON ST_DWithin(p.m, {_metric('t.geom', epsg)}, ?)
        GROUP BY p.pid, t.kind
    """, [near_m]).fetchall()
    serves = {}
    for pid, kind, n in served:
        if n >= minimum[kind]:
            serves.setdefault(pid, set()).add(kind)

    keep = (road_filter or {}).get("keep")
    rows = con.execute(f"""
        SELECT pid, rid, ST_AsGeoJSON(geom), {'list_contains(?, cls)' if keep else 'true'}
        FROM parts ORDER BY pid
    """, [keep] if keep else []).fetchall()
    con.close()
    return [{"rid": rid, "coords": json.loads(g)["coordinates"], "by_class": bool(by_class),
             "serves": serves.get(pid, set())} for pid, rid, g, by_class in rows]


def edge_exits(roads, bbox, road_filter, existing=(), merge_m=EXIT_MERGE_M):
    """Safe-location points where kept roads leave the atlas, not already in `existing`.

    One point per road per side (crossings within merge_m merge too), named
    '<road> (<side> edge)'. Returns GeoJSON Point features.
    """
    con = _connect()
    epsg = utm_epsg(bbox)
    kx, ky = gk.meters_per_degree(bbox)
    dx, dy = EDGE_INSET_M / kx, EDGE_INSET_M / ky
    _load(con, "roads", roads, road_filter.get("field"))
    _load(con, "existing", list(existing))
    rows = con.execute(f"""
        SELECT id, ST_X(geom), ST_Y(geom)
        FROM (
            SELECT r.id, UNNEST(ST_Dump(ST_Intersection(r.geom, ST_Boundary(env.g)))).geom AS geom
            FROM roads r, (SELECT ST_MakeEnvelope(?, ?, ?, ?) AS g) env
            WHERE list_contains(?, r.cls) AND ST_Intersects(r.geom, ST_Boundary(env.g))
        ) x
        WHERE ST_GeometryType(geom)::VARCHAR = 'POINT'
          AND NOT EXISTS (SELECT 1 FROM existing e
                          WHERE ST_DWithin({_metric('e.geom', epsg)}, {_metric('x.geom', epsg)}, ?))
        ORDER BY id
    """, [bbox["west"] + dx, bbox["south"] + dy, bbox["east"] - dx, bbox["north"] - dy,
          road_filter["keep"], merge_m]).fetchall()
    con.close()

    k = (kx, ky)
    exits = []
    for rid, x, y in rows:
        name = gk.feature_name(roads[rid])
        side = gk._nearest_side([x, y], bbox)
        if any((name and e["road"] == name and e["side"] == side)
               or gk._dist_m(e["lonlat"], [x, y], k) < merge_m for e in exits):
            continue
        exits.append({"road": name, "side": side, "lonlat": [x, y]})
    return [gk._pt(e["lonlat"], name=f"{e['road'] or 'Road'} ({e['side']} edge)",
                   source="edge", notes="") for e in exits]
