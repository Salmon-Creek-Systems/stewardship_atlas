"""
Board topology for the game kit: from filtered road pieces to playable spaces.

Input is the road parts game_roads.road_parts() returns: LineStrings clipped to
the atlas, each with

  rid       index of the source road feature (for its name)
  coords    [[lon, lat], ...]
  by_class  the road's category is one the board keeps
  serves    the target kinds it passes near (safe_locations, helispots,
            landmarks, residences...), already thresholded

build_board() then

  1. keeps the by-class parts and the serving parts;
  2. connects each serving part to the kept network along the shortest road
     path, so a road kept for a house or a helispot doesn't float alone;
  3. prunes short dead ends that serve nothing (repeatedly, junction to junction);
  4. picks a step length so the longest trip across the board is ~TARGET_SPACES;
  5. places spaces at every junction and dead end, and evenly along each edge;
  6. snaps safe locations to their nearest space and counts how far every
     space is from safety.

Pure Python, lengths in local metres (game_kit.meters_per_degree).
"""

import heapq
from collections import Counter, defaultdict, deque

import game_kit as gk

TARGET_SPACES = 32        # longest shortest trip across the board, in spaces
SPUR_STEPS = 2            # a dead end shorter than this many steps, serving nothing, is pruned
SAFE_SNAP_M = 250         # a safe location further than this from any board road isn't placed
SNAP_DIGITS = 6           # vertices equal to ~0.1 m are the same node


def _key(p):
    return (round(p[0], SNAP_DIGITS), round(p[1], SNAP_DIGITS))


def _length(coords, k):
    return sum(gk._dist_m(a, b, k) for a, b in zip(coords, coords[1:]))


class Graph:
    """Undirected road graph. Nodes are rounded (lon, lat); edges carry their polyline."""

    def __init__(self, k):
        self.k = k
        self.edges = {}                 # id -> {'a', 'b', 'coords', 'length', 'rid', 'sticky'}
        self.adj = defaultdict(set)     # node -> edge ids
        self._next = 0

    def add(self, coords, rid, sticky=False):
        eid = self._next
        self._next += 1
        a, b = _key(coords[0]), _key(coords[-1])
        self.edges[eid] = {"a": a, "b": b, "coords": coords, "length": _length(coords, self.k),
                           "rid": rid, "sticky": sticky}
        self.adj[a].add(eid)
        self.adj[b].add(eid)
        return eid

    def remove(self, eid):
        e = self.edges.pop(eid)
        for n in (e["a"], e["b"]):
            self.adj[n].discard(eid)
            if not self.adj[n]:
                del self.adj[n]

    def other(self, eid, node):
        e = self.edges[eid]
        return e["b"] if e["a"] == node else e["a"]

    def degree(self, node):
        return len(self.adj.get(node, ()))

    def subgraph(self, eids):
        g = Graph(self.k)
        for eid in eids:
            e = self.edges[eid]
            g.add(e["coords"], e["rid"], e["sticky"])
        return g


def build_graph(parts, k):
    """Split parts at shared vertices (OSM ways cross mid-way) into a Graph.

    Returns the graph and, per edge id, the index of the part it came from.
    """
    count = Counter(_key(p) for part in parts for p in part["coords"])
    g, part_of = Graph(k), {}
    for i, part in enumerate(parts):
        cs = part["coords"]
        start = 0
        for j in range(1, len(cs)):
            if j == len(cs) - 1 or count[_key(cs[j])] > 1:
                piece = cs[start:j + 1]
                if _length(piece, k) > 0:
                    part_of[g.add(piece, part["rid"])] = i
                start = j
    return g, part_of


def dijkstra(g, sources):
    """Distances (m) from the nearest source node, and the edge each node was reached by."""
    dist, via = {n: 0.0 for n in sources}, {}
    heap = [(0.0, n) for n in sources]
    heapq.heapify(heap)
    while heap:
        d, n = heapq.heappop(heap)
        if d > dist.get(n, float("inf")):
            continue
        for eid in g.adj.get(n, ()):
            m = g.other(eid, n)
            nd = d + g.edges[eid]["length"]
            if nd < dist.get(m, float("inf")):
                dist[m], via[m] = nd, eid
                heapq.heappush(heap, (nd, m))
    return dist, via


def connect_served(g, kept, served):
    """kept plus served, plus each served edge's shortest connection to kept."""
    result = set(kept) | set(served)
    sources = {n for eid in kept for n in (g.edges[eid]["a"], g.edges[eid]["b"])}
    if not sources:
        return result
    dist, via = dijkstra(g, sources)
    for eid in served:
        if eid in kept:
            continue
        ends = [n for n in (g.edges[eid]["a"], g.edges[eid]["b"]) if n in dist]
        if not ends:
            continue                                # no road connects it; it stays on its own
        node = min(ends, key=dist.get)
        while node not in sources:
            back = via[node]
            result.add(back)
            node = g.other(back, node)
    return result


def contract(g, keep_nodes=()):
    """Merge edges through pass-through (degree-2) nodes so edges run junction to junction."""
    changed = True
    while changed:
        changed = False
        for node in list(g.adj):
            eids = list(g.adj.get(node, ()))
            if len(eids) != 2 or node in keep_nodes:
                continue
            e1, e2 = (g.edges[i] for i in eids)
            if e1["a"] == e1["b"] or e2["a"] == e2["b"]:
                continue                            # a loop; leave it
            c1 = e1["coords"] if e1["b"] == node else e1["coords"][::-1]   # x .. node
            c2 = e2["coords"] if e2["a"] == node else e2["coords"][::-1]   # node .. y
            rid = e1["rid"] if e1["length"] >= e2["length"] else e2["rid"]
            sticky = e1["sticky"] or e2["sticky"]
            g.remove(eids[0])
            g.remove(eids[1])
            g.add(c1 + c2[1:], rid, sticky)
            changed = True


def prune_spurs(g, spur_m, protected=()):
    """Drop dead-end edges shorter than spur_m that aren't sticky, until none are left."""
    contract(g, protected)
    changed = True
    while changed:
        changed = False
        for node in list(g.adj):
            if g.degree(node) != 1 or node in protected:
                continue
            eid = next(iter(g.adj[node]))
            e = g.edges[eid]
            if not e["sticky"] and e["length"] < spur_m:
                g.remove(eid)
                changed = True
        contract(g, protected)


def components(g):
    """Lists of edge ids, one per connected component."""
    seen, comps = set(), []
    for start in list(g.edges):
        if start in seen:
            continue
        comp, queue = [], deque([start])
        seen.add(start)
        while queue:
            eid = queue.popleft()
            comp.append(eid)
            for n in (g.edges[eid]["a"], g.edges[eid]["b"]):
                for nxt in g.adj[n]:
                    if nxt not in seen:
                        seen.add(nxt)
                        queue.append(nxt)
        comps.append(comp)
    return comps


def diameter_m(g):
    """Longest shortest path in the largest component (double sweep: close, and cheap)."""
    comps = components(g)
    if not comps:
        return 0.0
    biggest = max(comps, key=lambda c: sum(g.edges[e]["length"] for e in c))
    start = g.edges[biggest[0]]["a"]
    dist, _ = dijkstra(g, [start])
    far = max(dist, key=dist.get)
    dist, _ = dijkstra(g, [far])
    return max(dist.values())


def step_for(diameter, target=TARGET_SPACES):
    """Step length (m, rounded to 50) putting `target` spaces across the diameter."""
    return max(100, round(diameter / target / 50) * 50) if diameter else 0


def _point_along(coords, k, d):
    walked = 0.0
    for a, b in zip(coords, coords[1:]):
        seg = gk._dist_m(a, b, k)
        if seg > 0 and walked + seg >= d:
            t = (d - walked) / seg
            return [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])]
        walked += seg
    return list(coords[-1][:2])


def place_spaces(g, step_m):
    """Spaces at every node, and n-1 more along each edge (n = its length in steps, at least 1).

    Returns (spaces [[lon, lat]], links [(i, j)]) — links join neighbouring spaces.
    """
    index, spaces, links = {}, [], []

    def node_space(n):
        if n not in index:
            index[n] = len(spaces)
            spaces.append([n[0], n[1]])
        return index[n]

    for e in g.edges.values():
        n = max(1, round(e["length"] / step_m))
        chain = [node_space(e["a"])]
        for i in range(1, n):
            spaces.append(_point_along(e["coords"], g.k, e["length"] * i / n))
            chain.append(len(spaces) - 1)
        chain.append(node_space(e["b"]))
        links += list(zip(chain, chain[1:]))
    return spaces, links


def snap_safe(safe_points, spaces, k, max_m=SAFE_SNAP_M):
    """Each safe location's nearest space; those further than max_m are left off."""
    placed, unplaced = [], []
    for s in safe_points:
        if not spaces:
            unplaced.append(s)
            continue
        i = min(range(len(spaces)), key=lambda j: gk._dist_m(spaces[j], s["lonlat"], k))
        if gk._dist_m(spaces[i], s["lonlat"], k) <= max_m:
            placed.append(dict(s, space=i, lonlat=spaces[i]))
        else:
            unplaced.append(s)
    return placed, unplaced


def spaces_to_safety(n_spaces, links, safe_spaces):
    """Fewest moves from each space to a safe space (None where there's no way)."""
    nbrs = defaultdict(list)
    for i, j in links:
        nbrs[i].append(j)
        nbrs[j].append(i)
    dist = [None] * n_spaces
    queue = deque()
    for s in safe_spaces:
        if dist[s] is None:
            dist[s] = 0
            queue.append(s)
    while queue:
        i = queue.popleft()
        for j in nbrs[i]:
            if dist[j] is None:
                dist[j] = dist[i] + 1
                queue.append(j)
    return dist


def build_board(parts, bbox, safe_points, step_m=None, target_spaces=TARGET_SPACES):
    """The playable board from road parts and safe locations.

    safe_points: [{'name', 'lonlat', 'source'}]. Returns a dict with roads
    (kept edges: rid, coords), step_m, spaces, links, safe (placed, with
    their space), unplaced_safe, to_safety (moves per space), kept_m, total_m.
    """
    k = gk.meters_per_degree(bbox)
    full, part_of = build_graph(parts, k)
    kept = {eid for eid, i in part_of.items() if parts[i]["by_class"]}
    served = {eid for eid, i in part_of.items() if parts[i]["serves"]}
    for eid in served:
        full.edges[eid]["sticky"] = True            # never pruned as a spur
    g = full.subgraph(connect_served(full, kept, served))

    edge_nodes = {n for n in g.adj if gk._edge_dist_m(list(n), bbox, k) <= gk.EDGE_TOLERANCE_M + 1}
    first_step = step_m or step_for(diameter_m(g), target_spaces) or gk.default_step_m(bbox)
    prune_spurs(g, SPUR_STEPS * first_step, protected=edge_nodes)
    step = step_m or step_for(diameter_m(g), target_spaces) or first_step

    spaces, links = place_spaces(g, step)
    safe, unplaced = snap_safe(safe_points, spaces, k)
    to_safety = spaces_to_safety(len(spaces), links, [s["space"] for s in safe])
    return {
        "roads": [{"rid": e["rid"], "coords": e["coords"]} for e in g.edges.values()],
        "step_m": step,
        "spaces": spaces,
        "links": links,
        "safe": safe,
        "unplaced_safe": unplaced,
        "to_safety": to_safety,
        "kept_m": sum(e["length"] for e in g.edges.values()),
        "total_m": sum(e["length"] for e in full.edges.values()),
    }
