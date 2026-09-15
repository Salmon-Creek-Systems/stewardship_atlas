"""Site-wide web assets — everything the consoles fetch from /local/.

The stylesheets, help pages, manuals and the about/contact pages are derived
from this repo and are the same for every atlas, so they are built once and
published to the root of the outlets bucket rather than regenerated as a side
effect of materializing an atlas (#181).

They used to be written into ``{atlas}/staging/local/``, which is a symlink to
the shared data directory for *every* atlas — so one copy existed and the last
atlas to materialize decided whose name appeared in the site's help pages, and
where its Home button pointed. ``templates/help.html`` carries no atlas name
now, which is what makes a single published copy correct for everyone.

Import-light on purpose: no boto3, and ``markdown`` is imported inside the
renderer, so the planning half imports (and tests) on a bare environment. The
S3 half lives in ``scripts/publish_site_assets.py``.
"""

import logging
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# The URL prefix nginx serves from /root/data today and the key prefix the
# outlets bucket serves at the distribution root. Renaming it to something that
# does not claim to be local is #182.
SITE_PREFIX = 'local'

# Only the two console stylesheets are linked by absolute /local/ URL; every
# other stylesheet is copied into the outlet directory that uses it.
CSS_FILES = ('console.css', 'console_new.css')

# The default `logo` for every atlas (atlas.py, default_atlas_config.json).
LOGO_FILE = 'scs-smallgrass1.png'

MANUALS = ('user_manual', 'admin_manual')
SITE_PAGES = ('about', 'contact')


def site_key(relative_posix: str) -> str:
    """The S3 key an output path is published at."""
    return f"{SITE_PREFIX}/{relative_posix.lstrip('/')}"


def doc_title(md_text: str, default: str) -> str:
    """A markdown document's title — its first heading, else the default."""
    first = md_text.splitlines()[0] if md_text else ''
    return first.replace('# ', '') if first.startswith('#') else default


def render_styled_doc(md_text: str, help_template: str, title: str = None) -> str:
    """Render one markdown doc to a styled HTML page using the help template.

    Rewrites intra-doc relative '.md' links to '.html' (so links between the
    rendered pages resolve), and enables tables + header anchors so in-page
    '#section' links work. Returns the full styled HTML string.
    """
    import markdown
    if title is None:
        title = doc_title(md_text, 'Document')
    html_content = markdown.markdown(md_text, extensions=['tables', 'toc'])
    # rewrite relative markdown links (skip absolute URLs containing a scheme)
    html_content = re.sub(
        r'href="([^":]+?)\.md(#[^"]*)?"',
        lambda m: f'href="{m.group(1)}.html{m.group(2) or ""}"',
        html_content)
    return help_template.format(title=title, content=html_content)


def help_index_content(page_list: list, manual_links: list) -> str:
    """The body of documents/help/index.html.

    ``page_list`` is (title, filename) for each help page; ``manual_links`` is
    (title, href) for each manual, which sits one level up.
    """
    manuals_section = ""
    if manual_links:
        items = "\n".join(f'        <li><a href="{href}">{title}</a></li>'
                          for title, href in manual_links)
        manuals_section = f"        <h2>Manuals</h2>\n        <ul>\n{items}\n        </ul>\n"

    page_links = "\n".join(f'        <li><a href="{filename}">{title}</a></li>'
                           for title, filename in sorted(page_list))
    return f"""
{manuals_section}        <h2>Help Topics</h2>
        <p>Browse the available help documentation:</p>
        <ul>
{page_links}
        </ul>
        """


def plan_site_assets(repo_root) -> list:
    """What publishing the site would write, as a list of entries.

    Each entry is ``{'path', 'kind', 'source', 'required'}`` where ``path`` is
    relative to the site root (so ``site_key(path)`` is its S3 key) and ``kind``
    is 'copy', 'render' or 'index'. Pure: reads first lines for titles, renders
    nothing, and needs no markdown.
    """
    repo_root = Path(repo_root)
    templates = repo_root / 'templates'
    documents = repo_root / 'documents'
    entries = []

    for name in CSS_FILES:
        entries.append({'path': f'css/{name}', 'kind': 'copy',
                        'source': templates / 'css' / name, 'required': True})
    entries.append({'path': LOGO_FILE, 'kind': 'copy',
                    'source': templates / LOGO_FILE, 'required': True})

    for source in sorted((documents / 'help').glob('*.md')):
        entries.append({'path': f'documents/help/{source.stem}.html',
                        'kind': 'render', 'source': source, 'required': True})
    entries.append({'path': 'documents/help/index.html', 'kind': 'index',
                    'source': None, 'required': True})

    # Manuals are optional — they are long-form docs that may not be present in
    # a checkout, and the index simply omits what is missing.
    for name in MANUALS:
        source = documents / f'{name}.md'
        if source.is_file():
            entries.append({'path': f'documents/{name}.html', 'kind': 'render',
                            'source': source, 'required': False})

    for name in SITE_PAGES:
        entries.append({'path': f'documents/{name}.html', 'kind': 'render',
                        'source': documents / 'site' / f'{name}.md',
                        'required': True})

    return entries


def missing_sources(entries: list) -> list:
    """Required sources that are not on disk — a site published without these
    is a broken site, so callers raise rather than publish a partial one."""
    return [str(e['source']) for e in entries
            if e['required'] and e['source'] is not None and not e['source'].is_file()]


def build_site_assets(repo_root, out_dir) -> list:
    """Build the whole site tree into ``out_dir``. Returns the relative paths
    written, which are exactly the ``path`` values of the plan."""
    repo_root = Path(repo_root)
    out_dir = Path(out_dir)
    entries = plan_site_assets(repo_root)

    missing = missing_sources(entries)
    if missing:
        raise FileNotFoundError(f"site assets missing required source(s): {', '.join(missing)}")

    help_template = (repo_root / 'templates' / 'help.html').read_text()

    # Titles for the index, collected as the pages are rendered.
    page_list = []
    manual_links = []
    index_entry = None

    for entry in entries:
        destination = out_dir / entry['path']
        destination.parent.mkdir(parents=True, exist_ok=True)

        if entry['kind'] == 'copy':
            shutil.copyfile(entry['source'], destination)
            continue

        if entry['kind'] == 'index':
            index_entry = entry
            continue

        md_text = entry['source'].read_text()
        title = doc_title(md_text, entry['source'].stem)
        destination.write_text(render_styled_doc(md_text, help_template, title=title),
                               encoding='utf-8')

        filename = Path(entry['path']).name
        if entry['path'].startswith('documents/help/'):
            page_list.append((title, filename))
        elif entry['source'].stem in MANUALS:
            manual_links.append((title, f'../{filename}'))

    if index_entry is not None:
        content = help_index_content(page_list, manual_links)
        (out_dir / index_entry['path']).write_text(
            help_template.format(title='Help Index', content=content), encoding='utf-8')

    written = [entry['path'] for entry in entries]
    logger.info(f"site_assets: built {len(written)} files into {out_dir}")
    return written
