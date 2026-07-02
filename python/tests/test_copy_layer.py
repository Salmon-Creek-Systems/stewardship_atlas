import os
import sys
import unittest
from pathlib import Path
import tempfile
import json
from unittest.mock import MagicMock

# Add the python directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Stub heavy server-side dependencies so atlas / dataswale_geojson import cleanly.
for _mod in ('outlets', 'outlets_qgis_atlas', 'vector_inlets', 'raster_inlets',
             'eddies', 'versioning', 'deltas_geojson', 'utils',
             'duckdb', 'osgeo', 'osgeo.gdal', 'osgeo.ogr', 'shapely',
             'shapely.geometry'):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

from atlas import plan_layer_copy
import dataswale_geojson


class TestPlanLayerCopy(unittest.TestCase):
    def _plan(self, assets, old='roads', new='roads2'):
        return plan_layer_copy(assets, old, new)

    def test_explicit_out_layer_is_cloned(self):
        assets = {
            'gdal_contours': {'config': {'in_layer': 'elevation', 'out_layer': 'roads'}},
        }
        clones, appends = self._plan(assets)
        self.assertEqual(appends, [])
        self.assertEqual(len(clones), 1)
        c = clones[0]
        self.assertEqual(c['base_key'], 'gdal_contours')
        self.assertEqual(c['new_key'], 'gdal_contours_roads2')
        self.assertEqual(c['overrides'], {'out_layer': 'roads2'})

    def test_in_place_transform_overrides_both_fields(self):
        # tiff_to_cog style: in_layer == out_layer == old
        assets = {'roads_cog': {'config': {'in_layer': 'roads', 'out_layer': 'roads',
                                           'fetch_type': 'tiff_to_cog'}}}
        clones, _ = self._plan(assets)
        self.assertEqual(clones[0]['overrides'],
                         {'out_layer': 'roads2', 'in_layer': 'roads2'})

    def test_single_layer_outlet_no_out_layer_is_cloned(self):
        # s3_upload style: in_layer only, no out_layer
        assets = {'roads_s3': {'config': {'in_layer': 'roads', 'fetch_type': 's3_upload'}}}
        clones, appends = self._plan(assets)
        self.assertEqual(appends, [])
        self.assertEqual(clones[0]['overrides'], {'in_layer': 'roads2'})
        self.assertEqual(clones[0]['new_key'], 'roads_s3_roads2')

    def test_read_only_asset_is_not_cloned(self):
        # reads old, writes elsewhere → must NOT be cloned
        assets = {'contours': {'config': {'in_layer': 'roads', 'out_layer': 'contours'}}}
        clones, appends = self._plan(assets)
        self.assertEqual(clones, [])
        self.assertEqual(appends, [])

    def test_in_layers_consumer_is_appended(self):
        assets = {'webmap': {'in_layers': ['basemap', 'roads', 'creeks']}}
        clones, appends = self._plan(assets)
        self.assertEqual(clones, [])
        self.assertEqual(appends, ['webmap'])

    def test_top_level_fields_without_config_wrapper(self):
        # some assets carry fields at top level rather than under 'config'
        assets = {'raw': {'out_layer': 'roads'}}
        clones, _ = self._plan(assets)
        self.assertEqual(clones[0]['overrides'], {'out_layer': 'roads2'})

    def test_unrelated_layer_untouched(self):
        assets = {
            'a': {'config': {'out_layer': 'creeks'}},
            'b': {'in_layers': ['creeks', 'ponds']},
        }
        clones, appends = self._plan(assets)
        self.assertEqual(clones, [])
        self.assertEqual(appends, [])


class TestCopyLayerFile(unittest.TestCase):
    def test_copies_dir_and_renames_layer_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp)
            src = staging / 'layers' / 'roads'
            src.mkdir(parents=True)
            (src / 'roads.geojson').write_text('{"type":"FeatureCollection","features":[]}')
            (src / 'stats.json').write_text('{"min":0}')

            dataswale_geojson.copy_layer_file(staging, 'roads', 'roads2')

            dst = staging / 'layers' / 'roads2'
            self.assertTrue((dst / 'roads2.geojson').exists())
            self.assertFalse((dst / 'roads.geojson').exists())
            # sidecar untouched
            self.assertTrue((dst / 'stats.json').exists())
            # original left in place
            self.assertTrue((src / 'roads.geojson').exists())

    def test_renames_compound_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp)
            src = staging / 'layers' / 'hillshade'
            src.mkdir(parents=True)
            (src / 'hillshade.tiff').write_bytes(b'\x00')
            (src / 'hillshade.tiff.jpg').write_bytes(b'\x00')

            dataswale_geojson.copy_layer_file(staging, 'hillshade', 'hs2')

            dst = staging / 'layers' / 'hs2'
            self.assertTrue((dst / 'hs2.tiff').exists())
            self.assertTrue((dst / 'hs2.tiff.jpg').exists())

    def test_missing_source_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                dataswale_geojson.copy_layer_file(Path(tmp), 'nope', 'x')

    def test_existing_dest_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp)
            (staging / 'layers' / 'roads').mkdir(parents=True)
            (staging / 'layers' / 'roads2').mkdir(parents=True)
            with self.assertRaises(FileExistsError):
                dataswale_geojson.copy_layer_file(staging, 'roads', 'roads2')


if __name__ == '__main__':
    unittest.main()
