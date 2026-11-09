"""
DuckDB side of the game kit: the spatial filtering, in SQL.

  road_parts()  clip roads to the atlas, mark which are in a kept road
                category, and which pass near something worth reaching
                (safe locations, helispots, landmarks, enough residences);
  edge_exits()  points where board roads leave the atlas, minus any already
                in the safe_locations layer — the game_safe_exits inlet's
                seed points.

Graph work (connecting, pruning, spaces, choosing exits) is game_board.py. Features go in as
GeoJSON dicts and come back the same way, so callers and tests never touch
files. Distances are metres in the atlas's UTM zone.
"""

import json

import duckdb

import game_board
import game_kit as gk

NEAR_M = 75                # a road "serves" a target this close to it
EXIT_MERGE_M = 300         # crossings this close are one exit


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

    road_filter: {'field': property, 'keep' or 'drop': [values]}; None keeps every road.
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

    rows = con.execute("SELECT pid, rid, ST_AsGeoJSON(geom), cls FROM parts ORDER BY pid").fetchall()
    con.close()
    return [{"rid": rid, "coords": json.loads(g)["coordinates"], "cls": cls,
             "by_class": gk.road_passes(cls, road_filter),
             "serves": serves.get(pid, set())} for pid, rid, g, cls in rows]


def edge_exits(roads, bbox, road_filter=None, main_roads=None, existing=(), merge_m=EXIT_MERGE_M):
    """Safe-location points where board roads leave the atlas, not already in `existing`.

    Clips the roads here, then game_board.seed_exits chooses the crossings:
    one per main road, side and network, and at least one for every network
    that reaches the edge. Named '<road> (<side> edge)'. Returns GeoJSON Point
    features.
    """
    parts = road_parts(roads, bbox, road_filter)
    for part in parts:
        part["name"] = gk.feature_name(roads[part["rid"]])
    exits = game_board.seed_exits(parts, bbox, main_roads,
                                  existing=[s["lonlat"] for s in gk.safe_points(list(existing))],
                                  class_order=list((road_filter or {}).get("keep") or []),
                                  merge_m=merge_m)
    return [gk._pt(e["lonlat"], name=f"{e['name'] or 'Road'} ({e['side']} edge)",
                   source="edge", notes="") for e in exits]
