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


class TestCogAddressing(unittest.TestCase):
    """A raster layer can read a COG published by a different atlas."""

    def test_a_layer_without_cog_url_uses_its_own_atlas(self):
        self.assertEqual(
            map_style.cog_href({'cog': True}, '../../layers/canopy/canopy.cog.tif'),
            '../../layers/canopy/canopy.cog.tif')

    def test_cog_url_wins(self):
        foreign = 'https://fireatlas.org/fhe/CURRENT/layers/canopy_density/canopy_density.cog.tif'
        self.assertEqual(map_style.cog_href({'cog': True, 'cog_url': foreign},
                                            '../../layers/x/x.cog.tif'), foreign)

    def test_empty_cog_url_falls_back_rather_than_emitting_nothing(self):
        self.assertEqual(map_style.cog_href({'cog_url': ''}, '../../layers/x/x.cog.tif'),
                         '../../layers/x/x.cog.tif')

    def test_is_external_cog(self):
        self.assertTrue(map_style.is_external_cog({'cog_url': 'https://example.org/a.tif'}))
        self.assertFalse(map_style.is_external_cog({'cog': True}))
        self.assertFalse(map_style.is_external_cog({'cog_url': ''}))


class TestCogColor(unittest.TestCase):
    def test_auto_is_substituted_from_stats(self):
        self.assertEqual(
            map_style.resolve_cog_color('BrewerPuBu9,auto,auto,c',
                                        {'min': 6.90775, 'max': 13.1223}),
            'BrewerPuBu9,6.9078,13.1223,c')

    def test_one_sided_auto(self):
        self.assertEqual(
            map_style.resolve_cog_color('BrewerYlGn9,0,auto,c', {'min': 1, 'max': 100}),
            'BrewerYlGn9,0,100,c')

    def test_literal_ranges_are_untouched_even_with_stats(self):
        # canopy_density and ladder_fuel_density carry explicit ranges.
        self.assertEqual(
            map_style.resolve_cog_color('BrewerBuGn9,0,50,c', {'min': 3, 'max': 9}),
            'BrewerBuGn9,0,50,c')

    def test_no_stats_leaves_the_string_for_the_caller_to_complain_about(self):
        # outlets._resolve_cog_color raises for an external COG and warns
        # otherwise; substituting a wrong number silently would be worse.
        self.assertEqual(map_style.resolve_cog_color('BrewerPuBu9,auto,auto,c', None),
                         'BrewerPuBu9,auto,auto,c')

    def test_wants_stats_detects_only_a_real_auto_field(self):
        self.assertTrue(map_style.cog_color_wants_stats('BrewerPuBu9,auto,auto,c'))
        self.assertTrue(map_style.cog_color_wants_stats('BrewerPuBu9,0,auto,c'))
        self.assertFalse(map_style.cog_color_wants_stats('BrewerBuGn9,0,50,c'))
        self.assertFalse(map_style.cog_color_wants_stats(None))
        # 'auto' must be a whole field, not a substring of a palette name
        self.assertFalse(map_style.cog_color_wants_stats('Autobahn9,0,50,c'))

    def test_malformed_cog_color_is_returned_rather_than_crashing(self):
        self.assertEqual(map_style.resolve_cog_color('auto', {'min': 1, 'max': 2}), 'auto')

    def test_missing_stats_keys_leave_that_end_alone(self):
        self.assertEqual(
            map_style.resolve_cog_color('BrewerPuBu9,auto,auto,c', {'min': 5}),
            'BrewerPuBu9,5,auto,c')


if __name__ == '__main__':
    unittest.main()
