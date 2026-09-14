"""Seeding S3 from the box's on-disk atlases (#159 task 8).

The planners are pure and take plain paths, so the whole exclusion policy is
testable without S3 or a copy of the box. What they get wrong is expensive in
both directions: following `staging/local` copies 25 GB into every atlas's
prefix, and dropping the wrong outlet file loses a customer deliverable.

    cd python && python -m pytest tests/test_atlas_seed.py -v
"""

import os
import sys
import unittest
from pathlib import Path
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from fake_s3 import FakeS3
import atlas_seed
import atlas_shared
import atlas_workspace as ws


def write(root: Path, rel: str, content='x'):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


class TestStagingPlan(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.staging = self.root / 'staging'
        self.addCleanup(self.tmp.cleanup)

        write(self.staging, 'atlas_config.json', '{}')
        write(self.staging, 'atlas.geojson', '{}')
        write(self.staging, 'layers/roads/roads.geojson', '{"features":[]}')
        write(self.staging, 'outlets/webmap/index.html', '<html>')
        write(self.staging, 'outlets/runbook/kennedy_runbook.pdf', 'PDF')
        write(self.staging, 'deltas/roads/pending__1__create.geojson', '{}')
        write(self.staging, 'deltas/roads/work/applied__0__create.geojson', '{}')

    def rels(self, **kwargs):
        return [rel for _, rel in atlas_seed.plan_staging_upload(self.staging, **kwargs)]

    def test_the_ordinary_tree_is_carried(self):
        rels = self.rels()
        for expected in ('atlas_config.json', 'atlas.geojson',
                         'layers/roads/roads.geojson',
                         'outlets/webmap/index.html',
                         'deltas/roads/pending__1__create.geojson'):
            self.assertIn(expected, rels)

    def test_the_source_geojson_travels_with_the_atlas(self):
        """staging/atlas.geojson is what a later config rebuild reads (task 7),
        so a seed that dropped it would strand the atlas."""
        self.assertIn('atlas.geojson', self.rels())

    def test_the_local_symlink_is_never_followed(self):
        """It points at the 25 GB shared store. Following it would copy that
        into every atlas's prefix."""
        shared = self.root / 'shared_store'
        write(shared, 'huge.gpkg', 'x' * 100)
        (self.staging / 'local').symlink_to(shared, target_is_directory=True)

        rels = self.rels()
        self.assertFalse([r for r in rels if r.startswith('local')], rels)

    def test_a_symlinked_file_is_not_followed_either(self):
        """The backstop: a symlink nobody named in EXCLUDE_PATTERNS must not
        silently multiply the upload."""
        target = write(self.root, 'elsewhere/big.tiff', 'y' * 50)
        (self.staging / 'layers' / 'roads' / 'linked.tiff').symlink_to(target)
        self.assertNotIn('layers/roads/linked.tiff', self.rels())

    def test_credentials_are_not_seeded(self):
        write(self.staging, 'outlets/html/.htpasswd', 'admin:$apr1$x')
        write(self.staging, '.htpasswd', 'admin:$apr1$x')
        self.assertFalse([r for r in self.rels() if 'htpasswd' in r])

    def test_config_backups_are_not_seeded(self):
        """The private bucket versions the config object itself."""
        write(self.staging, 'atlas_config-BACKUP-20260822_101500.json', '{}')
        self.assertFalse([r for r in self.rels() if 'BACKUP' in r])

    def test_scratch_and_dot_directories_are_skipped(self):
        write(self.staging, 'outlets/notebook/__pycache__/x.pyc', 'b')
        write(self.staging, 'outlets/notebook/.ipynb_checkpoints/n.ipynb', '{}')
        write(self.staging, 'layers/roads/roads.geojson.part', 'partial')
        rels = self.rels()
        self.assertFalse([r for r in rels
                          if 'pycache' in r or 'checkpoints' in r or r.endswith('.part')])

    def test_the_delta_archive_is_carried_by_default(self):
        """It is the only record of applied edits."""
        self.assertIn('deltas/roads/work/applied__0__create.geojson', self.rels())

    def test_the_delta_archive_can_be_left_behind_explicitly(self):
        rels = self.rels(include_delta_archive=False)
        self.assertNotIn('deltas/roads/work/applied__0__create.geojson', rels)
        self.assertIn('deltas/roads/pending__1__create.geojson', rels,
                      'a pending delta is not the archive')

    def test_an_outlet_can_be_dropped_by_name(self):
        """Per-outlet rather than by filename pattern: a QGIS runbook's
        per-region PDFs sit in the same directory as the merged deliverable."""
        rels = self.rels(skip_outlets=['runbook'])
        self.assertNotIn('outlets/runbook/kennedy_runbook.pdf', rels)
        self.assertIn('outlets/webmap/index.html', rels)

    def test_a_missing_staging_tree_plans_nothing(self):
        self.assertEqual(atlas_seed.plan_staging_upload(self.root / 'nope'), [])


class TestSharedPlan(unittest.TestCase):
    """Shared files come from what the config names, not from the directory.

    ~20 of the files in /root/data are referenced by any config, and one
    *unreferenced* file there is 4.9 GB.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.shared = Path(self.tmp.name) / 'data'
        self.shared.mkdir(parents=True)
        self.addCleanup(self.tmp.cleanup)

    def config(self, assets=None, layers=None):
        return {
            'name': 'scvfd',
            'assets': assets or {},
            'dataswale': {'layers': layers or []},
        }

    def test_file_inlets_are_referenced(self):
        config = self.config(assets={
            'local_ponds': {'config': {'fetch_type': 'local_ogr',
                                       'inpath_template': 'ponds_scvfd.geojson'}},
            'hillshade': {'config': {'fetch_type': 'local_raster',
                                     'inpath_template': 'hillshade.tiff'}},
        })
        self.assertEqual(atlas_shared.referenced_files(config),
                         ['hillshade.tiff', 'ponds_scvfd.geojson'])

    def test_remote_inlets_are_not(self):
        """overture_duckdb's inpath_template is a SQL query against S3."""
        config = self.config(assets={
            'overture': {'config': {'fetch_type': 'overture_duckdb',
                                    'inpath_template': "SELECT * FROM read_parquet('s3://...')"}},
            'osm': {'config': {'fetch_type': 'fetch_osm'}},
        })
        self.assertEqual(atlas_shared.referenced_files(config), [])

    def test_layer_icons_are_referenced(self):
        config = self.config(layers=[
            {'name': 'hydrants', 'symbol': {'png': 'hydrant.png'}},
            {'name': 'roads'},
        ])
        self.assertEqual(atlas_shared.referenced_files(config), ['hydrant.png'])

    def test_a_shapefile_brings_its_siblings(self):
        """A .shp alone leaves ogr2ogr opening something that looks present
        and is not readable."""
        config = self.config(assets={
            'roads': {'config': {'fetch_type': 'local_ogr',
                                 'inpath_template': 'humtranssp27.shp'}}})
        found = atlas_shared.referenced_files(config)
        self.assertIn('humtranssp27.shp', found)
        for sidecar in ('.dbf', '.shx', '.prj'):
            self.assertIn(f'humtranssp27{sidecar}', found)

    def test_a_template_with_fields_is_formatted(self):
        config = self.config(assets={
            'dem': {'config': {'fetch_type': 'local_raster',
                               'inpath_template': '{name}_dem.tiff'}}})
        self.assertEqual(atlas_shared.referenced_files(config), ['scvfd_dem.tiff'])

    def test_missing_inputs_are_reported_not_raised(self):
        """The box has several pre-existing broken inlet inputs. A seed that
        refused to run until they were fixed would block the migration on
        unrelated breakage."""
        (self.shared / 'ponds_scvfd.geojson').write_text('{}')
        config = self.config(assets={
            'ponds': {'config': {'fetch_type': 'local_ogr',
                                 'inpath_template': 'ponds_scvfd.geojson'}},
            'incidents': {'config': {'fetch_type': 'local_ogr',
                                     'inpath_template': 'westport_incidents.geojson'}},
        })
        plan, missing = atlas_seed.plan_shared_upload(config, self.shared)

        self.assertEqual([rel for _, rel in plan], ['ponds_scvfd.geojson'])
        self.assertEqual(missing, ['westport_incidents.geojson'])


class TestUpload(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.client = FakeS3()
        self.addCleanup(self.tmp.cleanup)
        self.staging = self.root / 'staging'
        write(self.staging, 'atlas_config.json', '{"name": "scvfd"}')
        write(self.staging, 'layers/roads/roads.geojson', '{"features":[]}')
        self.plan = atlas_seed.plan_staging_upload(self.staging)

    def run_upload(self, **kwargs):
        return atlas_seed.upload(self.client, 'PRIV', self.plan,
                                 lambda rel: ws._key('scvfd', rel), **kwargs)

    def test_objects_land_under_the_atlas_staging_prefix(self):
        self.run_upload()
        keys = sorted(k for (b, k) in self.client.objects if b == 'PRIV')
        self.assertEqual(keys, ['scvfd/staging/atlas_config.json',
                                'scvfd/staging/layers/roads/roads.geojson'])

    def test_a_seeded_object_looks_like_a_written_back_one(self):
        """The sha256 metadata is the one atlas_workspace writes and checks. A
        seed that produced objects a session could not recognise would make the
        first write-back re-upload everything."""
        self.run_upload()
        head = self.client.head_object(Bucket='PRIV',
                                       Key='scvfd/staging/atlas_config.json')
        expected = ws.sha256_file(self.staging / 'atlas_config.json')
        self.assertEqual(head['Metadata']['sha256'], expected)

    def test_a_rerun_uploads_nothing(self):
        self.run_upload()
        again = self.run_upload()
        self.assertEqual(again['uploaded'], 0)
        self.assertEqual(again['skipped'], len(self.plan))

    def test_a_changed_file_is_re_uploaded(self):
        self.run_upload()
        write(self.staging, 'layers/roads/roads.geojson', '{"features":[1]}')
        plan = atlas_seed.plan_staging_upload(self.staging)
        result = atlas_seed.upload(self.client, 'PRIV', plan,
                                   lambda rel: ws._key('scvfd', rel))
        self.assertEqual(result['uploaded'], 1)

    def test_a_dry_run_writes_nothing(self):
        result = self.run_upload(dry_run=True)
        self.assertEqual(result['uploaded'], len(self.plan))
        self.assertEqual(self.client.objects, {})

    def test_the_seeded_atlas_reads_as_seeded(self):
        self.assertFalse(ws.is_seeded(self.client, 'PRIV', 'scvfd'))
        self.run_upload()
        self.assertTrue(ws.is_seeded(self.client, 'PRIV', 'scvfd'))


class TestMigrationList(unittest.TestCase):

    def test_the_list_is_explicit_and_deduplicated(self):
        """A directory scan would carry 37 directories — backups, test atlases
        and superseded duplicates — into the thing that replaces them."""
        self.assertEqual(len(atlas_seed.MIGRATE), 15)
        self.assertEqual(len(set(atlas_seed.MIGRATE)), 15)

    def test_superseded_and_throwaway_atlases_are_not_on_it(self):
        for dropped in ('westportdev', 'king_range_hike', 'samuelsloop',
                        'ft1', 'ft6', 'demo', 'laytonville'):
            self.assertNotIn(dropped, atlas_seed.MIGRATE)

    def test_the_paying_and_anchor_customers_are(self):
        for kept in ('westport', 'scvfd', 'kennedy', 'mineralkinsey'):
            self.assertIn(kept, atlas_seed.MIGRATE)


if __name__ == '__main__':
    unittest.main()
