"""
Test map_style — the MapLibre style-layer helpers.

Standard library only, and it imports only `map_style`, so it runs on a bare
checkout and cannot be quietly satisfied by a MagicMock left in sys.modules by
another suite (#153).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import map_style


class TestVisibility(unittest.TestCase):
    def test_the_module_under_test_is_real(self):
        # #153: another suite stubs heavy modules into sys.modules, and a
        # MagicMock satisfies any assertion you care to make against it.
        self.assertFalse(hasattr(map_style.visibility, 'return_value'))

    def test_maxzoom_22_becomes_no_limit(self):
        # Every config says 22 and means "no limit"; MapLibre hides a layer at
        # zoom >= maxzoom, and 22 is the map's own ceiling — so the layer
        # disappeared exactly when you finished zooming in.
        self.assertEqual(map_style.visibility({'maxzoom': 22})['maxzoom'], 24)

    def test_anything_at_or_above_the_ceiling_becomes_no_limit(self):
        for value in (22, 23, 24):
            with self.subTest(maxzoom=value):
                self.assertEqual(map_style.visibility({'maxzoom': value})['maxzoom'], 24)

    def test_a_deliberate_maxzoom_is_left_alone(self):
        self.assertEqual(map_style.visibility({'maxzoom': 14})['maxzoom'], 14)
        self.assertEqual(map_style.visibility({'maxzoom': 21})['maxzoom'], 21)

    def test_minzoom_is_carried_through_untouched(self):
        self.assertEqual(map_style.visibility({'minzoom': 10, 'maxzoom': 22}),
                         {'minzoom': 10, 'maxzoom': 24})

    def test_no_maxzoom_stays_no_maxzoom(self):
        self.assertNotIn('maxzoom', map_style.visibility({'minzoom': 10}))

    def test_the_config_is_not_mutated(self):
        # One vis block is merged into the geometry layer, the label layer and
        # any badge layers, so mutating it would leak between them.
        vis = {'minzoom': 10, 'maxzoom': 22}
        map_style.visibility(vis)
        self.assertEqual(vis['maxzoom'], 22)

    def test_empty_and_missing_are_safe(self):
        self.assertEqual(map_style.visibility({}), {})
        self.assertEqual(map_style.visibility(None), {})


if __name__ == '__main__':
    unittest.main()
