"""
Tests for vector_inlets._inat_api_params — the api_params passthrough that
exposes the whole iNaturalist observations API to config (issue #171).

vector_inlets pulls in duckdb/shapely/overpass, none of which are installed
locally, so they're stubbed. The function under test is pure, so nothing real
is exercised through the stubs.
"""
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Other suites push MagicMocks into sys.modules and don't always remove them;
# a mocked vector_inlets here would make every assertion pass vacuously. Purge
# and restore, and assert we got the real module.
_REPLACED = {}
if isinstance(sys.modules.get('vector_inlets'), MagicMock):
    _REPLACED['vector_inlets'] = sys.modules.pop('vector_inlets')

_STUBBED = []
for _mod in ('duckdb', 'shapely', 'shapely.geometry', 'overpass', 'federation',
             'deltas_geojson', 'versioning', 'utils'):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()
        _STUBBED.append(_mod)

import vector_inlets

assert not isinstance(vector_inlets, MagicMock), "vector_inlets is mocked"


def tearDownModule():
    for _mod in _STUBBED:
        sys.modules.pop(_mod, None)
    sys.modules.pop('vector_inlets', None)
    sys.modules.update(_REPLACED)


class TestInatApiParams(unittest.TestCase):

    def call(self, api_params, **rest):
        return vector_inlets._inat_api_params(dict(api_params=api_params, **rest))

    def test_absent_or_empty_yields_nothing(self):
        self.assertEqual(vector_inlets._inat_api_params({}), {})
        self.assertEqual(self.call(None), {})
        self.assertEqual(self.call({}), {})

    def test_booleans_become_api_strings(self):
        """The API wants true/false as strings, not Python's True/False."""
        self.assertEqual(self.call({'threatened': True}), {'threatened': 'true'})
        self.assertEqual(self.call({'captive': False}), {'captive': 'false'})

    def test_non_boolean_values_pass_through(self):
        self.assertEqual(
            self.call({'iconic_taxa': 'Aves,Mammalia', 'taxon_id': 12345}),
            {'iconic_taxa': 'Aves,Mammalia', 'taxon_id': 12345})

    def test_none_values_are_dropped(self):
        """So a shared template can declare a key without setting it."""
        self.assertEqual(self.call({'threatened': None, 'native': True}),
                         {'native': 'true'})

    def test_reserved_params_are_rejected(self):
        """Overriding these would silently break the bbox scope or the paging loop."""
        for key in ('swlng', 'swlat', 'nelng', 'nelat',
                    'per_page', 'order', 'order_by', 'id_above'):
            with self.subTest(key=key):
                with self.assertRaises(ValueError) as ctx:
                    self.call({key: 'x'})
                self.assertIn(key, str(ctx.exception))

    def test_reserved_check_reports_every_offender(self):
        with self.assertRaises(ValueError) as ctx:
            self.call({'per_page': 1, 'swlng': 2, 'threatened': True})
        message = str(ctx.exception)
        self.assertIn('per_page', message)
        self.assertIn('swlng', message)

    def test_non_dict_is_rejected(self):
        with self.assertRaises(ValueError):
            self.call(['threatened'])
        with self.assertRaises(ValueError):
            self.call('threatened=true')

    def test_reserved_set_matches_what_the_fetch_loop_owns(self):
        self.assertEqual(
            set(vector_inlets.INAT_RESERVED_PARAMS),
            {'swlng', 'swlat', 'nelng', 'nelat',
             'per_page', 'order', 'order_by', 'id_above'})


if __name__ == '__main__':
    unittest.main()
