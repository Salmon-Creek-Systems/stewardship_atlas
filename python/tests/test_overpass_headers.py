"""fetch_osm sends a descriptive User-Agent: overpass-api.de answers 406 to
generic ones (found in the staging rehearsal, 2026-11-28)."""
import os
import sys
import unittest

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
pytest.importorskip("overpass")
import vector_inlets  # noqa: E402


class TestOverpassHeaders(unittest.TestCase):
    def test_user_agent_is_descriptive(self):
        ua = vector_inlets.OVERPASS_HEADERS['User-Agent']
        self.assertTrue(ua.startswith('stewardship-atlas/'))
        for generic in ('python-requests', 'curl', 'Mozilla'):
            self.assertNotIn(generic, ua)

    def test_fetch_osm_passes_them(self):
        src = open(vector_inlets.__file__).read()
        self.assertIn('overpass.API(timeout=60, headers=OVERPASS_HEADERS)', src)


if __name__ == '__main__':
    unittest.main()
