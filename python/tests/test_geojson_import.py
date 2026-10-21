"""
Test geojson_import — reading an uploaded GeoJSON into a new atlas (/create).

Standard library only, like the module: webapp.py needs fastapi and utils.py
needs gspread, neither of which is installed on a bare checkout, which is why
this logic lives on its own.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import geojson_import as gi


def poly(w, s, e, n, **props):
    return {'type': 'Feature',
            'properties': props,
            'geometry': {'type': 'Polygon',
                         'coordinates': [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}}


def fc(*features):
    return {'type': 'FeatureCollection', 'features': list(features)}


class TestBoundaryBbox(unittest.TestCase):
    def test_envelope_of_every_feature_not_just_the_first(self):
        # The point of using the envelope: the atlas covers everything uploaded,
        # so a separate "which one is the boundary" convention is unnecessary.
        box = gi.boundary_bbox(fc(poly(-123.8, 39.3, -123.2, 39.9),
                                  poly(-124.1, 39.1, -123.9, 39.4)))
        self.assertEqual(box, {'west': -124.1, 'east': -123.2,
                               'north': 39.9, 'south': 39.1})

    def test_bare_feature(self):
        self.assertEqual(gi.boundary_bbox(poly(-1, -2, 3, 4)),
                         {'west': -1, 'east': 3, 'north': 4, 'south': -2})

    def test_bare_geometry(self):
        geom = {'type': 'Point', 'coordinates': [-123.4, 39.7]}
        self.assertEqual(gi.boundary_bbox(geom),
                         {'west': -123.4, 'east': -123.4,
                          'north': 39.7, 'south': 39.7})

    def test_multipolygon_reads_every_ring(self):
        # utils.geojson_multipolygon_to_bbox reads only the first ring of the
        # first polygon; this must not.
        geom = {'type': 'MultiPolygon', 'coordinates': [
            [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
            [[[5, 5], [6, 5], [6, 6], [5, 6], [5, 5]]]]}
        self.assertEqual(gi.boundary_bbox(geom),
                         {'west': 0, 'east': 6, 'north': 6, 'south': 0})

    def test_geometry_collection(self):
        geom = {'type': 'GeometryCollection', 'geometries': [
            {'type': 'Point', 'coordinates': [1, 2]},
            {'type': 'Point', 'coordinates': [3, 4]}]}
        self.assertEqual(gi.boundary_bbox(geom),
                         {'west': 1, 'east': 3, 'north': 4, 'south': 2})

    def test_elevation_is_ignored(self):
        geom = {'type': 'Point', 'coordinates': [-123.4, 39.7, 812.0]}
        self.assertEqual(gi.boundary_bbox(geom)['west'], -123.4)

    def test_empty_collection_is_refused(self):
        with self.assertRaises(ValueError):
            gi.boundary_bbox(fc())

    def test_projected_coordinates_are_refused(self):
        # Web Mercator metres would otherwise sail through as a bbox off the
        # edge of the world.
        geom = {'type': 'Point', 'coordinates': [-13750000.0, 4830000.0]}
        with self.assertRaises(ValueError):
            gi.boundary_bbox(geom)

    def test_junk_is_refused(self):
        for junk in (None, [], 'a string', {}, {'features': []}):
            with self.subTest(junk=junk):
                with self.assertRaises(ValueError):
                    gi.boundary_bbox(junk)


class TestRouting(unittest.TestCase):
    def setUp(self):
        self.upload = fc(
            poly(-123.8, 39.3, -123.2, 39.9),                                # extent
            poly(-123.6, 39.5, -123.5, 39.6, layer='regions', Description='Upper'),
            poly(-123.5, 39.6, -123.4, 39.7, layer='regions', Description='Lower'),
            poly(-123.3, 39.4, -123.2, 39.5, layer='nonesuch'),
        )
        self.split = gi.split_features_by_layer(self.upload, ['regions', 'creeks'])

    def test_features_route_to_their_named_layer(self):
        self.assertEqual(len(self.split['routed']['regions']), 2)

    def test_a_layer_nobody_named_gets_nothing(self):
        self.assertNotIn('creeks', self.split['routed'])

    def test_features_without_a_layer_are_extent_only(self):
        self.assertEqual(self.split['extent_only'], 1)

    def test_unknown_layer_names_are_reported_not_dropped(self):
        self.assertEqual(self.split['unknown'], {'nonesuch': 1})

    def test_routing_key_is_stripped_but_other_properties_survive(self):
        first = self.split['routed']['regions'][0]
        self.assertNotIn('layer', first['properties'])
        self.assertEqual(first['properties']['Description'], 'Upper')

    def test_geometry_is_carried_through(self):
        self.assertEqual(self.split['routed']['regions'][0]['geometry']['type'],
                         'Polygon')

    def test_no_known_layers_means_everything_is_unknown(self):
        split = gi.split_features_by_layer(self.upload, [])
        self.assertEqual(split['routed'], {})
        self.assertEqual(sum(split['unknown'].values()), 3)


class TestSummary(unittest.TestCase):
    def test_summary_counts_rather_than_returning_features(self):
        summary = gi.summarize(
            fc(poly(-1, -1, 1, 1), poly(0, 0, 1, 1, layer='regions')),
            ['regions'])
        self.assertEqual(summary['routed'], {'regions': 1})
        self.assertEqual(summary['extent_only'], 1)
        self.assertEqual(summary['total'], 2)
        self.assertEqual(summary['unknown'], {})
        self.assertEqual(summary['bbox']['west'], -1)


if __name__ == '__main__':
    unittest.main()
