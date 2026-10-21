"""Unit tests for zoned LRS (road mileage) helpers in eddies.py.

These exercise the per-zone segment assignment and the per-zone distance reset.
They need the real geometry stack (shapely, h3, networkx, pyproj) and the heavy
`eddies` import chain, so they are skipped where those aren't installed (e.g. the
bare local dev env) and run on the server / CI.
"""
import json
import os
import sys

import pytest

# Real geometry deps required — skip the whole module if any is missing.
pytest.importorskip("shapely")
pytest.importorskip("h3")
pytest.importorskip("networkx")
pytest.importorskip("pyproj")

from shapely.geometry import Polygon  # noqa: E402

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from eddies import (
        _assign_features_to_zones,
        _lrs_annotate,
        _load_lrs_zones,
        road_name_matcher,
    )
    import eddies
except Exception as e:  # pragma: no cover - server-only import chain
    pytest.skip(f"eddies import failed (server-only deps): {e}", allow_module_level=True)


def _line(coords):
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords}, "properties": {}}


def _zone(name, polygon, anchor_lat, anchor_lng, roads=None):
    return {"name": name, "polygon": polygon, "anchor_lat": anchor_lat, "anchor_lng": anchor_lng,
            "roads": road_name_matcher(roads), "road_names": roads}


def _named_line(name, coords):
    f = _line(coords)
    f["properties"]["name"] = name
    return f


# Two side-by-side roads, each fully inside its own zone. ~0.005 deg ≈ 555 m,
# well above the H3 res-12 cell size so endpoints land in distinct cells.
WEST = [
    _line([[-123.02, 40.000], [-123.02, 40.005]]),
    _line([[-123.02, 40.005], [-123.02, 40.010]]),
]
EAST = [
    _line([[-123.00, 40.000], [-123.00, 40.005]]),
    _line([[-123.00, 40.005], [-123.00, 40.010]]),
]

ZONE_WEST = _zone(
    "West", Polygon([(-123.03, 39.99), (-123.01, 39.99), (-123.01, 40.02), (-123.03, 40.02)]),
    40.000, -123.02,
)
ZONE_EAST = _zone(
    "East", Polygon([(-123.01, 39.99), (-122.99, 39.99), (-122.99, 40.02), (-123.01, 40.02)]),
    40.000, -123.00,
)


def test_assign_features_to_zones_by_midpoint():
    zones = [ZONE_WEST, ZONE_EAST]
    outside = _line([[-122.50, 40.000], [-122.50, 40.005]])  # midpoint in neither zone
    buckets = _assign_features_to_zones(WEST + EAST + [outside], zones)

    assert len(buckets["West"]) == 2
    assert len(buckets["East"]) == 2
    # The outside segment is dropped entirely, not assigned to either zone.
    assert sum(len(v) for v in buckets.values()) == 4


def test_zone_distance_resets_to_zero_per_anchor():
    res = 12
    west = _lrs_annotate(WEST, ZONE_WEST["anchor_lat"], ZONE_WEST["anchor_lng"], res, route_name="West")
    east = _lrs_annotate(EAST, ZONE_EAST["anchor_lat"], ZONE_EAST["anchor_lng"], res, route_name="East")

    for annotated, route in ((west, "West"), (east, "East")):
        dists = [v for f in annotated for v in (f["properties"]["m_start"], f["properties"]["m_end"]) if v is not None]
        # Each route is measured from its own anchor, so its minimum distance is ~0.
        assert min(dists) == pytest.approx(0.0, abs=1.0)
        # Two ~555 m segments end-to-end ≈ 1110 m total.
        assert max(dists) == pytest.approx(1110, rel=0.1)
        assert all(f["properties"]["route_name"] == route for f in annotated)


def test_load_lrs_zones_parses_json_anchor_and_skips_bad(monkeypatch):
    good_anchor = '{"latitude": 40.205, "longitude": -123.897}'
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[[-124, 40], [-123, 40], [-123, 41], [-124, 41], [-124, 40]]]},
                "properties": {"name": "Thomas", "anchor": good_anchor},
            },
            {  # missing anchor -> skipped
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[[-124, 40], [-123, 40], [-123, 41], [-124, 41], [-124, 40]]]},
                "properties": {"name": "NoAnchor"},
            },
            {  # unparseable anchor -> skipped
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[[-124, 40], [-123, 40], [-123, 41], [-124, 41], [-124, 40]]]},
                "properties": {"name": "BadAnchor", "anchor": "not json"},
            },
        ],
    }
    monkeypatch.setattr(eddies.dataswale, "layer_as_featurecollection", lambda config, layer: fc)

    zones = _load_lrs_zones({}, "mileage_zones")
    assert [z["name"] for z in zones] == ["Thomas"]
    assert zones[0]["anchor_lat"] == pytest.approx(40.205)
    assert zones[0]["anchor_lng"] == pytest.approx(-123.897)
    assert zones[0]["polygon"].contains(zones[0]["polygon"].representative_point())

# ── Road-name restriction ────────────────────────────────────────────────
# A zone drawn around a route also contains every driveway hanging off it, so
# the LRS has to be restricted by road name or Dijkstra measures down all of
# them (the South Fork Eel route crosses ~20 such side roads).
ZONE_BOX = Polygon([(-123.03, 39.99), (-122.99, 39.99), (-122.99, 40.02), (-123.03, 40.02)])


def test_road_name_matcher_prefix_and_empty():
    m = road_name_matcher(["Branscomb", "Redwood Highway"])
    assert m("Branscomb Road") and m("Branscomb") and m("Redwood Highway")
    assert not m("Branscomb Creek Trail Lane".replace("Branscomb", "Branscombe"))  # not a prefix word match
    assert not m("Hargus Road") and not m(None) and not m("")
    # No restriction configured: everything matches.
    assert road_name_matcher(None)("Anything") and road_name_matcher([])("Anything")


def test_road_name_matcher_accepts_comma_string():
    # What an editable_columns text field gives us.
    m = road_name_matcher("Redwood Highway, Branscomb Road ")
    assert m("Branscomb Road") and m("Redwood Highway")
    assert not m("Jack of Hearts Road")


def test_zone_roads_filter_excludes_driveways():
    route = [_named_line("Branscomb Road", [[-123.02, 40.000], [-123.02, 40.005]]),
             _named_line("Branscomb Road", [[-123.02, 40.005], [-123.02, 40.010]])]
    driveway = _named_line("Jack of Hearts Road", [[-123.02, 40.005], [-123.015, 40.006]])
    unnamed = _line([[-123.02, 40.006], [-123.016, 40.007]])

    zone = _zone("Route", ZONE_BOX, 40.000, -123.02, roads=["Branscomb"])
    buckets = _assign_features_to_zones(route + [driveway, unnamed], [zone])
    assert len(buckets["Route"]) == 2
    assert {f["properties"]["name"] for f in buckets["Route"]} == {"Branscomb Road"}

    # Without the restriction, the same polygon takes everything in it.
    open_zone = _zone("Route", ZONE_BOX, 40.000, -123.02)
    assert len(_assign_features_to_zones(route + [driveway, unnamed], [open_zone])["Route"]) == 4


def test_load_lrs_zones_reads_roads_property(monkeypatch):
    fc = {"type": "FeatureCollection", "features": [{
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [[[-124, 40], [-123, 40], [-123, 41], [-124, 41], [-124, 40]]]},
        "properties": {"name": "Route", "anchor": '{"latitude": 40.2, "longitude": -123.9}',
                       "roads": "Redwood Highway, Branscomb Road"},
    }]}
    monkeypatch.setattr(eddies.dataswale, "layer_as_featurecollection", lambda config, layer: fc)
    zone = _load_lrs_zones({}, "mileage_zones")[0]
    assert zone["road_names"] == "Redwood Highway, Branscomb Road"
    assert zone["roads"]("Branscomb Road") and not zone["roads"]("Hargus Road")


def test_zone_without_roads_property_matches_everything(monkeypatch):
    fc = {"type": "FeatureCollection", "features": [{
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [[[-124, 40], [-123, 40], [-123, 41], [-124, 41], [-124, 40]]]},
        "properties": {"name": "Route", "anchor": '{"latitude": 40.2, "longitude": -123.9}'},
    }]}
    monkeypatch.setattr(eddies.dataswale, "layer_as_featurecollection", lambda config, layer: fc)
    zone = _load_lrs_zones({}, "mileage_zones")[0]
    assert zone["roads"]("Anything At All")


# ── Several road layers in one graph ─────────────────────────────────────

def test_road_lrs_reads_in_layers_in_order(monkeypatch, tmp_path):
    """SFE splits roads into primary/secondary/tertiary; a route that leaves the
    highway for a named side road needs them in one graph."""
    layers = {
        "roads_primary": {"type": "FeatureCollection", "features": [
            _named_line("Redwood Highway", [[-123.02, 40.000], [-123.02, 40.005]])]},
        "roads_secondary": {"type": "FeatureCollection", "features": [
            _named_line("Branscomb Road", [[-123.02, 40.005], [-123.02, 40.010]])]},
        "roads_tertiary": {"type": "FeatureCollection", "features": [
            _named_line("Wilderness Lodge Road", [[-123.02, 40.010], [-123.02, 40.015]])]},
    }
    monkeypatch.setattr(eddies.dataswale, "layer_as_featurecollection",
                        lambda config, layer: layers[layer])
    monkeypatch.setattr(eddies.versioning, "atlas_path", lambda config, kind: tmp_path)
    config = {"assets": {"lrs": {"config": {
        "in_layers": ["roads_primary", "roads_secondary", "roads_tertiary"],
        "out_layer": "road_mileage",
        "lrs_anchor_coordinates": [40.000, -123.02]}}}}

    eddies.road_lrs(config, "lrs")
    out = json.loads((tmp_path / "road_mileage" / "road_mileage.geojson").read_text())
    assert len(out["features"]) == 3                       # all three layers measured
    ends = [f["properties"]["m_end"] for f in out["features"]]
    assert max(ends) == pytest.approx(1665, rel=0.1)       # three ~555 m segments, end to end
