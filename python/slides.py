"""Pure helpers for the slides outlet: which pages to render and what to call them.

A slide is one pre-configured map view, given as either a share URL (pasted from
the webmap's or 3D view's Share button) or a bare `s` state blob. The renderer
(`outlets_slides.py`) loads each page headless and screenshots it; nothing in
this module needs a browser, so it is tested locally.

Asset config:
    "slides": {"type": "outlet", "config_def": "slides",
               "size": [1600, 900], "scale": 2,
               "views": [{"title": "Hargus Road", "url": "https://…/webmap/?s=…"},
                         {"title": "Overview", "s": "eyJh…", "view": "3dview"}]}

or, instead of `views`, `"tour": "scvfd"` to take the views from that atlas tour
(Salmon-Creek-Systems.github.io `_tours/scvfd.md`; see tour_views).
"""
import html
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

VIEW_KINDS = ('webmap', '3dview')
DEFAULT_SIZE = (1600, 900)
DEFAULT_SCALE = 2


def view_kind(url):
    """'3dview' or 'webmap', from the outlet directory named in the URL's path."""
    parts = [p for p in urlsplit(url).path.split('/') if p]
    return '3dview' if '3dview' in parts else 'webmap'


def bare_url(url):
    """The same page with chrome=0 added, which hides every panel and control."""
    scheme, netloc, path, query, fragment = urlsplit(url)
    params = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if k != 'chrome']
    params.append(('chrome', '0'))
    return urlunsplit((scheme, netloc, path, urlencode(params), fragment))


def view_url(view, base_url):
    """The page a view renders from: its `url`, or the staging outlet for a bare `s`."""
    if view.get('url'):
        return view['url']
    if view.get('s'):
        kind = view.get('view', 'webmap')
        if kind not in VIEW_KINDS:
            raise ValueError(f"view must be one of {VIEW_KINDS}, not {kind!r}")
        if not base_url:
            raise ValueError("a view given as a bare `s` needs base_url in the atlas config")
        return f"{base_url}/staging/outlets/{kind}/?{urlencode({'s': view['s']})}"
    raise ValueError("each view needs a `url` or an `s`")


def slug(text, max_len=40):
    """A filename-safe lower-case slug; 'view' if nothing usable is left."""
    s = re.sub(r'[^a-z0-9]+', '_', (text or '').lower()).strip('_')
    return s[:max_len].rstrip('_') or 'view'


TOUR_RAW_URL = ("https://raw.githubusercontent.com/Salmon-Creek-Systems/"
                "Salmon-Creek-Systems.github.io/main/_tours/{name}.md")
_LINK = re.compile(r'\[([^\]]+)\]\((https?://[^)\s]+)\)')


def _strip_front_matter(text):
    if text.startswith('---'):
        end = text.find('\n---', 3)
        if end != -1:
            return text[end + 4:]
    return text


def tour_meta(markdown):
    """`title` and `description` (any `key: value` lines) from a tour's front matter."""
    meta = {}
    if markdown.startswith('---'):
        end = markdown.find('\n---', 3)
        for line in markdown[3:end if end != -1 else 0].splitlines():
            key, sep, value = line.partition(':')
            if sep and key.strip():
                meta[key.strip()] = value.strip().strip('"\'')
    return meta


def tour_views(markdown):
    """Views from a tour: one per markdown link to an atlas outlet page.

    The link text is the title. The notes are the paragraph the link sits in
    (the lines above it, up to a blank line); if the link starts its own
    paragraph, the previous paragraph. Links that aren't to an outlet page
    (no `/outlets/` in the path) are ignored.
    """
    blocks = [b.splitlines() for b in re.split(r'\n\s*\n', _strip_front_matter(markdown))]
    views, prev_text = [], ''
    for lines in blocks:
        text_above = []
        for line in lines:
            links = [m for m in _LINK.finditer(line) if '/outlets/' in urlsplit(m.group(2)).path]
            if not links:
                text_above.append(line.strip())
                continue
            notes = ' '.join(t for t in text_above if t) or prev_text
            for m in links:
                views.append({'title': m.group(1).strip(), 'url': m.group(2), 'notes': notes})
        prev_text = ' '.join(l.strip() for l in lines if l.strip() and not _LINK.search(l))
    return views


def plan_slides(views, base_url):
    """One entry per view, in order: title, file name, the page to render and its kind.

    Files are numbered so they sort in deck order: 01_hargus_road.png, …
    """
    if not views:
        raise ValueError("the slides outlet needs a non-empty `views` list")
    width = len(str(len(views))) if len(views) >= 100 else 2
    plan = []
    for i, view in enumerate(views, start=1):
        url = view_url(view, base_url)
        title = view.get('title') or f"View {i}"
        plan.append({
            'title': title,
            'file': f"{i:0{width}d}_{slug(title)}.png",
            'url': url,
            'render_url': bare_url(url),
            'kind': view_kind(url),
            'notes': view.get('notes', ''),
        })
    return plan


def render_options(outlet_config):
    """Viewport (CSS px) and device scale factor; the PNG is size × scale."""
    w, h = outlet_config.get('size') or DEFAULT_SIZE
    scale = outlet_config.get('scale') or DEFAULT_SCALE
    return {'width': int(w), 'height': int(h), 'scale': float(scale)}


# --- the deck ------------------------------------------------------------------
# A remark.js deck (index.html beside the PNGs): a title slide, then one slide per
# rendered view with its title, the image and the notes as a caption. Styling
# follows the console's tokens (IBM Plex, white, blue links); the title slide
# borrows fireatlas.org's dark panel over a photo, using the first map image.

REMARK_JS = "https://remarkjs.com/downloads/remark-0.15.0.min.js"


def _one_line(text):
    """Text safe to sit on one line of remark markdown: no slide or notes separators."""
    text = ' '.join((text or '').split())
    return re.sub(r'^(-{2,}|\?{3})', r'\\\1', text)


def deck_markdown(meta, entries):
    """remark source for the deck: title slide, then one slide per rendered view."""
    title = _one_line(meta.get('title')) or 'Atlas views'
    first = entries[0]['file'] if entries else None
    parts = [
        "class: title-slide" + (f"\nbackground-image: url({first})" if first else ""),
        ".hero[\n# " + title
        + (f"\n\n{_one_line(meta['description'])}" if meta.get('description') else "")
        + (f"\n\n.meta[{_one_line(meta['byline'])}]" if meta.get('byline') else "")
        + "\n]",
    ]
    slides_md = ["\n".join(parts)]
    for e in entries:
        lines = ["class: map-slide", f"## {_one_line(e['title'])}", f".shot[![{_one_line(e['title'])}]({e['file']})]"]
        caption = _one_line(e.get('notes'))
        link = f"[Open the live view →]({e['url']})" if e.get('url') else ""
        if caption or link:
            lines.append(".caption[" + " ".join(x for x in (caption, link) if x) + "]")
        slides_md.append("\n".join(lines))
    return "\n---\n".join(slides_md)


def deck_html(meta, entries):
    """The deck page. Markdown goes in a <textarea>, so it is HTML-escaped there."""
    page_title = html.escape(meta.get('title') or 'Atlas views')
    source = html.escape(deck_markdown(meta, entries), quote=False)
    return DECK_TEMPLATE.replace('__TITLE__', page_title) \
                        .replace('__REMARK__', REMARK_JS) \
                        .replace('__SOURCE__', source)


DECK_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>__TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {
    --font: 'IBM Plex Sans', Arial, sans-serif;
    --mono: 'IBM Plex Mono', monospace;
    --bg: #ffffff;
    --surface: #f7f7f7;
    --border: #d8d8d8;
    --text: #111111;
    --text-muted: #555555;
    --blue: #1d4ed8;
    --hero-bg: rgba(10, 20, 10, 0.82);
    --hero-text: #e8e8e0;
    --hero-title: #d4e8c2;
    --hero-meta: #8aaa70;
  }
  body { font-family: var(--font); background: #222; }
  .remark-slide-content {
    font-family: var(--font); color: var(--text); background: var(--bg);
    padding: 18px 28px 14px; font-size: 15px; line-height: 1.45;
  }
  .remark-slide-content h2 {
    font-size: 22px; font-weight: 600; margin: 0 0 10px;
    padding-bottom: 8px; border-bottom: 1px solid var(--border);
  }
  .remark-slide-content a { color: var(--blue); text-decoration: none; white-space: nowrap; }
  .remark-slide-content a:hover { text-decoration: underline; }
  .shot { text-align: center; }
  .shot img {
    max-width: 100%; max-height: 360px; border: 1px solid var(--border);
    border-radius: 4px; box-shadow: 0 4px 16px rgba(0,0,0,0.11);
  }
  .caption { margin-top: 10px; color: var(--text-muted); font-size: 14px; }
  .remark-slide-number { font-family: var(--mono); font-size: 11px; color: #999; }
  .title-slide { background-size: cover; background-position: center; padding: 0; }
  .title-slide .remark-slide-number { color: var(--hero-meta); }
  .hero {
    position: absolute; left: 48px; right: 48px; bottom: 56px;
    background: var(--hero-bg); color: var(--hero-text);
    border-radius: 6px; padding: 22px 28px;
  }
  .hero h1 { font-size: 32px; font-weight: 600; color: var(--hero-title); margin: 0 0 8px; }
  .hero p { margin: 0 0 6px; font-size: 16px; }
  .hero .meta { font-family: var(--mono); font-size: 12px; color: var(--hero-meta); margin-top: 10px; }
</style>
</head>
<body>
<textarea id="source">
__SOURCE__
</textarea>
<script src="__REMARK__"></script>
<script>
  remark.create({ ratio: '16:9', navigation: { scroll: false } });
</script>
</body>
</html>
"""
