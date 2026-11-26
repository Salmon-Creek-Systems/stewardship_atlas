#!/usr/bin/env python3
"""Plot the 1968 60-ft easement (Humboldt Bk 986 OR p 236) and compare it with the
proposed haul route of THP 1-26-00170-HUM and with five road datasets.

This is a record of one analysis (2026-11-20), not a general tool. To redo it for
another easement, copy this directory and edit the constants and courses.csv.
README.md explains the method, the caveats and the atlas steps.

    python3 scripts/easement_986_236/easement_986_236.py OUT_DIR [--refresh]

Needs network access, shapely + duckdb (pip), Google Chrome for the screenshots,
and ssh to the box for the parcel clip. Downloads are cached in OUT_DIR/cache;
--refresh fetches them again.
"""
import argparse
import csv
import heapq
import json
import math
import subprocess
import sys
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------- constants
# Everything specific to this easement. Coordinates are lon/lat (WGS84) unless noted.

COURSES_CSV = HERE / 'courses.csv'       # pm22 = Parcel Map No. 22 sheet 4, courses 1-26;
                                         # grant = the grant's 8 calls through Sec 14

TOWNSHIP_IDS = ('CA150030S0020E0', 'CA150020S0020E0', 'CA150030S0030E0')  # BLM PLSSID prefixes fetched
SEC = '13'                               # section whose north line sets the rotation (T3S R2E HM)
RECORD_N_LINE_SEC13 = 'N 89-24-58 W'     # its bearing as recorded on Parcel Map No. 22
STATION26_END_OFFSET_FT = (0.0, -33.0)   # grant: course 26 ends on the 13/14 line, 33 ft S of the 1/4 corner

FIT_COURSES = 8                          # map note: the road along stations 1-8 is county maintained,
FIT_ROAD = 'THOMAS RD'                   # so courses 1-8 are fitted to this county road
EASEMENT_HALF_WIDTH_FT = 30

ROADS_URL = 'https://fireatlas.org/scvfd/staging/layers/roads/roads.geojson'   # Humboldt County roads
HAUL_ROADS = {'SERENDIPITY RD', 'LOWER SAMUELS RANCH RD', 'THOMAS RD', 'SALMON CREEK RD'}  # THP Sec. IV p. 120
HAUL_JUNCTION = (-123.95521, 40.23569)   # Serendipity / Lower Samuels Ranch; route starts at Serendipity's far end
HAUL_END = (-123.83229, 40.25086)        # Salmon Creek Rd meets the US 101 off-ramp (Exit 653)
OVERLAP_TOL_FT = 100                     # route counted "along the easement" within this of the centerline
OVERLAP_GAP_FT = 200                     # gaps shorter than this are merged

VIEW_BBOX = (-123.935, 40.192, -123.880, 40.218)   # W, S, E, N for road sources and parcels
OVERTURE_RELEASE = '2026-10-21.0'      # Overture keeps ~2 releases; bump when this one is gone
OVERPASS_SERVERS = ('https://overpass-api.de/api/interpreter', 'https://overpass.kumi.systems/api/interpreter')
PARCELS_ON_BOX = '/root/data/humboldt_parcels.geojson'
BOX = 'ubuntu@54.241.152.98'
BOX_KEY = '~/.ssh/SCSProd.pem'

XSEC_HALF_FT = 150          # cross-section length each side of the county road
XSEC_TOL_DEG = 45           # only road pieces within this of the road's heading count
XSEC_SITES = 6              # how many biggest-disagreement sites to show
XSEC_MIN_SEP_FT = 500

MAP_CENTER, MAP_ZOOM = (40.2045, -123.9065), 16
NAIP_NOTE = 'USDA NAIP, acquired 2022-05-31, 0.6 m'
NAIP_EXPORT = 'https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPImagery/ImageServer/exportImage'
CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
LEAFLET = 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4'

# ---------------------------------------------------------------- plain geometry (no dependencies; tested)

FT = 0.3048
_A, _E2 = 6378137.0, 0.00669437999014


class Local:
    """Flat east/north feet around an origin; good to well under a foot across a few km."""
    def __init__(self, lon0, lat0):
        self.lon0, self.lat0 = lon0, lat0
        p = math.radians(lat0)
        w = math.sqrt(1 - _E2 * math.sin(p) ** 2)
        self.m = _A * (1 - _E2) / w ** 3          # meridional radius
        self.n = _A / w * math.cos(p)             # parallel radius

    def fwd(self, lon, lat):
        return (math.radians(lon - self.lon0) * self.n / FT, math.radians(lat - self.lat0) * self.m / FT)

    def inv(self, e, n):
        return (self.lon0 + math.degrees(e * FT / self.n), self.lat0 + math.degrees(n * FT / self.m))


def az_deg(bearing):
    """'S 2-23-00 W' (quadrant bearing, deg-min-sec) or 'N'/'E'/'S'/'W' -> azimuth in degrees."""
    b = bearing.strip()
    if b in ('N', 'E', 'S', 'W'):
        return {'N': 0, 'E': 90, 'S': 180, 'W': 270}[b]
    ns, ang, ew = b.split()
    d, m, s = (float(x) for x in ang.split('-'))
    a = d + m / 60 + s / 3600
    return {('N', 'E'): a, ('S', 'E'): 180 - a, ('S', 'W'): 180 + a, ('N', 'W'): 360 - a}[(ns, ew)]


def heading(p, q):
    """Azimuth (radians, clockwise from north) from p to q."""
    return math.atan2(q[0] - p[0], q[1] - p[1])


def walk(start, courses, rot_deg=0.0, backward=False):
    """Points visited by (azimuth, distance) courses from start; backward walks them in reverse
    so the result ends at start."""
    if backward:
        pts = [start]
        for a, d in reversed(courses):
            t = math.radians(a + rot_deg)
            e, n = pts[-1]
            pts.append((e - d * math.sin(t), n - d * math.cos(t)))
        return pts[::-1]
    pts = [start]
    for a, d in courses:
        t = math.radians(a + rot_deg)
        e, n = pts[-1]
        pts.append((e + d * math.sin(t), n + d * math.cos(t)))
    return pts


def densify(poly, step):
    out = [poly[0]]
    for a, b in zip(poly, poly[1:]):
        k = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) // step))
        out += [(a[0] + (b[0] - a[0]) * i / k, a[1] + (b[1] - a[1]) * i / k) for i in range(1, k + 1)]
    return out


def nearest_on_seg(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
    q = (a[0] + t * dx, a[1] + t * dy)
    return math.hypot(p[0] - q[0], p[1] - q[1]), q


def nearest_on_lines(p, lines):
    return min((nearest_on_seg(p, a, b) for ln in lines for a, b in zip(ln, ln[1:])), key=lambda r: r[0])


def apply_rigid(pts, theta, tx, ty):
    c, s = math.cos(theta), math.sin(theta)
    return [(c * x - s * y + tx, s * x + c * y + ty) for x, y in pts]


def icp_fit(points, lines, iters=30):
    """Rigid (rotation + translation) fit of points onto polylines: iterated closest point,
    each step a 2D Procrustes solve. Returns (theta radians CCW, tx, ty)."""
    theta, tx, ty = 0.0, 0.0, 0.0
    mx = sum(p[0] for p in points) / len(points)
    my = sum(p[1] for p in points) / len(points)
    for _ in range(iters):
        cur = apply_rigid(points, theta, tx, ty)
        tgt = [nearest_on_lines(p, lines)[1] for p in cur]
        nx = sum(q[0] for q in tgt) / len(tgt)
        ny = sum(q[1] for q in tgt) / len(tgt)
        sxx = sum((p[0] - mx) * (q[0] - nx) + (p[1] - my) * (q[1] - ny) for p, q in zip(points, tgt))
        sxy = sum((p[0] - mx) * (q[1] - ny) - (p[1] - my) * (q[0] - nx) for p, q in zip(points, tgt))
        theta = math.atan2(sxy, sxx)
        c, s = math.cos(theta), math.sin(theta)
        tx, ty = nx - (c * mx - s * my), ny - (s * mx + c * my)
    return theta, tx, ty


def seg_intersection(p1, p2, q1, q2):
    """Intersection point of segments p1p2 and q1q2, or None."""
    d = (p2[0] - p1[0]) * (q2[1] - q1[1]) - (p2[1] - p1[1]) * (q2[0] - q1[0])
    if d == 0:
        return None
    t = ((q1[0] - p1[0]) * (q2[1] - q1[1]) - (q1[1] - p1[1]) * (q2[0] - q1[0])) / d
    u = ((q1[0] - p1[0]) * (p2[1] - p1[1]) - (q1[1] - p1[1]) * (p2[0] - p1[0])) / d
    if 0 <= t <= 1 and 0 <= u <= 1:
        return (p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1]))
    return None


def cross_section(ref, i, lines, half=XSEC_HALF_FT, tol=XSEC_TOL_DEG):
    """Signed offset (ft, + = right of travel) where `lines` cross the perpendicular to the
    polyline `ref` at vertex i. Only road pieces within tol degrees of ref's heading count,
    so side roads are ignored; the crossing nearest ref wins. None if nothing crosses."""
    p = ref[i]
    h = heading(ref[max(0, i - 4)], ref[min(len(ref) - 1, i + 4)])
    nx, ny = math.cos(h), -math.sin(h)
    a, b = (p[0] - nx * half, p[1] - ny * half), (p[0] + nx * half, p[1] + ny * half)
    best = None
    for ln in lines:
        for u, v in zip(ln, ln[1:]):
            if abs((math.degrees(heading(u, v) - h) + 90) % 180 - 90) > tol:
                continue
            x = seg_intersection(a, b, u, v)
            if x is None:
                continue
            off = (x[0] - p[0]) * nx + (x[1] - p[1]) * ny
            if best is None or abs(off) < abs(best):
                best = off
    return best, (nx, ny)


def read_courses(path=COURSES_CSV):
    rows = list(csv.DictReader(open(path)))
    pick = lambda src: [(az_deg(r['bearing']), float(r['distance_ft'])) for r in rows if r['source'] == src]
    return pick('pm22'), pick('grant')

# ---------------------------------------------------------------- fetching (cached)


def fetch(cache, name, url, data=None, refresh=False, timeout=300):
    """Download via curl (macOS's Python 3.9 can't do TLS with some of these servers)."""
    path = cache / name
    if refresh or not path.exists():
        cmd = ['curl', '-sSf', '--max-time', str(timeout), '-A', 'atlas-research-client', '-o', str(path), url]
        if data is not None:
            cmd[1:1] = ['--data-binary', data]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            path.unlink(missing_ok=True)
            raise OSError(r.stderr.strip() or f'curl exit {r.returncode}')
    return json.loads(path.read_text())


def arcgis_query(cache, name, url, refresh, **params):
    q = {'outSR': '4326', 'f': 'geojson', **params}
    return fetch(cache, name, url + '/query?' + urllib.parse.urlencode(q), refresh=refresh)


def fetch_all(cache, refresh):
    W, S, E, N = VIEW_BBOX
    env = dict(geometry=f'{W},{S},{E},{N}', geometryType='esriGeometryEnvelope', inSR='4326',
               spatialRel='esriSpatialRelIntersects')
    plss = 'https://gis.blm.gov/arcgis/rest/services/Cadastral/BLM_Natl_PLSS_CadNSDI/MapServer'
    where = ' or '.join(f"FRSTDIVID like '{t}SN%'" for t in TOWNSHIP_IDS)
    d = {
        'roads': fetch(cache, 'county_roads.geojson', ROADS_URL, refresh=refresh),
        'sections': arcgis_query(cache, 'plss_sections.geojson', plss + '/2', refresh, where=where,
                                 outFields='FRSTDIVID,FRSTDIVNO'),
        'qq': arcgis_query(cache, 'plss_qq.geojson', plss + '/3', refresh,
                           where=f"FRSTDIVID in ('{TOWNSHIP_IDS[0]}SN130','{TOWNSHIP_IDS[0]}SN140')",
                           outFields='FRSTDIVNO,SECDIVNO'),
        'tiger': arcgis_query(cache, 'tiger.geojson',
                              'https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Transportation/MapServer/8',
                              refresh, outFields='NAME,MTFCC', **env),
        'usgs': arcgis_query(cache, 'usgs_ntd.geojson',
                             'https://carto.nationalmap.gov/arcgis/rest/services/transportation/MapServer/32',
                             refresh, outFields='*', **env),
    }
    # Public Overpass servers are flaky (502s, timeouts); try each in turn.
    query = urllib.parse.urlencode({'data': f'[out:json][timeout:60];way[highway]({S},{W},{N},{E});out geom;'})
    for i, server in enumerate(OVERPASS_SERVERS):
        try:
            d['osm'] = fetch(cache, 'osm.json', server, data=query, refresh=refresh, timeout=90)
            break
        except OSError as e:
            print(f'Overpass {server} failed ({e})' + ('; trying the next server' if i + 1 < len(OVERPASS_SERVERS) else ''))
    else:
        raise RuntimeError('every Overpass server failed; re-run later')
    ov = cache / 'overture.geojson'
    if refresh or not ov.exists():
        import duckdb
        con = duckdb.connect()
        con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial; SET s3_region='us-west-2';")
        rows = con.execute(f"""SELECT names.primary, class, ST_AsGeoJSON(geometry)
            FROM read_parquet('s3://overturemaps-us-west-2/release/{OVERTURE_RELEASE}/theme=transportation/type=segment/*', hive_partitioning=1)
            WHERE subtype='road' AND bbox.xmax > {W} AND bbox.xmin < {E} AND bbox.ymax > {S} AND bbox.ymin < {N}""").fetchall()
        ov.write_text(json.dumps({'type': 'FeatureCollection', 'features': [
            {'type': 'Feature', 'properties': {'name': r[0], 'class': r[1]}, 'geometry': json.loads(r[2])} for r in rows]}))
    d['overture'] = json.loads(ov.read_text())
    pc = cache / 'parcels.geojson'
    if refresh or not pc.exists():
        clip = f"""import json
fc = json.load(open({PARCELS_ON_BOX!r}))
def hit(g):
    rings = g['coordinates'] if g['type'] == 'Polygon' else [r for p in g['coordinates'] for r in p]
    return any({W} <= x <= {E} and {S} <= y <= {N} for r in rings for x, y in r)
print(json.dumps({{'type': 'FeatureCollection', 'features': [f for f in fc['features'] if f.get('geometry') and hit(f['geometry'])]}}))
"""
        out = subprocess.run(['ssh', '-i', str(Path(BOX_KEY).expanduser()), BOX, 'sudo python3 -'],
                             input=clip, capture_output=True, text=True, check=True).stdout
        pc.write_text(out)
    d['parcels'] = json.loads(pc.read_text())
    return d


def lines_lonlat(fc):
    out = []
    for f in fc['features']:
        g = f.get('geometry') or {}
        if g.get('type') == 'LineString':
            out.append(g['coordinates'])
        elif g.get('type') == 'MultiLineString':
            out += g['coordinates']
    return out


def osm_lonlat(osm):
    return [[(p['lon'], p['lat']) for p in e['geometry']] for e in osm['elements']
            if e.get('type') == 'way' and 'geometry' in e
            and e['tags'].get('highway') not in ('footway', 'path', 'steps', 'cycleway')]

# ---------------------------------------------------------------- the analysis


def outer_ring(f):
    g = f['geometry']
    return g['coordinates'][0] if g['type'] == 'Polygon' else g['coordinates'][0][0]


def plss_frame(qq):
    """The 1/4 corner common to Secs 13 & 14 (shared vertex of Sec 13 SWNW/NWSW and Sec 14 SENE/NESE)
    and the BLM azimuth of Sec 13's north line."""
    Q = {(f['properties']['FRSTDIVNO'], f['properties']['SECDIVNO']): outer_ring(f) for f in qq['features']}
    key = lambda c: (round(c[0], 7), round(c[1], 7))
    shared = ({key(c) for c in Q[(SEC, 'SWNW')]} & {key(c) for c in Q[(SEC, 'NWSW')]}
              & {key(c) for c in Q['14', 'SENE']})
    assert len(shared) == 1, f'expected one shared 1/4-corner vertex, got {shared}'
    corner = shared.pop()
    L = Local(*corner)
    def extreme(k, f):
        return max((L.fwd(*c) for c in Q[k]), key=f)
    ne = extreme((SEC, 'NENE'), lambda p: p[0] + p[1])
    nw = extreme((SEC, 'NWNW'), lambda p: -p[0] + p[1])
    return corner, L, math.degrees(heading(ne, nw)) % 360


def centerline(pm22, grant, rot):
    """pm22 walked backward from the course-26 end point (so it ends there), grant walked forward."""
    end26 = STATION26_END_OFFSET_FT
    pm = walk(end26, pm22, rot, backward=True)
    gr = walk(end26, grant, rot)
    labels = [f'Station {i + 1}' for i in range(len(pm22))] + ['Course 26 end (tie point)'] + [f'G{i + 1}' for i in range(len(grant))]
    return pm + gr[1:], labels


def road_graph(roads_fc, L, names):
    key = lambda c: (round(c[0], 5), round(c[1], 5))
    G = {}
    for f in roads_fc['features']:
        if f['properties'].get('name') not in names:
            continue
        for ln in lines_lonlat({'features': [f]}):
            for a, b in zip(ln, ln[1:]):
                ka, kb = key(a), key(b)
                w = math.hypot(*(u - v for u, v in zip(L.fwd(*ka), L.fwd(*kb))))
                G.setdefault(ka, []).append((kb, w))
                G.setdefault(kb, []).append((ka, w))
    return G, key


def shortest_path(G, s, t):
    D, P, h = {s: 0.0}, {}, [(0.0, s)]
    while h:
        d, u = heapq.heappop(h)
        if u == t:
            break
        if d > D[u]:
            continue
        for v, w in G[u]:
            if d + w < D.get(v, 1e18):
                D[v], P[v] = d + w, u
                heapq.heappush(h, (d + w, v))
    path = [t]
    while path[-1] != s:
        path.append(P[path[-1]])
    return path[::-1], D[t]


def haul_route(roads_fc, L):
    G, key = road_graph(roads_fc, L, HAUL_ROADS)
    junction = key(HAUL_JUNCTION)
    seren = [key(c) for f in roads_fc['features'] if f['properties'].get('name') == 'SERENDIPITY RD'
             for ln in lines_lonlat({'features': [f]}) for c in (ln[0], ln[-1])]
    start = max(seren, key=lambda k: math.hypot(*(u - v for u, v in zip(L.fwd(*k), L.fwd(*junction)))))
    path, length = shortest_path(G, start, key(HAUL_END))
    return path, length


def overlap_runs(route_ft, line_ft, tol, step=5, gap=OVERLAP_GAP_FT):
    samp = densify(route_ft, step)
    lines = [line_ft]
    near = [nearest_on_lines(p, lines)[0] <= tol for p in samp]
    runs, cur, last = [], None, None
    for i, ok in enumerate(near):
        if not ok:
            continue
        if cur is None or (i - last) * step > gap:
            if cur is not None:
                runs.append((cur, last))
            cur = i
        last = i
    if cur is not None:
        runs.append((cur, last))
    return samp, runs


def analyze(data):
    pm22, grant = read_courses()
    corner, L, blm_az = plss_frame(data['qq'])
    rot = blm_az - az_deg(RECORD_N_LINE_SEC13)
    raw, labels = centerline(pm22, grant, rot)
    county = [[L.fwd(*c[:2]) for c in ln] for ln in lines_lonlat(data['roads'])]
    fit_road = [[L.fwd(*c[:2]) for c in ln] for f in data['roads']['features']
                if f['properties'].get('name') == FIT_ROAD for ln in lines_lonlat({'features': [f]})]
    theta, tx, ty = icp_fit(densify(raw[:FIT_COURSES + 1], 10), fit_road)
    fitted = apply_rigid(raw, theta, tx, ty)
    path_ll, length = haul_route(data['roads'], L)
    route = [L.fwd(*k) for k in path_ll]
    samp, runs = overlap_runs(route, fitted, OVERLAP_TOL_FT)
    (a, b), = runs
    print(f'1/4 corner {corner}; BLM N line of Sec {SEC} az {blm_az:.4f}; rotation {rot:+.4f} deg')
    print(f'fit of courses 1-{FIT_COURSES} to {FIT_ROAD}: rotate {math.degrees(theta):+.3f} deg, shift E {tx:+.1f} N {ty:+.1f} ft')
    for name, line in (('unfitted', raw), ('fitted', fitted)):
        d = sorted(nearest_on_lines(p, fit_road)[0] for p in densify(line, 10))
        print(f'  {name} centerline vs {FIT_ROAD}: median {d[len(d) // 2]:.0f} ft, p90 {d[int(.9 * len(d))]:.0f}, max {d[-1]:.0f}')
    print(f'haul route {length / 5280:.2f} mi; along the easement (within {OVERLAP_TOL_FT} ft) for {(b - a) * 5} ft')

    # cross-sections from the county route over the easement against the other four sources
    others = {
        'OpenStreetMap': osm_lonlat(data['osm']),
        'Overture': lines_lonlat(data['overture']),
        'Census TIGER/Line': lines_lonlat(data['tiger']),
        'USGS National Transportation': lines_lonlat(data['usgs']),
    }
    others = {n: [[L.fwd(*c[:2]) for c in ln] for ln in v] for n, v in others.items()}
    ref = samp[a:b + 1]
    rows = []
    for i in range(0, len(ref), 2):
        offs, nrm = {}, None
        for n, lns in others.items():
            offs[n], nrm = cross_section(ref, i, lns)
        if any(v is None for v in offs.values()):
            continue
        vals = [0.0, *offs.values()]
        rows.append((i, max(vals) - min(vals), offs, nrm))
    spreads = sorted(r[1] for r in rows)
    print(f'cross-sections every 10 ft: spread median {spreads[len(spreads) // 2]:.0f} ft, max {spreads[-1]:.0f} ft')
    picked = []
    for r in sorted(rows, key=lambda r: -r[1]):
        if len(picked) < XSEC_SITES and all(abs(r[0] - s[0]) * 5 >= XSEC_MIN_SEP_FT for s in picked):
            picked.append(r)
    sites = []
    for k, (i, spread, offs, (nx, ny)) in enumerate(sorted(picked, key=lambda r: r[0])):
        p = ref[i]
        vals = {'County': 0.0, **offs}
        lo, hi = min(vals.values()), max(vals.values())
        ll = lambda off: L.inv(p[0] + nx * off, p[1] + ny * off)[::-1]
        sites.append({'id': 'ABCDEF'[k], 'spread': round(spread), 'll': ll(0), 'a': ll(lo - 12), 'b': ll(hi + 12),
                      'pts': {n: ll(v) for n, v in vals.items()}})
        print(f'  {"ABCDEF"[k]}: {spread:.0f} ft  ' + '  '.join(f'{n.split()[0]} {v:+.0f}' for n, v in vals.items()))
    return {'L': L, 'labels': labels, 'fitted': fitted, 'route_ll': path_ll, 'route_len': length,
            'overlap': (samp[a], samp[b]), 'overlap_ft': (b - a) * 5, 'sites': sites,
            'notes': {'rot': rot, 'theta': math.degrees(theta), 'tx': tx, 'ty': ty}}

# ---------------------------------------------------------------- outputs


def write_geojson(res, out):
    from shapely.geometry import LineString
    L = res['L']
    to_ll = lambda cs: [list(L.inv(*c)) for c in cs]
    strip = LineString(res['fitted']).buffer(EASEMENT_HALF_WIDTH_FT, cap_style='flat', join_style='mitre', mitre_limit=3)
    inside = LineString([L.fwd(*k) for k in res['route_ll']]).intersection(strip).length
    res['inside_ft'] = round(inside)
    print(f'route strictly inside the {2 * EASEMENT_HALF_WIDTH_FT}-ft strip: {inside:.0f} ft; strip {strip.area / 43560:.2f} acres')
    n = res['notes']
    easement = {'type': 'FeatureCollection', 'features': [{'type': 'Feature', 'properties': {
        'name': '60 ft easement (Bk 986 OR p 236)', 'width_ft': 2 * EASEMENT_HALF_WIDTH_FT,
        'centerline_length_ft': round(LineString(res['fitted']).length),
        'restriction': 'No commercial use, incl. transport of logs, timber or forest products (commercial easement expired 1970-08-15)',
        'note': ('Approximate, not a survey. Parcel Map No. 22 courses 1-26 and the 1968 grant\'s 8 calls, anchored 33 ft S of the '
                 f'BLM CadNSDI 1/4 corner common to Secs 13/14 T3S R2E HM, rotated {n["rot"]:+.2f} deg to BLM bearings, then '
                 f'rotated {n["theta"]:+.2f} deg and shifted {math.hypot(n["tx"], n["ty"]):.0f} ft so courses 1-8 fit the county road.')},
        'geometry': {'type': 'Polygon', 'coordinates': [to_ll(strip.exterior.coords)]}}]}
    haul = {'type': 'FeatureCollection', 'features': [{'type': 'Feature', 'properties': {
        'name': 'Proposed haul route, THP 1-26-00170-HUM', 'length_mi': round(res['route_len'] / 5280, 2),
        'note': ('Reconstructed from THP Sec. IV p. 120 (Traffic Assessment Area): Serendipity Rd -> Lower Samuels Ranch Rd -> '
                 'Thomas Rd -> Salmon Creek Rd -> US 101 Exit 653, shortest path over the atlas roads layer.')},
        'geometry': {'type': 'LineString', 'coordinates': [list(k) for k in res['route_ll']]}}]}
    (out / 'easement_986_236.geojson').write_text(json.dumps(easement))
    (out / 'thp_haul_route.geojson').write_text(json.dumps(haul))
    return easement, haul


def parcel_layers(parcels, easement):
    from shapely.geometry import shape
    strip = shape(easement['features'][0]['geometry'])
    labels = []
    for f in parcels['features']:
        pid = str(f['properties'].get('pid') or '')
        apn = f'{pid[:3]}-{pid[3:6]}-{pid[6:9]}' if len(pid) >= 9 else pid
        f['properties'] = {'apn': apn}
        g = shape(f['geometry']).buffer(0)
        if g.intersection(strip).area > 1e-9:
            p = g.representative_point()
            labels.append({'apn': apn, 'll': [p.y, p.x]})
    return parcels, labels


COLORS = {'County': '#ff2d2d', 'OpenStreetMap': '#00e5ff', 'Overture': '#7cff4f',
          'Census TIGER/Line': '#ff9f1c', 'USGS National Transportation': '#c77dff'}

CSS = """html,body{margin:0;background:#1b1b1b;font-family:-apple-system,Helvetica,sans-serif}
.lab{font:700 14px/1.2 sans-serif;color:#fff;text-shadow:0 0 3px #000,0 0 6px #000;white-space:nowrap}
.sta{font:700 11px sans-serif;color:#fff;text-shadow:0 0 2px #000,0 0 4px #000;white-space:nowrap}
.sec{font:700 22px sans-serif;color:#ffffffcc;text-shadow:0 0 4px #000}
.apn{font:600 12px sans-serif;color:#ffb3ff;text-shadow:0 0 3px #000,0 0 5px #000;white-space:nowrap}
.tid{font:800 15px sans-serif;color:#000;background:#fff;border:2px solid #000;border-radius:50%;width:22px;height:22px;line-height:22px;text-align:center}
.ft{font:700 13px sans-serif;color:#fff;background:#000c;padding:1px 5px;border-radius:3px;white-space:nowrap}
.box{position:absolute;z-index:1000;background:#fffffff2;padding:12px 14px;font-size:13px;line-height:1.45;border-radius:6px;box-shadow:0 1px 6px #0006;max-width:600px}
.sw{display:inline-block;width:26px;height:10px;margin-right:6px;vertical-align:middle}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin:0 3px 0 6px;border:1px solid #000;vertical-align:middle}"""

# Shared page script: NAIP exported for each map's exact extent, plus the common layers.
JS_COMMON = """
function naip(map){const b=map.getPixelBounds(),z=map.getZoom(),R=6378137,res=2*Math.PI*R/256/Math.pow(2,z),o=Math.PI*R;
 const x0=b.min.x*res-o,x1=b.max.x*res-o,y1=o-b.min.y*res,y0=o-b.max.y*res,w=b.max.x-b.min.x,h=b.max.y-b.min.y;
 L.imageOverlay(NAIP+'?bbox='+[x0,y0,x1,y1].join(',')+'&bboxSR=3857&imageSR=3857&size='+(2*w)+','+(2*h)+'&format=jpg&compressionQuality=90&f=image',map.getBounds()).addTo(map);}
function base(map,small){
 L.geoJSON(PAR,{style:{color:'#ff8cff',weight:small?2:1.5,fill:false,opacity:.9}}).addTo(map);
 L.geoJSON(EAS,{style:{color:'#ffd400',weight:2,fillColor:'#ffd400',fillOpacity:small?.18:.3}}).addTo(map);
 L.geoJSON(HAUL,{style:{color:'#ff2d2d',weight:small?4:3,opacity:.95}}).addTo(map);
 STS.forEach(p=>{L.circleMarker([p.ll[1],p.ll[0]],{radius:3.5,color:'#000',weight:1,fillColor:p.g?'#ffd400':'#fff',fillOpacity:1}).addTo(map);
  L.marker([p.ll[1],p.ll[0]],{icon:L.divIcon({className:'sta',html:p.t,iconSize:null,iconAnchor:[-4,13]})}).addTo(map);});}
function overview(map){naip(map);
 L.geoJSON(SECS,{style:{color:'#fff',weight:1.5,dashArray:'6 6',fill:false,opacity:.7}}).addTo(map);
 SECS.features.forEach(f=>{const c=L.geoJSON(f).getBounds().getCenter();L.marker(c,{icon:L.divIcon({className:'sec',html:'Sec '+f.properties.FRSTDIVNO,iconSize:null})}).addTo(map)});
 base(map,false);
 APNS.forEach(a=>L.marker(a.ll,{icon:L.divIcon({className:'apn',html:a.apn,iconSize:null})}).addTo(map));
 L.marker([40.2085,-123.9215],{icon:L.divIcon({className:'lab',html:'↑ haul route from Lower Samuels Ranch Rd,<br>Serendipity Rd and the THP',iconSize:null})}).addTo(map);
 L.marker([40.2085,-123.8960],{icon:L.divIcon({className:'lab',html:'→ Thomas Rd continues to<br>Salmon Creek Rd and US 101',iconSize:null})}).addTo(map);}
"""

FOOT = ('APPROXIMATE, NOT A SURVEY. Easement centerline plotted from Parcel Map No. 22 (Bk 1 PM pp 34–37, courses 1–26) and '
        'the 1968 Grant of Easement (8 calls), anchored on the BLM ¼ corner common to Secs 13 &amp; 14, T3S R2E HM, and fitted '
        "to the county road along courses 1–8 per the map's note. {roads}Parcel lines are assessor GIS, also approximate. "
        f'Imagery: {NAIP_NOTE}.')


def page(title, body, data, script):
    head = (f'<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>'
            f'<link rel="stylesheet" href="{LEAFLET}/leaflet.min.css"><script src="{LEAFLET}/leaflet.min.js"></script>'
            f'<style>{CSS}</style></head><body>')
    consts = ''.join(f'const {k}={json.dumps(v)};\n' for k, v in data.items())
    return f'{head}{body}<script>\n{consts}{JS_COMMON}{script}</script></body></html>'


def write_pages(res, data, easement, haul, out):
    parcels, apns = parcel_layers(data['parcels'], easement)
    secs = {'type': 'FeatureCollection', 'features': [f for f in data['sections']['features']
            if f['properties']['FRSTDIVID'].startswith(TOWNSHIP_IDS[0]) and f['properties']['FRSTDIVNO'] in ('11', '12', '13', '14', '23', '24')]}
    L = res['L']
    sts = [{'ll': L.inv(*p), 't': lab.replace('Station ', '').replace('Course 26 end (tie point)', ''), 'g': lab.startswith('G')}
           for p, lab in zip(res['fitted'], res['labels']) if lab != 'Course 26 end (tie point)']
    common = {'NAIP': NAIP_EXPORT, 'PAR': parcels, 'APNS': apns, 'EAS': easement, 'HAUL': haul, 'STS': sts, 'SECS': secs}
    s_ll, e_ll = (L.inv(*res['overlap'][0]), L.inv(*res['overlap'][1]))
    mi = res['overlap_ft'] / 5280

    # 1. haul route vs easement, with stations and parcels
    body = (f'<div id="map" style="width:2000px;height:1250px"></div>'
            f'<div class="box" style="top:12px;left:12px"><b style="font-size:15px">Proposed THP haul route vs. 60-ft easement (Bk 986 OR p 236)</b><br>'
            '<span class="sw" style="background:#ffd400aa;border:2px solid #ffd400"></span>60-ft easement (deed centerline ± 30 ft)<br>'
            '<span class="sw" style="background:#ff2d2d"></span>Proposed haul route, THP 1-26-00170-HUM<br>'
            '<span class="sw" style="background:none;border-top:2px dashed #fff;height:0"></span>Section lines (BLM PLSS)<br>'
            '<span class="sw" style="background:none;border-top:2px solid #ff8cff;height:0"></span>Parcels (Humboldt Co. assessor GIS); APN labeled where the easement crosses<br>'
            '<span class="dot" style="background:#fff;margin-left:0"></span> Stations 1–26 (Parcel Map No. 22) and ends of grant calls G1–G8<br>'
            f'Haul route runs along the easement for ~{res["overlap_ft"]:,} ft (~{mi:.2f} mi);<br>'
            f'{res["inside_ft"]:,} ft of it lies strictly inside the 60-ft strip.</div>'
            f'<div class="box" style="bottom:12px;left:12px;font-size:11px;max-width:660px">'
            + FOOT.format(roads='Haul route reconstructed from THP Sec. IV p. 120 narrative over county road centerlines. ') + '</div>')
    script = (f'const map=L.map("map",{{zoomControl:false,attributionControl:false}}).setView({list(MAP_CENTER)},{MAP_ZOOM});overview(map);\n'
              'function mark(ll,txt,anchor){L.circleMarker([ll[1],ll[0]],{radius:9,color:"#fff",weight:3,fillColor:"#00e5ff",fillOpacity:1}).addTo(map);'
              'L.marker([ll[1],ll[0]],{icon:L.divIcon({className:"lab",html:txt,iconSize:null,iconAnchor:anchor})}).addTo(map);}\n'
              f'mark({list(s_ll)},"▶ Overlap starts (west end of easement)<br>&nbsp;&nbsp;&nbsp;{s_ll[1]:.5f}, {s_ll[0]:.5f}",[230,-16]);\n'
              f'mark({list(e_ll)},"Overlap ends (Station 1) ◀<br>{e_ll[1]:.5f}, {e_ll[0]:.5f}",[210,-14]);\n')
    (out / 'haul_route_vs_easement.html').write_text(page('Haul route vs easement', body, common, script))

    # 2. road datasets: overview + close-up cross-sections A-F
    legend = ''.join(f'<span class="dot" style="background:{c}"></span>{n} ' for n, c in COLORS.items())
    insets = ''.join(f'<div class="inset"><div id="m{s["id"]}" class="im"></div><div class="cap"><b>{s["id"]}</b> — datasets span '
                     f'<b>{s["spread"]} ft</b> across the road</div></div>' for s in res['sites'])
    mx = max(s['spread'] for s in res['sites'])
    body = ('<style>#wrap{display:flex;width:2400px;height:1400px}#map{width:1480px;height:1400px;position:relative}'
            '#side{width:920px;display:grid;grid-template-columns:1fr 1fr;grid-template-rows:repeat(3,1fr);gap:8px;padding:8px;box-sizing:border-box}'
            '.inset{position:relative;background:#000;border-radius:4px;overflow:hidden}.im{position:absolute;inset:0}'
            '.cap{position:absolute;z-index:1000;left:0;right:0;bottom:0;background:#000b;color:#fff;font-size:14px;padding:5px 9px}</style>'
            '<div id="wrap"><div id="map"><div class="box" style="top:12px;left:12px"><b style="font-size:16px">Thomas Rd vs. the 60-ft easement (Bk 986 OR p 236)</b><br>'
            '<span class="sw" style="background:#ffd400aa;border:2px solid #ffd400"></span>60-ft easement (deed centerline ± 30 ft), stations 1–26, grant calls G1–G8<br>'
            '<span class="sw" style="background:#ff2d2d"></span>Humboldt County road centerline (basis of the proposed haul route)<br>'
            '<span class="sw" style="background:none;border-top:2px solid #ff8cff;height:0"></span>Parcels (Humboldt Co. assessor GIS); APN labeled where the easement crosses<br>'
            '<b>A–F:</b> the six places where other road datasets disagree most with the county line<br>'
            f'(close-ups at right; white bar = cross-section, dots = where each dataset crosses it):<br>{legend}<br>'
            f'Checked across the road every 10 ft, the five datasets never differ by more than <b>{mx} ft</b>.</div>'
            '<div class="box" style="bottom:12px;left:12px;font-size:11px;max-width:900px">'
            + FOOT.format(roads=f'Roads: Humboldt Co.; OpenStreetMap contributors; Overture Maps {OVERTURE_RELEASE[:-2]}; US Census TIGER/Line; '
                          'USGS National Transportation Dataset. OSM, Overture and TIGER share lineage and coincide (nested rings). ')
            + f'</div></div><div id="side">{insets}</div></div>')
    script = (f'const map=L.map("map",{{zoomControl:false,attributionControl:false}}).setView([40.2040,-123.9070],{MAP_ZOOM});overview(map);\n'
              'SITES.forEach(s=>L.marker(s.ll,{icon:L.divIcon({className:"tid",html:s.id,iconSize:[26,26],iconAnchor:[13,36]})}).addTo(map));\n'
              'SITES.forEach(s=>{const m=L.map("m"+s.id,{zoomControl:false,attributionControl:false}).setView(s.ll,19);naip(m);base(m,true);\n'
              ' L.polyline([s.a,s.b],{color:"#000",weight:7,opacity:.6}).addTo(m);L.polyline([s.a,s.b],{color:"#fff",weight:3}).addTo(m);\n'
              ' const used=[];Object.entries(s.pts).forEach(([k,p])=>{const pt=m.latLngToContainerPoint(p);const n=used.filter(q=>q.distanceTo(pt)<14).length;used.push(pt);\n'
              '  L.circleMarker(p,{radius:13-3*n,color:"#000",weight:1.5,fillColor:COL[k],fillOpacity:1}).addTo(m);});\n'
              ' L.marker(s.b,{icon:L.divIcon({className:"ft",html:s.spread+" ft",iconSize:null,iconAnchor:[-8,10]})}).addTo(m);\n'
              ' L.control.scale({imperial:true,metric:false,position:"topright"}).addTo(m);});\n')
    (out / 'road_datasets_cross_sections.html').write_text(
        page('Road datasets vs easement', body, {**common, 'SITES': res['sites'], 'COL': COLORS}, script))


def screenshot(out, name, size):
    subprocess.run([CHROME, '--headless=new', '--disable-gpu', '--hide-scrollbars', f'--window-size={size[0]},{size[1]}',
                    '--virtual-time-budget=40000', f'--screenshot={out / (name + ".png")}', (out / (name + '.html')).as_uri()],
                   capture_output=True, check=True)
    print(f'wrote {out / (name + ".png")}')


ATLAS_STEPS = """
To load the two GeoJSONs into the scvfd atlas (layers easement_986_236 and thp_haul_route are already
configured in configuration/scvfd.geojson as s3_geojson inlets, admin-only, private_webmap only):

  aws s3 cp {out}/easement_986_236.geojson s3://scs-atlas-private-prod/scvfd/imports/easement_986_236.geojson --profile atlas
  aws s3 cp {out}/thp_haul_route.geojson   s3://scs-atlas-private-prod/scvfd/imports/thp_haul_route.geojson --profile atlas

First load: run each inlet, then apply its delta.
  curl 'https://fireatlas.org:9000/refresh?swale=scvfd&asset=easement_986_236_import'
  curl 'https://fireatlas.org:9000/refresh?swale=scvfd&asset=thp_haul_route_import'
  curl 'https://fireatlas.org:9000/refresh_layer?swale=scvfd&layer=easement_986_236&mode=update&cascade=false'
  curl 'https://fireatlas.org:9000/refresh_layer?swale=scvfd&layer=thp_haul_route&mode=update&cascade=false'
  curl 'https://fireatlas.org:9000/refresh?swale=scvfd&asset=private_webmap'

Replacing geometry later: re-running an s3 inlet APPENDS a second copy (#190). Rebuild instead:
  curl 'https://fireatlas.org:9000/refresh_layer?swale=scvfd&layer=easement_986_236&mode=rebuild&cascade=false'
  (same for thp_haul_route), then re-materialize private_webmap. Check /refresh-layer-status between calls.
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('out', type=Path)
    ap.add_argument('--refresh', action='store_true', help='re-download everything in OUT/cache')
    args = ap.parse_args()
    cache = args.out / 'cache'
    cache.mkdir(parents=True, exist_ok=True)
    data = fetch_all(cache, args.refresh)
    res = analyze(data)
    easement, haul = write_geojson(res, args.out)
    write_pages(res, data, easement, haul, args.out)
    screenshot(args.out, 'haul_route_vs_easement', (2000, 1250))
    screenshot(args.out, 'road_datasets_cross_sections', (2400, 1400))
    print(ATLAS_STEPS.format(out=args.out))


if __name__ == '__main__':
    sys.exit(main())
