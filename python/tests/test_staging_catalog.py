"""staging_catalog: STAC Items for what staging holds, kept by write-back."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

import staging_catalog as sc  # noqa: E402
from fake_s3 import FakeS3  # noqa: E402

OUT, PRIV, ATLAS = 'outb', 'privb', 'kennedy'


def entry(sha, size=3, etag='"e"'):
    return {'sha256': sha, 'size': size, 'etag': etag}


class TestPure(unittest.TestCase):
    def test_item_of(self):
        self.assertEqual(sc.item_of('layers/roads/roads.geojson'), ('layer', 'roads', 'roads.geojson'))
        self.assertEqual(sc.item_of('outlets/console/public/index.html'),
                         ('outlet', 'console', 'public/index.html'))
        self.assertIsNone(sc.item_of('atlas_config.json'))
        self.assertIsNone(sc.item_of('deltas/roads/x.geojson'))
        self.assertIsNone(sc.item_of('layers/roads'))

    def test_apply_changes_adds_replaces_and_removes(self):
        item = sc.empty_item(ATLAS, 'layer', 'roads')
        item = sc.apply_changes(item, {'roads.geojson': entry('aa'), 'roads.pmtiles': entry('bb')},
                                PRIV, ATLAS)
        self.assertEqual(item['assets']['roads.geojson']['file:checksum'], '1220aa')
        self.assertEqual(item['assets']['roads.geojson']['href'],
                         f's3://{PRIV}/{ATLAS}/staging/layers/roads/roads.geojson')
        item = sc.apply_changes(item, {'roads.geojson': entry('cc'), 'roads.pmtiles': None},
                                PRIV, ATLAS)
        self.assertEqual(list(item['assets']), ['roads.geojson'])
        self.assertEqual(sc.sha256_of(item['assets']['roads.geojson']), 'cc')

    def test_tree_round_trip(self):
        item = sc.apply_changes(sc.empty_item(ATLAS, 'outlet', 'webmap'),
                                {'index.html': entry('ab', 10, '"w"')}, PRIV, ATLAS)
        self.assertEqual(sc.tree_from_items([item]),
                         {'outlets/webmap/index.html': {'sha256': 'ab', 'size': 10, 'etag': '"w"'}})


class TestS3(unittest.TestCase):
    def setUp(self):
        self.s3 = FakeS3()

    def doc(self, key):
        obj = self.s3.objects.get((OUT, key))
        return None if obj is None else json.loads(obj['body'])

    def test_update_creates_items_and_root(self):
        sc.update(self.s3, OUT, PRIV, ATLAS, {'layers/roads/roads.geojson': entry('aa'),
                                              'atlas_config.json': entry('zz'),
                                              'outlets/webmap/index.html': entry('bb')})
        root = self.doc(f'{ATLAS}/catalog/staging/catalog.json')
        self.assertEqual([l['href'] for l in root['links']],
                         ['./layer-roads.json', './outlet-webmap.json'])
        self.assertEqual(set(sc.load_tree(self.s3, OUT, ATLAS)),
                         {'layers/roads/roads.geojson', 'outlets/webmap/index.html'})

    def test_update_touches_only_changed_items_and_drops_empty_ones(self):
        sc.update(self.s3, OUT, PRIV, ATLAS, {'layers/roads/roads.geojson': entry('aa'),
                                              'layers/creeks/creeks.geojson': entry('bb')})
        self.s3.calls.clear()
        sc.update(self.s3, OUT, PRIV, ATLAS, {'layers/roads/roads.geojson': None})
        self.assertNotIn(('get_object', f'{ATLAS}/catalog/staging/layer-creeks.json'),
                         [(c[0], c[1]) for c in self.s3.calls if len(c) > 1])
        self.assertIsNone(self.doc(f'{ATLAS}/catalog/staging/layer-roads.json'))
        self.assertEqual(set(sc.load_tree(self.s3, OUT, ATLAS)), {'layers/creeks/creeks.geojson'})

    def test_rebuild_from_listing_uses_metadata_and_hashes_the_rest(self):
        import hashlib
        pre = f'{ATLAS}/staging/'
        self.s3.put_object(Bucket=PRIV, Key=pre + 'layers/roads/roads.geojson', Body=b'r',
                           Metadata={'sha256': 'from-metadata'})
        self.s3.put_object(Bucket=PRIV, Key=pre + 'outlets/webmap/index.html', Body=b'w')
        self.s3.put_object(Bucket=PRIV, Key=pre + 'atlas_config.json', Body=b'{}')
        # a stale item from an earlier catalog is removed
        sc.update(self.s3, OUT, PRIV, ATLAS, {'layers/gone/gone.geojson': entry('xx')})
        got = sc.rebuild(self.s3, OUT, PRIV, ATLAS)
        self.assertEqual(got, {'items': 2, 'files': 2, 'hashed': 1})
        tree = sc.load_tree(self.s3, OUT, ATLAS)
        self.assertEqual(tree['layers/roads/roads.geojson']['sha256'], 'from-metadata')
        self.assertEqual(tree['outlets/webmap/index.html']['sha256'],
                         hashlib.sha256(b'w').hexdigest())
        self.assertIsNone(self.doc(f'{ATLAS}/catalog/staging/layer-gone.json'))


if __name__ == '__main__':
    unittest.main()
