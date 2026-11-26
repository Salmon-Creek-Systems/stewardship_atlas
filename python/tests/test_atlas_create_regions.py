"""atlas.create() must not write layer data — in particular it must not
overwrite the regions layer with the atlas bbox (#188).

Needs server deps (gspread etc.) to import `atlas`; skipped locally.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import atlas
    ATLAS_AVAILABLE = True
except ImportError:
    ATLAS_AVAILABLE = False


BBOX_FC = {
    "type": "FeatureCollection",
    "features": [{
        "type": "Feature",
        "properties": {"name": "testatlas"},
        "geometry": {"type": "Polygon", "coordinates": [[
            [-123.9, 39.5], [-123.3, 39.5], [-123.3, 40.0],
            [-123.9, 40.0], [-123.9, 39.5]]]},
    }],
}

HAND_DRAWN = {
    "type": "FeatureCollection",
    "features": [{
        "type": "Feature",
        "properties": {"name": "Cherry Creek Unit", "atlas_id": "abc"},
        "geometry": {"type": "Polygon", "coordinates": [[
            [-123.46, 39.54], [-123.37, 39.54], [-123.37, 39.63],
            [-123.46, 39.63], [-123.46, 39.54]]]},
    }],
}


def _config(data_root):
    return {
        'name': 'testatlas',
        'data_root': str(data_root),
        'assets': {},
        'dataswale': {
            'bbox': {'west': -123.9, 'east': -123.3, 'south': 39.5, 'north': 40.0},
            'layers': [{'name': 'regions', 'geometry_type': 'polygon'}],
        },
    }


@unittest.skipUnless(ATLAS_AVAILABLE, "server deps not installed")
class TestCreateLeavesRegionsAlone(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.shared = self.root / 'shared'
        self.shared.mkdir()
        self.regions_dir = self.root / 'testatlas' / 'staging' / 'layers' / 'regions'

    def tearDown(self):
        self._tmp.cleanup()

    def _create(self):
        with patch.object(atlas, 'create_config', return_value=_config(self.root)):
            atlas.create(data_root=str(self.root), shared_dir=self.shared,
                         feature_collection=BBOX_FC)

    def test_fresh_atlas_gets_no_bbox_region(self):
        self._create()
        self.assertTrue(self.regions_dir.is_dir())
        self.assertFalse((self.regions_dir / 'regions.geojson').exists())
        self.assertFalse((self.regions_dir / 'default_atlas_regions.geojson').exists())

    def test_rebuild_keeps_hand_drawn_regions(self):
        self.regions_dir.mkdir(parents=True)
        path = self.regions_dir / 'regions.geojson'
        path.write_text(json.dumps(HAND_DRAWN))
        before = path.read_bytes()
        self._create()
        self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
