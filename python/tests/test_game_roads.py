"""game_roads.py: the DuckDB road filter and edge exits. Needs duckdb + spatial (runs on the box)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    import duckdb
    duckdb.connect().execute("INSTALL spatial; LOAD spatial;")
    import game_roads as gr
    HAVE_DUCKDB = True
except Exception:                       # not installed locally, or no spatial extension
    HAVE_DUCKDB = False

BBOX = {"west": -124.0, "east": -123.9, "south": 40.15, "north": 40.25}
FILTER = {"field": "FUNCTIONAL", "keep": ["Principal Arterials"]}


def road(coords, name, cls):
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"name": name, "FUNCTIONAL": cls}}


def house(lon, lat):
    d = 0.0001
    return {"type": "Feature", "properties": {},
            "geometry": {"type": "Polygon", "coordinates": [[[lon, lat], [lon + d, lat], [lon + d, lat + d],
                                                             [lon, lat + d], [lon, lat]]]}}


HWY = road([[-124.05, 40.2], [-123.95, 40.2], [-123.85, 40.2]], "US HWY 101", "Principal Arterials")
LANE = road([[-123.95, 40.2], [-123.95, 40.22]], "Lane Rd", "Private")
DRIVE = road([[-123.97, 40.2], [-123.97, 40.17]], "Drive", "Private")
BACK = road([[-123.92, 40.2], [-123.92, 40.10]], "Back Rd", "Local Roads")


@unittest.skipUnless(HAVE_DUCKDB, "duckdb with spatial not available")
class TestRoadParts(unittest.TestCase):

    def test_clipped_and_classified(self):
        parts = gr.road_parts([HWY, LANE, BACK], BBOX, FILTER)
        by_rid = {p["rid"]: p for p in parts}
        self.assertEqual(set(by_rid), {0, 1, 2})
        self.assertTrue(by_rid[0]["by_class"])
        self.assertFalse(by_rid[1]["by_class"])
        xs = [c[0] for c in by_rid[0]["coords"]]
        self.assertAlmostEqual(min(xs), -124.0, places=6)          # clipped to the atlas
        self.assertAlmostEqual(max(xs), -123.9, places=6)
        self.assertAlmostEqual(min(c[1] for c in by_rid[2]["coords"]), 40.15, places=6)

    def test_no_filter_keeps_everything(self):
        self.assertTrue(all(p["by_class"] for p in gr.road_parts([HWY, LANE], BBOX)))

    def test_residences_need_the_minimum(self):
        two = [house(-123.9705, 40.18), house(-123.9705, 40.181)]
        three = two + [house(-123.9705, 40.182)]
        for houses, expect in ((two, set()), (three, {"residences"})):
            parts = gr.road_parts([HWY, DRIVE], BBOX, FILTER, {"residences": (houses, 3)})
            drive = next(p for p in parts if p["rid"] == 1)
            self.assertEqual(drive["serves"], expect)

    def test_one_point_serves(self):
        heli = {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [-123.9502, 40.21]}}
        parts = gr.road_parts([HWY, LANE], BBOX, FILTER, {"helispots": ([heli], 1)})
        self.assertEqual(next(p for p in parts if p["rid"] == 1)["serves"], {"helispots"})


@unittest.skipUnless(HAVE_DUCKDB, "duckdb with spatial not available")
class TestEdgeExits(unittest.TestCase):

    def test_only_kept_roads_one_per_side(self):
        pts = gr.edge_exits([HWY, BACK], BBOX, FILTER)
        names = sorted(p["properties"]["name"] for p in pts)
        self.assertEqual(names, ["US Hwy 101 (east edge)", "US Hwy 101 (west edge)"])
        self.assertTrue(all(p["properties"]["source"] == "edge" for p in pts))

    def test_wiggle_on_one_side_is_one_exit(self):
        wiggle = road([[-123.95, 40.2], [-124.01, 40.2], [-123.99, 40.18], [-124.01, 40.17]],
                      "Wiggle Rd", "Principal Arterials")
        self.assertEqual(len(gr.edge_exits([wiggle], BBOX, FILTER)), 1)

    def test_rerun_skips_existing_points(self):
        first = gr.edge_exits([HWY], BBOX, FILTER)
        self.assertEqual(gr.edge_exits([HWY], BBOX, FILTER, existing=first), [])
        self.assertEqual(len(gr.edge_exits([HWY], BBOX, FILTER, existing=first[:1])), 1)


if __name__ == '__main__':
    unittest.main()
