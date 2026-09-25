"""
Test map_style — the MapLibre style-layer helpers.

Standard library only, and it imports only `map_style`, so it runs on a bare
checkout and cannot be quietly satisfied by a MagicMock left in sys.modules by
another suite (#153).
"""
import os
import sys
import base64
import json
import tempfile
import unittest
from urllib.parse import parse_qs, urlparse
from pathlib import Path

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


class TestLocalBasemapOption(unittest.TestCase):
    """The basemap dropdown may only offer a layer the style actually has."""

    RASTER = {'geometry_type': 'raster'}
    COG_RASTER = {'geometry_type': 'raster', 'cog': True}
    LINE = {'geometry_type': 'linestring'}

    def test_offered_when_the_named_raster_is_in_the_outlet(self):
        # kennedy: 'basemap' is a raster and is in the webmap's in_layers.
        self.assertTrue(map_style.has_local_basemap(
            ['basemap', 'roads'], {'basemap': self.RASTER, 'roads': self.LINE}))

    def test_not_offered_when_the_atlas_has_no_such_layer(self):
        # An atlas showing only COGs borrowed from elsewhere, with no basemap
        # layer of its own — south_fork_eel before it got one.
        self.assertFalse(map_style.has_local_basemap(
            ['fhe_canopy_density', 'roads'],
            {'fhe_canopy_density': self.RASTER, 'roads': self.LINE}))

    def test_a_cog_backed_basemap_is_still_a_basemap(self):
        # south_fork_eel now: 'basemap' is a COG windowed from a shared source,
        # so its style layer is added after load rather than sitting in the
        # style. It is still `basemap-layer`, so the option is genuine — the
        # ordering that makes it true lives in webmap.js, not here.
        self.assertTrue(map_style.has_local_basemap(
            ['basemap', 'fhe_canopy_density', 'roads'],
            {'basemap': self.COG_RASTER,
             'fhe_canopy_density': self.COG_RASTER,
             'roads': self.LINE}))

    def test_a_leading_raster_is_not_evidence_of_a_basemap(self):
        # The bug this replaces: any raster first in in_layers offered a
        # basemap option wired to a style layer that does not exist.
        layers = {'canopy': self.RASTER, 'basemap': self.RASTER, 'roads': self.LINE}
        self.assertFalse(map_style.has_local_basemap(['canopy', 'roads'], layers))

    def test_declared_but_not_in_the_outlet_is_not_offered(self):
        # fhe has a 'basemap' layer but its webmap does not reference it, so
        # no 'basemap-layer' exists in that style.
        self.assertFalse(map_style.has_local_basemap(
            ['creeks'], {'basemap': self.RASTER, 'creeks': self.LINE}))

    def test_a_vector_layer_of_that_name_is_not_a_basemap(self):
        self.assertFalse(map_style.has_local_basemap(
            ['basemap'], {'basemap': self.LINE}))

    def test_webedit_uses_its_own_layer_name(self):
        layers = {'hillshade': self.RASTER, 'basemap': self.RASTER}
        self.assertTrue(map_style.has_local_basemap(['hillshade'], layers, 'hillshade'))
        self.assertFalse(map_style.has_local_basemap(['basemap'], layers, 'hillshade'))

    def test_empty_and_missing_are_safe(self):
        self.assertFalse(map_style.has_local_basemap([], {}))
        self.assertFalse(map_style.has_local_basemap(None, {}))


if __name__ == '__main__':
    unittest.main()

class TestPaintFor(unittest.TestCase):
    """MapLibre refuses a whole style over one paint property that does not
    belong to the layer type, so each generated layer keeps only its own."""

    POINT_PAINT = {'circle-radius': 9, 'circle-color': '#0c5e2e', 'circle-opacity': 0.8,
                   'icon-color': '#ffffff', 'text-halo-width': 1}

    def test_circle_layer_keeps_circle_properties(self):
        self.assertEqual(map_style.paint_for('circle', self.POINT_PAINT),
                         {'circle-radius': 9, 'circle-color': '#0c5e2e', 'circle-opacity': 0.8})

    def test_symbol_layer_keeps_text_and_icon_only(self):
        # The reported failure: circle-* reaching the icon layer of a point
        # layer that has both.
        self.assertEqual(map_style.paint_for('symbol', self.POINT_PAINT),
                         {'icon-color': '#ffffff', 'text-halo-width': 1})

    def test_line_and_fill(self):
        paint = {'line-width': 4, 'line-color': '#fff', 'fill-opacity': 0.5}
        self.assertEqual(map_style.paint_for('line', paint), {'line-width': 4, 'line-color': '#fff'})
        self.assertEqual(map_style.paint_for('fill', paint), {'fill-opacity': 0.5})

    def test_unknown_type_passes_through(self):
        self.assertEqual(map_style.paint_for('heatmap', {'heatmap-radius': 3}), {'heatmap-radius': 3})

    def test_empty_paint(self):
        self.assertEqual(map_style.paint_for('circle', {}), {})
        self.assertEqual(map_style.paint_for('circle', None), {})



class TestQgisMmWidth(unittest.TestCase):
    ROAD = {'vector_width': True, 'qgis_width_units': 'mm'}

    def test_opt_in_only(self):
        # Atlases tuned against the ground-metres behaviour must keep it.
        self.assertFalse(map_style.qgis_width_in_mm({'vector_width': True}))
        self.assertTrue(map_style.qgis_width_in_mm(self.ROAD))

    def test_reads_vector_width_as_paper_mm(self):
        expr, _ = map_style.qgis_mm_width(self.ROAD, has_width_field=True)
        self.assertEqual(expr, f'coalesce("vector_width", 2) * {map_style.QGIS_MM_PER_WIDTH_UNIT:g}')

    def test_width_scale_and_line_scale_multiply(self):
        layer = dict(self.ROAD, qgis_width_scale=2.0)
        _, constant = map_style.qgis_mm_width(layer, has_width_field=True, line_scale=1.5)
        self.assertAlmostEqual(constant, 2 * map_style.QGIS_MM_PER_WIDTH_UNIT * 3.0)

    def test_no_width_field_means_constant_only(self):
        expr, constant = map_style.qgis_mm_width(
            dict(self.ROAD, constant_width=5), has_width_field=False)
        self.assertIsNone(expr)
        self.assertAlmostEqual(constant, 5 * map_style.QGIS_MM_PER_WIDTH_UNIT)

    def test_layer_without_vector_width_ignores_the_field(self):
        expr, _ = map_style.qgis_mm_width({'qgis_width_units': 'mm'}, has_width_field=True)
        self.assertIsNone(expr)


class TestQgisCogSources(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_local_files_come_first_cog_before_tiff(self):
        (self.dir / 'basemap.tiff').touch()
        (self.dir / 'basemap.cog.tif').touch()
        sources = map_style.qgis_cog_sources('basemap', {'cog': True}, self.dir, 'sfe')
        self.assertEqual(sources[:2], [str(self.dir / 'basemap.cog.tif'),
                                       str(self.dir / 'basemap.tiff')])

    def test_missing_local_files_are_skipped(self):
        sources = map_style.qgis_cog_sources('basemap', {'cog': True}, self.dir, 'sfe')
        self.assertEqual(len(sources), 1)
        self.assertTrue(sources[0].startswith('/vsicurl/'))

    def test_s3_fallback_is_the_s3_upload_location(self):
        # s3_upload writes {atlas}/rasters/{layer}/{layer}.cog.tif to
        # scs-atlas-data, which is in us-east-1 (us-west-1 answers 301).
        sources = map_style.qgis_cog_sources('basemap', {}, self.dir, 'sfe')
        self.assertEqual(sources[-1], '/vsicurl/https://scs-atlas-data.s3.us-east-1'
                         '.amazonaws.com/sfe/rasters/basemap/basemap.cog.tif')

    def test_cog_url_before_own_s3(self):
        layer = {'cog_url': 'https://example.org/fhe/canopy.cog.tif'}
        sources = map_style.qgis_cog_sources('canopy', layer, self.dir, 'sfe')
        self.assertEqual(sources[0], '/vsicurl/https://example.org/fhe/canopy.cog.tif')


class TestRegionViewUrl(unittest.TestCase):
    # ~1.1 km square near SFE
    BBOX = (-123.70, 39.70, -123.687, 39.71)

    def _state(self, url):
        s = parse_qs(urlparse(url).query)['s'][0]
        return json.loads(base64.b64decode(s))

    def test_state_decodes_to_what_webmap_js_reads(self):
        url = map_style.region_view_url(self.BBOX, 'https://fireatlas.org/sfe', ['roads_primary'])
        self.assertTrue(url.startswith('https://fireatlas.org/sfe/staging/outlets/webmap/?s='))
        state = self._state(url)
        self.assertAlmostEqual(state['a'], 39.705)
        self.assertAlmostEqual(state['o'], -123.6935)
        self.assertNotIn('p', state)      # a view, not a point: no pin

    def test_regions_layer_is_hidden_and_missing_layers_dropped(self):
        url = map_style.region_view_url(self.BBOX, '', ['regions', 'creeks', 'roads_primary', 'photos'])
        self.assertEqual(self._state(url)['l'], ['roads_primary', 'creeks'])

    def test_state_is_percent_encoded(self):
        # URLSearchParams reads a raw '+' back as a space and breaks the base64.
        state = map_style.encode_view_state(1, 2, 3, ['a' * 40])
        url = map_style.webmap_view_url('', state + '+/=')
        self.assertNotIn('+', url.split('?s=')[1])

    def test_fit_zoom_bigger_region_zooms_out(self):
        small = map_style.fit_zoom(self.BBOX)
        large = map_style.fit_zoom((-123.75, 39.70, -123.69, 39.76))
        self.assertGreater(small, large)
        self.assertTrue(14 < small < 16.5)

    def test_fit_zoom_degenerate_bbox(self):
        self.assertEqual(map_style.fit_zoom((1, 1, 1, 1)), 17)


class TestRegionsPanel(unittest.TestCase):
    def _feature(self, name, url):
        return {'type': 'Feature', 'properties': {'name': name, 'webmap_url': url}}

    def test_no_linked_regions_means_no_panel(self):
        self.assertEqual(map_style.regions_panel_html([]), '')
        self.assertEqual(map_style.regions_panel_html([self._feature('A', None)]), '')

    def test_one_option_per_linked_region_in_order(self):
        out = map_style.regions_panel_html([self._feature('North', 'u1'),
                                            self._feature('Nowhere', None),
                                            self._feature('South', 'u2')])
        self.assertIn('id="regions-select"', out)
        self.assertLess(out.index('North'), out.index('South'))
        self.assertNotIn('Nowhere', out)

    def test_names_and_urls_are_escaped(self):
        out = map_style.regions_panel_html([self._feature('<b>', 'x?a=1&b="2"')])
        self.assertIn('&lt;b&gt;', out)
        self.assertIn('&amp;b=&quot;2&quot;', out)

    def test_panel_has_no_format_braces(self):
        # generate_map_page substitutes it into a str.format template.
        out = map_style.regions_panel_html([self._feature('A', 'u')])
        self.assertNotIn('{', out)


class TestEditPageLayers(unittest.TestCase):
    """An edit page must show the layer it edits (south_fork_eel's Hargus page
    showed only `photos`, whose identical camera icons got selected instead)."""

    def test_missing_edit_layer_is_added_on_top(self):
        self.assertEqual(map_style.edit_page_layers(['creeks', 'photos'], 'hargus'),
                         ['creeks', 'photos', 'hargus'])

    def test_listed_edit_layer_keeps_its_place(self):
        self.assertEqual(map_style.edit_page_layers(['photos', 'creeks'], 'photos'),
                         ['photos', 'creeks'])

    def test_does_not_mutate_the_outlet_list(self):
        in_layers = ['creeks']
        map_style.edit_page_layers(in_layers, 'hargus')
        self.assertEqual(in_layers, ['creeks'])

    def test_empty_in_layers(self):
        self.assertEqual(map_style.edit_page_layers(None, 'hargus'), ['hargus'])
