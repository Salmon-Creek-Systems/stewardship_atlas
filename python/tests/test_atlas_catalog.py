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
import atlas_store


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


def test_find_layer_file_falls_back_within_the_allowlist_only(tmp_path):
    """The fallback picks the largest *servable* file, not the largest file.

    A layer directory is not curated: kennedy's held `.htpasswd`, and an
    unfiltered fallback published it. An arbitrarily-named file is no longer a
    candidate — only `{layer}` or `{layer}.*`.
    """
    layers = tmp_path / 'layers'
    (layers / 'odd').mkdir(parents=True)
    (layers / 'odd' / 'stats.json').write_text('x' * 5000)
    (layers / 'odd' / 'weird_name.tiff').write_text('yy')
    assert AC.find_layer_file(layers, 'odd') is None

    (layers / 'odd' / 'odd.tiff.jpg').write_text('IMG')
    assert AC.find_layer_file(layers, 'odd').name == 'odd.tiff.jpg'


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


def test_the_local_href_survives_as_an_alternate(tmp_path):
    """The old outlet-convention href is kept, demoted to `alternate.local`.

    It is what lets the box resolve a layer from its own disk and what an
    offline copy of an outlet needs, so making S3 primary must not discard it.
    """
    config = _config(tmp_path)
    version_dir = _make_version(tmp_path, V1)
    AC.publish_catalog(config, version_dir, V1)

    item = json.loads(
        (version_dir / 'stac' / 'roads' / f'roads-{V1}.json').read_text())
    data = item['assets']['data']
    assert data['alternate']['local']['href'] == \
        f'https://example.org/scvfd/{V1}/layers/roads/roads.geojson'
    assert data['type'] == 'application/geo+json'


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


# --------------------------------------------------------------------------- #
# Layer addressing
#
# Pinning is gone with this step. It addressed a sibling *version* directory
# (`../../../{version}/layers/...`), which exists on the box but has no
# counterpart under the published `current/` prefix, so those URLs would 404 on
# CloudFront. One relative form now serves both hosts, backed by the mirror in
# atlas_store.plan_current_layers.
# --------------------------------------------------------------------------- #

def _item_filenames(item):
    """Basenames of every file an Item's assets name — local test helper."""
    from pathlib import Path as _P
    return {_P(a['href']).name for a in (item.get('assets') or {}).values()
            if isinstance(a, dict) and a.get('href')}


def test_layer_data_url_is_relative_and_host_independent():
    """One form, resolving correctly from the box and from CloudFront.

    From `{atlas}/{version}/outlets/webmap/` it finds the local layer tree;
    from `{atlas}/current/outlets/webmap/` on S3 it finds the mirror at
    `{atlas}/current/layers/`. That is what lets publish stay a pure snapshot:
    there is no URL to rewrite and no second materialize.
    """
    assert AC.layer_data_url('roads', 'roads.geojson') == '../../layers/roads/roads.geojson'
    assert AC.layer_data_url('lidar_basemap', 'lidar_basemap.tiff.jpg') == \
        '../../layers/lidar_basemap/lidar_basemap.tiff.jpg'


def test_layer_data_url_climbs_exactly_two_levels():
    """Regression guard on the depth, which is what makes both hosts work.

    An outlet directory always sits two below the version/current root, so the
    URL must climb exactly two. Off-by-one here is invisible locally — a wrong
    directory and a right one both resolve to *something* during a relative
    join — and only shows up as a 404 on the served copy.
    """
    url = AC.layer_data_url('roads', 'roads.geojson')
    assert url.startswith('../../layers/')
    assert not url.startswith('../../../')


def test_item_records_every_servable_file_in_the_layer_dir(tmp_path):
    """A raster layer's Item must name the rendered image too, not just the tiff."""
    config = _config(tmp_path)
    version_dir = _make_version(tmp_path, V1)
    # what a raster layer really looks like: source + what the webmap requests
    (version_dir / 'layers' / 'lidar_basemap' / 'lidar_basemap.tiff.jpg').write_text('JPG')
    (version_dir / 'layers' / 'lidar_basemap' / 'stats.json').write_text('{}')

    AC.publish_catalog(config, version_dir, V1)
    item = json.loads((version_dir / 'stac' / 'lidar_basemap' /
                       f'lidar_basemap-{V1}.json').read_text())

    names = _item_filenames(item)
    assert 'lidar_basemap.tiff' in names
    assert 'lidar_basemap.tiff.jpg' in names
    assert 'stats.json' not in names, 'sidecar is not servable data'
    assert F.item_checksum(item), 'primary asset still carries the checksum'



# --------------------------------------------------------------------------- #
# S3 alternates (slice 4)
# --------------------------------------------------------------------------- #

def _cloud_config(tmp_path, **cloud):
    config = _config(tmp_path)
    config['cloud'] = {'outlets_bucket': 'OUT', 'private_bucket': 'PRIV',
                       'public_base_url': 'https://cdn.example.org', **cloud}
    return config


def test_public_layers_get_an_https_primary_href(tmp_path):
    """S3 is the source of truth, so the served URL is the primary href.

    Slice 4 kept the primary local because a deterministic key written before
    a failed upload would name a missing object. The push now raises and takes
    the publish with it, so that cannot happen.
    """
    config = _cloud_config(tmp_path)
    version_dir = _make_version(tmp_path, V1)
    AC.publish_catalog(config, version_dir, V1)

    item = json.loads((version_dir / 'stac' / 'hydrants' /
                       f'hydrants-{V1}.json').read_text())
    data = item['assets']['data']
    assert data['href'] == \
        f'https://cdn.example.org/scvfd/layers/hydrants/{V1}/hydrants.geojson'
    assert data['alternate']['s3']['href'] == \
        f's3://OUT/scvfd/layers/hydrants/{V1}/hydrants.geojson'
    assert data['alternate']['local']['href'].startswith('https://example.org/')
    assert AC.ALTERNATE_EXTENSION in item['stac_extensions']


def test_protected_layers_keep_an_s3_primary_href(tmp_path):
    """A protected layer has no HTTPS reader until Phase 4.

    Naming a CloudFront URL that 403s would be less honest than naming the
    bucket, and it would let a reader believe the tier split had been crossed.
    """
    config = _cloud_config(tmp_path)
    version_dir = _make_version(tmp_path, V1)
    AC.publish_catalog(config, version_dir, V1)

    # hydrants is shareable -> public; roads has no tier -> internal
    roads = json.loads((version_dir / 'stac' / 'roads' /
                        f'roads-{V1}.json').read_text())
    assert roads['assets']['data']['href'] == \
        f's3://PRIV/scvfd/layers/roads/{V1}/roads.geojson'
    assert 'cdn.example.org' not in json.dumps(roads)


def test_hrefs_are_written_with_no_cloud_block_at_all(tmp_path):
    """There is no opt-out: an atlas with no `cloud` config still resolves to
    the code-default buckets, because S3 is the backend rather than a feature.
    """
    config = _config(tmp_path)
    version_dir = _make_version(tmp_path, V1)
    summary = AC.publish_catalog(config, version_dir, V1)

    assert summary['asset_hrefs'] > 0
    item = json.loads((version_dir / 'stac' / 'hydrants' /
                       f'hydrants-{V1}.json').read_text())
    assert item['assets']['data']['href'].startswith(
        atlas_store.DEFAULT_PUBLIC_BASE_URL + '/scvfd/layers/hydrants/')


def test_a_reused_item_keeps_the_version_that_holds_its_bytes(tmp_path):
    """Regression: hrefs must never be rebuilt at the *current* version.

    This is the shape of the first bug this branch hit — a reused Item's href
    reconstructed from the new version's base, naming a key that holds nothing.
    Only newly written Items may be rewritten.
    """
    config = _cloud_config(tmp_path)
    v1_dir = _make_version(tmp_path, V1)
    AC.publish_catalog(config, v1_dir, V1)
    v2_dir = _make_version(tmp_path, V2)
    AC.publish_catalog(config, v2_dir, V2, previous_version_path=v1_dir)

    # A reused layer writes no Item into the new version's directory at all —
    # V2's Collection links back to where the bytes actually are.
    assert not (v2_dir / 'stac' / 'hydrants' / f'hydrants-{V2}.json').exists()
    item = json.loads((v1_dir / 'stac' / 'hydrants' /
                       f'hydrants-{V1}.json').read_text())
    assert item['assets']['data']['href'] == \
        f'https://cdn.example.org/scvfd/layers/hydrants/{V1}/hydrants.geojson'
    assert V2 not in item['assets']['data']['href']

    collection = json.loads((v2_dir / 'stac' / 'hydrants' / 'collection.json').read_text())
    item_links = [l['href'] for l in collection['links'] if l['rel'] == 'item']
    assert item_links == [f'https://example.org/scvfd/{V1}/stac/hydrants/hydrants-{V1}.json']


def test_summary_carries_what_the_uploader_needs(tmp_path):
    config = _cloud_config(tmp_path)
    version_dir = _make_version(tmp_path, V1)
    summary = AC.publish_catalog(config, version_dir, V1)

    assert set(summary['layer_assets']) == {'hydrants', 'roads', 'lidar_basemap'}
    assert summary['access_by_layer']['hydrants'] == ['public']
    assert summary['access_by_layer']['roads'] == ['internal']
    assert 'files' in summary['layer_assets']['roads']


def test_summary_reports_the_version_holding_every_layer(tmp_path):
    """Written and reused alike — the S3 push needs a key for all of them.

    Reporting only the written set is what made kennedy's first push upload
    zero layer objects: every layer was reused locally, so nothing was
    planned, even though S3 had never held any of it.
    """
    config = _cloud_config(tmp_path)
    v1_dir = _make_version(tmp_path, V1)
    AC.publish_catalog(config, v1_dir, V1)
    v2_dir = _make_version(tmp_path, V2, hydrants='EDITED')
    summary = AC.publish_catalog(config, v2_dir, V2, previous_version_path=v1_dir)

    assert summary['written_layers'] == ['hydrants']
    versions = summary['layer_versions']
    assert set(versions) == {'hydrants', 'roads', 'lidar_basemap'}, \
        'every layer in the version, not just the rewritten one'
    assert versions['hydrants'] == V2
    assert versions['roads'] == V1, 'reused layer keyed where its data lives'
    assert versions['lidar_basemap'] == V1


# --------------------------------------------------------------------------- #
# What counts as a layer's servable data
# --------------------------------------------------------------------------- #

def test_credentials_and_strays_are_never_servable():
    """kennedy's layer dirs held .htpasswd, and it was published world-readable.

    The rule was 'everything except stats.json' — a denylist, so anything
    unanticipated was published by default. It is now an allowlist by name.
    """
    for stray in ('.htpasswd', '.DS_Store', 'stats.json', 'biochar_summary.csv',
                  'burns_simulation.geojson', 'notes.geojson'):
        assert AC.is_servable_file(stray, 'processing_sites') is False, stray


def test_the_layers_own_files_are_servable():
    for good in ('processing_sites.geojson', 'processing_sites.tiff',
                 'processing_sites.tiff.jpg', 'processing_sites.pmtiles'):
        assert AC.is_servable_file(good, 'processing_sites') is True, good


def test_scan_excludes_strays_from_the_upload_set(tmp_path):
    version_dir = _make_version(tmp_path, V1)
    layer_dir = version_dir / 'layers' / 'roads'
    (layer_dir / '.htpasswd').write_text('admin:$apr1$redacted')
    (layer_dir / 'unrelated_export.csv').write_text('a,b,c')
    (layer_dir / 'roads.tiff.jpg').write_text('IMG')

    assets = AC.scan_layers(LAYERS, version_dir / 'layers')
    assert sorted(assets['roads']['files']) == ['roads.geojson', 'roads.tiff.jpg']


def test_a_stray_file_never_reaches_an_item(tmp_path):
    config = _cloud_config(tmp_path)
    version_dir = _make_version(tmp_path, V1)
    (version_dir / 'layers' / 'roads' / '.htpasswd').write_text('admin:$apr1$redacted')
    AC.publish_catalog(config, version_dir, V1)

    item = json.loads((version_dir / 'stac' / 'roads' / f'roads-{V1}.json').read_text())
    assert '.htpasswd' not in _item_filenames(item)
    for asset in item['assets'].values():
        assert '.htpasswd' not in asset['href']
        assert '.htpasswd' not in asset.get('alternate', {}).get('s3', {}).get('href', '')


def test_fallback_never_selects_a_non_servable_file(tmp_path):
    """kennedy's `lpss`: a layer directory with no lpss.* file at all.

    find_layer_file() fell through to 'largest file in the directory' without
    applying the servable allowlist, so it chose `.htpasswd` as the layer's
    primary asset and published it. Filtering the sibling scan but not the
    primary selection left a gap visible only on the one layer taking the
    odd path.
    """
    layers = tmp_path / 'layers'
    (layers / 'lpss').mkdir(parents=True)
    (layers / 'lpss' / '.htpasswd').write_text('admin:$apr1$redacted')
    (layers / 'lpss' / 'notes_export.csv').write_text('a,b,c')

    assert AC.find_layer_file(layers, 'lpss') is None, \
        'a directory with nothing servable has no layer file'


def test_fallback_still_finds_an_oddly_named_servable_file(tmp_path):
    layers = tmp_path / 'layers'
    (layers / 'basemap').mkdir(parents=True)
    (layers / 'basemap' / '.htpasswd').write_text('x')
    (layers / 'basemap' / 'basemap.tiff.jpg').write_text('IMAGE-DATA')
    assert AC.find_layer_file(layers, 'basemap').name == 'basemap.tiff.jpg'


def test_a_layer_with_only_strays_is_reported_missing_not_published(tmp_path):
    config = _cloud_config(tmp_path)
    version_dir = _make_version(tmp_path, V1)
    stray_layer = version_dir / 'layers' / 'lpss'
    stray_layer.mkdir(parents=True)
    (stray_layer / '.htpasswd').write_text('admin:$apr1$redacted')

    layers = LAYERS + [{'name': 'lpss', 'access': ['admin']}]
    assets = AC.scan_layers(layers, version_dir / 'layers')
    assert 'lpss' not in assets, 'nothing servable -> not catalogued at all'


def test_gdal_sidecars_are_not_servable():
    """basemap.tiff.aux.xml rides along beside basemap.tiff and is name-prefixed,
    so the name rule alone admits it. The extension is an allowlist too."""
    for sidecar in ('basemap.tiff.aux.xml', 'basemap.tiff.jpg.aux.xml',
                    'basemap.lock', 'basemap.xml'):
        assert AC.is_servable_file(sidecar, 'basemap') is False, sidecar
    for real in ('basemap.tiff', 'basemap.tiff.jpg', 'basemap.tiff.png'):
        assert AC.is_servable_file(real, 'basemap') is True, real


def test_sidecars_stay_out_of_the_upload_set(tmp_path):
    version_dir = _make_version(tmp_path, V1)
    raster = version_dir / 'layers' / 'lidar_basemap'
    for f in ('lidar_basemap.tiff.jpg', 'lidar_basemap.tiff.aux.xml',
              'lidar_basemap.tiff.jpg.aux.xml'):
        (raster / f).write_text('x')

    assets = AC.scan_layers(LAYERS, version_dir / 'layers')
    assert sorted(assets['lidar_basemap']['files']) == [
        'lidar_basemap.tiff', 'lidar_basemap.tiff.jpg']
