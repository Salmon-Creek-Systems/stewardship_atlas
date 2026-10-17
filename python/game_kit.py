"""
Local game kit: the atlas side of a Wildfire Resilience board game.

Adapts the DIY kit at https://prototypingresilience.com/diy-game-kit (game
design: Tom Maiorana, UC Davis) to one atlas. That kit ships rules, spinners,
vehicle and generic chance cards, and leaves the board and the place-specific
cards to each community. This module builds those two from atlas layers:

  - board overlays: the 1-12 fire-location grid, step dots along the roads,
    exits (roads crossing the atlas edge), numbered places, safe places and
    helispots;
  - local chance cards filled in from real road names, hazards, exits and
    places;
  - a readiness check: what the atlas is missing before the game can be
    built. A missing ingredient (no safe places, no exits, unnamed roads) is
    a gap in the atlas worth fixing whether or not anyone plays.

Pure Python on GeoJSON dicts (no shapely/QGIS) so it tests locally;
outlets_game_kit.py renders the board PDF with QGIS and writes the pages.
"""

import html
import math
import random
import re

KIT_URL = "https://prototypingresilience.com/diy-game-kit"
KIT_FILES = [
    ("Instructions", "https://prototypingresilience.com/s/2-Instructions_SPECIFIC_12.pdf"),
    ("Player setup sheet", "https://prototypingresilience.com/s/1-Setup-WIldfire-Resilience-Setup-SLT_36.pdf"),
    ("Spinners", "https://prototypingresilience.com/s/Spinners_6Up.pdf"),
    ("Fire pieces", "https://prototypingresilience.com/s/Fire-Cards.pdf"),
    ("Transportation cards", "https://prototypingresilience.com/s/Template_001-Transportation.pdf"),
    ("Generic chance cards", "https://prototypingresilience.com/s/Cards_Chance_Generic-COMBO-jkj7.pdf"),
]

GRID_COLS, GRID_ROWS = 4, 3          # 12 cells, matching the Fire Location spinner
STEPS_ACROSS = 30                    # default step spacing: long side of the atlas / 30
EDGE_TOLERANCE_M = 10                # a vertex this close to the atlas edge counts as on it
EXIT_MERGE_M = 300                   # crossings closer than this are one exit
MIN_EXITS = 2
MAX_EXITS = 8                        # exits on the board, longest roads first
MIN_PLACES = 6                       # enough numbered places for the "go to ⑰" cards

# Road "names" that aren't: a bare street type or a number.
_STREET_TYPES = {"DR", "RD", "LN", "ST", "WAY", "CT", "AVE", "PL", "TRL", "HWY", "CIR", "BLVD", "LOOP"}
_KEEP_UPPER = {"US", "CA", "SR", "CR", "FS", "BLM", "NF"}


# ── geometry helpers ────────────────────────────────────────────────────────

def meters_per_degree(bbox):
    """(metres per degree of longitude, of latitude) at the bbox's mid-latitude."""
    lat0 = math.radians((bbox["north"] + bbox["south"]) / 2)
    return 111320 * math.cos(lat0), 110540


def bbox_size_m(bbox):
    kx, ky = meters_per_degree(bbox)
    return (bbox["east"] - bbox["west"]) * kx, (bbox["north"] - bbox["south"]) * ky


def default_step_m(bbox):
    """Step spacing so the long side of the atlas is ~STEPS_ACROSS steps, rounded to 50 m."""
    return max(50, round(max(bbox_size_m(bbox)) / STEPS_ACROSS / 50) * 50)


def _dist_m(a, b, k):
    return math.hypot((a[0] - b[0]) * k[0], (a[1] - b[1]) * k[1])


def _edge_dist_m(p, bbox, k):
    """Distance from p to the nearest atlas edge; negative outside."""
    return min((p[0] - bbox["west"]) * k[0], (bbox["east"] - p[0]) * k[0],
               (p[1] - bbox["south"]) * k[1], (bbox["north"] - p[1]) * k[1])


def _inside(p, bbox, k):
    return _edge_dist_m(p, bbox, k) > EDGE_TOLERANCE_M


def _lines(feature):
    """Coordinate lists of a LineString/MultiLineString feature."""
    geom = feature.get("geometry") or {}
    if geom.get("type") == "LineString":
        return [geom["coordinates"]]
    if geom.get("type") == "MultiLineString":
        return geom["coordinates"]
    return []


def _point(feature):
    """A representative [lon, lat] for any feature (first vertex for lines/polygons)."""
    geom = feature.get("geometry") or {}
    c = geom.get("coordinates")
    t = geom.get("type")
    if not c:
        return None
    if t == "Point":
        return c[:2]
    if t in ("LineString", "MultiPoint"):
        return c[len(c) // 2][:2]
    if t in ("Polygon", "MultiLineString"):
        ring = c[0]
        return [sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)]
    if t == "MultiPolygon":
        ring = c[0][0]
        return [sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)]
    return None


def _edge_crossing(a, b, bbox):
    """Where segment a→b (a inside, b on/outside) meets the bbox edge, and which side."""
    best = None
    for side, axis, value in (("west", 0, bbox["west"]), ("east", 0, bbox["east"]),
                              ("south", 1, bbox["south"]), ("north", 1, bbox["north"])):
        d = b[axis] - a[axis]
        if d == 0:
            continue
        t = (value - a[axis]) / d
        if 0 <= t <= 1 and (best is None or t < best[0]):
            best = (t, side)
    if best is None:                         # b sits within tolerance of the edge
        return list(b[:2]), _nearest_side(b, bbox)
    t, side = best
    return [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])], side


def _nearest_side(p, bbox):
    return min((abs(p[0] - bbox["west"]), "west"), (abs(bbox["east"] - p[0]), "east"),
               (abs(p[1] - bbox["south"]), "south"), (abs(bbox["north"] - p[1]), "north"))[1]


def _fc(features):
    return {"type": "FeatureCollection", "features": features}


def _pt(lonlat, **props):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": list(lonlat)},
            "properties": props}


# ── names ───────────────────────────────────────────────────────────────────

def clean_name(name):
    """A display name, or None when the 'name' is blank, a number or a bare street type."""
    if not isinstance(name, str):
        return None
    n = " ".join(name.split())
    if len(n) < 3 or n.upper() in _STREET_TYPES or re.fullmatch(r"[\d\s.\-]+", n):
        return None
    if n.isupper():
        n = " ".join(w if w in _KEEP_UPPER or any(ch.isdigit() for ch in w) else w.capitalize()
                     for w in n.split())
    return n


def is_highway(name):
    return bool(name and re.search(r"\b(hwy|highway|route|sr|us|interstate|i-\d+)\b", name, re.I))


def feature_name(feature, keys=("name", "title", "label")):
    props = feature.get("properties") or {}
    for key in keys:
        n = clean_name(props.get(key))
        if n:
            return n
    return None


# ── board overlays ──────────────────────────────────────────────────────────

def grid_cells(bbox, cols=GRID_COLS, rows=GRID_ROWS):
    """The fire-location grid: polygons numbered 1..cols*rows, row by row from the north-west."""
    w, e, s, n = bbox["west"], bbox["east"], bbox["south"], bbox["north"]
    dx, dy = (e - w) / cols, (n - s) / rows
    cells = []
    for r in range(rows):
        for c in range(cols):
            x0, y1 = w + c * dx, n - r * dy
            x1, y0 = x0 + dx, y1 - dy
            cells.append({
                "type": "Feature",
                "geometry": {"type": "Polygon",
                             "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]},
                "properties": {"cell": r * cols + c + 1,
                               # label anchor just inside the cell's north-west corner
                               "label_x": x0 + dx * 0.06, "label_y": y1 - dy * 0.08},
            })
    return _fc(cells)


def grid_cell_of(lonlat, bbox, cols=GRID_COLS, rows=GRID_ROWS):
    """The grid cell number a point falls in (clamped to the edge cells)."""
    c = int((lonlat[0] - bbox["west"]) / (bbox["east"] - bbox["west"]) * cols)
    r = int((bbox["north"] - lonlat[1]) / (bbox["north"] - bbox["south"]) * rows)
    return min(max(r, 0), rows - 1) * cols + min(max(c, 0), cols - 1) + 1


def find_exits(road_features, bbox, merge_m=EXIT_MERGE_M):
    """Exits: points where a road leaves the atlas. Nearby crossings merge into one.

    Roads clipped exactly to the atlas edge count too (their last vertex sits on it).
    A road leaving the same side more than once is one exit. Returns
    [{'name', 'side', 'lonlat'}]; name is None for unnamed roads.
    """
    k = meters_per_degree(bbox)
    raw = []
    for f in road_features:
        name = feature_name(f)
        for line in _lines(f):
            for a, b in zip(line, line[1:]):
                ia, ib = _inside(a, bbox, k), _inside(b, bbox, k)
                if ia == ib:
                    continue
                inner, outer = (a, b) if ia else (b, a)
                lonlat, side = _edge_crossing(inner, outer, bbox)
                raw.append({"name": name, "side": side, "lonlat": lonlat})

    exits = []
    for x in raw:
        near = next((e for e in exits if _dist_m(e["lonlat"], x["lonlat"], k) < merge_m
                     or (x["name"] and e["name"] == x["name"] and e["side"] == x["side"])), None)
        if near is None:
            exits.append(dict(x))
        elif not near["name"] and x["name"]:
            near["name"] = x["name"]
    return exits


def board_exits(all_exits, named_lengths, max_exits=MAX_EXITS, exit_roads=None):
    """The exits that go on the board, lettered A, B, ... clockwise from the north.

    The atlas edge is a rectangle, so every back road that leaves it is a crossing.
    Keep the named exits on exit_roads if given, else highways first, then the
    longest roads, up to max_exits.
    """
    named = [e for e in all_exits if e["name"]]
    if exit_roads:
        wanted = {clean_name(n) for n in exit_roads}
        chosen = [e for e in named if e["name"] in wanted]
    else:
        chosen = sorted(named, key=lambda e: (not is_highway(e["name"]),
                                              -named_lengths.get(e["name"], 0.0)))[:max_exits]

    order = {"north": 0, "east": 1, "south": 2, "west": 3}
    chosen = sorted(chosen, key=lambda e: (order[e["side"]],
                    e["lonlat"][0] if e["side"] in ("north", "south") else -e["lonlat"][1]))
    return [dict(e, letter=_letter(i)) for i, e in enumerate(chosen)]


def _letter(i):
    return chr(ord("A") + i) if i < 26 else f"A{i - 25}"


def step_points(road_features, bbox, step_m):
    """Step dots every step_m metres along each road, inside the atlas only."""
    k = meters_per_degree(bbox)
    dots = []
    for f in road_features:
        for line in _lines(f):
            walked, next_at = 0.0, step_m      # distance along the line; first dot one step in
            for a, b in zip(line, line[1:]):
                seg = _dist_m(a, b, k)
                while seg > 0 and next_at <= walked + seg:
                    t = (next_at - walked) / seg
                    p = [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])]
                    if _inside(p, bbox, k):
                        dots.append(p)
                    next_at += step_m
                walked += seg
    return dots


def named_road_lengths(road_features, bbox):
    """{display name: metres inside the atlas}, and the metres with no usable name."""
    k = meters_per_degree(bbox)
    named, unnamed = {}, 0.0
    for f in road_features:
        name = feature_name(f)
        for line in _lines(f):
            for a, b in zip(line, line[1:]):
                mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]
                if not _inside(mid, bbox, k):
                    continue
                d = _dist_m(a, b, k)
                if name:
                    named[name] = named.get(name, 0.0) + d
                else:
                    unnamed += d
    return named, unnamed


def numbered_places(features, bbox):
    """Named features inside the atlas, numbered 1.. reading north-west to south-east."""
    k = meters_per_degree(bbox)
    places = []
    for f in features:
        p, name = _point(f), feature_name(f)
        if p and name and _inside(p, bbox, k):
            places.append({"name": name, "lonlat": p})
    places.sort(key=lambda x: (grid_cell_of(x["lonlat"], bbox), -x["lonlat"][1], x["lonlat"][0]))
    for i, x in enumerate(places, 1):
        x["number"] = i
    return places


def nearest_road(lonlat, road_features, bbox):
    """The display name of the named road nearest lonlat, or None."""
    k = meters_per_degree(bbox)
    best = None
    for f in road_features:
        name = feature_name(f)
        if not name:
            continue
        for line in _lines(f):
            for a, b in zip(line, line[1:]):
                d = _segment_dist_m(lonlat, a, b, k)
                if best is None or d < best[0]:
                    best = (d, name)
    return best[1] if best else None


def _segment_dist_m(p, a, b, k):
    """Distance from p to segment a-b, in local metres."""
    ax, ay = (a[0] - p[0]) * k[0], (a[1] - p[1]) * k[1]
    bx, by = (b[0] - p[0]) * k[0], (b[1] - p[1]) * k[1]
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    t = 0.0 if seg2 == 0 else min(1.0, max(0.0, -(ax * dx + ay * dy) / seg2))
    return math.hypot(ax + t * dx, ay + t * dy)


# ── the kit ─────────────────────────────────────────────────────────────────

def build_kit(atlas_name, bbox, role_layers, options=None):
    """Everything the outlet writes, from layer features grouped by game role.

    role_layers: {role: {layer_name: [features]}} for roles roads, landmarks,
    safe_places, helispots, hazards, water. A role with no layers configured is
    absent; a configured layer that is empty maps to [].
    """
    options = options or {}
    feats = {role: [f for fs in layers.values() for f in fs] for role, layers in role_layers.items()}
    roads = feats.get("roads", [])
    step_m = options.get("step_m") or default_step_m(bbox)

    named, unnamed_m = named_road_lengths(roads, bbox)
    all_exits = find_exits(roads, bbox)
    exits = board_exits(all_exits, named, options.get("max_exits", MAX_EXITS), options.get("exit_roads"))
    places = numbered_places(feats.get("landmarks", []), bbox)
    safe = numbered_places(feats.get("safe_places", []), bbox)
    helis = [{"name": feature_name(f) or "Helispot", "lonlat": _point(f)}
             for f in feats.get("helispots", []) if _point(f)]

    kit = {
        "atlas": atlas_name,
        "title": options.get("title") or atlas_name,
        "bbox": bbox,
        "step_m": step_m,
        "grid": grid_cells(bbox),
        "steps": step_points(roads, bbox, step_m),
        "exits": exits,
        "all_exit_count": len(all_exits),
        "places": places,
        "safe_places": safe,
        "helispots": helis,
        "named_roads": named,
        "unnamed_road_m": unnamed_m,
        "alert_system": options.get("alert_system"),
    }
    kit["readiness"] = readiness(kit, role_layers, options)
    kit["cards"] = local_cards(kit, feats, options)
    kit["evacuation_order"] = evacuation_order(kit)
    return kit


def overlay_layers(kit):
    """The board's generated layers as GeoJSON FeatureCollections, keyed by file stem."""
    g = kit["grid"]
    return {
        "game_grid": g,
        "game_grid_labels": _fc([_pt([c["properties"]["label_x"], c["properties"]["label_y"]],
                                     label=str(c["properties"]["cell"])) for c in g["features"]]),
        "game_steps": _fc([_pt(p) for p in kit["steps"]]),
        "game_exits": _fc([_pt(e["lonlat"], label=f"EXIT {e['letter']}", name=e["name"])
                           for e in kit["exits"]]),
        "game_places": _fc([_pt(p["lonlat"], label=str(p["number"]), name=p["name"])
                            for p in kit["places"]]),
        "game_safe": _fc([_pt(p["lonlat"], label=p["name"]) for p in kit["safe_places"]]),
        "game_helispots": _fc([_pt(h["lonlat"], label="H", name=h["name"]) for h in kit["helispots"]]),
    }


# ── readiness ───────────────────────────────────────────────────────────────

def _check(key, label, ok, detail, fix, warn_only=False):
    return {"key": key, "label": label,
            "status": "ok" if ok else ("warn" if warn_only else "missing"),
            "detail": detail, "fix": "" if ok else fix}


def readiness(kit, role_layers, options):
    """What the atlas is missing before a local board can be built.

    'missing' blocks a playable board; 'warn' makes it worse but playable.
    """
    checks = []

    def layer_note(role):
        layers = role_layers.get(role)
        if layers is None:
            return "no layer is assigned to this in the game_kit outlet"
        return ", ".join(f"{name} ({len(fs)})" for name, fs in layers.items()) or "no layers"

    n_exits = len(kit["exits"])
    checks.append(_check(
        "exits", "Ways out", n_exits >= MIN_EXITS,
        f"{kit['all_exit_count']} road crossing(s) of the atlas edge; {n_exits} on the board"
        + (": " + ", ".join(f"{e['letter']} {e['name']}" for e in kit["exits"]) if n_exits else ""),
        "Fewer than two ways out. Either the road data stops short of the atlas edge, "
        "or the community really has one road out. Both are worth knowing; check the roads layer.",
    ))
    checks.append(_check(
        "safe_places", "Safe places to go", bool(kit["safe_places"]),
        f"{len(kit['safe_places'])} named safe place(s): {layer_note('safe_places')}",
        "No evacuation sites or temporary refuge areas are mapped. The Fire's evacuation order "
        "needs somewhere to send people: add a safe-places layer and mark them.",
    ))
    checks.append(_check(
        "helispots", "Helicopter landing spots", bool(kit["helispots"]),
        f"{len(kit['helispots'])} helispot(s): {layer_note('helispots')}",
        "No helicopter landing spots are mapped. Add them if the community has any.",
        warn_only=True,
    ))
    n_places = len(kit["places"])
    checks.append(_check(
        "places", "Numbered places", n_places >= MIN_PLACES,
        f"{n_places} named place(s) to number on the board: {layer_note('landmarks')}",
        f"The 'loved one lives at ⑰' cards need at least {MIN_PLACES} named places "
        "(school, store, community hall, trailheads). Add names to the landmarks layer.",
        warn_only=n_places > 0,
    ))
    named_m = sum(kit["named_roads"].values())
    total = named_m + kit["unnamed_road_m"]
    share = named_m / total if total else 0.0
    checks.append(_check(
        "road_names", "Named roads", total > 0 and share >= 0.8,
        f"{share:.0%} of road length has a usable name ({len(kit['named_roads'])} names)",
        "Many roads have no usable name (blank, a number, or only 'DR'/'RD'). Cards and "
        "evacuation orders read badly without names, and so does a radio call. Fix names in the roads layer.",
        warn_only=total > 0,
    ))
    checks.append(_check(
        "hazards", "Mapped hazards", bool(role_layers.get("hazards")) and any(role_layers["hazards"].values()),
        f"hazards: {layer_note('hazards')}",
        "No hazards are mapped (one-lane bridges, washouts, gates). Each one becomes a chance card.",
        warn_only=True,
    ))
    if not options.get("alert_system"):
        checks.append(_check(
            "alert_system", "Local alert system", False, "",
            "Set alert_system on the game_kit outlet (e.g. 'Humboldt Alert') so the setup "
            "sheet names the county's sign-up.", warn_only=True))
    return checks


def ready(kit):
    return all(c["status"] != "missing" for c in kit["readiness"])


# ── cards and evacuation order ──────────────────────────────────────────────

def local_cards(kit, feats, options):
    """Place-specific chance cards from the atlas. Deterministic per atlas."""
    rng = random.Random(kit["atlas"])
    roads = [n for n, _ in sorted(kit["named_roads"].items(), key=lambda kv: -kv[1])]
    main_roads = roads[:8]
    cards = []

    def card(title, text, source):
        cards.append({"title": title, "text": text, "source": source})

    for road in rng.sample(main_roads, min(3, len(main_roads))):
        card("Tree down", f"A tree is down across {road}. If you are on {road}, skip a turn.", "roads")
    if main_roads:
        road = rng.choice(main_roads)
        card("Road work", f"There is road work on {road}. Lose two steps if you are on it.", "roads")
        road = rng.choice(main_roads)
        card("Lines down", f"Power lines are down on {road}. Move one space only. Do not spin.", "roads")

    for f in feats.get("hazards", [])[:6]:
        name = feature_name(f)
        p = _point(f)
        if not name or not p:
            continue
        road = nearest_road(p, feats.get("roads", []), kit["bbox"])
        where = f" on {road}" if road else ""
        card("Known hazard", f"{name.rstrip('. ')}{where}. Subtract two steps if your route passes it. "
                             "(Disregard if you've driven it this year.)", "hazards")

    for e in kit["exits"][:3]:
        card("Exit blocked", f"Fire is blocking exit {e['letter']} ({e['name']}). "
                             "Nobody can leave that way until the Fire's next turn.", "exits")

    if kit["places"]:
        for p in rng.sample(kit["places"], min(2, len(kit["places"]))):
            card("Loved one in need", f"A friend lives at ({p['number']}) {p['name']}. Their car won't "
                                      "start. Go to them now. (Disregard if you've made backup plans "
                                      "with friends and family.)", "landmarks")

    n_water = len(feats.get("water", []))
    if n_water:
        card("Know your water", f"There are {n_water} water sources mapped in this atlas. If you can "
                                "name the one nearest your home, add 2 steps.", "water")

    if kit["safe_places"]:
        s = rng.choice(kit["safe_places"])
        card("Safe place full", f"{s['name']} is full. The Fire names another safe place; "
                                "head there instead.", "safe_places")
    else:
        card("Where do we go?", "Nobody at the table can name an official safe place. Everyone loses "
                                "a turn while you agree on one. (Then go put it in the atlas.)",
             "safe_places")
    return cards


def evacuation_order(kit):
    """A sample of the Fire's order, filled in with this atlas's places."""
    exits = kit["exits"]
    dest = (kit["safe_places"][0]["name"] if kit["safe_places"]
            else f"exit {exits[0]['letter']} ({exits[0]['name']})" if exits
            else "[a safe place — none mapped yet]")
    route = f" by way of exit {exits[0]['letter']}" if exits and kit["safe_places"] else ""
    return (f"There is a [small/medium/large] fire in cell [1–12] and it is moving [wind direction]. "
            f"There is an evacuation order for cells [ ]. Everyone should head to {dest}{route}.")


# ── pages ───────────────────────────────────────────────────────────────────

_CSS = """
body { font: 15px/1.45 system-ui, sans-serif; margin: 1.5rem; max-width: 56rem; color: #222; }
h1 { margin-bottom: .2rem; } .muted { color: #666; font-size: .9rem; }
table { border-collapse: collapse; width: 100%; margin: .5rem 0 1.5rem; }
th, td { text-align: left; padding: .35rem .6rem; border-bottom: 1px solid #ddd; vertical-align: top; }
.ok { color: #1b7a2e; } .warn { color: #a66a00; } .missing { color: #b3261e; font-weight: 600; }
blockquote { border-left: 4px solid #c33; margin: 1rem 0; padding: .4rem 1rem; background: #fbf3f2; }
"""

_STATUS = {"ok": "✓ ready", "warn": "△ thin", "missing": "✗ missing"}


def index_html(kit, board_ok):
    e = html.escape
    rows = "".join(
        f"<tr><td>{e(c['label'])}</td><td class='{c['status']}'>{_STATUS[c['status']]}</td>"
        f"<td>{e(c['detail'])}{'<br><em>' + e(c['fix']) + '</em>' if c['fix'] else ''}</td></tr>"
        for c in kit["readiness"])
    files = "".join(f"<li><a href='{e(u)}'>{e(n)}</a></li>" for n, u in KIT_FILES)
    places = "".join(f"<li>({p['number']}) {e(p['name'])}</li>" for p in kit["places"]) or "<li><em>none</em></li>"
    exits = "".join(f"<li>{e(x['letter'])}: {e(x['name'])} ({e(x['side'])} edge)</li>"
                    for x in kit["exits"]) or "<li><em>none found</em></li>"
    verdict = ("This atlas has what the game needs." if ready(kit)
               else "This atlas is missing something the game needs — see below. "
                    "The board still prints, with the gaps showing.")
    board = ("<a href='board.pdf'>board.pdf</a> (36 × 24 in; print at a copy shop or tile it)"
             if board_ok else "<em>The board PDF was not generated; see the server log.</em>")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{e(kit['title'])} game kit</title><style>{_CSS}</style></head><body>
<h1>{e(kit['title'])}: wildfire game kit</h1>
<p class="muted">A local board and chance cards for the <a href="{KIT_URL}">Wildfire Resilience
board game</a> (game design: Tom Maiorana, UC Davis), built from this atlas.</p>
<p><strong>{verdict}</strong></p>

<h2>Print</h2>
<ul>
<li>{board}</li>
<li><a href="cards.html">Local chance cards</a> ({len(kit['cards'])}; print from the browser, cut on the lines)</li>
</ul>
<p>The rest comes from the original kit, unchanged:</p>
<ul>{files}</ul>
<p class="muted">Local setup-sheet line: “Have you signed up for
{e(kit.get('alert_system') or '[your county alert system]')}? If so, put a 2 in the box.”</p>

<h2>Can this atlas be played?</h2>
<table><tr><th>Ingredient</th><th>Status</th><th>Detail</th></tr>{rows}</table>

<h2>Sample evacuation order (for the Fire)</h2>
<blockquote>{e(kit['evacuation_order'])}</blockquote>

<h2>Board key</h2>
<p>One step = {kit['step_m']} m of road. Grid cells 1–12 match the Fire Location spinner.</p>
<h3>Exits</h3><ul>{exits}</ul>
<h3>Numbered places</h3><ul>{places}</ul>
</body></html>
"""


def cards_html(kit):
    e = html.escape
    cells = "".join(
        f"<div class='card'><div class='tag'>{e(kit['title'])} · local</div>"
        f"<h3>{e(c['title'])}</h3><p>{e(c['text'])}</p></div>"
        for c in kit["cards"])
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{e(kit['title'])} chance cards</title>
<style>
@page {{ size: letter; margin: 0.4in; }}
body {{ font: 12pt/1.35 system-ui, sans-serif; margin: 0; }}
.sheet {{ display: grid; grid-template-columns: repeat(3, 1fr); }}
.card {{ height: 3.25in; border: 1px dashed #999; padding: .25in; box-sizing: border-box;
        break-inside: avoid; }}
.card h3 {{ margin: .35in 0 .15in; text-align: center; }}
.card p {{ margin: 0; text-align: center; }}
.tag {{ font-size: 8pt; color: #888; text-transform: uppercase; letter-spacing: .08em; text-align: center; }}
@media screen {{ body {{ margin: 1rem; max-width: 8in; }} }}
</style></head><body><div class="sheet">{cells}</div></body></html>
"""
