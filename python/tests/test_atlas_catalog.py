"""Catalog assembly and on-disk writing (Phase 3, issue #159).

Exercises the real `federation.build_atlas_catalog()` plus the filesystem half
in `atlas_catalog`, against a tmpdir version tree. No GDAL/QGIS/S3/duckdb, so
it runs in the bare local env.

    cd python && python -m pytest tests/test_atlas_catalog.py -v
"""

import hashlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import atlas_catalog as AC
import federation as F


BBOX = {'north': 38.6, 'south': 38.4, 'east': -122.9, 'west': -123.1}
V1 = '2026-07-02'
V2 = '2026-08-25'

LAYERS = [
    {'name': 'hydrants', 'title': 'Hydrants', 'shareable': {'enabled': True}},
    {'name': 'roads', 'title': 'Roads'},
    {'name': 'lidar_basemap', 'geometry_type': 'raster'},
    {'name': 'never_materialized'},
]


def _config(tmp_path):
    return {
        'name': 'scvfd',
        'base_url': 'https://example.org/scvfd',
        'data_root': str(tmp_path),
        'dataswale': {'bbox': BBOX, 'layers': LAYERS},
    }


def _write_layer(version_dir, name, content, suffix='.geojson'):
    layer_dir = version_dir / 'layers' / name
    layer_dir.mkdir(parents=True, exist_ok=True)
    path = layer_dir / f'{name}{suffix}'
    path.write_text(content)
    return path


def _make_version(tmp_path, version, *, hydrants='H1', roads='R1'):
    """A version snapshot holding three of the four declared layers."""
    version_dir = tmp_path / version
    _write_layer(version_dir, 'hydrants', hydrants)
    _write_layer(version_dir, 'roads', roads)
    _write_layer(version_dir, 'lidar_basemap', 'RASTERBYTES', suffix='.tiff')
    return version_dir


# --------------------------------------------------------------------------- #
# checksums and file discovery
# --------------------------------------------------------------------------- #

def test_checksum_is_a_sha256_multihash(tmp_path):
    path = tmp_path / 'x.geojson'
    path.write_text('hello')
    expected = '1220' + hashlib.sha256(b'hello').hexdigest()
    assert AC.sha256_multihash(path) == expected
    assert AC.sha256_multihash(path).startswith('1220'), 'sha2-256 multihash prefix'


def test_find_layer_file_prefers_the_name_matched_file(tmp_path):
    layers = tmp_path / 'layers'
    (layers / 'roads').mkdir(parents=True)
    (layers / 'roads' / 'roads.geojson').write_text('{}')
    (layers / 'roads' / 'something_else.geojson').write_text('x' * 999)
    assert AC.find_layer_file(layers, 'roads').name == 'roads.geojson'


def test_find_layer_file_falls_back_to_largest_and_skips_stats(tmp_path):
    layers = tmp_path / 'layers'
    (layers / 'odd').mkdir(parents=True)
    (layers / 'odd' / 'stats.json').write_text('x' * 5000)
    (layers / 'odd' / 'weird_name.tiff').write_text('yy')
    assert AC.find_layer_file(layers, 'odd').name == 'weird_name.tiff'


def test_find_layer_file_returns_none_when_absent_or_empty(tmp_path):
    layers = tmp_path / 'layers'
    (layers / 'empty').mkdir(parents=True)
    assert AC.find_layer_file(layers, 'empty') is None
    assert AC.find_layer_file(layers, 'no_such_layer') is None


# --------------------------------------------------------------------------- #
# scanning is driven by config, not by the filesystem (#173)
# --------------------------------------------------------------------------- #

def test_scan_only_catalogues_layers_the_config_declares(tmp_path):
    version_dir = _make_version(tmp_path, V1)
    _write_layer(version_dir, 'terrain_rgb_tiles', 'ORPHANED')  # on disk, not in config

    assets = AC.scan_layers(LAYERS, version_dir / 'layers', 'https://d/')
    assert 'terrain_rgb_tiles' not in assets, \
        'an undeclared directory must never be catalogued (#173)'
    assert set(assets) == {'hydrants', 'roads', 'lidar_basemap'}


def test_scan_populates_href_size_and_checksum(tmp_path):
    version_dir = _make_version(tmp_path, V1)
    assets = AC.scan_layers(LAYERS, version_dir / 'layers', 'https://d/')
    hydrants = assets['hydrants']
    assert hydrants['href'] == 'https://d/hydrants/hydrants.geojson'
    assert hydrants['size'] == len('H1')
    assert hydrants['checksum'] == '1220' + hashlib.sha256(b'H1').hexdigest()


# --------------------------------------------------------------------------- #
# build_atlas_catalog
# --------------------------------------------------------------------------- #

def test_first_publish_writes_everything_and_reuses_nothing(tmp_path):
    version_dir = _make_version(tmp_path, V1)
    assets = AC.scan_layers(LAYERS, version_dir / 'layers')
    built = F.build_atlas_catalog('scvfd', 'desc', LAYERS, BBOX, V1, assets)

    assert sorted(built['written']) == ['hydrants', 'lidar_basemap', 'roads']
    assert built['reused'] == []
    assert built['missing'] == ['never_materialized']


def test_layer_type_and_access_land_in_item_properties(tmp_path):
    version_dir = _make_version(tmp_path, V1)
    assets = AC.scan_layers(LAYERS, version_dir / 'layers')
    built = F.build_atlas_catalog('scvfd', 'desc', LAYERS, BBOX, V1, assets)

    assert built['items']['lidar_basemap']['properties']['atlas:layer_type'] == 'raster'
    assert built['items']['roads']['properties']['atlas:layer_type'] == 'vector'
    # shareable promotes to public; a plain layer stays internal
    assert built['items']['hydrants']['properties']['atlas:access'] == ['public']
    assert built['items']['roads']['properties']['atlas:access'] == ['internal']


def test_unchanged_layer_is_reused_at_its_own_version(tmp_path):
    v1_dir = _make_version(tmp_path, V1)
    assets_v1 = AC.scan_layers(LAYERS, v1_dir / 'layers')
    built_v1 = F.build_atlas_catalog('scvfd', 'desc', LAYERS, BBOX, V1, assets_v1)
    history = {name: [item] for name, item in built_v1['items'].items()}

    # hydrants edited; roads and the 154 MB-alike raster untouched
    v2_dir = _make_version(tmp_path, V2, hydrants='H2-EDITED')
    assets_v2 = AC.scan_layers(LAYERS, v2_dir / 'layers')
    built_v2 = F.build_atlas_catalog('scvfd', 'desc', LAYERS, BBOX, V2, assets_v2,
                                     history=history)

    assert built_v2['written'] == ['hydrants']
    assert sorted(built_v2['reused']) == ['lidar_basemap', 'roads']

    hrefs = [l['href'] for l in built_v2['version_catalog']['links']
             if l.get('rel') == 'item']
    assert any(f'roads-{V1}' in h for h in hrefs), \
        'unchanged layer referenced at V1, not re-stamped at V2'
    assert any(f'hydrants-{V2}' in h for h in hrefs)
    # The href must be the reused Item's OWN self link. Asserting only that the
    # id appears is what let a wrong *directory* through: the reconstructed
    # href named V2's directory with V1's filename, pointing at nothing.
    roads_self = F.item_self_href(built_v1['items']['roads'])
    assert [h for h in hrefs if 'roads-' in h] == [roads_self]


def test_collection_accumulates_history_across_versions(tmp_path):
    v1_dir = _make_version(tmp_path, V1)
    built_v1 = F.build_atlas_catalog('scvfd', 'd', LAYERS, BBOX, V1,
                                     AC.scan_layers(LAYERS, v1_dir / 'layers'))
    history = {n: [i] for n, i in built_v1['items'].items()}
    v2_dir = _make_version(tmp_path, V2, hydrants='H2')
    built_v2 = F.build_atlas_catalog('scvfd', 'd', LAYERS, BBOX, V2,
                                     AC.scan_layers(LAYERS, v2_dir / 'layers'),
                                     history=history)

    hydrants = built_v2['collections']['hydrants']
    item_links = [l for l in hydrants['links'] if l.get('rel') == 'item']
    assert len(item_links) == 2, 'both versions listed'
    assert hydrants['version'] == V2, 'collection names the newest'

    roads = built_v2['collections']['roads']
    assert len([l for l in roads['links'] if l.get('rel') == 'item']) == 1, \
        'a reused layer gains no new Item'


def test_root_catalog_links_children_and_the_version(tmp_path):
    v1_dir = _make_version(tmp_path, V1)
    built = F.build_atlas_catalog('scvfd', 'd', LAYERS, BBOX, V1,
                                  AC.scan_layers(LAYERS, v1_dir / 'layers'))
    children = [l['href'] for l in built['catalog']['links'] if l.get('rel') == 'child']
    assert './hydrants/collection.json' in children
    assert './never_materialized/collection.json' not in children
    assert [l['href'] for l in built['catalog']['links']
            if l.get('rel') == 'version-history'] == [f'./versions/{V1}/catalog.json']


# --------------------------------------------------------------------------- #
# on-disk round trip
# --------------------------------------------------------------------------- #

def test_publish_catalog_writes_the_expected_tree(tmp_path):
    config = _config(tmp_path)
    version_dir = _make_version(tmp_path, V1)

    summary = AC.publish_catalog(config, version_dir, V1)

    stac = version_dir / 'stac'
    assert (stac / 'catalog.json').is_file()
    assert (stac / 'hydrants' / 'collection.json').is_file()
    assert (stac / 'hydrants' / f'hydrants-{V1}.json').is_file()
    assert (stac / 'versions' / V1 / 'catalog.json').is_file()
    assert summary['status'] == 'ok'
    assert summary['missing_layers'] == ['never_materialized']


def test_hrefs_follow_the_existing_outlet_convention(tmp_path):
    config = _config(tmp_path)
    version_dir = _make_version(tmp_path, V1)
    AC.publish_catalog(config, version_dir, V1)

    item = json.loads(
        (version_dir / 'stac' / 'roads' / f'roads-{V1}.json').read_text())
    assert item['assets']['data']['href'] == \
        f'https://example.org/scvfd/{V1}/layers/roads/roads.geojson'
    assert item['assets']['data']['type'] == 'application/geo+json'


def test_history_round_trips_through_disk(tmp_path):
    config = _config(tmp_path)
    v1_dir = _make_version(tmp_path, V1)
    AC.publish_catalog(config, v1_dir, V1)

    history = AC.load_history(v1_dir / 'stac')
    assert set(history) == {'hydrants', 'roads', 'lidar_basemap'}
    assert history['roads'][0]['id'] == f'roads-{V1}'


def test_second_publish_reuses_unchanged_layers_end_to_end(tmp_path):
    config = _config(tmp_path)
    v1_dir = _make_version(tmp_path, V1)
    AC.publish_catalog(config, v1_dir, V1)

    v2_dir = _make_version(tmp_path, V2, hydrants='EDITED')
    summary = AC.publish_catalog(config, v2_dir, V2, previous_version_path=v1_dir)

    assert summary['written_layers'] == ['hydrants']
    assert sorted(summary['reused_layers']) == ['lidar_basemap', 'roads']
    # the reused layers get no new Item file in V2's catalog
    assert not (v2_dir / 'stac' / 'roads' / f'roads-{V2}.json').exists()
    assert (v2_dir / 'stac' / 'hydrants' / f'hydrants-{V2}.json').is_file()


def test_corrupt_previous_catalog_does_not_stop_a_publish(tmp_path):
    config = _config(tmp_path)
    v1_dir = _make_version(tmp_path, V1)
    AC.publish_catalog(config, v1_dir, V1)
    (v1_dir / 'stac' / 'roads' / f'roads-{V1}.json').write_text('{ not json')

    v2_dir = _make_version(tmp_path, V2)
    summary = AC.publish_catalog(config, v2_dir, V2, previous_version_path=v1_dir)

    assert summary['status'] == 'ok'
    assert 'roads' in summary['written_layers'], \
        'unreadable history must mean rewrite, never silent reuse'


def test_load_history_is_empty_for_a_first_publish(tmp_path):
    assert AC.load_history(tmp_path / 'nope' / 'stac') == {}


def _href_to_path(href, config, tmp_path):
    """Map a catalog href back to the file it claims to be at."""
    prefix = config['base_url'].rstrip('/') + '/'
    assert href.startswith(prefix), href
    return tmp_path / href[len(prefix):]


def test_every_href_in_a_catalog_resolves_to_a_real_file(tmp_path):
    """The regression that the on-box kennedy publish caught.

    A reused Item lives under the version that first wrote it. Rebuilding its
    href from the *current* version's base URL produced
    `.../{V2}/stac/roads/roads-{V1}.json` — V2's directory, V1's filename,
    nothing there. Resolving every href to disk is the assertion that holds.
    """
    config = _config(tmp_path)
    v1_dir = _make_version(tmp_path, V1)
    AC.publish_catalog(config, v1_dir, V1)
    v2_dir = _make_version(tmp_path, V2, hydrants='EDITED')
    AC.publish_catalog(config, v2_dir, V2, previous_version_path=v1_dir)

    version_catalog = json.loads(
        (v2_dir / 'stac' / 'versions' / V2 / 'catalog.json').read_text())
    item_hrefs = [l['href'] for l in version_catalog['links']
                  if l.get('rel') == 'item']
    assert item_hrefs, 'version catalog must name its items'
    for href in item_hrefs:
        assert _href_to_path(href, config, tmp_path).is_file(), \
            f'version catalog points at a nonexistent file: {href}'

    for layer in ('roads', 'hydrants', 'lidar_basemap'):
        collection = json.loads(
            (v2_dir / 'stac' / layer / 'collection.json').read_text())
        for link in collection['links']:
            if link.get('rel') == 'item':
                assert _href_to_path(link['href'], config, tmp_path).is_file(), \
                    f"collection {layer} points at a nonexistent file: {link['href']}"


def test_reused_item_href_names_the_version_that_wrote_it(tmp_path):
    config = _config(tmp_path)
    v1_dir = _make_version(tmp_path, V1)
    AC.publish_catalog(config, v1_dir, V1)
    v2_dir = _make_version(tmp_path, V2, hydrants='EDITED')
    AC.publish_catalog(config, v2_dir, V2, previous_version_path=v1_dir)

    version_catalog = json.loads(
        (v2_dir / 'stac' / 'versions' / V2 / 'catalog.json').read_text())
    roads = next(l['href'] for l in version_catalog['links']
                 if l.get('title') == 'roads')
    hydrants = next(l['href'] for l in version_catalog['links']
                    if l.get('title') == 'hydrants')

    assert roads == f'{config["base_url"]}/{V1}/stac/roads/roads-{V1}.json'
    assert hydrants == f'{config["base_url"]}/{V2}/stac/hydrants/hydrants-{V2}.json'


V3 = '2026-09-08_00-40-50'


def test_reuse_survives_across_multiple_publishes(tmp_path):
    """The kennedy three-publish regression.

    A layer reused in V2 has no Item file in V2 — only a Collection pointing
    back at V1. Loading history by listing V2's directory therefore lost the
    layer entirely and rewrote it in V3, so reuse alternated on/off/on. History
    has to follow the Collection's item links.
    """
    config = _config(tmp_path)
    v1_dir = _make_version(tmp_path, V1)
    AC.publish_catalog(config, v1_dir, V1)

    # V2: hydrants edited, everything else untouched -> roads/raster reused.
    v2_dir = _make_version(tmp_path, V2, hydrants='EDITED')
    s2 = AC.publish_catalog(config, v2_dir, V2, previous_version_path=v1_dir)
    assert s2['written_layers'] == ['hydrants']
    assert not (v2_dir / 'stac' / 'roads' / f'roads-{V2}.json').exists()

    # V3: nothing changed at all since V2. Everything must be reused.
    v3_dir = _make_version(tmp_path, V3, hydrants='EDITED')
    s3 = AC.publish_catalog(config, v3_dir, V3, previous_version_path=v2_dir)

    assert s3['written_layers'] == [], \
        'a publish with no edits must write no new Items'
    assert sorted(s3['reused_layers']) == ['hydrants', 'lidar_basemap', 'roads']

    # roads is still referenced all the way back at V1, two hops later.
    version_catalog = json.loads(
        (v3_dir / 'stac' / 'versions' / V3 / 'catalog.json').read_text())
    roads = next(l['href'] for l in version_catalog['links']
                 if l.get('title') == 'roads')
    assert roads == f'{config["base_url"]}/{V1}/stac/roads/roads-{V1}.json'
    assert _href_to_path(roads, config, tmp_path).is_file()


def test_history_survives_a_hop_for_every_layer(tmp_path):
    config = _config(tmp_path)
    v1_dir = _make_version(tmp_path, V1)
    AC.publish_catalog(config, v1_dir, V1)
    v2_dir = _make_version(tmp_path, V2, hydrants='EDITED')
    AC.publish_catalog(config, v2_dir, V2, previous_version_path=v1_dir)

    history = AC.load_history(v2_dir / 'stac', base_url=config['base_url'],
                              atlas_root=tmp_path)
    assert set(history) == {'hydrants', 'roads', 'lidar_basemap'}
    assert [i['id'] for i in history['hydrants']] == [
        f'hydrants-{V1}', f'hydrants-{V2}'], 'both versions, oldest first'
    assert [i['id'] for i in history['roads']] == [f'roads-{V1}'], \
        'reused layer keeps its history through the collection link'


def test_missing_referenced_item_falls_back_to_rewriting(tmp_path):
    """If history points at an Item that has been deleted, rewrite it."""
    config = _config(tmp_path)
    v1_dir = _make_version(tmp_path, V1)
    AC.publish_catalog(config, v1_dir, V1)
    v2_dir = _make_version(tmp_path, V2, hydrants='EDITED')
    AC.publish_catalog(config, v2_dir, V2, previous_version_path=v1_dir)

    (v1_dir / 'stac' / 'roads' / f'roads-{V1}.json').unlink()

    v3_dir = _make_version(tmp_path, V3, hydrants='EDITED')
    s3 = AC.publish_catalog(config, v3_dir, V3, previous_version_path=v2_dir)
    assert 'roads' in s3['written_layers'], \
        'a dangling reference must mean rewrite, never a broken catalog'
