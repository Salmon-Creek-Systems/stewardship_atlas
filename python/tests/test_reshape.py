"""
Test reshape.apply_reshape — the geometry-replacing delta behind webedit Reshape.

Standard library only, so it runs on a bare checkout.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import reshape


def _feature(atlas_id, geometry, **props):
    return {'type': 'Feature', 'geometry': geometry,
            'properties': {'atlas_id': atlas_id, **props}}


def _square(x, y, size=1.0):
    return {'type': 'Polygon', 'coordinates': [[[x, y], [x + size, y], [x + size, y + size],
                                                [x, y + size], [x, y]]]}


class TestApplyReshape(unittest.TestCase):

    def setUp(self):
        self.layer = [
            _feature('a', _square(0, 0), name='zone A', anchor='{"latitude": 1}'),
            _feature('b', {'type': 'Point', 'coordinates': [5, 5]}, name='hydrant'),
        ]

    def test_replaces_geometry_and_keeps_properties(self):
        bigger = _square(0, 0, size=2.0)
        reshaped, skipped = reshape.apply_reshape(self.layer, [_feature('a', bigger)])
        self.assertEqual(reshaped, ['a'])
        self.assertEqual(skipped, [])
        self.assertEqual(self.layer[0]['geometry'], bigger)
        self.assertEqual(self.layer[0]['properties'],
                         {'atlas_id': 'a', 'name': 'zone A', 'anchor': '{"latitude": 1}'})

    def test_other_features_untouched(self):
        before = dict(self.layer[1])
        reshape.apply_reshape(self.layer, [_feature('a', _square(3, 3))])
        self.assertEqual(self.layer[1], before)

    def test_moved_point(self):
        reshape.apply_reshape(self.layer, [_feature('b', {'type': 'Point', 'coordinates': [6, 7]})])
        self.assertEqual(self.layer[1]['geometry']['coordinates'], [6, 7])

    def test_unknown_id_is_skipped_not_added(self):
        reshaped, skipped = reshape.apply_reshape(self.layer, [_feature('zzz', _square(9, 9))])
        self.assertEqual(reshaped, [])
        self.assertEqual(skipped[0][0], 'zzz')
        self.assertEqual(len(self.layer), 2)

    def test_geometry_type_change_is_refused(self):
        reshaped, skipped = reshape.apply_reshape(
            self.layer, [_feature('b', _square(5, 5))])
        self.assertEqual(reshaped, [])
        self.assertIn('Polygon', skipped[0][1])
        self.assertEqual(self.layer[1]['geometry']['type'], 'Point')

    def test_missing_atlas_id_is_skipped(self):
        delta = {'type': 'Feature', 'geometry': _square(0, 0), 'properties': {}}
        reshaped, skipped = reshape.apply_reshape(self.layer, [delta])
        self.assertEqual(reshaped, [])
        self.assertEqual(skipped, [(None, 'no atlas_id')])

    def test_shape_fn_applies_to_reshaped_only(self):
        seen = []

        def shape_fn(f):
            seen.append(f['properties']['atlas_id'])
            return dict(f, shaped=True)

        reshape.apply_reshape(self.layer, [_feature('a', _square(0, 0, 2.0))], shape_fn)
        self.assertEqual(seen, ['a'])
        self.assertTrue(self.layer[0].get('shaped'))
        self.assertNotIn('shaped', self.layer[1])

    def test_delta_properties_beyond_id_are_ignored(self):
        delta = _feature('a', _square(0, 0, 2.0), name='overwritten?')
        reshape.apply_reshape(self.layer, [delta])
        self.assertEqual(self.layer[0]['properties']['name'], 'zone A')


if __name__ == '__main__':
    unittest.main()
