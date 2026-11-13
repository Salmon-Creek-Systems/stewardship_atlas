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
"""
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
