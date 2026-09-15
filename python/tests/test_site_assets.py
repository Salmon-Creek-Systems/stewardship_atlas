"""
Test site_assets — the site-wide web assets served at /local/ (#181).

The planning half is deliberately free of markdown and boto3 so it runs on a
bare checkout; the one test that actually renders skips without markdown.
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import atlas_store
import site_assets

REPO_ROOT = Path(__file__).resolve().parents[2]


def fixture_repo(root: Path, manuals=('user_manual',), help_pages=('draw_vector',)):
    """A minimal checkout: the files site_assets reads, and nothing else."""
    (root / 'templates' / 'css').mkdir(parents=True)
    for name in site_assets.CSS_FILES:
        (root / 'templates' / 'css' / name).write_text('body {}')
    (root / 'templates' / site_assets.LOGO_FILE).write_bytes(b'PNG')
    (root / 'templates' / 'help.html').write_text(
        '<html><title>{title}</title><body>{content}</body></html>')

    (root / 'documents' / 'help').mkdir(parents=True)
    for name in help_pages:
        (root / 'documents' / 'help' / f'{name}.md').write_text(f'# {name.title()} Page\n\ntext')
    for name in manuals:
        (root / 'documents' / f'{name}.md').write_text(f'# {name} title\n\ntext')

    (root / 'documents' / 'site').mkdir(parents=True)
    for name in site_assets.SITE_PAGES:
        (root / 'documents' / 'site' / f'{name}.md').write_text(f'# {name.title()}\n\ntext')
    return root


class SiteAssetsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = fixture_repo(self.tmp / 'repo')

    def paths(self, entries=None):
        entries = entries if entries is not None else site_assets.plan_site_assets(self.repo)
        return [entry['path'] for entry in entries]


class TestPlan(SiteAssetsTestCase):
    def test_stylesheets_and_logo_sit_at_the_site_root(self):
        paths = self.paths()
        self.assertIn('css/console.css', paths)
        self.assertIn('css/console_new.css', paths)
        self.assertIn(site_assets.LOGO_FILE, paths)

    def test_help_markdown_becomes_html_under_documents_help(self):
        self.assertIn('documents/help/draw_vector.html', self.paths())

    def test_help_index_is_generated_not_copied(self):
        entries = site_assets.plan_site_assets(self.repo)
        index = [e for e in entries if e['path'] == 'documents/help/index.html']
        self.assertEqual(len(index), 1)
        self.assertEqual(index[0]['kind'], 'index')
        self.assertIsNone(index[0]['source'])

    def test_about_and_contact_come_from_documents_site(self):
        entries = {e['path']: e for e in site_assets.plan_site_assets(self.repo)}
        self.assertEqual(entries['documents/about.html']['source'],
                         self.repo / 'documents' / 'site' / 'about.md')
        self.assertIn('documents/contact.html', entries)

    def test_absent_manual_is_simply_omitted(self):
        repo = fixture_repo(self.tmp / 'nomanuals', manuals=())
        paths = self.paths(site_assets.plan_site_assets(repo))
        self.assertNotIn('documents/user_manual.html', paths)
        self.assertNotIn('documents/admin_manual.html', paths)

    def test_keys_are_prefixed_with_the_site_prefix(self):
        self.assertEqual(site_assets.site_key('documents/help/foo.html'),
                         'local/documents/help/foo.html')


class TestMissingSources(SiteAssetsTestCase):
    def test_a_missing_required_source_is_reported(self):
        (self.repo / 'templates' / 'css' / 'console.css').unlink()
        entries = site_assets.plan_site_assets(self.repo)
        self.assertEqual(len(site_assets.missing_sources(entries)), 1)

    def test_build_refuses_to_publish_a_partial_site(self):
        (self.repo / 'documents' / 'site' / 'about.md').unlink()
        with self.assertRaises(FileNotFoundError):
            site_assets.build_site_assets(self.repo, self.tmp / 'out')


class TestHelpIndex(unittest.TestCase):
    def test_pages_are_listed_alphabetically(self):
        content = site_assets.help_index_content([('Zebra', 'z.html'), ('Alpha', 'a.html')], [])
        self.assertLess(content.index('a.html'), content.index('z.html'))

    def test_manuals_section_links_one_level_up(self):
        content = site_assets.help_index_content([], [('User Manual', '../user_manual.html')])
        self.assertIn('<h2>Manuals</h2>', content)
        self.assertIn('href="../user_manual.html"', content)

    def test_no_manuals_section_without_manuals(self):
        self.assertNotIn('Manuals', site_assets.help_index_content([('A', 'a.html')], []))


class TestTheRealTemplate(unittest.TestCase):
    """The published pages are one copy shared by every atlas, so the template
    may not carry an atlas name or an atlas-specific link."""

    def setUp(self):
        self.template = (REPO_ROOT / 'templates' / 'help.html').read_text()

    def test_template_has_no_atlas_placeholders(self):
        self.assertNotIn('{atlas_name}', self.template)
        self.assertNotIn('base_url', self.template)

    def test_template_still_takes_title_and_content(self):
        self.assertIn('{title}', self.template)
        self.assertIn('{content}', self.template)

    def test_back_link_replaces_the_atlas_home_button(self):
        self.assertIn('history.back()', self.template)
        self.assertNotIn('outlets/html/admin', self.template)


class TestBuild(SiteAssetsTestCase):
    def setUp(self):
        super().setUp()
        try:
            import markdown  # noqa: F401
        except ImportError:
            self.skipTest('markdown is not installed')

    def test_build_writes_every_planned_path(self):
        out = self.tmp / 'out'
        written = site_assets.build_site_assets(self.repo, out)
        for relative in written:
            self.assertTrue((out / relative).is_file(), relative)

    def test_index_lists_the_help_page_and_the_manual(self):
        out = self.tmp / 'out'
        site_assets.build_site_assets(self.repo, out)
        index = (out / 'documents' / 'help' / 'index.html').read_text()
        self.assertIn('href="draw_vector.html"', index)
        self.assertIn('href="../user_manual.html"', index)

    def test_a_removed_help_page_shows_up_as_stale(self):
        # The publish prunes what the plan no longer writes; without that a
        # deleted help page keeps being served.
        out = self.tmp / 'out'
        site_assets.build_site_assets(self.repo, out)
        planned = [key for _, key, _ in atlas_store.plan_upload(out, site_assets.SITE_PREFIX)]
        existing = planned + ['local/documents/help/removed.html']
        self.assertEqual(atlas_store.stale_keys(existing, planned),
                         ['local/documents/help/removed.html'])


if __name__ == '__main__':
    unittest.main()
