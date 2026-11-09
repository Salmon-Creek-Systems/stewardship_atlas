"""Overlook outlet: change scan, headlines, privacy rules, products."""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import overlook

V1 = '2026-01-01_00-00-00'


def pt(x, y, **props):
    return {'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [x, y]}, 'properties': props}


class OverlookFixture(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.staging = self.root / 'testatlas' / 'staging'
        self.config = {
            'name': 'testatlas',
            'data_root': str(self.root),
            'title': 'Test Atlas',
            'dataswale': {
                'versions': [V1],
                'bbox': {'west': -124.0, 'south': 40.0, 'east': -123.9, 'north': 40.1},
                'layers': [
                    {'name': 'hydrants', 'geometry_type': 'point', 'access': ['public']},
                    {'name': 'roads', 'geometry_type': 'linestring', 'access': ['public']},
                    {'name': 'photos', 'geometry_type': 'point', 'access': ['public']},
                    {'name': 'secret', 'geometry_type': 'point', 'access': ['admin']},
                    {'name': 'undeclared', 'geometry_type': 'point'},
                ],
            },
            'cloud': {'outlets': ['webmap', 'overlook']},
            'assets': {
                'overlook': {'type': 'outlet', 'access': ['public'], 'config': {
                    'fetch_type': 'overlook', 'bulk_threshold': 3,
                    'headlines': [
                        {'label': 'Hydrants', 'layer': 'hydrants', 'measure': 'count'},
                        {'label': 'Miles of road', 'layer': 'roads', 'measure': 'length_mi'},
                        {'label': 'Secrets', 'layer': 'secret', 'measure': 'count'},
                        {'label': 'Undeclared', 'layer': 'undeclared', 'measure': 'count'},
                        {'label': 'Ghost', 'layer': 'nope', 'measure': 'count'},
                    ]}},
                'webmap': {'type': 'outlet', 'access': ['public'], 'in_layers': ['hydrants', 'roads'],
                           'config': {'fetch_type': 'webmap'}},
                'admin_map': {'type': 'outlet', 'access': ['admin'], 'in_layers': ['secret'],
                              'config': {'fetch_type': 'webmap'}},
                'unpublished': {'type': 'outlet', 'access': ['public'], 'config': {'fetch_type': 'webmap'}},
            },
        }
        self.write_layer('hydrants', [pt(-123.95, 40.05, atlas_id='h1', name='H1'),
                                      pt(-123.96, 40.06, atlas_id='h2', name='H2')])
        self.write_layer('roads', [{'type': 'Feature', 'properties': {}, 'geometry': {
            'type': 'LineString', 'coordinates': [[-123.95, 40.0], [-123.95, 40.1]]}}])
        self.write_layer('photos', [pt(-123.97, 40.07, source='email', sender='someone@example.com',
                                       image_url='https://x/p.jpg', name='Tank', timestamp='2026-01-05',
                                       email_body='private body text')])
        self.write_layer('secret', [pt(-123.9, 40.0, name='S', source='email', image_url='https://x/s.jpg')])
        self.write_layer('undeclared', [pt(-123.9, 40.0, name='U', source='email', image_url='https://x/u.jpg')])

    def write_layer(self, name, feats):
        d = self.staging / 'layers' / name
        d.mkdir(parents=True, exist_ok=True)
        (d / f'{name}.geojson').write_text(json.dumps({'type': 'FeatureCollection', 'features': feats}))

    def write_delta(self, layer, fname, feats, provenance=None):
        d = self.staging / 'deltas' / layer / 'work'
        d.mkdir(parents=True, exist_ok=True)
        fc = {'type': 'FeatureCollection', 'features': feats}
        if provenance is not None:
            fc['provenance'] = provenance
        (d / fname).write_text(json.dumps(fc))


class TestChangeScan(OverlookFixture):
    def test_release_window_and_pending(self):
        self.write_delta('hydrants', 'webedit__20260102_000000__create.geojson', [pt(0, 0)],
                         {'source': 'webedit', 'base_version': V1, 'feature_count': 1})
        self.write_delta('hydrants', 'webedit__20251201_000000__create.geojson', [pt(0, 0)],
                         {'source': 'webedit', 'base_version': None, 'feature_count': 1})
        # Pending (not in work/) is not a change yet.
        (self.staging / 'deltas' / 'hydrants' / 'webedit__20260103_000000__create.geojson').write_text(
            json.dumps({'type': 'FeatureCollection', 'features': [pt(0, 0)]}))
        entries = overlook.scan_changes(self.config, ['hydrants'], V1, 3)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['label'], 'web edits')

    def test_legacy_classification_and_bulk(self):
        self.write_delta('photos', 'assetless__20260102_000000__create.geojson',
                         [pt(0, 0, source='email', image_url='u')])
        self.write_delta('roads', 'assetless__20260102_000001__create.geojson', [pt(0, 0)])
        self.write_delta('roads', 'overture_roads__20260102_000002__create.geojson', [pt(0, 0)] * 4)
        entries = {(e['layer'], e['source']): e for e in
                   overlook.scan_changes(self.config, ['photos', 'roads'], V1, 3)}
        self.assertIn(('photos', 'email_photo'), entries)
        self.assertEqual(entries[('roads', 'unrecorded')]['label'], 'edits (source not recorded)')
        self.assertTrue(entries[('roads', 'overture_roads')]['bulk'])
        summary = overlook.summarize_changes(list(entries.values()), [])
        roads = next(g for g in summary if g['layer'] == 'roads')
        self.assertIn('rebuilt from overture roads (4 features)', roads['parts'])

    def test_match_resolves_by_atlas_id(self):
        self.write_delta('hydrants', 'comment__20260102_000000__match.geojson',
                         [{'type': 'Feature', 'geometry': None, 'properties': {'atlas_id': 'h2'}},
                          {'type': 'Feature', 'geometry': None, 'properties': {'atlas_id': 'gone'}}],
                         {'source': 'comment', 'base_version': V1, 'feature_count': 2, 'action': 'match'})
        entries = overlook.scan_changes(self.config, ['hydrants'], V1, 3)
        feats, truncated = overlook.change_features(
            entries, {'hydrants': json.loads((self.staging / 'layers/hydrants/hydrants.geojson').read_text())['features']}, 100)
        self.assertEqual(len(feats), 1)
        self.assertEqual(feats[0]['geometry']['coordinates'], [-123.96, 40.06])
        self.assertEqual(feats[0]['properties']['name'], 'H2')
        self.assertFalse(truncated)


class TestHeadlines(OverlookFixture):
    def test_omits_missing_nonpublic_and_zero(self):
        summary, _, _ = overlook.build_summary(self.config, 'overlook')
        labels = {t['label']: t['value'] for t in summary['headlines']}
        self.assertEqual(labels['Hydrants'], 2)
        self.assertAlmostEqual(labels['Miles of road'], 6.9, delta=0.1)
        for absent in ('Secrets', 'Undeclared', 'Ghost', 'Edits since last release'):
            self.assertNotIn(absent, labels)
        self.assertEqual(labels['Field photos'], 1)
        self.assertEqual(labels['Releases published'], 1)


class TestPrivacy(OverlookFixture):
    def test_outputs_exclude_protected_layers_and_props(self):
        self.write_delta('secret', 'webedit__20260102_000000__create.geojson', [pt(0, 0, name='S')],
                         {'source': 'webedit', 'base_version': V1, 'feature_count': 1})
        self.write_delta('undeclared', 'webedit__20260102_000000__create.geojson', [pt(0, 0, name='U')],
                         {'source': 'webedit', 'base_version': V1, 'feature_count': 1})
        self.write_delta('hydrants', 'webedit__20260102_000000__create.geojson',
                         [pt(0, 0, name='N', sender='x@y', notes='internal')],
                         {'source': 'webedit', 'base_version': V1, 'feature_count': 1})
        overlook.outlet_overlook(self.config, 'overlook')
        out = self.staging / 'outlets' / 'overlook'
        everything = ''.join(p.read_text() for p in out.rglob('*') if p.is_file() and p.suffix in ('.json', '.geojson', '.html'))
        for leaked in ('secret', 'undeclared', 'someone@example.com', 'x@y', 'private body text',
                       'https://x/s.jpg', 'https://x/u.jpg', '/staging/', 'admin/'):
            self.assertNotIn(leaked, everything, leaked)

        photos = json.loads((out / 'data' / 'photos.geojson').read_text())['features']
        self.assertEqual(len(photos), 1)
        self.assertEqual(set(photos[0]['properties']), set(overlook.PHOTO_PROPS))
        # Protected outlets are listed by name (rule 5) but never linked.
        summary = json.loads((out / 'overlook.json').read_text())
        admin_row = next(r for r in summary['products'] if r['name'] == 'admin_map')
        self.assertIsNone(admin_row['href'])
        changes = json.loads((out / 'data' / 'changes.geojson').read_text())['features']
        self.assertEqual(len(changes), 1)
        self.assertTrue(set(changes[0]['properties']) <= set(overlook.CHANGE_PROPS))

    def test_script_block_cannot_be_closed(self):
        self.config['title'] = 'x</script><script>alert(1)</script>'
        overlook.outlet_overlook(self.config, 'overlook')
        page = (self.staging / 'outlets' / 'overlook' / 'index.html').read_text()
        self.assertNotIn('</script><script>alert', page)


class TestProducts(OverlookFixture):
    def test_links_status_and_tiers(self):
        self.config['assets']['admin_map']['access'] = ['admin']
        webmap = self.staging / 'outlets' / 'webmap'
        webmap.mkdir(parents=True)
        (webmap / 'index.html').write_text('x')
        old = time.time() - 3600
        os.utime(webmap / 'index.html', (old, old))  # built before the layers were written
        rows = {r['name']: r for r in overlook.products(self.config, 'overlook')}
        self.assertNotIn('overlook', rows)
        self.assertEqual(rows['webmap']['href'], '../webmap/')
        self.assertEqual(rows['webmap']['status'], 'needs rebuild')
        self.assertEqual(rows['admin_map']['access'], 'Admins')
        self.assertIsNone(rows['admin_map']['href'])
        self.assertEqual(rows['unpublished']['status'], 'not built')
        self.assertIsNone(rows['unpublished']['href'])

    def test_current_when_built_after_inputs(self):
        webmap = self.staging / 'outlets' / 'webmap'
        webmap.mkdir(parents=True)
        (webmap / 'index.html').write_text('x')
        future = time.time() + 60
        os.utime(webmap / 'index.html', (future, future))
        rows = {r['name']: r for r in overlook.products(self.config, 'overlook')}
        self.assertEqual(rows['webmap']['status'], 'current')


class TestGeometry(unittest.TestCase):
    def test_area_of_a_square_km(self):
        # ~1 km square near the equator ≈ 247 acres.
        d = 1000 / 111320
        poly = {'type': 'Polygon', 'coordinates': [[[0, 0], [d, 0], [d, d], [0, d], [0, 0]]]}
        self.assertAlmostEqual(overlook.area_m2(poly) / overlook.M2_PER_ACRE, 247.1, delta=2)


if __name__ == '__main__':
    unittest.main()
