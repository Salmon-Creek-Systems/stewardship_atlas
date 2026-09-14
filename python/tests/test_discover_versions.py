"""
Tests for atlas.discover_versions (issue #131 T6, rewritten for #159 task 6).

Published versions come from the atlas's STAC root Catalog now, not from
listing a swale directory for subdirectories holding an atlas_config.json.
There are no version directories any more, and a listing could only ever have
described one machine's disk.

atlas.py has heavy imports, so these skip where its dependency chain isn't
installed.
"""
import os
import sys
import unittest

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from fake_s3 import FakeS3
import atlas_store

atlas = pytest.importorskip("atlas")

OUT = 'OUT'
V1 = '2026-07-01_10-00-00'
V2 = '2026-07-16_18-25-18'


class TestDiscoverVersions(unittest.TestCase):

    def setUp(self):
        self.client = FakeS3()
        self.config = {'name': 'testatlas',
                       'cloud': {'outlets_bucket': OUT, 'private_bucket': 'PRIV'}}

    def _publish_catalog(self, versions):
        """Only the root Catalog matters here — it is the version index."""
        links = [{'rel': 'self', 'href': './catalog.json'}]
        links += [{'rel': 'version-history', 'title': v,
                   'href': f'./versions/{v}/catalog.json'} for v in versions]
        atlas_store.upload_documents(
            OUT, {'testatlas/catalog/catalog.json':
                  {'type': 'Catalog', 'id': 'testatlas', 'links': links}},
            client=self.client)

    def test_versions_come_back_newest_first(self):
        self._publish_catalog([V1, V2])
        self.assertEqual(atlas.discover_versions(self.config, client=self.client),
                         [V2, V1])

    def test_an_unpublished_atlas_has_no_versions(self):
        self.assertEqual(atlas.discover_versions(self.config, client=self.client), [])

    def test_a_version_is_recoverable_without_its_title(self):
        """The title is a convenience; the href is the real record."""
        atlas_store.upload_documents(
            OUT, {'testatlas/catalog/catalog.json': {
                'type': 'Catalog', 'id': 'testatlas', 'links': [
                    {'rel': 'version-history',
                     'href': f'./versions/{V1}/catalog.json'}]}},
            client=self.client)
        self.assertEqual(atlas.discover_versions(self.config, client=self.client),
                         [V1])

    def test_a_corrupt_catalog_reads_as_no_versions(self):
        """A config build must never fail because the index is unreadable."""
        self.client.put_object(Bucket=OUT, Key='testatlas/catalog/catalog.json',
                               Body=b'{ not json')
        self.assertEqual(atlas.discover_versions(self.config, client=self.client), [])

    def test_an_unreachable_bucket_reads_as_no_versions(self):
        class Broken:
            def get_object(self, **_):
                raise RuntimeError('no credentials')
        self.assertEqual(atlas.discover_versions(self.config, client=Broken()), [])


if __name__ == '__main__':
    unittest.main()
