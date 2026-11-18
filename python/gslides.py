"""Google Slides copy of a slides deck: same title slide and view slides as index.html.

The Slides API takes a list of requests per batchUpdate. Building that list is
pure (deck_requests, notes_requests) and tested locally; sync_deck does the
network part with the box's gspread service account, the one the spreadsheet
export already uses.

One deck per slides outlet. Its id is kept in `gslides.json` beside the PNGs, so
a re-run rebuilds the same deck and its link stays stable (edits made in Google
Slides are overwritten). Slides fetches each image from its public fireatlas.org
URL, so the PNGs must be served before this runs.

Units: the API positions everything in EMU (914400 per inch, 12700 per point).
"""
import json
import logging
import time

logger = logging.getLogger(__name__)

EMU_PER_IN = 914400
EMU_PER_PT = 12700
# A new presentation's default page: 10 × 5.625 in (16:9).
PAGE_W = 10 * EMU_PER_IN
PAGE_H = int(5.625 * EMU_PER_IN)

MARGIN = int(0.3 * EMU_PER_IN)
TITLE_TOP = int(0.18 * EMU_PER_IN)
TITLE_H = int(0.5 * EMU_PER_IN)
RULE_Y = int(0.7 * EMU_PER_IN)
IMAGE_TOP = int(0.8 * EMU_PER_IN)
CAPTION_H = int(0.85 * EMU_PER_IN)
GAP = int(0.1 * EMU_PER_IN)

FONT = 'IBM Plex Sans'
MONO = 'IBM Plex Mono'
# Console / fireatlas.org colours (see slides.DECK_TEMPLATE).
TEXT = '#111111'
MUTED = '#555555'
BORDER = '#d8d8d8'
BLUE = '#1d4ed8'
HERO_BG = '#0a140a'
HERO_ALPHA = 0.82
HERO_TITLE = '#d4e8c2'
HERO_TEXT = '#e8e8e0'
HERO_META = '#8aaa70'

LINK_TEXT = 'Open the live view →'

SCOPES = ['https://www.googleapis.com/auth/presentations',
          'https://www.googleapis.com/auth/drive']
SERVICE_ACCOUNT_FILE = '/root/.config/gspread/service_account.json'
SLIDES_API = 'https://slides.googleapis.com/v1/presentations'
DRIVE_API = 'https://www.googleapis.com/drive/v3/files'


# --- layout (pure) ----------------------------------------------------------------

def fit_image(img_w, img_h, box_x, box_y, box_w, box_h):
    """Largest size with the image's aspect ratio that fits the box, centred in it.

    Returns (x, y, w, h) in the box's units (EMU).
    """
    scale = min(box_w / img_w, box_h / img_h)
    w, h = int(img_w * scale), int(img_h * scale)
    return box_x + (box_w - w) // 2, box_y + (box_h - h) // 2, w, h


def _utf16_len(text):
    """Slides text indexes count UTF-16 code units, not Python characters."""
    return len(text.encode('utf-16-le')) // 2


def _rgb(hex_color):
    h = hex_color.lstrip('#')
    return {'red': int(h[0:2], 16) / 255, 'green': int(h[2:4], 16) / 255, 'blue': int(h[4:6], 16) / 255}


def _element(page_id, x, y, w, h):
    return {'pageObjectId': page_id,
            'size': {'width': {'magnitude': w, 'unit': 'EMU'}, 'height': {'magnitude': h, 'unit': 'EMU'}},
            'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': x, 'translateY': y, 'unit': 'EMU'}}


def _text_box(obj_id, page_id, x, y, w, h, text, size_pt, color, bold=False, font=FONT):
    """Create a text box, fill it and style all of its text."""
    return [
        {'createShape': {'objectId': obj_id, 'shapeType': 'TEXT_BOX',
                         'elementProperties': _element(page_id, x, y, w, h)}},
        {'insertText': {'objectId': obj_id, 'insertionIndex': 0, 'text': text}},
        {'updateTextStyle': {'objectId': obj_id, 'textRange': {'type': 'ALL'},
                             'style': {'fontFamily': font, 'fontSize': {'magnitude': size_pt, 'unit': 'PT'},
                                       'bold': bold, 'foregroundColor': {'opaqueColor': {'rgbColor': _rgb(color)}}},
                             'fields': 'fontFamily,fontSize,bold,foregroundColor'}},
    ]


def _image(obj_id, page_id, url, x, y, w, h):
    return {'createImage': {'objectId': obj_id, 'url': url, 'elementProperties': _element(page_id, x, y, w, h)}}


def _title_slide(sid, meta, first_image_url):
    reqs = [{'createSlide': {'objectId': sid, 'slideLayoutReference': {'predefinedLayout': 'BLANK'}}}]
    if first_image_url:
        reqs.append(_image(f'{sid}_bg', sid, first_image_url, 0, 0, PAGE_W, PAGE_H))
    panel_x, panel_w = int(0.5 * EMU_PER_IN), PAGE_W - EMU_PER_IN
    panel_h = int(1.7 * EMU_PER_IN)
    panel_y = PAGE_H - panel_h - int(0.55 * EMU_PER_IN)
    reqs += [
        {'createShape': {'objectId': f'{sid}_panel', 'shapeType': 'ROUND_RECTANGLE',
                         'elementProperties': _element(sid, panel_x, panel_y, panel_w, panel_h)}},
        {'updateShapeProperties': {
            'objectId': f'{sid}_panel',
            'shapeProperties': {
                'shapeBackgroundFill': {'solidFill': {'color': {'rgbColor': _rgb(HERO_BG)}, 'alpha': HERO_ALPHA}},
                'outline': {'propertyState': 'NOT_RENDERED'}},
            'fields': 'shapeBackgroundFill.solidFill.color,shapeBackgroundFill.solidFill.alpha,outline.propertyState'}},
    ]
    inner_x, inner_w = panel_x + int(0.3 * EMU_PER_IN), panel_w - int(0.6 * EMU_PER_IN)
    y = panel_y + int(0.15 * EMU_PER_IN)
    reqs += _text_box(f'{sid}_title', sid, inner_x, y, inner_w, int(0.6 * EMU_PER_IN),
                      meta.get('title') or 'Atlas views', 28, HERO_TITLE, bold=True)
    y += int(0.65 * EMU_PER_IN)
    if meta.get('description'):
        reqs += _text_box(f'{sid}_desc', sid, inner_x, y, inner_w, int(0.5 * EMU_PER_IN),
                          meta['description'], 13, HERO_TEXT)
    if meta.get('byline'):
        reqs += _text_box(f'{sid}_meta', sid, inner_x, panel_y + panel_h - int(0.45 * EMU_PER_IN),
                          inner_w, int(0.3 * EMU_PER_IN), meta['byline'], 9, HERO_META, font=MONO)
    return reqs


def _view_slide(sid, entry, image_url, image_size):
    content_w = PAGE_W - 2 * MARGIN
    reqs = [{'createSlide': {'objectId': sid, 'slideLayoutReference': {'predefinedLayout': 'BLANK'}}}]
    reqs += _text_box(f'{sid}_title', sid, MARGIN, TITLE_TOP, content_w, TITLE_H,
                      entry['title'], 20, TEXT, bold=True)
    reqs += [
        {'createLine': {'objectId': f'{sid}_rule', 'category': 'STRAIGHT',
                        'elementProperties': _element(sid, MARGIN, RULE_Y, content_w, 0)}},
        {'updateLineProperties': {'objectId': f'{sid}_rule',
                                  'lineProperties': {'lineFill': {'solidFill': {'color': {'rgbColor': _rgb(BORDER)}}},
                                                     'weight': {'magnitude': 1, 'unit': 'PT'}},
                                  'fields': 'lineFill.solidFill.color,weight'}},
    ]
    caption_y = PAGE_H - CAPTION_H - GAP
    x, y, w, h = fit_image(*image_size, MARGIN, IMAGE_TOP, content_w, caption_y - GAP - IMAGE_TOP)
    reqs.append(_image(f'{sid}_img', sid, image_url, x, y, w, h))

    notes = ' '.join((entry.get('notes') or '').split())
    link = entry.get('url')
    caption = ' '.join(t for t in (notes, LINK_TEXT if link else '') if t)
    if caption:
        reqs += _text_box(f'{sid}_cap', sid, MARGIN, caption_y, content_w, CAPTION_H, caption, 11, MUTED)
        if link:
            start = _utf16_len(caption) - _utf16_len(LINK_TEXT)
            reqs.append({'updateTextStyle': {
                'objectId': f'{sid}_cap',
                'textRange': {'type': 'FIXED_RANGE', 'startIndex': start, 'endIndex': _utf16_len(caption)},
                'style': {'link': {'url': link}, 'foregroundColor': {'opaqueColor': {'rgbColor': _rgb(BLUE)}}},
                'fields': 'link,foregroundColor'}})
    return reqs


def deck_requests(meta, entries, image_url, image_size, old_slide_ids=(), run_id=None):
    """Requests that replace a deck's slides with a title slide and one per entry.

    image_url: file name → public URL. image_size: (w, h) of the PNGs, any units.
    New slides are created before the old ones are deleted, in one batch, so the
    deck is never empty. run_id keeps new object ids clear of the old ones.
    Returns (requests, slide_ids) with slide_ids in deck order.
    """
    run = run_id or str(int(time.time()))
    title_id = f'r{run}_title'
    first = image_url(entries[0]['file']) if entries else None
    reqs = _title_slide(title_id, meta, first)
    slide_ids = [title_id]
    for i, entry in enumerate(entries, start=1):
        sid = f'r{run}_v{i}'
        reqs += _view_slide(sid, entry, image_url(entry['file']), image_size)
        slide_ids.append(sid)
    reqs += [{'deleteObject': {'objectId': old}} for old in old_slide_ids]
    return reqs, slide_ids


def notes_requests(notes_ids, entries):
    """Put each view's paragraph in its speaker notes.

    notes_ids: speaker-notes shape ids for the view slides, in deck order (only
    known once the slides exist, hence a second batch).
    """
    reqs = []
    for notes_id, entry in zip(notes_ids, entries):
        text = ' '.join((entry.get('notes') or '').split())
        if text:
            reqs.append({'insertText': {'objectId': notes_id, 'insertionIndex': 0, 'text': text}})
    return reqs


# --- network ----------------------------------------------------------------------

def _session():
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account
    creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return AuthorizedSession(creds)


def _check(resp, what):
    if not resp.ok:
        raise RuntimeError(f"gslides: {what} failed: HTTP {resp.status_code} {resp.text[:400]}")
    return resp.json() if resp.content else {}


def _create_and_share(session, title, writers):
    pres = _check(session.post(SLIDES_API, json={'title': title}), 'create presentation')
    pid = pres['presentationId']
    for email in writers:
        _check(session.post(f"{DRIVE_API}/{pid}/permissions",
                            json={'type': 'user', 'role': 'writer', 'emailAddress': email}),
               f'share with a writer')
    _check(session.post(f"{DRIVE_API}/{pid}/permissions", json={'type': 'anyone', 'role': 'reader'}),
           'share with anyone with the link')
    logger.info(f"gslides: created deck {pid}, shared with {len(writers)} writer(s) and anyone with the link")
    return pid


def sync_deck(out_dir, meta, entries, image_base_url, image_size, writers):
    """Create or rebuild the outlet's Google Slides deck. Returns its URL."""
    session = _session()
    state_path = out_dir / 'gslides.json'
    pid = None
    if state_path.exists():
        pid = json.loads(state_path.read_text()).get('presentation_id')
    pres = None
    if pid:
        resp = session.get(f"{SLIDES_API}/{pid}")
        if resp.ok:
            pres = resp.json()
        else:
            logger.warning(f"gslides: stored deck {pid} not readable (HTTP {resp.status_code}); creating a new one")
    if pres is None:
        pid = _create_and_share(session, f"{meta.get('title') or 'Atlas views'} — slides", writers)
        pres = _check(session.get(f"{SLIDES_API}/{pid}"), 'read new presentation')
        state_path.write_text(json.dumps({'presentation_id': pid}, indent=2))

    # A cache-buster makes Slides fetch this run's PNGs, not a copy it fetched before.
    stamp = str(int(time.time()))
    reqs, slide_ids = deck_requests(
        meta, entries, lambda f: f"{image_base_url}/{f}?v={stamp}", image_size,
        old_slide_ids=[s['objectId'] for s in pres.get('slides', [])], run_id=stamp)
    _check(session.post(f"{SLIDES_API}/{pid}:batchUpdate", json={'requests': reqs}), 'build slides')

    pres = _check(session.get(f"{SLIDES_API}/{pid}"), 'read built presentation')
    notes_by_slide = {s['objectId']: s['slideProperties']['notesPage']['notesProperties']['speakerNotesObjectId']
                      for s in pres['slides']}
    notes_reqs = notes_requests([notes_by_slide[s] for s in slide_ids[1:]], entries)
    if notes_reqs:
        _check(session.post(f"{SLIDES_API}/{pid}:batchUpdate", json={'requests': notes_reqs}), 'speaker notes')

    url = f"https://docs.google.com/presentation/d/{pid}/edit"
    state_path.write_text(json.dumps({'presentation_id': pid, 'url': url}, indent=2))
    logger.info(f"gslides: {len(slide_ids)} slides at {url}")
    return url
