"""Help pages and manuals: audience tags, the help index, and link checking.

Every documents/help/*.md page starts with a `# Title` line followed by an
audience tag on the second line:

    # Share a View or a Pin
    <!-- audience: user -->

The tag is an HTML comment, so the Markdown stays plain and the rendered page
shows nothing. Audiences:

    user       anyone viewing an atlas (webmap, 3D view, downloads, photos)
    admin      an atlas maintainer (editing, layers, refresh, build, publish)
    developer  platform work; normally lives in documents/developers_guide.md

The manuals (documents/user_manual.md, documents/admin_manual.md) are the
connective tissue: short narrative sections that link into the help pages
rather than repeating them. In-app `?` links point at a help page, or at a
manual section when no single page covers the screen.

Stdlib-only so it is testable without the server's dependencies.
"""

import re
import unicodedata
from pathlib import Path

AUDIENCES = ('user', 'admin', 'developer')

# Index and console headings for each audience, in display order.
AUDIENCE_HEADINGS = {
    'user': 'Using the Atlas',
    'admin': 'Maintaining the Atlas',
    'developer': 'Platform and Development',
}

# Public URL prefixes the rendered docs are served under.
HELP_URL_PREFIX = '/local/documents/help/'
DOCS_URL_PREFIX = '/local/documents/'
MANUALS = ('user_manual', 'admin_manual')

# The help page behind each webedit page's "?" (keyed by its action).
EDIT_PAGE_HELP = {
    'create': 'draw_vector',
    'annotate': 'editing_layer_data',
    'reshape': 'reshape_feature',
}


def help_url(stem, anchor=None):
    """Served URL of a help page, optionally at a heading anchor."""
    return f'{HELP_URL_PREFIX}{stem}.html' + (f'#{anchor}' if anchor else '')


_AUDIENCE_RE = re.compile(r'^<!--\s*audience:\s*([a-z]+)\s*-->\s*$')


def read_help_page(path):
    """Parse one help page: {'stem', 'title', 'audience', 'url'}.

    `audience` is None when the tag is missing or names an unknown audience;
    the docs tests treat that as an error, the renderer files it under 'user'.
    """
    path = Path(path)
    lines = path.read_text(encoding='utf-8').splitlines()
    first = lines[0] if lines else ''
    title = first[2:].strip() if first.startswith('# ') else path.stem
    audience = None
    if len(lines) > 1:
        m = _AUDIENCE_RE.match(lines[1].strip())
        if m and m.group(1) in AUDIENCES:
            audience = m.group(1)
    return {
        'stem': path.stem,
        'title': title,
        'audience': audience,
        'url': f'{HELP_URL_PREFIX}{path.stem}.html',
    }


def read_help_pages(help_dir):
    """Every help page in a directory, sorted by title."""
    pages = [read_help_page(p) for p in Path(help_dir).glob('*.md')]
    return sorted(pages, key=lambda p: p['title'].lower())


def group_by_audience(pages, audiences=AUDIENCES):
    """[(heading, [page, ...]), ...] for the given audiences, in AUDIENCES order.

    Untagged pages count as 'user' so a missing tag never hides a page.
    Empty groups are left out.
    """
    groups = []
    for audience in AUDIENCES:
        if audience not in audiences:
            continue
        members = [p for p in pages if (p['audience'] or 'user') == audience]
        if members:
            groups.append((AUDIENCE_HEADINGS[audience], members))
    return groups


def use_case_groups(pages, audiences):
    """The console's help list: [{'name': heading, 'cases': [{'name', 'uri'}]}]."""
    return [
        {'name': heading,
         'cases': [{'name': p['title'], 'uri': p['url']} for p in members]}
        for heading, members in group_by_audience(pages, audiences)
    ]


def help_index_content(pages, manual_links):
    """HTML body of help/index.html: the manuals, then pages grouped by audience.

    `manual_links` is [(title, href), ...]; page hrefs are relative to help/.
    """
    parts = []
    if manual_links:
        items = '\n'.join(f'        <li><a href="{href}">{t}</a></li>' for t, href in manual_links)
        parts.append(f'        <h2>Manuals</h2>\n        <ul>\n{items}\n        </ul>')
    for heading, members in group_by_audience(pages):
        items = '\n'.join(f'        <li><a href="{p["stem"]}.html">{p["title"]}</a></li>'
                          for p in members)
        parts.append(f'        <h2>{heading}</h2>\n        <ul>\n{items}\n        </ul>')
    return '\n' + '\n'.join(parts) + '\n'


# ── Link checking (used by the docs tests) ───────────────────────────────────

def slugify(value, separator='-'):
    """Heading anchor, matching Python-Markdown's toc extension default."""
    value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode('ascii')
    value = re.sub(r'[^\w\s-]', '', value).strip().lower()
    return re.sub(r'[{}\s]+'.format(separator), separator, value)


def heading_anchors(md_text):
    """Anchors the toc extension would give each Markdown heading."""
    anchors = set()
    for line in md_text.splitlines():
        m = re.match(r'^#{1,6}\s+(.*?)\s*#*\s*$', line)
        if m:
            anchors.add(slugify(re.sub(r'[*_`]', '', m.group(1))))
    return anchors


def markdown_links(md_text):
    """Relative link targets in a Markdown doc: [(path, anchor_or_None), ...]."""
    links = []
    for target in re.findall(r'\]\(([^)\s]+)\)', md_text):
        if '://' in target or target.startswith(('mailto:', '/')):
            continue
        path, _, anchor = target.partition('#')
        links.append((path, anchor or None))
    return links


# A help link baked into a template or Python string: the help page or manual
# URL, optionally with an anchor.
_SERVED_LINK_RE = re.compile(
    r'/local/documents/(help/)?([a-z0-9_]+)\.html(?:#([a-z0-9_-]+))?')


def served_doc_links(text):
    """Doc URLs referenced in a template: [(kind, stem, anchor), ...].

    kind is 'help' for help/*.html, 'doc' for documents/*.html (manuals,
    about, contact, and help/index).
    """
    return [('help' if m.group(1) else 'doc', m.group(2), m.group(3))
            for m in _SERVED_LINK_RE.finditer(text)]
