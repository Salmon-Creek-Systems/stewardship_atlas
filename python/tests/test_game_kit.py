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


def classed(field, value, **extra):
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
            "properties": {field: value, **extra}}


class TestRoadConfig(unittest.TestCase):

    def test_road_passes(self):
        self.assertTrue(gk.road_passes("x", None))
        self.assertTrue(gk.road_passes("a", {"field": "f", "keep": ["a"]}))
        self.assertFalse(gk.road_passes("b", {"field": "f", "keep": ["a"]}))
        self.assertFalse(gk.road_passes(None, {"field": "f", "keep": ["a"]}))
        self.assertFalse(gk.road_passes("path", {"field": "f", "drop": ["path"]}))
        self.assertTrue(gk.road_passes(None, {"field": "f", "drop": ["path"]}))

    def test_class_field_is_detected_for_each_source(self):
        for field, value, main in (("FUNCTIONAL", "Private", "Principal Arterials"),
                                   ("class", "service", "tertiary"),
                                   ("highway", "track", "primary")):
            opts = gk.road_options([classed(field, None), classed(field, value)], {})
            self.assertEqual(opts["road_filter"]["field"], field)
            self.assertIn("footway", opts["road_filter"]["drop"])
            self.assertIn(main, opts["main_roads"])

    def test_unknown_fields_keep_every_road(self):
        opts = gk.road_options([classed("kind", "x")], {})
        self.assertIsNone(opts["road_filter"])
        self.assertEqual(opts["main_roads"], [])

    def test_configured_values_win(self):
        configured = {"field": "tier", "keep": ["1"]}
        opts = gk.road_options([classed("class", "service")],
                               {"road_filter": configured, "main_roads": ["1"], "name_field": "NM"})
        self.assertEqual(opts, {"name_field": "NM", "road_filter": configured, "main_roads": ["1"]})

    def test_name_field_comes_from_the_webmap_label(self):
        self.assertEqual(gk.road_options([], {}, {"label_field": "FULLNAME"})["name_field"], "FULLNAME")
        self.assertEqual(gk.road_options([], {}, {})["name_field"], "name")

    def test_apply_name_field(self):
        roads = [classed("FULLNAME", "Thomas Rd")]
        gk.apply_name_field(roads, "FULLNAME")
        self.assertEqual(gk.feature_name(roads[0]), "Thomas Rd")

    def test_roles_fill_in_around_configured_ones(self):
        roles = gk.resolve_roles({"landmarks": ["my_places"]}, {"roads", "buildings", "poi", "my_places"})
        self.assertEqual(roles, {"landmarks": ["my_places"], "roads": ["roads"], "residences": ["buildings"]})

    def test_base_layers_use_one_basemap(self):
        self.assertEqual(gk.default_base_layers({"basemap", "lidar_basemap", "creeks"}), ["lidar_basemap", "creeks"])
        self.assertEqual(gk.default_base_layers({"roads"}), [])


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

    def test_stranded_roads_are_split_out_for_red(self):
        island = road([[-123.99, 40.17], [-123.966, 40.17]], "Island Rd")
        kit = self.kit(roads=(HIGHWAY, island), safe_locations={"safe_locations": EDGE_SAFE})
        layers = gk.overlay_layers(kit)
        self.assertEqual([f["properties"]["name"] for f in layers["game_roads_stranded"]["features"]], ["Island Rd"])
        self.assertEqual(len(layers["game_spaces_stranded"]["features"]), kit["stranded_spaces"])
        self.assertNotIn("Island Rd", [f["properties"]["name"] for f in layers["game_roads"]["features"]])
        self.assertIn("Island Rd", next(c for c in kit["readiness"] if c["key"] == "way_out")["detail"])

    def test_no_roads_is_reported_and_still_builds(self):
        kit = self.kit(roads=())
        self.assertEqual(self.status(kit)["roads"], "missing")
        self.assertFalse(gk.ready(kit))
        self.assertTrue(gk.index_html(kit, board_ok=False))

    def test_main_network_with_one_way_out_is_called_out(self):
        kit = self.kit(safe_locations={"safe_locations": EDGE_SAFE[:1]})
        check = next(c for c in kit["readiness"] if c["key"] == "main_exits")
        self.assertEqual(check["status"], "warn")
        self.assertIn("only one way out: US Hwy 101 (west edge)", check["detail"])

    def test_main_network_with_two_ways_out_is_fine(self):
        self.assertEqual(self.status(self.kit(safe_locations={"safe_locations": EDGE_SAFE}))["main_exits"], "ok")

    def test_chokepoints_named_and_on_the_page(self):
        role_layers = {"roads": {"roads": [HIGHWAY, SPUR]}, "residences": {"buildings": [point(-123.95, 40.228, "h")]},
                       "safe_locations": {"safe_locations": EDGE_SAFE}}
        parts = [{"rid": i, "coords": f["geometry"]["coordinates"], "by_class": True, "serves": {"residences"}}
                 for i, f in enumerate([HIGHWAY, SPUR])]
        board = gb.build_board(parts, BBOX, gk.safe_points(EDGE_SAFE), step_m=500,
                               residence_points=[[-123.95, 40.228]])
        kit = gk.build_kit("test", BBOX, role_layers, board, {})
        self.assertEqual(kit["chokepoints"][0]["name"], "Spur Rd")
        self.assertEqual(self.status(kit)["chokepoints"], "warn")
        self.assertIn("Single points of failure", gk.index_html(kit, board_ok=True))

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
