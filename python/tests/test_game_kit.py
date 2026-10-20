"""game_kit.py: exits, step dots, names, readiness and cards on small synthetic atlases."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import game_board as gb
import game_kit as gk

# ~8.5 km x 11 km around 40.2 N
BBOX = {"west": -124.0, "east": -123.9, "south": 40.15, "north": 40.25}


def road(coords, name="Salmon Creek Rd"):
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"name": name}}


def point(lon, lat, name):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"name": name}}


# A T: a highway across the whole atlas west→east (already clipped to it), a
# dead-end spur north from it, and a back road to the south edge.
HIGHWAY = road([[-124.0, 40.2], [-123.95, 40.2], [-123.92, 40.2], [-123.9, 40.2]], "US HWY 101")
SPUR = road([[-123.95, 40.2], [-123.95, 40.23]], "Spur Rd")
BACK = road([[-123.92, 40.2], [-123.92, 40.15]], "Back Rd")


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

    def test_places_numbered_and_unnamed_dropped(self):
        places = gk.numbered_places([point(-123.95, 40.17, "Store"), point(-123.99, 40.24, "School"),
                                     point(-123.95, 40.2, ""), point(-125, 40.2, "Far away")], BBOX)
        self.assertEqual([(p["number"], p["name"]) for p in places], [(1, "School"), (2, "Store")])


def safe(lon, lat, name, source="drawn"):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"name": name, "source": source}}


EDGE_SAFE = [safe(-124.0, 40.2, "US Hwy 101 (west edge)", "edge"),
             safe(-123.9, 40.2, "US Hwy 101 (east edge)", "edge")]


class TestKit(unittest.TestCase):

    def kit(self, roads=(HIGHWAY, SPUR, BACK), **roles):
        role_layers = {"roads": {"roads": list(roads)}, **roles}
        parts = [{"rid": i, "coords": f["geometry"]["coordinates"], "by_class": True, "serves": set()}
                 for i, f in enumerate(roads)]
        safe_feats = [f for fs in role_layers.get("safe_locations", {}).values() for f in fs]
        board = gb.build_board(parts, BBOX, gk.safe_points(safe_feats), step_m=500)
        return gk.build_kit("test", BBOX, role_layers, board, {"alert_system": "Humboldt Alert"})

    def status(self, kit):
        return {c["key"]: c["status"] for c in kit["readiness"]}

    def test_bare_atlas_is_missing_safe_locations_and_places(self):
        s = self.status(self.kit())
        self.assertEqual(s["safe_locations"], "missing")
        self.assertEqual(s["way_out"], "missing")
        self.assertEqual(s["places"], "missing")
        self.assertEqual(s["helispots"], "warn")
        self.assertFalse(gk.ready(self.kit()))

    def test_complete_atlas_is_ready(self):
        landmarks = [point(-123.99 + i * 0.01, 40.16 + i * 0.01, f"Place {i}") for i in range(6)]
        kit = self.kit(landmarks={"poi": landmarks},
                       safe_locations={"safe_locations": EDGE_SAFE + [safe(-123.92, 40.151, "Community Hall")]},
                       helispots={"heli": [point(-123.94, 40.22, "Ridge")]},
                       hazards={"hazards": [point(-123.95, 40.21, "One-lane bridge")]})
        self.assertTrue(gk.ready(kit), kit["readiness"])
        self.assertEqual(self.status(kit)["way_out"], "ok")
        self.assertIn("Community Hall", kit["evacuation_order"])

    def test_one_safe_location_is_missing(self):
        kit = self.kit(safe_locations={"safe_locations": EDGE_SAFE[:1]})
        self.assertEqual(self.status(kit)["safe_locations"], "missing")

    def test_stranded_roads_warn(self):
        island = road([[-123.99, 40.17], [-123.966, 40.17]], "Island Rd")
        kit = self.kit(roads=(HIGHWAY, SPUR, BACK, island), safe_locations={"safe_locations": EDGE_SAFE})
        self.assertEqual(self.status(kit)["way_out"], "warn")
        self.assertEqual(kit["stranded_spaces"], 5)

    def test_cards_use_local_names_and_skip_empty_layers(self):
        kit = self.kit(hazards={"hazards": [point(-123.95, 40.21, "One-lane bridge")]},
                       safe_locations={"safe_locations": EDGE_SAFE})
        text = " ".join(c["text"] for c in kit["cards"])
        self.assertIn("One-lane bridge on Spur Rd", text)
        self.assertIn("US Hwy 101 (west edge)", text + " ".join(s["name"] for s in kit["safe"]))
        self.assertNotIn("Loved one", " ".join(c["title"] for c in kit["cards"]))
        self.assertIn("Where do we go?", [c["title"] for c in kit["cards"]])   # only edge points

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
        layers = gk.overlay_layers(self.kit(safe_locations={"safe_locations": EDGE_SAFE}))
        self.assertEqual(len(layers["game_grid"]["features"]), 12)
        self.assertEqual([f["properties"]["label"] for f in layers["game_safe"]["features"]], ["SAFE", "SAFE"])
        self.assertTrue(layers["game_roads"]["features"])
        self.assertTrue(layers["game_spaces"]["features"])


if __name__ == '__main__':
    unittest.main()
