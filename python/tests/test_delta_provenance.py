"""Delta provenance: every delta names its source and the release it belongs to."""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import delta_provenance as dp


def _config(root, versions=None):
    return {'name': 'testatlas', 'data_root': str(root),
            'dataswale': {'versions': versions or [], 'layers': []}, 'assets': {}}


class TestValidateSource(unittest.TestCase):
    def test_known_and_asset_tokens_pass(self):
        for s in ('webedit', 'email_photo', 'overture_buildings', 'h3-grid'):
            self.assertEqual(dp.validate_source(s), s)

    def test_assetless_is_refused(self):
        with self.assertRaises(ValueError):
            dp.validate_source('assetless')

    def test_missing_or_malformed_refused(self):
        for bad in (None, '', 'a__b', 'Web Edit', '../x'):
            with self.assertRaises(ValueError):
                dp.validate_source(bad)


class TestRecord(unittest.TestCase):
    def test_base_version_is_latest_published(self):
        cfg = _config('/x', ['2026-01-01_00-00-00', '2026-03-01_00-00-00', '2026-02-01_00-00-00'])
        rec = dp.record(cfg, source='webedit', action='create', layer='hydrants', feature_count=2)
        self.assertEqual(rec['base_version'], '2026-03-01_00-00-00')
        self.assertEqual(rec['source'], 'webedit')
        self.assertEqual(rec['feature_count'], 2)
        self.assertTrue(rec['written_at'].endswith('Z'))

    def test_never_published(self):
        rec = dp.record(_config('/x'), source='webedit', action='create', layer='l', feature_count=0)
        self.assertIsNone(rec['base_version'])


class TestRead(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_legacy_assetless_reads_as_unrecorded(self):
        p = self.tmp / 'assetless__20260101_000000__create.geojson'
        p.write_text(json.dumps({'type': 'FeatureCollection', 'features': []}))
        prov = dp.read(p)
        self.assertEqual(prov['source'], dp.UNRECORDED)
        self.assertFalse(prov['recorded'])
        self.assertEqual(prov['timestamp'], datetime(2026, 1, 1))

    def test_member_wins_over_filename(self):
        p = self.tmp / 'webedit__20260101_000000_1__match.geojson'
        p.write_text(json.dumps({'type': 'FeatureCollection', 'features': [],
                                 'provenance': {'source': 'webedit', 'base_version': '2025-12-01_00-00-00',
                                                'action': 'match'}}))
        prov = dp.read(p)
        self.assertTrue(prov['recorded'])
        self.assertEqual(prov['base_version'], '2025-12-01_00-00-00')
        self.assertEqual(prov['timestamp'], datetime(2026, 1, 1))  # collision suffix tolerated


class TestInReleaseAfter(unittest.TestCase):
    V1 = '2026-01-01_00-00-00'
    V2 = '2026-02-01_00-00-00'

    def test_recorded_base_version_decides(self):
        self.assertTrue(dp.in_release_after({'recorded': True, 'base_version': self.V2}, self.V2))
        self.assertFalse(dp.in_release_after({'recorded': True, 'base_version': self.V1}, self.V2))
        self.assertFalse(dp.in_release_after({'recorded': True, 'base_version': None}, self.V2))

    def test_legacy_falls_back_to_timestamp(self):
        late = {'recorded': False, 'timestamp': datetime(2026, 2, 2)}
        early = {'recorded': False, 'timestamp': datetime(2026, 1, 15)}
        self.assertTrue(dp.in_release_after(late, self.V2))
        self.assertFalse(dp.in_release_after(early, self.V2))

    def test_never_published_means_everything(self):
        self.assertTrue(dp.in_release_after({'recorded': True, 'base_version': None}, None))


class TestAppliedDeltas(unittest.TestCase):
    def test_only_work_dir_is_scanned(self):
        root = Path(tempfile.mkdtemp())
        (root / 'roads' / 'work').mkdir(parents=True)
        fc = json.dumps({'type': 'FeatureCollection', 'features': []})
        (root / 'roads' / 'webedit__20260101_000000__create.geojson').write_text(fc)  # pending
        (root / 'roads' / 'work' / 'webedit__20260101_000001__create.geojson').write_text(fc)
        found = list(dp.applied_deltas(root))
        self.assertEqual([d['path'].name for d in found], ['webedit__20260101_000001__create.geojson'])
        self.assertEqual(found[0]['layer'], 'roads')


class TestWriteDelta(unittest.TestCase):
    """write_delta lives in deltas_geojson, which needs DuckDB/GDAL to import."""

    def setUp(self):
        try:
            import outlets  # noqa: F401 — import order avoids the deltas_geojson cycle
            import deltas_geojson
        except Exception as e:  # pragma: no cover - depends on the environment
            self.skipTest(f"deltas_geojson not importable here: {e}")
        self.dg = deltas_geojson
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = _config(self.tmp, ['2026-01-01_00-00-00'])

    def test_names_file_and_embeds_record(self):
        fc = {'type': 'FeatureCollection', 'features': [{'type': 'Feature', 'geometry': None, 'properties': {}}]}
        p = self.dg.write_delta(self.cfg, 'hydrants', fc, 'create', source='webedit')
        self.assertTrue(p.name.startswith('webedit__'))
        self.assertTrue(p.name.endswith('__create.geojson'))
        body = json.loads(p.read_text())
        self.assertEqual(body['provenance']['base_version'], '2026-01-01_00-00-00')
        self.assertEqual(body['provenance']['layer'], 'hydrants')
        self.assertEqual(body['provenance']['feature_count'], 1)
        self.assertNotIn('provenance', fc)  # caller's dict untouched

    def test_same_second_does_not_collide(self):
        fc = {'type': 'FeatureCollection', 'features': []}
        a = self.dg.write_delta(self.cfg, 'hydrants', fc, 'create', source='webedit')
        b = self.dg.write_delta(self.cfg, 'hydrants', fc, 'create', source='webedit')
        c = self.dg.write_delta(self.cfg, 'hydrants', fc, 'create', source='email_photo')
        self.assertEqual(len({a, b, c}), 3)
        order = sorted([c, b, a], key=self.dg.delta_sort_key)
        self.assertEqual(order, [a, b, c])  # apply order is write order, whatever the source

    def test_apply_order_ignores_source_name(self):
        names = ['webedit__20260101_000002__create.geojson',
                 'email_photo__20260101_000003__create.geojson',
                 'zz_inlet__20260101_000001__create.geojson',
                 'webedit__20260101_000001_c001__create.geojson']
        ordered = [p.name for p in sorted(map(Path, names), key=self.dg.delta_sort_key)]
        self.assertEqual(ordered, ['zz_inlet__20260101_000001__create.geojson',
                                   'webedit__20260101_000001_c001__create.geojson',
                                   'webedit__20260101_000002__create.geojson',
                                   'email_photo__20260101_000003__create.geojson'])

    def test_source_required(self):
        with self.assertRaises(TypeError):
            self.dg.delta_path_from_layer(self.cfg, 'hydrants', 'create')
        with self.assertRaises(ValueError):
            self.dg.write_delta(self.cfg, 'hydrants', {'features': []}, 'create', source='assetless')


if __name__ == '__main__':
    unittest.main()
