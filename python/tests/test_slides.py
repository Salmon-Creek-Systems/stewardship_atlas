"""Tests for the slides outlet's pure planning helpers (no browser needed)."""
import sys
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import slides

BASE = "https://fireatlas.org/scvfd"
S = "eyJhIjozOS44LCJvIjotMTIzLjYsInoiOjE1LCJsIjpbImNyZWVrcyJdfQ=="  # contains '='
WEBMAP_URL = f"{BASE}/staging/outlets/webmap/?s=eyJh%2BIjo%3D"
URL_3D = "https://fireatlas.org/sfe/staging/outlets/3dview/?s=abc"
PUBLIC = "https://next.fireatlas.org"
URL_3D_PUBLISHED = f"{PUBLIC}/sfe/current/outlets/3dview/?s=abc"


def test_view_kind_from_path():
    assert slides.view_kind(WEBMAP_URL) == 'webmap'
    assert slides.view_kind(URL_3D) == '3dview'
    assert slides.view_kind(f"{BASE}/CURRENT/outlets/3dview/index.html?s=x") == '3dview'


def test_bare_url_adds_chrome_and_keeps_state():
    out = slides.bare_url(WEBMAP_URL)
    q = parse_qs(urlsplit(out).query)
    assert q['chrome'] == ['0']
    # '+' and '=' in the base64 state survive the round trip
    assert q['s'] == ['eyJh+Ijo=']


def test_bare_url_replaces_existing_chrome():
    q = parse_qs(urlsplit(slides.bare_url(URL_3D + "&chrome=1")).query)
    assert q['chrome'] == ['0']


def test_view_url_prefers_url_and_maps_it_to_current():
    assert slides.view_url({'url': URL_3D, 's': S}, PUBLIC, 'scvfd') == URL_3D_PUBLISHED


def test_view_url_from_bare_state():
    url = slides.view_url({'s': S}, PUBLIC, 'scvfd')
    assert url.startswith(f"{PUBLIC}/scvfd/current/outlets/webmap/?s=")
    assert parse_qs(urlsplit(url).query)['s'] == [S]
    assert '/outlets/3dview/' in slides.view_url({'s': S, 'view': '3dview'}, PUBLIC, 'scvfd')


@pytest.mark.parametrize('url', [
    "https://fireatlas.org/scvfd/staging/outlets/webmap/?s=x",
    "https://fireatlas.org/scvfd/CURRENT/outlets/webmap/?s=x",
    "https://fireatlas.org/scvfd/2026-10-01_12-00-00/outlets/webmap/?s=x",
    "https://next.fireatlas.org/scvfd/current/outlets/webmap/?s=x",
])
def test_published_url_maps_every_version_shape_to_current(url):
    """Slides render the last official version, never work in progress."""
    assert slides.published_url(url, PUBLIC) == f"{PUBLIC}/scvfd/current/outlets/webmap/?s=x"


def test_published_url_keeps_a_path_prefix_on_the_public_base():
    out = slides.published_url("https://fireatlas.org/scvfd/staging/outlets/3dview/index.html?s=y",
                               "https://d1.cloudfront.net/prefix/")
    assert out == "https://d1.cloudfront.net/prefix/scvfd/current/outlets/3dview/index.html?s=y"


def test_published_url_rejects_non_outlet_links():
    with pytest.raises(ValueError, match='outlet'):
        slides.published_url("https://fireatlas.org/about/", PUBLIC)


@pytest.mark.parametrize('view, base, atlas, msg', [
    ({}, PUBLIC, 'scvfd', 'url'),
    ({'s': S, 'view': 'runbook'}, PUBLIC, 'scvfd', 'view must be'),
    ({'s': S}, '', 'scvfd', 'public base'),
    ({'s': S}, PUBLIC, '', 'public base'),
])
def test_view_url_errors(view, base, atlas, msg):
    with pytest.raises(ValueError, match=msg):
        slides.view_url(view, base, atlas)


def test_slug():
    assert slides.slug("Hargus Road — treatments (2026)") == 'hargus_road_treatments_2026'
    assert slides.slug("") == 'view'
    assert slides.slug("!!!") == 'view'
    assert len(slides.slug("x" * 100)) == 40


def test_plan_slides_orders_and_names():
    plan = slides.plan_slides([
        {'title': 'Overview', 'url': URL_3D, 'notes': 'start here'},
        {'s': S},
    ], PUBLIC, 'scvfd')
    assert [p['file'] for p in plan] == ['01_overview.png', '02_view_2.png']
    assert plan[0]['kind'] == '3dview' and plan[1]['kind'] == 'webmap'
    assert plan[0]['notes'] == 'start here'
    assert plan[1]['title'] == 'View 2'
    assert all('chrome=0' in p['render_url'] for p in plan)
    assert plan[0]['url'] == URL_3D_PUBLISHED  # the shareable link, without chrome=0


def test_plan_slides_pads_for_large_decks():
    plan = slides.plan_slides([{'url': URL_3D}] * 120, PUBLIC)
    assert plan[0]['file'] == '001_view_1.png'
    assert plan[-1]['file'] == '120_view_120.png'


def test_plan_slides_requires_views():
    with pytest.raises(ValueError, match='views'):
        slides.plan_slides([], PUBLIC)
    with pytest.raises(ValueError, match='views'):
        slides.plan_slides(None, PUBLIC)


def test_render_options_defaults_and_overrides():
    assert slides.render_options({}) == {'width': 1600, 'height': 900, 'scale': 2.0}
    assert slides.render_options({'size': [1280, 720], 'scale': 1}) == \
        {'width': 1280, 'height': 720, 'scale': 1.0}


# --- tour_views --------------------------------------------------------------

TOUR = """---
title: Test Tour
description: has a [link](https://fireatlas.org/x/outlets/webmap/?s=front) in front matter
---

Intro paragraph with an [outside link](https://example.com/page).

First we look around.
It continues here:

[3D View](https://fireatlas.org/scvfd/staging/outlets/3dview/?s=AAA=)

Water is important.
[Water Sources](https://fireatlas.org/scvfd/staging/outlets/webmap/?s=BBB)

[No Text Above](https://fireatlas.org/scvfd/staging/outlets/webmap/?s=CCC)
"""


def test_tour_views_titles_urls_in_order():
    views = slides.tour_views(TOUR)
    assert [v['title'] for v in views] == ['3D View', 'Water Sources', 'No Text Above']
    assert views[0]['url'].endswith('/3dview/?s=AAA=')


def test_tour_views_notes_from_previous_paragraph():
    views = slides.tour_views(TOUR)
    assert views[0]['notes'] == 'First we look around. It continues here:'


def test_tour_views_notes_from_same_paragraph():
    assert slides.tour_views(TOUR)[1]['notes'] == 'Water is important.'


def test_tour_views_link_alone_takes_previous_paragraph_prose():
    # The paragraph above also holds a link; only its prose becomes the notes.
    assert slides.tour_views(TOUR)[2]['notes'] == 'Water is important.'


def test_tour_views_skips_front_matter_and_non_outlet_links():
    urls = [v['url'] for v in slides.tour_views(TOUR)]
    assert not any('front' in u or 'example.com' in u for u in urls)


def test_tour_views_feed_plan_slides():
    plan = slides.plan_slides(slides.tour_views(TOUR), PUBLIC)
    assert [p['kind'] for p in plan] == ['3dview', 'webmap', 'webmap']
    assert plan[1]['file'] == '02_water_sources.png'


# --- tour_meta and the deck ----------------------------------------------------

def test_tour_meta_reads_front_matter():
    meta = slides.tour_meta(TOUR)
    assert meta['title'] == 'Test Tour'
    assert meta['description'].startswith('has a [link]')
    assert slides.tour_meta("no front matter\n") == {}


ENTRIES = [
    {'title': 'Overview', 'file': '01_overview.png', 'url': URL_3D, 'notes': 'Look\naround.'},
    {'title': 'Water', 'file': '02_water.png', 'url': WEBMAP_URL, 'notes': ''},
]


def test_deck_markdown_title_slide_then_one_per_view():
    md = slides.deck_markdown({'title': 'Tour', 'description': 'About it'}, ENTRIES)
    parts = md.split('\n---\n')
    assert len(parts) == 3
    assert '# Tour' in parts[0] and 'About it' in parts[0]
    assert 'background-image: url(01_overview.png)' in parts[0]
    assert '## Overview' in parts[1] and '](01_overview.png)' in parts[1]
    assert 'Look around.' in parts[1]                     # notes kept on one line
    assert f'[Open the live view →]({URL_3D})' in parts[1]


def test_deck_markdown_notes_cannot_start_a_new_slide():
    md = slides.deck_markdown({'title': 'T'}, [{**ENTRIES[0], 'notes': '--- not a break'}])
    assert len(md.split('\n---\n')) == 2


def test_deck_html_escapes_into_the_textarea():
    page = slides.deck_html({'title': 'A & B <x>'}, ENTRIES)
    assert '<title>A &amp; B &lt;x&gt;</title>' in page
    assert '# A &amp; B &lt;x&gt;' in page
    assert slides.REMARK_JS in page and '__' not in page.replace('__TITLE__', '')
