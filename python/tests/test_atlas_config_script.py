"""scripts/atlas_config.py: the S3 source config is pushed only when it validates,
only the config files are hydrated, and every change is logged."""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import atlas_store  # noqa: E402
from fake_s3 import FakeS3  # noqa: E402

spec = importlib.util.spec_from_file_location(
    'atlas_config_script', HERE.parent.parent / 'scripts' / 'atlas_config.py')
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)

BUCKET = 'scs-atlas-private-test'
ATLAS = 'kennedy'
PREFIX = f'{ATLAS}/staging/'
REPO_KENNEDY = HERE.parent.parent / 'configuration' / 'kennedy.geojson'


class TestPush(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s3 = FakeS3()
        self.old = REPO_KENNEDY.read_text()
        for rel, body in {'atlas.geojson': self.old,
                          'atlas_config.json': json.dumps({'name': ATLAS}),
                          'layers/roads/roads.geojson': '{"features": []}'}.items():
            self.s3.put_object(Bucket=BUCKET, Key=PREFIX + rel, Body=body.encode())
        self.env = mock.patch.dict(os.environ, {
            'ATLAS_PRIVATE_BUCKET': BUCKET,
            'ATLAS_WORKSPACE_ROOT': str(Path(self.tmp.name) / 'ws'),
            'ATLAS_SHARED_DIR': str(Path(self.tmp.name) / 'shared')})
        self.env.start()
        self.s3_patch = mock.patch.object(atlas_store, '_s3', return_value=self.s3)
        self.s3_patch.start()

    def tearDown(self):
        self.s3_patch.stop()
        self.env.stop()
        self.tmp.cleanup()

    def remote(self, rel):
        obj = self.s3.objects.get((BUCKET, PREFIX + rel))
        return obj['body'].decode() if obj else None

    def local(self, text):
        p = Path(self.tmp.name) / 'edit.geojson'
        p.write_text(text)
        return p

    def test_pull_returns_the_exact_bytes(self):
        self.assertEqual(script.read_source(ATLAS), self.old)

    def test_push_writes_logs_and_hydrates_only_config(self):
        d = json.loads(self.old)
        d['features'][0]['properties']['description'] = 'edited'
        new = json.dumps(d, indent=2)
        self.assertEqual(script.push(ATLAS, self.local(new), 'test edit', False, True), 0)
        self.assertEqual(self.remote('atlas.geojson'), new)
        log = self.remote('config_changes.log')
        self.assertIn('test edit', log)
        self.assertEqual(len(log.splitlines()), 1)
        ws = Path(os.environ['ATLAS_WORKSPACE_ROOT']) / ATLAS / 'staging'
        self.assertFalse((ws / 'layers' / 'roads' / 'roads.geojson').exists())
        # the unhydrated layer is untouched in S3
        self.assertEqual(self.remote('layers/roads/roads.geojson'), '{"features": []}')

    def test_invalid_config_is_refused_and_nothing_written(self):
        d = json.loads(self.old)
        d['features'][0]['properties']['versioned_outlets'] = ['html', 'webmap']
        self.assertEqual(script.push(ATLAS, self.local(json.dumps(d)), 'bad', False, True), 1)
        self.assertEqual(self.remote('atlas.geojson'), self.old)
        self.assertIsNone(self.remote('config_changes.log'))

    def test_unchanged_is_a_no_op(self):
        self.assertEqual(script.push(ATLAS, self.local(self.old), 'same', False, True), 0)
        self.assertIsNone(self.remote('config_changes.log'))


if __name__ == '__main__':
    unittest.main()
