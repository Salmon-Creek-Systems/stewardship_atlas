"""Unit tests for the QGIS color-ramp expression builder in utils.

Pure string-building — no QGIS needed. utils pulls in gspread/geojson at import,
so those are stubbed (same pattern as test_eddies).
"""
import os
import sys
from unittest.mock import MagicMock

for _mod in ('gspread', 'geojson'):
    sys.modules.setdefault(_mod, MagicMock())

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils import build_qgis_color_expression, hex_to_rgb

WEBMAP_STOPS = [
    [0, "#00c800"], [5000, "#ffff00"], [9000, "#ffa500"],
    [13000, "#c80000"], [14000, "#888888"],
]
VALUE = 'min(coalesce("m_start", 14000), coalesce("m_end", 14000))'


def test_hex_to_rgb():
    assert hex_to_rgb("#00c800") == (0, 200, 0)
    assert hex_to_rgb("888888") == (136, 136, 136)


def test_expression_structure():
    expr = build_qgis_color_expression(VALUE, WEBMAP_STOPS)
    assert expr.startswith("CASE")
    assert expr.rstrip().endswith("END")
    # First stop is a flat color; subsequent stops interpolate.
    assert "WHEN (min(coalesce" in expr
    assert "color_rgb(0, 200, 0)" in expr          # first stop flat
    assert "ELSE color_rgb(136, 136, 136)" in expr  # clamp above last stop
    # One flat WHEN + one interpolated WHEN per gap between stops.
    assert expr.count("WHEN ") == len(WEBMAP_STOPS)
    assert expr.count("scale_linear") == 3 * (len(WEBMAP_STOPS) - 1)  # 3 channels per gap


def test_stops_sorted_regardless_of_input_order():
    shuffled = [WEBMAP_STOPS[2], WEBMAP_STOPS[0], WEBMAP_STOPS[4], WEBMAP_STOPS[1], WEBMAP_STOPS[3]]
    assert build_qgis_color_expression(VALUE, shuffled) == build_qgis_color_expression(VALUE, WEBMAP_STOPS)


def test_channels_use_integer_conversion():
    # color_rgb wants ints; scale_linear yields floats, so each channel is wrapped.
    expr = build_qgis_color_expression(VALUE, WEBMAP_STOPS)
    assert "to_int(scale_linear(" in expr
