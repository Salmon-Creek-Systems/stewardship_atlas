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
        spaces, links, link_edges = gb.place_spaces(g, 1000)
        junction = [-123.92, 40.2]
        self.assertTrue(any(gk._dist_m(s, junction, K) < 1 for s in spaces))
        # every edge contributes round(len/step) links
        expected = sum(max(1, round(e["length"] / 1000)) for e in g.edges.values())
        self.assertEqual(len(links), expected)
        self.assertEqual(len(link_edges), expected)

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

    def test_stranded_roads_are_flagged(self):
        island = part([[-123.99, 40.17], [-123.966, 40.17]], rid=9)
        board = gb.build_board([MAIN, island], BBOX,
                               [{"name": "West", "lonlat": [-124.0, 40.2], "source": "edge"}], step_m=500)
        self.assertEqual({r["rid"] for r in board["roads"] if r["stranded"]}, {9})
        self.assertEqual({r["rid"] for r in board["roads"] if not r["stranded"]}, {0})
        self.assertEqual([len(n["safe"]) for n in board["networks"]], [1, 0])


def named(coords, rid, name, cls, by_class=True):
    return dict(part(coords, rid=rid, by_class=by_class), name=name, cls=cls)


MAIN_CLASSES = ["Arterial"]
ORDER = ["Arterial", "Local", "Private"]


class TestSeedExits(unittest.TestCase):

    def seed(self, parts, **kw):
        return gb.seed_exits(parts, BBOX, kw.pop("main", MAIN_CLASSES), class_order=ORDER, **kw)

    def test_road_clipped_into_two_pieces_gets_an_exit_on_each(self):
        # SCVFD's 101: runs out the east edge and back in. The south piece carries
        # the local network; picking only the north one left that network no exit.
        south = named([[-123.9, 40.17], [-123.91, 40.18], [-123.9, 40.19]], 0, "US 101", "Arterial")
        local = named([[-123.91, 40.18], [-123.97, 40.18]], 1, "Salmon Creek Rd", "Local")
        north = named([[-123.9, 40.22], [-123.92, 40.24], [-123.95, 40.25]], 2, "US 101", "Arterial")
        exits = self.seed([south, local, north])
        self.assertEqual(sorted(e["lonlat"][1] for e in exits if e["side"] == "east"), [40.17, 40.22])

        parts = [south, local, north]
        safe = [{"name": "x", "lonlat": e["lonlat"], "source": "edge"} for e in exits]
        board = gb.build_board(parts, BBOX, safe, step_m=500)
        self.assertNotIn(None, board["to_safety"])

    def test_same_road_same_side_same_network_is_one_exit(self):
        road = named([[-123.9, 40.17], [-123.95, 40.2], [-123.9, 40.23]], 0, "US 101", "Arterial")
        self.assertEqual(len(self.seed([road])), 1)

    def test_network_without_a_main_road_gets_its_best_crossing(self):
        short = named([[-124.0, 40.2], [-123.99, 40.2]], 0, "Short", "Private")
        better = named([[-123.99, 40.2], [-123.98, 40.15]], 1, "Better", "Local")
        exits = self.seed([short, better])
        self.assertEqual([e["name"] for e in exits], ["Better"])

    def test_filtered_out_roads_never_seed(self):
        off = named([[-124.0, 40.2], [-123.95, 40.2]], 0, "Footpath", None, by_class=False)
        self.assertEqual(self.seed([off]), [])

    def test_inland_network_gets_nothing(self):
        self.assertEqual(self.seed([named([[-123.97, 40.2], [-123.95, 40.2]], 0, "Inland", "Local")]), [])

    def test_rerun_adds_nothing(self):
        road = named([[-124.0, 40.2], [-123.95, 40.2], [-123.9, 40.2]], 0, "US 101", "Arterial")
        side = named([[-123.95, 40.2], [-123.95, 40.15]], 1, "Side", "Local")
        lone = named([[-123.98, 40.25], [-123.98, 40.22]], 2, "Lone", "Local")
        first = self.seed([road, side, lone])
        self.assertEqual(len(first), 3)            # 101 west, 101 east, Lone
        self.assertEqual(self.seed([road, side, lone], existing=[e["lonlat"] for e in first]), [])

    def test_second_crossing_is_not_added_on_rerun(self):
        road = named([[-123.9, 40.17], [-123.95, 40.2], [-123.9, 40.23]], 0, "US 101", "Arterial")
        first = self.seed([road])
        self.assertEqual(self.seed([road], existing=[first[0]["lonlat"]]), [])

    def test_hand_drawn_point_counts_for_its_network(self):
        lone = named([[-123.98, 40.25], [-123.98, 40.22]], 0, "Lone", "Local")
        self.assertEqual(self.seed([lone], existing=[[-123.98, 40.222]]), [])

    def test_point_counts_only_for_the_network_nearest_it(self):
        # Two networks ~100 m apart; a point on the first must not satisfy the second.
        first = named([[-123.98, 40.25], [-123.98, 40.22]], 0, "First", "Local")
        second = named([[-123.979, 40.25], [-123.979, 40.22]], 1, "Second", "Local")
        exits = self.seed([first, second], existing=[[-123.98, 40.221]])
        self.assertEqual([e["name"] for e in exits], ["Second"])


class TestNetworkAnalysis(unittest.TestCase):

    def test_line_lists_the_top_cut_and_a_distinct_nested_one(self):
        #  safe 0 — 1 — 2 — 3
        links = [(0, 1), (1, 2), (2, 3)]
        chokes = gb.chokepoints(4, links, [10, 11, 12], [0], [0, 0, 5, 2])
        self.assertEqual([(c["kind"], c["rid"], c["households"]) for c in chokes],
                         [("road", 10, 7), ("road", 12, 2)])
        self.assertEqual(chokes[0]["spaces_cut"], 3)

    def test_ring_with_two_exits_has_no_choke(self):
        self.assertEqual(gb.chokepoints(3, [(0, 1), (1, 2), (2, 0)], [1, 2, 3], [0, 2], [0, 5, 0]), [])

    def test_ring_with_one_exit_chokes_at_that_exit(self):
        chokes = gb.chokepoints(3, [(0, 1), (1, 2), (2, 0)], [1, 2, 3], [0], [0, 5, 4])
        self.assertEqual([(c["kind"], c["space"], c["households"]) for c in chokes], [("junction", 0, 9)])

    def test_parallel_roads_are_not_a_bridge(self):
        chokes = gb.chokepoints(2, [(0, 1), (0, 1)], [1, 2], [0], [0, 4])
        self.assertEqual([c["kind"] for c in chokes], ["junction"])

    def test_junction_where_two_branches_meet(self):
        #  safe 0 — 1 — (2 and 3); blocking 1 cuts both branches, and neither branch road does
        chokes = gb.chokepoints(4, [(0, 1), (1, 2), (1, 3)], [1, 2, 3], [0], [0, 0, 5, 5])
        self.assertEqual((chokes[0]["kind"], chokes[0]["households"]), ("road", 10))
        self.assertTrue(any(c["rid"] == 2 and c["households"] == 5 for c in chokes))

    def test_stranded_spaces_are_ignored(self):
        chokes = gb.chokepoints(4, [(0, 1), (2, 3)], [1, 2], [0], [0, 1, 9, 9])
        self.assertEqual([c["households"] for c in chokes], [1])

    def test_buildings_count_at_their_nearest_space(self):
        spaces = [[-124.0, 40.2], [-123.9, 40.2]]
        pts = [[-123.99, 40.2], [-123.91, 40.2], [-123.92, 40.2]]
        self.assertEqual(gb.households_per_space(pts, spaces, K), [1, 2])


if __name__ == '__main__':
    unittest.main()
