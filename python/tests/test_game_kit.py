"""game_kit.py: exits, step dots, names, readiness and cards on small synthetic atlases."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import game_kit as gk

# ~8.5 km x 11 km around 40.2 N
BBOX = {"west": -124.0, "east": -123.9, "south": 40.15, "north": 40.25}


def road(coords, name="Salmon Creek Rd"):
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"name": name}}


def point(lon, lat, name):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"name": name}}


# A T: a highway crossing the whole atlas west→east, a dead-end spur north from it,
# and a back road that leaves through the south edge.
HIGHWAY = road([[-124.05, 40.2], [-123.95, 40.2], [-123.85, 40.2]], "US HWY 101")
SPUR = road([[-123.95, 40.2], [-123.95, 40.23]], "Spur Rd")
BACK = road([[-123.92, 40.2], [-123.92, 40.10]], "Back Rd")


class TestNames(unittest.TestCase):

    def test_junk_names(self):
        for junk in [None, "", "  ", "DR", "rd", "17", "3", "Ln"]:
            self.assertIsNone(gk.clean_name(junk), junk)

    def test_display_case(self):
        self.assertEqual(gk.clean_name("SALMON CREEK RD"), "Salmon Creek Rd")
        self.assertEqual(gk.clean_name("US HWY 101"), "US Hwy 101")
        self.assertEqual(gk.clean_name("Bird of Paradise"), "Bird of Paradise")

    def test_highway(self):
        self.assertTrue(gk.is_highway("US Hwy 101"))
        self.assertTrue(gk.is_highway("State Route 254"))
        self.assertFalse(gk.is_highway("Usal Rd"))


class TestBoard(unittest.TestCase):

    def test_grid_numbers_row_by_row_from_north_west(self):
        cells = gk.grid_cells(BBOX)["features"]
        self.assertEqual([c["properties"]["cell"] for c in cells], list(range(1, 13)))
        self.assertEqual(gk.grid_cell_of([-123.99, 40.249], BBOX), 1)
        self.assertEqual(gk.grid_cell_of([-123.901, 40.151], BBOX), 12)
        self.assertEqual(gk.grid_cell_of([-120, 50], BBOX), 4)     # clamped to the north-east

    def test_exits_where_roads_leave(self):
        exits = gk.find_exits([HIGHWAY, SPUR, BACK], BBOX)
        sides = sorted((e["name"], e["side"]) for e in exits)
        self.assertEqual(sides, [("Back Rd", "south"), ("US Hwy 101", "east"), ("US Hwy 101", "west")])
        west = next(e for e in exits if e["side"] == "west")
        self.assertAlmostEqual(west["lonlat"][0], -124.0)
        self.assertAlmostEqual(west["lonlat"][1], 40.2)

    def test_road_clipped_to_the_edge_still_exits(self):
        clipped = road([[-123.95, 40.2], [-124.0, 40.2]], "Clipped Rd")
        self.assertEqual([e["side"] for e in gk.find_exits([clipped], BBOX)], ["west"])

    def test_same_road_same_side_is_one_exit(self):
        wiggle = road([[-123.95, 40.2], [-124.01, 40.2], [-123.99, 40.18], [-124.01, 40.17]], "Wiggle Rd")
        self.assertEqual(len(gk.find_exits([wiggle], BBOX)), 1)

    def test_board_exits_prefer_highways_then_length(self):
        exits = gk.find_exits([HIGHWAY, SPUR, BACK], BBOX)
        named = {"US Hwy 101": 100.0, "Back Rd": 5000.0}
        chosen = gk.board_exits(exits, named, max_exits=2)
        self.assertEqual({e["name"] for e in chosen}, {"US Hwy 101"})
        self.assertEqual([e["letter"] for e in chosen], ["A", "B"])
        only_back = gk.board_exits(exits, named, exit_roads=["BACK RD"])
        self.assertEqual([e["name"] for e in only_back], ["Back Rd"])

    def test_step_dots_spacing_and_clipping(self):
        k = gk.meters_per_degree(BBOX)
        dots = gk.step_points([SPUR], BBOX, 500)
        spur_m = 0.03 * k[1]
        self.assertEqual(len(dots), int(spur_m // 500))
        self.assertAlmostEqual((dots[1][1] - dots[0][1]) * k[1], 500, delta=1)
        # the highway's dots stop at the atlas edge
        hwy = gk.step_points([HIGHWAY], BBOX, 500)
        self.assertTrue(all(BBOX["west"] < p[0] < BBOX["east"] for p in hwy))

    def test_places_numbered_and_unnamed_dropped(self):
        places = gk.numbered_places([point(-123.95, 40.17, "Store"), point(-123.99, 40.24, "School"),
                                     point(-123.95, 40.2, ""), point(-125, 40.2, "Far away")], BBOX)
        self.assertEqual([(p["number"], p["name"]) for p in places], [(1, "School"), (2, "Store")])


class TestKit(unittest.TestCase):

    def kit(self, **roles):
        base = {"roads": {"roads": [HIGHWAY, SPUR, BACK]}}
        return gk.build_kit("test", BBOX, {**base, **roles}, {"alert_system": "Humboldt Alert"})

    def status(self, kit):
        return {c["key"]: c["status"] for c in kit["readiness"]}

    def test_bare_atlas_is_missing_safe_places_and_places(self):
        s = self.status(self.kit())
        self.assertEqual(s["exits"], "ok")
        self.assertEqual(s["safe_places"], "missing")
        self.assertEqual(s["places"], "missing")
        self.assertEqual(s["helispots"], "warn")
        self.assertFalse(gk.ready(self.kit()))

    def test_complete_atlas_is_ready(self):
        landmarks = [point(-123.99 + i * 0.01, 40.16 + i * 0.01, f"Place {i}") for i in range(6)]
        kit = self.kit(landmarks={"poi": landmarks},
                       safe_places={"sites": [point(-123.93, 40.21, "Community Hall")]},
                       helispots={"heli": [point(-123.94, 40.22, "Ridge")]},
                       hazards={"hazards": [point(-123.95, 40.21, "One-lane bridge")]})
        self.assertTrue(gk.ready(kit), kit["readiness"])
        self.assertIn("Community Hall", kit["evacuation_order"])

    def test_one_exit_is_missing(self):
        kit = gk.build_kit("test", BBOX, {"roads": {"roads": [SPUR, BACK]}})
        self.assertEqual(self.status(kit)["exits"], "missing")

    def test_cards_use_local_names_and_skip_empty_layers(self):
        kit = self.kit(hazards={"hazards": [point(-123.95, 40.21, "One-lane bridge")]})
        text = " ".join(c["text"] for c in kit["cards"])
        self.assertIn("One-lane bridge on Spur Rd", text)
        self.assertIn("US Hwy 101", text)
        self.assertNotIn("Loved one", " ".join(c["title"] for c in kit["cards"]))
        self.assertIn("Where do we go?", [c["title"] for c in kit["cards"]])

    def test_cards_are_deterministic(self):
        self.assertEqual(self.kit()["cards"], self.kit()["cards"])

    def test_pages_render(self):
        kit = self.kit()
        page = gk.index_html(kit, board_ok=False)
        self.assertIn("missing", page)
        self.assertIn("Humboldt Alert", page)
        self.assertIn("board PDF was not generated", page)
        self.assertEqual(gk.cards_html(kit).count("class='card'"), len(kit["cards"]))

    def test_overlays_are_feature_collections(self):
        layers = gk.overlay_layers(self.kit())
        self.assertEqual(len(layers["game_grid"]["features"]), 12)
        self.assertEqual(len(layers["game_exits"]["features"]), 3)
        self.assertEqual(layers["game_safe"]["features"], [])


if __name__ == '__main__':
    unittest.main()
