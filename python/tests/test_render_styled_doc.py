"""
Test site_assets.render_styled_doc — markdown docs rendered to styled HTML with
intra-doc .md->.html link rewriting and header anchors.

Needs markdown (skips where it isn't installed), but no longer drags in
outlets' import chain: the renderer moved to site_assets when the site's web
assets stopped being a per-atlas materialize side effect (#181).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

pytest.importorskip("markdown")
import site_assets

TEMPLATE = "<html><head><title>{title}</title></head><body>{content}</body></html>"


def render(md, **kwargs):
    return site_assets.render_styled_doc(md, TEMPLATE, **kwargs)


def test_title_from_first_heading():
    html = render("# My Manual\n\nHello.")
    assert "<title>My Manual</title>" in html


def test_explicit_title_wins():
    assert "<title>Help Index</title>" in render("# Ignored", title="Help Index")


def test_page_carries_no_atlas_identity():
    # One published copy serves every atlas, so nothing atlas-specific may be
    # baked in — that was the last-writer-wins bug this replaced.
    html = render("# About\n\ntext")
    assert "{atlas_name}" not in html
    assert "base_url" not in html


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
