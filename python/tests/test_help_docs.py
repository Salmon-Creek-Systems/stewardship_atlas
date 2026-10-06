"""Docs integrity: audience tags, manual links, and the in-app "?" links.

These catch the drift that a doc-per-task layout invites: a help page nobody
links to, a manual link to a renamed page, a "?" in a template pointing at a
page or section that doesn't exist.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import help_docs

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / 'documents'
HELP = DOCS / 'help'

# Files whose baked HTML carries in-app help links.
LINK_SOURCES = (
    sorted((REPO / 'templates').glob('*.html'))
    + sorted((REPO / 'templates' / 'js').glob('*.js'))
    + [REPO / 'python' / 'outlets.py', REPO / 'python' / 'outlets_qgis.py',
       REPO / 'python' / '3dview.py', REPO / 'python' / 'map_style.py',
       REPO / 'python' / 'help_docs.py']
)

# documents/*.html pages generated at materialize time besides the manuals.
GENERATED_DOCS = {'about', 'contact'}


def _manual_text(name):
    return (DOCS / f'{name}.md').read_text(encoding='utf-8')


class TestHelpPages(unittest.TestCase):

    def test_every_page_has_a_title_and_a_known_audience(self):
        for path in sorted(HELP.glob('*.md')):
            page = help_docs.read_help_page(path)
            with self.subTest(page=path.name):
                self.assertTrue(path.read_text().startswith('# '), "first line must be '# Title'")
                self.assertIn(page['audience'], help_docs.AUDIENCES,
                              "second line must be <!-- audience: user|admin|developer -->")

    def test_links_between_help_pages_resolve(self):
        for path in sorted(HELP.glob('*.md')):
            for target, anchor in help_docs.markdown_links(path.read_text()):
                with self.subTest(page=path.name, link=target):
                    resolved = (HELP / target).resolve()
                    self.assertTrue(resolved.exists(), f"{target} does not exist")
                    if anchor:
                        self.assertIn(anchor, help_docs.heading_anchors(resolved.read_text()))

    def test_every_help_page_is_linked_from_a_manual(self):
        linked = set()
        for manual in help_docs.MANUALS:
            for target, _ in help_docs.markdown_links(_manual_text(manual)):
                if target.startswith('help/') and target.endswith('.md'):
                    linked.add(target[len('help/'):-3])
        unlinked = sorted(p.stem for p in HELP.glob('*.md') if p.stem not in linked)
        self.assertEqual(unlinked, [], "help pages no manual links to")


class TestManuals(unittest.TestCase):

    def test_manual_links_and_anchors_resolve(self):
        for manual in help_docs.MANUALS:
            for target, anchor in help_docs.markdown_links(_manual_text(manual)):
                with self.subTest(manual=manual, link=target):
                    if target == 'help/index.html':
                        continue  # generated at materialize time
                    path = (DOCS / target).resolve()
                    self.assertTrue(path.exists(), f"{target} does not exist")
                    if anchor:
                        self.assertIn(anchor, help_docs.heading_anchors(path.read_text()))


class TestInAppLinks(unittest.TestCase):

    def test_every_baked_help_link_resolves(self):
        found = 0
        for source in LINK_SOURCES:
            text = source.read_text(encoding='utf-8')
            for kind, stem, anchor in help_docs.served_doc_links(text):
                found += 1
                with self.subTest(source=source.name, link=f'{kind}:{stem}#{anchor}'):
                    if kind == 'help':
                        if stem == 'index':
                            continue
                        md = HELP / f'{stem}.md'
                    else:
                        if stem in GENERATED_DOCS:
                            continue
                        md = DOCS / f'{stem}.md'
                    self.assertTrue(md.exists(), f"no {md.relative_to(REPO)}")
                    if anchor:
                        self.assertIn(anchor, help_docs.heading_anchors(md.read_text()),
                                      f"no '#{anchor}' heading in {md.name}")
        self.assertGreater(found, 20, "expected the templates to carry help links")

    def test_edit_page_help_targets_exist(self):
        for action, stem in help_docs.EDIT_PAGE_HELP.items():
            with self.subTest(action=action):
                self.assertTrue((HELP / f'{stem}.md').exists())


class TestHelpDocsHelpers(unittest.TestCase):

    def _page(self, text, name='p.md'):
        d = tempfile.mkdtemp()
        path = Path(d) / name
        path.write_text(text)
        return help_docs.read_help_page(path)

    def test_reads_title_and_audience(self):
        page = self._page('# Share a View\n<!-- audience: user -->\n\nBody\n', 'share.md')
        self.assertEqual(page['title'], 'Share a View')
        self.assertEqual(page['audience'], 'user')
        self.assertEqual(page['url'], '/local/documents/help/share.html')

    def test_missing_or_unknown_audience_is_none(self):
        self.assertIsNone(self._page('# T\n\nBody\n')['audience'])
        self.assertIsNone(self._page('# T\n<!-- audience: everyone -->\n')['audience'])

    def test_grouping_files_untagged_pages_as_user_and_respects_filter(self):
        pages = [{'title': 'A', 'audience': 'admin', 'stem': 'a', 'url': 'u/a'},
                 {'title': 'B', 'audience': None, 'stem': 'b', 'url': 'u/b'}]
        groups = help_docs.group_by_audience(pages)
        self.assertEqual([h for h, _ in groups], ['Using the Atlas', 'Maintaining the Atlas'])
        self.assertEqual(help_docs.use_case_groups(pages, ('user',)),
                         [{'name': 'Using the Atlas', 'cases': [{'name': 'B', 'uri': 'u/b'}]}])

    def test_slugify_matches_markdown_toc(self):
        self.assertEqual(help_docs.slugify('The Web Map'), 'the-web-map')
        self.assertEqual(help_docs.slugify('Making Edits Appear: Refresh'), 'making-edits-appear-refresh')
        self.assertEqual(help_docs.slugify('Staging vs. Published'), 'staging-vs-published')

    def test_served_links_are_found_with_anchors(self):
        text = ('<a href="/local/documents/help/share_location.html">'
                '<a href="/local/documents/user_manual.html#the-web-map">')
        self.assertEqual(help_docs.served_doc_links(text),
                         [('help', 'share_location', None), ('doc', 'user_manual', 'the-web-map')])


if __name__ == '__main__':
    unittest.main()
