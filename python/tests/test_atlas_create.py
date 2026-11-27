"""atlas_create: the create_atlas job (Phase 4, task 7).

The pure pieces run anywhere. ``create`` needs the server env (atlas and its
dependencies); there the session, S3 and every materialize are faked, and
what is checked is the order of steps and what is reported.
"""
import json
import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import atlas_create  # noqa: E402

REPO = Path(__file__).resolve().parents[2]

BBOX = {"west": -124.0, "east": -123.8, "south": 40.16, "north": 40.26}

STARTER = {
    "properties": {"show_user_location": True},
    "layers": {"regions": {}, "roads": {"geometry_type": "linestring"},
               "dem": {"geometry_type": "raster"}},
    "assets": {
        "public_roads": {"type": "inlet", "out_layer": "roads"},
        "webmap": {"type": "outlet"},
        "console": {"type": "outlet"},
        "notebook": {"type": "outlet"},
        "runbook": {"type": "outlet", "config": {"data_type": "geopdf"}},
    },
}


class TestPure(unittest.TestCase):

    def test_h3_resolution_scales_with_the_extent(self):
        small = atlas_create.default_h3_resolution(BBOX)
        county = atlas_create.default_h3_resolution(
            {"west": -124.4, "east": -122.8, "south": 39.0, "north": 40.5})
        self.assertGreater(small, county)
        self.assertTrue(5 <= county <= small <= 12)

    def test_feature_collection_is_the_bbox_with_starter_properties(self):
        fc = atlas_create.atlas_feature_collection('newatlas', 'New Atlas', BBOX, 8,
                                                   STARTER['properties'])
        f = fc['features'][0]
        self.assertEqual(f['geometry']['coordinates'][0][0], [-124.0, 40.16])
        self.assertEqual(f['geometry']['coordinates'][0][0], f['geometry']['coordinates'][0][-1])
        props = f['properties']
        self.assertEqual((props['name'], props['description']), ('newatlas', 'New Atlas'))
        self.assertEqual(props['app_url'], '/api')
        self.assertEqual(props['versioned_outlets'], [])
        self.assertTrue(props['show_user_location'])

    def test_pdf_outlets_are_deferred_and_the_console_is_last(self):
        self.assertEqual(atlas_create.deferred_outlets(STARTER['assets']), ['runbook'])
        self.assertEqual(atlas_create.outlets_to_build(STARTER['assets']),
                         ['notebook', 'webmap', 'console'])

    def test_expected_steps(self):
        # structure 2 + email 1 + inlet 2 + vector layers 2 + deferred 1 + outlets 2*3
        self.assertEqual(atlas_create.expected_steps(STARTER), 14)

    def test_every_real_starter_has_an_estimate(self):
        for path in sorted((REPO / 'configuration').glob('*_starter.json')):
            self.assertGreater(atlas_create.expected_steps(json.loads(path.read_text())), 5,
                               path.name)

    def test_boundary_sits_beside_the_job(self):
        self.assertEqual(atlas_create.boundary_key('newatlas', 'j1'),
                         'newatlas/jobs/j1.boundary.geojson')


class FakeSes:
    def __init__(self, recipients):
        self.rules = [{'Name': 'bounce', 'Actions': [{'BounceAction': {}}]},
                      {'Name': 'store', 'Actions': [{'S3Action': {}}], 'Recipients': recipients}]
        self.updates = []

    def describe_receipt_rule_set(self, RuleSetName):
        return {'Rules': json.loads(json.dumps(self.rules))}

    def update_receipt_rule(self, RuleSetName, Rule):
        self.updates.append(Rule)


class TestRegisterEmail(unittest.TestCase):

    def test_adds_the_address_to_the_storing_rule(self):
        ses = FakeSes(['scvfd@fireatlas.org'])
        self.assertEqual(atlas_create.register_email_address('newatlas', ses),
                         'newatlas@fireatlas.org')
        [rule] = ses.updates
        self.assertEqual(rule['Name'], 'store')
        self.assertEqual(rule['Recipients'], ['scvfd@fireatlas.org', 'newatlas@fireatlas.org'])

    def test_an_address_already_there_is_left_alone(self):
        ses = FakeSes(['newatlas@fireatlas.org'])
        atlas_create.register_email_address('newatlas', ses)
        self.assertEqual(ses.updates, [])


def job(**params):
    base = {'slug': 'newatlas', 'name': 'New Atlas', 'starter': 'simple', 'bbox': BBOX,
            'default_h3_resolution': 8, 'register_email': False}
    return {'id': 'j1', 'kind': 'create_atlas', 'target': 'box', 'params': {**base, **params}}


class TestCreate(unittest.TestCase):
    """The runner, with everything it calls faked."""

    def setUp(self):
        try:
            import atlas  # noqa: F401
            import atlas_session  # noqa: F401
            import dataswale_geojson  # noqa: F401
        except ImportError as e:
            self.skipTest(f'server env needed: {e}')
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / 'staging').mkdir()
        self.lines = []
        self.materialized = []
        self.seeded = False
        self.ses = MagicMock(return_value=None)

        @contextmanager
        def open_session(slug, purpose='', wait=0.0, require_config=True):
            self.assertFalse(require_config)
            yield SimpleNamespace(workspace_root=self.tmp, staging_dir=self.tmp / 'staging')

        ac = {'assets': STARTER['assets'],
              'dataswale': {'layers': [{'name': n, **l} for n, l in STARTER['layers'].items()]}}
        patches = [
            patch('atlas_workspace.is_seeded', lambda *a: self.seeded),
            patch('atlas_store._s3', lambda: MagicMock()),
            patch('atlas_session.open_session', open_session),
            patch('atlas_create.load_starter', lambda key: json.loads(json.dumps(STARTER))),
            patch('atlas.create', lambda **kw: ac),
            patch('atlas.materialize', lambda c, name: self.materialized.append(name)),
            patch('dataswale_geojson.refresh_all_vector_layers',
                  lambda c: [('roads', 'refreshed'), ('regions', 'created empty')]),
            patch('atlas_create.register_email_address',
                  lambda slug: self.ses(slug) or f'{slug}@fireatlas.org'),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def run_create(self, **params):
        return atlas_create.create(job(**params), self.lines.append)

    def test_steps_in_order(self):
        self.run_create()
        self.assertEqual(self.materialized, ['public_roads', 'notebook', 'webmap', 'console'])
        self.assertEqual(self.lines[0], 'Creating atlas structure (starter: simple)')
        self.assertIn('Deferred runbook (PDF outlets are materialized on request)', self.lines)
        self.assertEqual(self.lines[-1], 'Finished console')

    def test_the_estimate_matches_a_create_without_an_upload(self):
        self.run_create()
        self.assertEqual(len(self.lines), atlas_create.expected_steps(STARTER))

    def test_an_existing_atlas_is_refused_before_anything_runs(self):
        self.seeded = True
        with self.assertRaises(FileExistsError):
            self.run_create()
        self.assertEqual((self.lines, self.materialized), ([], []))

    def test_email_is_left_alone_unless_asked(self):
        self.run_create()
        self.ses.assert_not_called()
        self.assertIn('Email ingest not configured (not requested)', self.lines)

    def test_email_registered_when_asked(self):
        self.run_create(register_email=True)
        self.ses.assert_called_once_with('newatlas')
        self.assertIn('Email ingest configured for newatlas@fireatlas.org', self.lines)

    def test_atlas_geojson_takes_the_site_url_from_the_environment(self):
        with patch.dict(os.environ, {'ATLAS_PUBLIC_BASE_URL': 'https://d1.cloudfront.net'}):
            self.run_create()
        props = json.loads((self.tmp / 'staging' / 'atlas.geojson').read_text())['features'][0]['properties']
        self.assertEqual(props['base_url'], 'https://d1.cloudfront.net/newatlas')
        self.assertEqual(props['app_url'], '/api')
        self.assertIn('layers', props)

    def test_a_failed_outlet_is_reported_and_the_rest_still_build(self):
        def materialize(c, name):
            if name == 'webmap':
                raise RuntimeError('no tiles')
            self.materialized.append(name)
        with patch('atlas.materialize', materialize):
            self.run_create()
        self.assertIn('Warning: webmap failed (no tiles) — skipped', self.lines)
        self.assertIn('console', self.materialized)


if __name__ == '__main__':
    unittest.main()
