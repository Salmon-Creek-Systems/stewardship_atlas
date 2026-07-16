"""
Tests for atlas.discover_versions (issue #131, T6).

Published versions only: 'staging' and 'CURRENT' are excluded, newest
first. atlas.py has heavy imports, so these skip where its dependency
chain isn't installed.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

atlas = pytest.importorskip("atlas")


class TestDiscoverVersions(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _make_version(self, name, with_config=True):
        d = self.root / name
        d.mkdir()
        if with_config:
            (d / 'atlas_config.json').write_text('{}')
        return d

    def test_excludes_staging_and_current_sorts_newest_first(self):
        self._make_version('staging')
        v1 = self._make_version('2026-07-01_10-00-00')
        self._make_version('2026-07-16_18-25-18')
        (self.root / 'CURRENT').symlink_to(v1)  # symlinked dir with config inside

        versions = atlas.discover_versions(self.root)
        self.assertEqual(versions, ['2026-07-16_18-25-18', '2026-07-01_10-00-00'])

    def test_ignores_dirs_without_config(self):
        self._make_version('app', with_config=False)
        self._make_version('2026-07-01_10-00-00')
        (self.root / 'dump.txt').write_text('x')

        self.assertEqual(atlas.discover_versions(self.root), ['2026-07-01_10-00-00'])

    def test_missing_path_returns_empty(self):
        self.assertEqual(atlas.discover_versions(self.root / 'nope'), [])
