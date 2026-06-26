"""Unit tests for zoned LRS (road mileage) helpers in eddies.py.

These exercise the per-zone segment assignment and the per-zone distance reset.
They need the real geometry stack (shapely, h3, networkx, pyproj) and the heavy
`eddies` import chain, so they are skipped where those aren't installed (e.g. the
bare local dev env) and run on the server / CI.
"""
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
    )
    import eddies
except Exception as e:  # pragma: no cover - server-only import chain
    pytest.skip(f"eddies import failed (server-only deps): {e}", allow_module_level=True)


def _line(coords):
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords}, "properties": {}}


def _zone(name, polygon, anchor_lat, anchor_lng):
    return {"name": name, "polygon": polygon, "anchor_lat": anchor_lat, "anchor_lng": anchor_lng}


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
