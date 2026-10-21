"""
Test layer_plans — what deleting a layer changes, and what blocks it.

Standard library only and imports only `layer_plans`, so it runs on a bare
checkout and cannot be satisfied by a MagicMock another suite left in
sys.modules (#153).
"""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import layer_plans


def resolved(**fields):
    """An asset as config['assets'] holds it: template merged into 'config'."""
    return {**fields, 'config': dict(fields)}


class TestPlanLayerDelete(unittest.TestCase):
    def setUp(self):
        self.props = {'layers': {'roads': {}, 'creeks': {}, 'regions': {}, 'grid': {}},
                      'email_photo_default_layer': 'photos'}
        self.assets = {
            'roads_inlet': resolved(out_layer='roads', config_def='overture_roads'),
            'roads_cog': resolved(in_layer='roads'),                 # in-place: producer
            'webmap': resolved(in_layers=['roads', 'creeks'], hidden_layers=['roads']),
            'sqldb': resolved(layers=['roads', 'creeks']),
        }

    def plan(self, layer='roads'):
        return layer_plans.plan_layer_delete(self.props, self.assets, layer)

    def test_the_module_under_test_is_real(self):
        self.assertFalse(hasattr(layer_plans.plan_layer_delete, 'return_value'))

    def test_producers_are_removed(self):
        self.assertEqual(self.plan()['remove_assets'], ['roads_cog', 'roads_inlet'])

    def test_every_list_field_loses_the_name(self):
        self.assertEqual(self.plan()['list_edits'],
                         [('sqldb', 'layers'), ('webmap', 'in_layers'), ('webmap', 'hidden_layers')])
        self.assertEqual(self.plan()['blockers'], [])

    def test_eddy_building_another_layer_blocks(self):
        self.assets['contours'] = resolved(in_layer='roads', out_layer='roads_buffer')
        blockers = self.plan()['blockers']
        self.assertEqual(len(blockers), 1)
        self.assertIn("'contours'", blockers[0])

    def test_regions_and_lrs_zone_references_block(self):
        self.assets['runbook'] = resolved(regions_layer='regions', in_layers=['roads', 'creeks'])
        self.assets['road_lrs'] = resolved(in_layers=['roads'], lrs_zones_layer='grid',
                                           out_layer='road_mileage')
        self.assertIn('regions_layer', self.plan('regions')['blockers'][0])
        self.assertIn('lrs_zones_layer', self.plan('grid')['blockers'][0])

    def test_emptying_a_required_list_blocks(self):
        self.assets['count'] = resolved(in_layer='grid', in_layers=['roads'], out_layer='counts')
        self.assertIn("'count'", ' '.join(self.plan()['blockers']))

    def test_emptying_hidden_layers_is_fine(self):
        self.assets['webmap'] = resolved(in_layers=['roads', 'creeks'], hidden_layers=['roads'])
        self.assertEqual(self.plan()['blockers'], [])

    def test_email_default_layer_blocks(self):
        self.props['layers']['photos'] = {}
        self.assertIn('email_photo_default_layer', self.plan('photos')['blockers'][0])

    def test_unknown_layer_blocks(self):
        self.assertTrue(self.plan('nope')['blockers'])

    def test_names_that_merely_match_are_not_references(self):
        # An asset may be *named* like a layer, or use it as a config_def — not a reference.
        self.assets['creeks'] = resolved(config_def='creeks', in_layers=['roads', 'creeks'])
        plan = self.plan('creeks')
        self.assertEqual(plan['remove_assets'], [])
        self.assertIn(('creeks', 'in_layers'), plan['list_edits'])


class TestApplyLayerDelete(unittest.TestCase):
    def test_source_edited_and_template_only_list_overridden(self):
        props = {'layers': {'roads': {}, 'creeks': {}},
                 'assets': {'roads_inlet': {'out_layer': 'roads'},
                            'webmap': {'in_layers': ['roads', 'creeks']},
                            'sqldb': {'config_def': 'sqldb'}}}   # layers come from the template
        assets = {'roads_inlet': resolved(out_layer='roads'),
                  'webmap': resolved(in_layers=['roads', 'creeks']),
                  'sqldb': resolved(config_def='sqldb', layers=['roads', 'creeks'])}
        plan = layer_plans.plan_layer_delete(props, assets, 'roads')
        layer_plans.apply_layer_delete(props, assets, plan)
        self.assertNotIn('roads', props['layers'])
        self.assertNotIn('roads_inlet', props['assets'])
        self.assertEqual(props['assets']['webmap']['in_layers'], ['creeks'])
        self.assertEqual(props['assets']['sqldb']['layers'], ['creeks'])

    def test_blocked_plan_changes_nothing(self):
        props = {'layers': {'roads': {}}, 'assets': {'e': {'in_layer': 'roads', 'out_layer': 'x'}}}
        before = copy.deepcopy(props)
        assets = {'e': resolved(in_layer='roads', out_layer='x')}
        plan = layer_plans.plan_layer_delete(props, assets, 'roads')
        with self.assertRaises(ValueError):
            layer_plans.apply_layer_delete(props, assets, plan)
        self.assertEqual(props, before)


if __name__ == '__main__':
    unittest.main()
