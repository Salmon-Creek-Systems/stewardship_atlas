"""
Test outlets.render_styled_doc — markdown docs rendered to styled HTML with
intra-doc .md->.html link rewriting and header anchors.

Requires markdown + outlets' import chain (duckdb/geopandas/etc.), so it
skips where those aren't installed (runs on the server).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

pytest.importorskip("markdown")
outlets = pytest.importorskip("outlets")

TEMPLATE = "<html><head><title>{title}</title></head><body>{atlas_name}|{base_url}|{content}</body></html>"
CONFIG = {"name": "testatlas", "base_url": "https://fireatlas.org/testatlas"}


def render(md):
    return outlets.render_styled_doc(md, TEMPLATE, CONFIG)


def test_title_from_first_heading_and_template_fields():
    html = render("# My Manual\n\nHello.")
    assert "<title>My Manual</title>" in html
    assert "testatlas|https://fireatlas.org/testatlas|" in html


def test_relative_md_links_rewritten_to_html():
    html = render("See [webmap](help/webmap_help.md) and [admin](admin_manual.md).")
    assert 'href="help/webmap_help.html"' in html
    assert 'href="admin_manual.html"' in html
    assert '.md"' not in html


def test_absolute_and_anchor_links_untouched():
    html = render("[ext](https://example.org/a.md) and [sec](#where-to-get-more-help)")
    assert 'href="https://example.org/a.md"' in html
    assert 'href="#where-to-get-more-help"' in html


def test_md_anchor_link_rewritten_preserving_fragment():
    html = render("[x](help/foo.md#step-2)")
    assert 'href="help/foo.html#step-2"' in html


def test_header_anchors_generated_for_in_page_links():
    # toc extension slugifies headers to ids matching '#where-to-get-more-help'
    html = render("## Where to Get More Help\n\ntext")
    assert 'id="where-to-get-more-help"' in html
