"""Tests for map_trace: pulling colored outlines out of a map image.

The mask steps need only numpy. Tracing needs shapely + rasterio (the box has
both) and is skipped where they are missing.
"""
import sys
from pathlib import Path

import pytest

np = pytest.importorskip('numpy')
sys.path.insert(0, str(Path(__file__).parent.parent))
import map_trace

RED = (230, 0, 0)


def blank(h=200, w=200, bg=(120, 60, 50)):
    """A maroon background, the sort of slope ramp red lines are drawn on."""
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = bg
    return img


def draw_rect_outline(img, top, left, bottom, right, width, color=RED):
    img[top:top + width, left:right] = color
    img[bottom - width:bottom, left:right] = color
    img[top:bottom, left:left + width] = color
    img[top:bottom, right - width:right] = color
    return img


# --- color_mask ------------------------------------------------------------

def test_color_mask_matches_near_colors_only():
    img = blank(4, 4)
    img[0, 0] = (230, 0, 0)
    img[0, 1] = (250, 30, 20)      # JPEG-shifted red: in
    img[0, 2] = (150, 40, 40)      # dark maroon: out
    m = map_trace.color_mask(img, RED, tolerance=60)
    assert m[0, 0] and m[0, 1] and not m[0, 2]
    assert m.sum() == 2


def test_color_mask_accepts_band_first_arrays():
    img = blank(5, 7)
    img[2, 3] = RED
    band_first = np.moveaxis(img, -1, 0)   # (3, H, W), as GDAL reads it
    assert map_trace.color_mask(band_first, RED).shape == (5, 7)
    assert map_trace.color_mask(band_first, RED)[2, 3]


def test_color_mask_rejects_2d():
    with pytest.raises(ValueError):
        map_trace.color_mask(np.zeros((4, 4)), RED)


# --- close_mask ------------------------------------------------------------

def test_close_mask_bridges_a_small_gap():
    m = np.zeros((10, 20), dtype=bool)
    m[5, 2:9] = True
    m[5, 11:18] = True              # 2-pixel gap at 9, 10
    closed = map_trace.close_mask(m, radius=2)
    assert closed[5, 9] and closed[5, 10]


def test_close_mask_does_not_grow_a_shape():
    m = np.zeros((20, 20), dtype=bool)
    m[5:15, 5:15] = True
    assert (map_trace.close_mask(m, radius=2) == m).all()


def test_close_mask_radius_zero_is_identity():
    m = np.random.default_rng(0).random((8, 8)) > 0.5
    assert (map_trace.close_mask(m, radius=0) == m).all()


# --- trace_polygons (needs shapely + rasterio) -----------------------------

def _trace(img, **kw):
    pytest.importorskip('shapely')
    pytest.importorskip('rasterio')
    from affine import Affine
    mask = map_trace.color_mask(img, RED)
    # 1 m pixels, north-up, origin at (1000, 2000)
    return map_trace.trace_polygons(mask, Affine(1, 0, 1000, 0, -1, 2000), **kw)


def test_trace_rectangle_outline_follows_line_centre():
    img = draw_rect_outline(blank(), 20, 30, 120, 170, width=6)
    polys, skipped = _trace(img, min_area_m2=100, simplify_m=0)
    assert len(polys) == 1 and not skipped
    p = polys[0]
    assert p['width_m'] == pytest.approx(6, abs=0.5)
    # Centreline rectangle: (170-30-6) × (120-20-6) = 134 × 94
    assert p['geometry'].area == pytest.approx(134 * 94, rel=0.03)
    minx, miny, maxx, maxy = p['geometry'].bounds
    assert minx == pytest.approx(1000 + 33, abs=1) and maxy == pytest.approx(2000 - 23, abs=1)


def test_trace_two_outlines_largest_first():
    img = blank(300, 300)
    draw_rect_outline(img, 10, 10, 60, 60, width=4)
    draw_rect_outline(img, 100, 100, 280, 280, width=4)
    polys, _ = _trace(img, min_area_m2=100)
    assert len(polys) == 2
    assert polys[0]['geometry'].area > polys[1]['geometry'].area


def test_trace_drops_small_outlines_like_a_legend_swatch():
    img = blank(300, 300)
    draw_rect_outline(img, 10, 10, 30, 40, width=3)       # swatch-sized
    draw_rect_outline(img, 100, 100, 280, 280, width=4)
    polys, _ = _trace(img, min_area_m2=1000)
    assert len(polys) == 1


def test_trace_reports_an_open_line():
    img = blank()
    img[50:56, 20:180] = RED                               # a line, not a ring
    polys, skipped = _trace(img, min_area_m2=100)
    assert polys == [] and len(skipped) == 1 and 'open line' in skipped[0]


def test_trace_closes_a_small_break_in_an_outline():
    img = draw_rect_outline(blank(), 20, 30, 120, 170, width=6)
    img[20:26, 100:102] = (120, 60, 50)                    # 2-px break in the top edge
    pytest.importorskip('shapely')
    pytest.importorskip('rasterio')
    from affine import Affine
    mask = map_trace.close_mask(map_trace.color_mask(img, RED), radius=2)
    polys, _ = map_trace.trace_polygons(mask, Affine(1, 0, 0, 0, -1, 0), min_area_m2=100)
    assert len(polys) == 1


def test_fixed_width_overrides_measurement():
    img = draw_rect_outline(blank(), 20, 30, 120, 170, width=6)
    polys, _ = _trace(img, min_area_m2=100, width_m=0)
    assert polys[0]['width_m'] == 0
    assert polys[0]['geometry'].area == pytest.approx(140 * 100, rel=0.03)  # outer edge


def test_acres():
    pytest.importorskip('shapely')
    from shapely.geometry import box
    assert map_trace.acres(box(0, 0, 4046.8564224, 1)) == 1.0
