"""Tests for the Google Slides request builder (no network)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import gslides

ENTRIES = [
    {'title': 'Overview', 'file': '01_overview.png', 'url': 'https://fireatlas.org/a/staging/outlets/3dview/?s=x',
     'notes': 'Look\naround.'},
    {'title': 'Water', 'file': '02_water.png', 'url': 'https://fireatlas.org/a/staging/outlets/webmap/?s=y',
     'notes': ''},
]
URL = lambda f: f"https://fireatlas.org/a/staging/outlets/slides/{f}"


def ops(reqs, name):
    return [r[name] for r in reqs if name in r]


# --- fit_image ---------------------------------------------------------------

def test_fit_image_wide_box_limits_on_height_and_centres():
    x, y, w, h = gslides.fit_image(1600, 900, 0, 0, 1000, 300)
    assert (w, h) == (533, 300) and y == 0 and x == (1000 - 533) // 2


def test_fit_image_tall_box_limits_on_width():
    x, y, w, h = gslides.fit_image(1600, 900, 10, 20, 800, 1000)
    assert (w, h) == (800, 450) and x == 10 and y == 20 + (1000 - 450) // 2


def test_view_image_stays_on_page_above_caption():
    reqs, _ = gslides.deck_requests({'title': 'T'}, ENTRIES, URL, (1600, 900), run_id='1')
    img = next(i for i in ops(reqs, 'createImage') if i['objectId'] == 'r1_v1_img')['elementProperties']
    x, y = img['transform']['translateX'], img['transform']['translateY']
    w, h = img['size']['width']['magnitude'], img['size']['height']['magnitude']
    assert x >= gslides.MARGIN and x + w <= gslides.PAGE_W - gslides.MARGIN
    assert y >= gslides.IMAGE_TOP and y + h <= gslides.PAGE_H - gslides.CAPTION_H - gslides.GAP
    assert w / h == pytest.approx(16 / 9, rel=0.01)


# --- deck_requests -----------------------------------------------------------

def test_deck_has_title_slide_then_one_per_view():
    reqs, ids = gslides.deck_requests({'title': 'T'}, ENTRIES, URL, (1600, 900), run_id='7')
    assert ids == ['r7_title', 'r7_v1', 'r7_v2']
    assert [c['objectId'] for c in ops(reqs, 'createSlide')] == ids


def test_title_slide_background_is_first_image():
    reqs, _ = gslides.deck_requests({'title': 'T'}, ENTRIES, URL, (1600, 900), run_id='1')
    bg = next(i for i in ops(reqs, 'createImage') if i['objectId'] == 'r1_title_bg')
    assert bg['url'] == URL('01_overview.png')


def test_old_slides_deleted_after_new_ones_created():
    reqs, _ = gslides.deck_requests({'title': 'T'}, ENTRIES, URL, (1600, 900), old_slide_ids=['p', 'q'], run_id='1')
    kinds = [next(iter(r)) for r in reqs]
    assert kinds[-2:] == ['deleteObject', 'deleteObject']
    assert kinds.index('deleteObject') > max(i for i, k in enumerate(kinds) if k == 'createSlide')


def test_caption_link_covers_link_text_in_utf16_units():
    entries = [{**ENTRIES[0], 'notes': 'Fire 🔥 road'}]  # the emoji is two UTF-16 units
    reqs, _ = gslides.deck_requests({'title': 'T'}, entries, URL, (1600, 900), run_id='1')
    text = next(t['text'] for t in ops(reqs, 'insertText') if t['objectId'] == 'r1_v1_cap')
    link = next(s for s in ops(reqs, 'updateTextStyle') if 'link' in s['style'])
    assert text == f"Fire 🔥 road {gslides.LINK_TEXT}"
    rng = link['textRange']
    assert rng['endIndex'] - rng['startIndex'] == len(gslides.LINK_TEXT)
    assert rng['endIndex'] == len(text) + 1
    assert link['style']['link']['url'] == ENTRIES[0]['url']


def test_object_ids_unique_and_valid():
    reqs, _ = gslides.deck_requests({'title': 'T', 'description': 'D', 'byline': 'b'}, ENTRIES, URL,
                                    (1600, 900), run_id='1700000000')
    ids = [v['objectId'] for r in reqs for k, v in r.items() if k.startswith('create')]
    assert len(ids) == len(set(ids))
    assert all(5 <= len(i) <= 50 for i in ids)


def test_notes_requests_skip_empty_notes():
    reqs = gslides.notes_requests(['n1', 'n2'], ENTRIES)
    assert reqs == [{'insertText': {'objectId': 'n1', 'insertionIndex': 0, 'text': 'Look around.'}}]
