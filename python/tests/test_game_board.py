"""game_board.py: road graph, connectors, spur pruning, spaces and safety on synthetic networks."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import game_board as gb
import game_kit as gk

# ~8.5 km x 11 km around 40.2 N
BBOX = {"west": -124.0, "east": -123.9, "south": 40.15, "north": 40.25}
K = gk.meters_per_degree(BBOX)


def part(coords, rid=0, by_class=True, serves=()):
    return {"rid": rid, "coords": coords, "by_class": by_class, "serves": set(serves)}


# A main road across the atlas west→east at 40.2, a short stub north off it,
# and a long side road south.
MAIN = part([[-124.0, 40.2], [-123.95, 40.2], [-123.92, 40.2], [-123.9, 40.2]], rid=0)
STUB = part([[-123.95, 40.2], [-123.95, 40.203]], rid=1)               # ~330 m
SIDE = part([[-123.92, 40.2], [-123.92, 40.16]], rid=2)                # ~4.4 km


class TestGraph(unittest.TestCase):

    def test_split_at_shared_interior_vertex(self):
        # OSM-style: two ways crossing at a vertex inside both
        a = part([[-123.99, 40.2], [-123.95, 40.2], [-123.91, 40.2]])
        b = part([[-123.95, 40.17], [-123.95, 40.2], [-123.95, 40.23]])
        g, _ = gb.build_graph([a, b], K)
        self.assertEqual(len(g.edges), 4)
        self.assertEqual(g.degree(gb._key([-123.95, 40.2])), 4)

    def test_contract_merges_pass_through_vertices(self):
        pieces = [part([[-123.99, 40.2], [-123.98, 40.2]]), part([[-123.98, 40.2], [-123.97, 40.2]]),
                  part([[-123.97, 40.2], [-123.96, 40.2]])]
        g, _ = gb.build_graph(pieces, K)
        gb.contract(g)
        self.assertEqual(len(g.edges), 1)
        self.assertAlmostEqual(next(iter(g.edges.values()))["length"], 0.03 * K[0], delta=1)

    def test_served_road_is_connected_by_the_shortest_path(self):
        # kept main road; a private drive serving houses hangs off an unkept lane
        lane = part([[-123.95, 40.2], [-123.95, 40.21]], rid=1, by_class=False)
        drive = part([[-123.95, 40.21], [-123.96, 40.21]], rid=2, by_class=False, serves={"residences"})
        detour = part([[-123.96, 40.21], [-123.99, 40.24]], rid=3, by_class=False)
        g, part_of = gb.build_graph([MAIN, lane, drive, detour], K)
        kept = {e for e, i in part_of.items() if i == 0}
        served = {e for e, i in part_of.items() if i == 2}
        result = {part_of[e] for e in gb.connect_served(g, kept, served)}
        self.assertEqual(result, {0, 1, 2})            # lane pulled in, detour not

    def test_prune_short_unserved_spurs_only(self):
        sticky_stub = part([[-123.93, 40.2], [-123.93, 40.203]], rid=3)
        g, part_of = gb.build_graph([MAIN, STUB, SIDE, sticky_stub], K)
        for e, i in part_of.items():
            g.edges[e]["sticky"] = i == 3
        ends = {gb._key([-124.0, 40.2]), gb._key([-123.9, 40.2])}
        gb.prune_spurs(g, 1000, protected=ends)
        rids = sorted(e["rid"] for e in g.edges.values())
        self.assertNotIn(1, rids)                      # short stub gone
        self.assertIn(2, rids)                         # long side road stays
        self.assertIn(3, rids)                         # serving stub stays
        self.assertTrue(all(g.degree(n) > 0 for n in ends))


class TestSpaces(unittest.TestCase):

    def test_step_hits_the_target(self):
        self.assertEqual(gb.step_for(16000, 32), 500)
        self.assertEqual(gb.step_for(1000, 32), 100)  # floor
        self.assertEqual(gb.step_for(0), 0)

    def test_spaces_at_junctions_and_along_edges(self):
        g, _ = gb.build_graph([MAIN, SIDE], K)
        gb.contract(g)
        spaces, links = gb.place_spaces(g, 1000)
        junction = [-123.92, 40.2]
        self.assertTrue(any(gk._dist_m(s, junction, K) < 1 for s in spaces))
        # every edge contributes round(len/step) links
        expected = sum(max(1, round(e["length"] / 1000)) for e in g.edges.values())
        self.assertEqual(len(links), expected)

    def test_safety_distances_and_stranded_spaces(self):
        island = part([[-123.99, 40.17], [-123.966, 40.17]], rid=9)       # ~2 km, too long to prune
        board = gb.build_board([MAIN, SIDE, island], BBOX,
                               [{"name": "West", "lonlat": [-124.0, 40.2], "source": "edge"}], step_m=500)
        self.assertEqual(len(board["safe"]), 1)
        d = board["to_safety"]
        self.assertEqual(d[board["safe"][0]["space"]], 0)
        self.assertEqual(sum(x is None for x in d), 5)   # the island: ~2 km / 500 m → 4 links, 5 spaces
        self.assertGreater(max(x for x in d if x is not None), 10)

    def test_build_board_sizes_the_longest_trip(self):
        board = gb.build_board([MAIN, STUB, SIDE], BBOX, [], target_spaces=20)
        # main road 8.5 km + side road 4.4 km ≈ 11 km across: step ≈ 550 → 550
        self.assertEqual(board["step_m"], 550)
        self.assertNotIn(1, [r["rid"] for r in board["roads"]])   # stub pruned

    def test_far_safe_location_is_not_placed(self):
        board = gb.build_board([MAIN], BBOX, [{"name": "Far", "lonlat": [-123.95, 40.24], "source": "drawn"}],
                               step_m=500)
        self.assertEqual(board["safe"], [])
        self.assertEqual(len(board["unplaced_safe"]), 1)


if __name__ == '__main__':
    unittest.main()
