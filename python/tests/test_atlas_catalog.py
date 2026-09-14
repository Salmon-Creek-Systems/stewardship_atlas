"""Catalog assembly and the S3 documents it becomes (Phase 3, issue #159).

Exercises the real `federation.build_atlas_catalog()` plus the filesystem and
S3 halves in `atlas_catalog`, against a tmpdir staging tree and an in-memory
bucket. No GDAL/QGIS/boto3/duckdb, so it runs in the bare local env.

Task 6 moved the catalog off the version directory: there is no `{version}/`
snapshot any more, so a publish reads *staging* and the previous versions come
back from the catalog in S3 rather than from a sibling directory on disk.

    cd python && python -m pytest tests/test_atlas_catalog.py -v
"""

import hashlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import atlas_catalog as AC
import federation as F
import atlas_store
from fake_s3 import FakeS3


BBOX = {'north': 38.6, 'south': 38.4, 'east': -122.9, 'west': -123.1}
V1 = '2026-07-02'
V2 = '2026-08-25'
V3 = '2026-09-08_00-40-50'

OUT = 'OUT'
PRIV = 'PRIV'
CDN = 'https://cdn.example.org'
CATALOG = f'{CDN}/scvfd/catalog/'

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
        'cloud': {'outlets_bucket': OUT, 'private_bucket': PRIV,
                  'public_base_url': CDN, 'outlets': []},
        'dataswale': {'bbox': BBOX, 'layers': LAYERS},
    }


# Kept as a distinct name because several tests below are specifically about
# an atlas that declares no `cloud` block at all.
def _cloud_config(tmp_path, **cloud):
    config = _config(tmp_path)
    config['cloud'] = {'outlets_bucket': OUT, 'private_bucket': PRIV,
                       'public_base_url': CDN, **cloud}
    return config


def _write_layer(staging, name, content, suffix='.geojson'):
    layer_dir = staging / 'layers' / name
    layer_dir.mkdir(parents=True, exist_ok=True)
    path = layer_dir / f'{name}{suffix}'
    path.write_text(content)
    return path


def _make_staging(tmp_path, *, hydrants='H1', roads='R1'):
    """A staging tree holding three of the four declared layers."""
    staging = tmp_path / 'staging'
    _write_layer(staging, 'hydrants', hydrants)
    _write_layer(staging, 'roads', roads)
    _write_layer(staging, 'lidar_basemap', 'RASTERBYTES', suffix='.tiff')
    return staging


def _publish(config, client, staging, version):
    """One publish: build the documents, then put them where the next one reads.

    Uploading is the caller's job in production too — publish writes the
    objects first and the documents last, so a catalog can never name bytes
    that are not there.
    """
    summary = AC.publish_catalog(config, staging, version,
                                 client=client, bucket=OUT)
    atlas_store.upload_documents(OUT, summary['documents'], client=client)
    return summary


def _doc(client, key, bucket=OUT):
    """A published document, or None."""
    return atlas_store.get_json(bucket, key, client=client)


def _item(client, name, version):
    return _doc(client, f'scvfd/catalog/{name}/{name}-{version}.json')


def _has(client, key, bucket=OUT):
    return (bucket, key) in client.objects


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
    staging = _make_staging(tmp_path)
    _write_layer(staging, 'terrain_rgb_tiles', 'ORPHANED')  # on disk, not in config

    assets = AC.scan_layers(LAYERS, staging / 'layers', 'https://d/')
    assert 'terrain_rgb_tiles' not in assets, \
        'an undeclared directory must never be catalogued (#173)'
    assert set(assets) == {'hydrants', 'roads', 'lidar_basemap'}


def test_scan_populates_href_size_and_checksum(tmp_path):
    staging = _make_staging(tmp_path)
    assets = AC.scan_layers(LAYERS, staging / 'layers', 'https://d/')
    hydrants = assets['hydrants']
    assert hydrants['href'] == 'https://d/hydrants/hydrants.geojson'
    assert hydrants['size'] == len('H1')
    assert hydrants['checksum'] == '1220' + hashlib.sha256(b'H1').hexdigest()


# --------------------------------------------------------------------------- #
# build_atlas_catalog
# --------------------------------------------------------------------------- #

def test_first_publish_writes_everything_and_reuses_nothing(tmp_path):
    staging = _make_staging(tmp_path)
    assets = AC.scan_layers(LAYERS, staging / 'layers')
    built = F.build_atlas_catalog('scvfd', 'desc', LAYERS, BBOX, V1, assets)

    assert sorted(built['written']) == ['hydrants', 'lidar_basemap', 'roads']
    assert built['reused'] == []
    assert built['missing'] == ['never_materialized']


def test_layer_type_and_access_land_in_item_properties(tmp_path):
    staging = _make_staging(tmp_path)
    assets = AC.scan_layers(LAYERS, staging / 'layers')
    built = F.build_atlas_catalog('scvfd', 'desc', LAYERS, BBOX, V1, assets)

    assert built['items']['lidar_basemap']['properties']['atlas:layer_type'] == 'raster'
    assert built['items']['roads']['properties']['atlas:layer_type'] == 'vector'
    # shareable promotes to public; a plain layer stays internal
    assert built['items']['hydrants']['properties']['atlas:access'] == ['public']
    assert built['items']['roads']['properties']['atlas:access'] == ['internal']


def test_unchanged_layer_is_reused_at_its_own_version(tmp_path):
    staging = _make_staging(tmp_path)
    assets_v1 = AC.scan_layers(LAYERS, staging / 'layers')
    built_v1 = F.build_atlas_catalog('scvfd', 'desc', LAYERS, BBOX, V1, assets_v1)
    history = {name: [item] for name, item in built_v1['items'].items()}

    # hydrants edited; roads and the 154 MB-alike raster untouched
    _write_layer(staging, 'hydrants', 'H2-EDITED')
    assets_v2 = AC.scan_layers(LAYERS, staging / 'layers')
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
    staging = _make_staging(tmp_path)
    built_v1 = F.build_atlas_catalog('scvfd', 'd', LAYERS, BBOX, V1,
                                     AC.scan_layers(LAYERS, staging / 'layers'))
    history = {n: [i] for n, i in built_v1['items'].items()}
    _write_layer(staging, 'hydrants', 'H2')
    built_v2 = F.build_atlas_catalog('scvfd', 'd', LAYERS, BBOX, V2,
                                     AC.scan_layers(LAYERS, staging / 'layers'),
                                     history=history)

    hydrants = built_v2['collections']['hydrants']
    item_links = [l for l in hydrants['links'] if l.get('rel') == 'item']
    assert len(item_links) == 2, 'both versions listed'
    assert hydrants['version'] == V2, 'collection names the newest'

    roads = built_v2['collections']['roads']
    assert len([l for l in roads['links'] if l.get('rel') == 'item']) == 1, \
        'a reused layer gains no new Item'


def test_root_catalog_links_children_and_every_version(tmp_path):
    staging = _make_staging(tmp_path)
    built = F.build_atlas_catalog('scvfd', 'd', LAYERS, BBOX, V2,
                                  AC.scan_layers(LAYERS, staging / 'layers'),
                                  known_versions=[V1])
    children = [l['href'] for l in built['catalog']['links'] if l.get('rel') == 'child']
    assert './hydrants/collection.json' in children
    assert './never_materialized/collection.json' not in children

    versions = [l['href'] for l in built['catalog']['links']
                if l.get('rel') == 'version-history']
    assert versions == [f'./versions/{V1}/catalog.json',
                        f'./versions/{V2}/catalog.json'], \
        'the root catalog is the version index — an older version must stay reachable'


# --------------------------------------------------------------------------- #
# S3 round trip
# --------------------------------------------------------------------------- #

def test_publish_catalog_writes_the_expected_documents(tmp_path):
    client = FakeS3()
    config = _config(tmp_path)
    staging = _make_staging(tmp_path)

    summary = _publish(config, client, staging, V1)

    assert _has(client, 'scvfd/catalog/catalog.json')
    assert _has(client, 'scvfd/catalog/hydrants/collection.json')
    assert _has(client, f'scvfd/catalog/hydrants/hydrants-{V1}.json')
    assert _has(client, f'scvfd/catalog/versions/{V1}/catalog.json')
    assert summary['status'] == 'ok'
    assert summary['missing_layers'] == ['never_materialized']


def test_documents_live_at_one_prefix_not_one_per_version(tmp_path):
    """The point of the move: an Item written once keeps its URL forever, so a
    later version Catalog can link to it instead of copying it."""
    client = FakeS3()
    config = _config(tmp_path)
    staging = _make_staging(tmp_path)

    _publish(config, client, staging, V1)
    _write_layer(staging, 'hydrants', 'EDITED')
    _publish(config, client, staging, V2)

    assert _has(client, f'scvfd/catalog/hydrants/hydrants-{V1}.json'), \
        "V1's Item is still at its original key after a second publish"
    assert _has(client, f'scvfd/catalog/hydrants/hydrants-{V2}.json')
    assert not _has(client, f'scvfd/catalog/{V2}/catalog.json'), \
        'no per-version copy of the whole tree'


def test_the_relative_href_survives_as_an_alternate(tmp_path):
    """The layer-tree-relative href is kept, demoted to `alternate.local`.

    It is how a workspace resolves a layer from its own disk and what an
    offline copy of an outlet needs, so making S3 primary must not discard it.
    """
    client = FakeS3()
    config = _config(tmp_path)
    _publish(config, client, _make_staging(tmp_path), V1)

    data = _item(client, 'roads', V1)['assets']['data']
    assert data['alternate']['local']['href'] == 'roads/roads.geojson'
    assert data['type'] == 'application/geo+json'


def test_history_round_trips_through_s3(tmp_path):
    client = FakeS3()
    config = _config(tmp_path)
    _publish(config, client, _make_staging(tmp_path), V1)

    history, versions = AC.load_history_from_s3(client, OUT, 'scvfd', CATALOG)
    assert set(history) == {'hydrants', 'roads', 'lidar_basemap'}
    assert history['roads'][0]['id'] == f'roads-{V1}'
    assert versions == [V1]


def test_second_publish_reuses_unchanged_layers_end_to_end(tmp_path):
    client = FakeS3()
    config = _config(tmp_path)
    staging = _make_staging(tmp_path)
    _publish(config, client, staging, V1)

    _write_layer(staging, 'hydrants', 'EDITED')
    summary = _publish(config, client, staging, V2)

    assert summary['written_layers'] == ['hydrants']
    assert sorted(summary['reused_layers']) == ['lidar_basemap', 'roads']
    # the reused layers get no new Item at all
    assert not _has(client, f'scvfd/catalog/roads/roads-{V2}.json')
    assert _has(client, f'scvfd/catalog/hydrants/hydrants-{V2}.json')


def test_corrupt_previous_catalog_does_not_stop_a_publish(tmp_path):
    client = FakeS3()
    config = _config(tmp_path)
    staging = _make_staging(tmp_path)
    _publish(config, client, staging, V1)
    client.put_object(Bucket=OUT, Key=f'scvfd/catalog/roads/roads-{V1}.json',
                      Body=b'{ not json')

    summary = _publish(config, client, staging, V2)

    assert summary['status'] == 'ok'
    assert 'roads' in summary['written_layers'], \
        'unreadable history must mean rewrite, never silent reuse'


def test_history_is_empty_for_a_first_publish(tmp_path):
    client = FakeS3()
    assert AC.load_history_from_s3(client, OUT, 'nobody', CATALOG) == ({}, [])


def test_every_href_in_a_catalog_resolves_to_a_real_object(tmp_path):
    """The regression that the on-box kennedy publish caught.

    A reused Item lives under the version that first wrote it. Rebuilding its
    href from the *current* version's base URL produced a path naming the new
    version's directory and the old version's filename, with nothing there.
    Resolving every href to an object is the assertion that holds.
    """
    client = FakeS3()
    config = _config(tmp_path)
    staging = _make_staging(tmp_path)
    _publish(config, client, staging, V1)
    _write_layer(staging, 'hydrants', 'EDITED')
    _publish(config, client, staging, V2)

    version_catalog = _doc(client, f'scvfd/catalog/versions/{V2}/catalog.json')
    item_hrefs = [l['href'] for l in version_catalog['links']
                  if l.get('rel') == 'item']
    assert item_hrefs, 'version catalog must name its items'
    for href in item_hrefs:
        key = atlas_store.key_from_href(href, CATALOG, 'scvfd/catalog')
        assert key and _has(client, key), \
            f'version catalog points at a nonexistent object: {href}'

    for layer in ('roads', 'hydrants', 'lidar_basemap'):
        collection = _doc(client, f'scvfd/catalog/{layer}/collection.json')
        for link in collection['links']:
            if link.get('rel') != 'item':
                continue
            key = atlas_store.key_from_href(link['href'], CATALOG, 'scvfd/catalog')
            assert key and _has(client, key), \
                f"collection {layer} points at a nonexistent object: {link['href']}"


def test_reused_item_href_names_the_version_that_wrote_it(tmp_path):
    client = FakeS3()
    config = _config(tmp_path)
    staging = _make_staging(tmp_path)
    _publish(config, client, staging, V1)
    _write_layer(staging, 'hydrants', 'EDITED')
    _publish(config, client, staging, V2)

    version_catalog = _doc(client, f'scvfd/catalog/versions/{V2}/catalog.json')
    roads = next(l['href'] for l in version_catalog['links']
                 if l.get('title') == 'roads')
    hydrants = next(l['href'] for l in version_catalog['links']
                    if l.get('title') == 'hydrants')

    assert roads == f'{CATALOG}roads/roads-{V1}.json'
    assert hydrants == f'{CATALOG}hydrants/hydrants-{V2}.json'


def test_reuse_survives_across_multiple_publishes(tmp_path):
    """The kennedy three-publish regression.

    A layer reused in V2 has no Item in V2 — only a Collection pointing back at
    V1. Loading history by listing therefore lost the layer entirely and
    rewrote it in V3, so reuse alternated on/off/on. History has to follow the
    Collection's item links.
    """
    client = FakeS3()
    config = _config(tmp_path)
    staging = _make_staging(tmp_path)
    _publish(config, client, staging, V1)

    # V2: hydrants edited, everything else untouched -> roads/raster reused.
    _write_layer(staging, 'hydrants', 'EDITED')
    s2 = _publish(config, client, staging, V2)
    assert s2['written_layers'] == ['hydrants']
    assert not _has(client, f'scvfd/catalog/roads/roads-{V2}.json')

    # V3: nothing changed at all since V2. Everything must be reused.
    s3 = _publish(config, client, staging, V3)

    assert s3['written_layers'] == [], \
        'a publish with no edits must write no new Items'
    assert sorted(s3['reused_layers']) == ['hydrants', 'lidar_basemap', 'roads']

    # roads is still referenced all the way back at V1, two hops later.
    version_catalog = _doc(client, f'scvfd/catalog/versions/{V3}/catalog.json')
    roads = next(l['href'] for l in version_catalog['links']
                 if l.get('title') == 'roads')
    assert roads == f'{CATALOG}roads/roads-{V1}.json'
    assert _has(client, atlas_store.key_from_href(roads, CATALOG, 'scvfd/catalog'))


def test_the_version_list_accumulates_across_publishes(tmp_path):
    """`discover_versions` reads this, so a lost link is a lost version."""
    client = FakeS3()
    config = _config(tmp_path)
    staging = _make_staging(tmp_path)
    _publish(config, client, staging, V1)
    _write_layer(staging, 'hydrants', 'EDITED')
    s2 = _publish(config, client, staging, V2)

    assert s2['versions'] == [V1, V2]
    assert AC.known_versions(_doc(client, 'scvfd/catalog/catalog.json')) == [V1, V2]
    assert _has(client, f'scvfd/catalog/versions/{V1}/catalog.json')
    assert _has(client, f'scvfd/catalog/versions/{V2}/catalog.json')


def test_the_version_list_is_rebuildable_from_a_listing(tmp_path):
    """The root Catalog is the one mutable document, so it must never be the
    only record of what exists."""
    client = FakeS3()
    config = _config(tmp_path)
    staging = _make_staging(tmp_path)
    _publish(config, client, staging, V1)
    _write_layer(staging, 'hydrants', 'EDITED')
    _publish(config, client, staging, V2)

    client.delete_object(Bucket=OUT, Key='scvfd/catalog/catalog.json')
    keys = atlas_store.list_keys(OUT, 'scvfd/catalog/versions/', client=client)
    recovered = sorted(k.split('/')[-2] for k in keys)
    assert recovered == [V1, V2]


def test_history_survives_a_hop_for_every_layer(tmp_path):
    client = FakeS3()
    config = _config(tmp_path)
    staging = _make_staging(tmp_path)
    _publish(config, client, staging, V1)
    _write_layer(staging, 'hydrants', 'EDITED')
    _publish(config, client, staging, V2)

    history, _ = AC.load_history_from_s3(client, OUT, 'scvfd', CATALOG)
    assert set(history) == {'hydrants', 'roads', 'lidar_basemap'}
    assert [i['id'] for i in history['hydrants']] == [
        f'hydrants-{V1}', f'hydrants-{V2}'], 'both versions, oldest first'
    assert [i['id'] for i in history['roads']] == [f'roads-{V1}'], \
        'reused layer keeps its history through the collection link'


def test_missing_referenced_item_falls_back_to_rewriting(tmp_path):
    """If history points at an Item that has been deleted, rewrite it."""
    client = FakeS3()
    config = _config(tmp_path)
    staging = _make_staging(tmp_path)
    _publish(config, client, staging, V1)
    _write_layer(staging, 'hydrants', 'EDITED')
    _publish(config, client, staging, V2)

    client.delete_object(Bucket=OUT, Key=f'scvfd/catalog/roads/roads-{V1}.json')

    s3 = _publish(config, client, staging, V3)
    assert 'roads' in s3['written_layers'], \
        'a dangling reference must mean rewrite, never a broken catalog'


# --------------------------------------------------------------------------- #
# Layer addressing
#
# Pinning is gone. It addressed a sibling *version* directory
# (`../../../{version}/layers/...`), which no longer exists anywhere and had no
# counterpart under the published `current/` prefix, so those URLs would 404 on
# CloudFront. One relative form serves both hosts, backed by the mirror in
# atlas_store.plan_current_layers.
# --------------------------------------------------------------------------- #

def _item_filenames(item):
    """Basenames of every file an Item's assets name — local test helper."""
    from pathlib import Path as _P
    return {_P(a['href']).name for a in (item.get('assets') or {}).values()
            if isinstance(a, dict) and a.get('href')}


def test_layer_data_url_is_relative_and_host_independent():
    """One form, resolving correctly from a workspace and from CloudFront.

    From `{atlas}/current/outlets/webmap/` on S3 it finds the mirror at
    `{atlas}/current/layers/`. That is what lets publish stay a pure snapshot:
    there is no URL to rewrite and no second materialize.
    """
    assert AC.layer_data_url('roads', 'roads.geojson') == '../../layers/roads/roads.geojson'
    assert AC.layer_data_url('lidar_basemap', 'lidar_basemap.tiff.jpg') == \
        '../../layers/lidar_basemap/lidar_basemap.tiff.jpg'


def test_layer_data_url_climbs_exactly_two_levels():
    """Regression guard on the depth, which is what makes both hosts work.

    An outlet directory always sits two below the current root, so the URL must
    climb exactly two. Off-by-one here is invisible locally — a wrong directory
    and a right one both resolve to *something* during a relative join — and
    only shows up as a 404 on the served copy.
    """
    url = AC.layer_data_url('roads', 'roads.geojson')
    assert url.startswith('../../layers/')
    assert not url.startswith('../../../')


def test_item_records_every_servable_file_in_the_layer_dir(tmp_path):
    """A raster layer's Item must name the rendered image too, not just the tiff."""
    client = FakeS3()
    config = _config(tmp_path)
    staging = _make_staging(tmp_path)
    # what a raster layer really looks like: source + what the webmap requests
    (staging / 'layers' / 'lidar_basemap' / 'lidar_basemap.tiff.jpg').write_text('JPG')
    (staging / 'layers' / 'lidar_basemap' / 'stats.json').write_text('{}')

    _publish(config, client, staging, V1)
    item = _item(client, 'lidar_basemap', V1)

    names = _item_filenames(item)
    assert 'lidar_basemap.tiff' in names
    assert 'lidar_basemap.tiff.jpg' in names
    assert 'stats.json' not in names, 'sidecar is not servable data'
    assert F.item_checksum(item), 'primary asset still carries the checksum'


# --------------------------------------------------------------------------- #
# S3 alternates
# --------------------------------------------------------------------------- #

def test_public_layers_get_an_https_primary_href(tmp_path):
    """S3 is the source of truth, so the served URL is the primary href.

    An earlier slice kept the primary local because a deterministic key written
    before a failed upload would name a missing object. The push now raises and
    takes the publish with it, so that cannot happen.
    """
    client = FakeS3()
    config = _cloud_config(tmp_path)
    _publish(config, client, _make_staging(tmp_path), V1)

    data = _item(client, 'hydrants', V1)['assets']['data']
    assert data['href'] == f'{CDN}/scvfd/layers/hydrants/{V1}/hydrants.geojson'
    assert data['alternate']['s3']['href'] == \
        f's3://{OUT}/scvfd/layers/hydrants/{V1}/hydrants.geojson'
    assert data['alternate']['local']['href'] == 'hydrants/hydrants.geojson'
    assert AC.ALTERNATE_EXTENSION in _item(client, 'hydrants', V1)['stac_extensions']


def test_protected_layers_keep_an_s3_primary_href(tmp_path):
    """A protected layer has no HTTPS reader until Phase 4.

    Naming a CloudFront URL that 403s would be less honest than naming the
    bucket, and it would let a reader believe the tier split had been crossed.
    """
    client = FakeS3()
    config = _cloud_config(tmp_path)
    _publish(config, client, _make_staging(tmp_path), V1)

    # hydrants is shareable -> public; roads has no tier -> internal
    roads = _item(client, 'roads', V1)
    assert roads['assets']['data']['href'] == \
        f's3://{PRIV}/scvfd/layers/roads/{V1}/roads.geojson'
    # The catalog document itself is served from CloudFront whatever the tier;
    # what must never appear there is a public URL for the *data*.
    assert 'cdn.example.org' not in json.dumps(roads['assets'])


def test_hrefs_are_written_with_no_cloud_block_at_all(tmp_path):
    """There is no opt-out: an atlas with no `cloud` config still resolves to
    the code-default buckets, because S3 is the backend rather than a feature.
    """
    client = FakeS3()
    config = _config(tmp_path)
    del config['cloud']
    bucket = atlas_store.DEFAULT_OUTLETS_BUCKET
    summary = AC.publish_catalog(config, _make_staging(tmp_path), V1, client=client)
    atlas_store.upload_documents(bucket, summary['documents'], client=client)

    assert summary['asset_hrefs'] > 0
    item = _doc(client, f'scvfd/catalog/hydrants/hydrants-{V1}.json', bucket=bucket)
    assert item['assets']['data']['href'].startswith(
        atlas_store.DEFAULT_PUBLIC_BASE_URL + '/scvfd/layers/hydrants/')


def test_a_reused_item_keeps_the_version_that_holds_its_bytes(tmp_path):
    """Regression: hrefs must never be rebuilt at the *current* version.

    This is the shape of the first bug this branch hit — a reused Item's href
    reconstructed from the new version's base, naming a key that holds nothing.
    Only newly written Items may be rewritten.
    """
    client = FakeS3()
    config = _cloud_config(tmp_path)
    staging = _make_staging(tmp_path)
    _publish(config, client, staging, V1)
    _publish(config, client, staging, V2)

    # A reused layer writes no Item at the new version at all — V2's Collection
    # links back to where the bytes actually are.
    assert not _has(client, f'scvfd/catalog/hydrants/hydrants-{V2}.json')
    item = _item(client, 'hydrants', V1)
    assert item['assets']['data']['href'] == \
        f'{CDN}/scvfd/layers/hydrants/{V1}/hydrants.geojson'
    assert V2 not in item['assets']['data']['href']

    collection = _doc(client, 'scvfd/catalog/hydrants/collection.json')
    item_links = [l['href'] for l in collection['links'] if l['rel'] == 'item']
    assert item_links == [f'{CATALOG}hydrants/hydrants-{V1}.json']


def test_summary_carries_what_the_uploader_needs(tmp_path):
    client = FakeS3()
    config = _cloud_config(tmp_path)
    summary = _publish(config, client, _make_staging(tmp_path), V1)

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
    client = FakeS3()
    config = _cloud_config(tmp_path)
    staging = _make_staging(tmp_path)
    _publish(config, client, staging, V1)
    _write_layer(staging, 'hydrants', 'EDITED')
    summary = _publish(config, client, staging, V2)

    assert summary['written_layers'] == ['hydrants']
    versions = summary['layer_versions']
    assert set(versions) == {'hydrants', 'roads', 'lidar_basemap'}, \
        'every layer in the version, not just the rewritten one'
    assert versions['hydrants'] == V2
    assert versions['roads'] == V1, 'reused layer keyed where its data lives'
    assert versions['lidar_basemap'] == V1


# --------------------------------------------------------------------------- #
# Outlets in a published version
# --------------------------------------------------------------------------- #

def _with_outlets(config):
    config['assets'] = {
        'webmap': {'type': 'outlet', 'access': ['public'],
                   'in_layers': ['hydrants', 'roads']},
        'console': {'type': 'outlet', 'access': ['admin']},
    }
    return config


def _build_outlet(staging, name, rel='index.html', content='<html>'):
    path = staging / 'outlets' / name / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_outlets_are_catalogued_beside_layers(tmp_path):
    client = FakeS3()
    config = _with_outlets(_cloud_config(tmp_path))
    staging = _make_staging(tmp_path)
    _build_outlet(staging, 'webmap')
    _build_outlet(staging, 'console', rel='public/index.html')

    summary = _publish(config, client, staging, V1)

    assert sorted(summary['written_outlets']) == ['console', 'webmap']
    item = _item(client, 'webmap', V1)
    assert item['properties'][F.PREFIX_PROP] == f'scvfd/outlets/webmap/{V1}'
    assert item['assets']['index']['href'] == \
        f's3://{PRIV}/scvfd/outlets/webmap/{V1}/index.html'


def test_an_outlet_item_records_its_layer_lineage(tmp_path):
    client = FakeS3()
    config = _with_outlets(_cloud_config(tmp_path))
    staging = _make_staging(tmp_path)
    _build_outlet(staging, 'webmap')

    _publish(config, client, staging, V1)

    derived = [l['href'] for l in _item(client, 'webmap', V1)['links']
               if l['rel'] == 'derived_from']
    assert derived == [f'{CATALOG}hydrants/hydrants-{V1}.json',
                       f'{CATALOG}roads/roads-{V1}.json']


def test_an_unchanged_outlet_is_reused_across_publishes(tmp_path):
    """The redundancy this removes: westport's 491 MB of outlets was re-copied
    on every publish."""
    client = FakeS3()
    config = _with_outlets(_cloud_config(tmp_path))
    staging = _make_staging(tmp_path)
    _build_outlet(staging, 'webmap')
    _publish(config, client, staging, V1)

    _write_layer(staging, 'hydrants', 'EDITED')
    summary = _publish(config, client, staging, V2)

    assert summary['reused_outlets'] == ['webmap']
    assert summary['outlet_versions']['webmap'] == V1
    assert not _has(client, f'scvfd/catalog/webmap/webmap-{V2}.json')


def test_a_rebuilt_outlet_is_written_at_the_new_version(tmp_path):
    client = FakeS3()
    config = _with_outlets(_cloud_config(tmp_path))
    staging = _make_staging(tmp_path)
    _build_outlet(staging, 'webmap')
    _publish(config, client, staging, V1)

    _build_outlet(staging, 'webmap', rel='data/hydrants.geojson', content='{}')
    summary = _publish(config, client, staging, V2)

    assert summary['written_outlets'] == ['webmap']
    assert summary['outlet_versions']['webmap'] == V2


def test_a_never_built_outlet_is_missing_not_fatal(tmp_path):
    client = FakeS3()
    config = _with_outlets(_cloud_config(tmp_path))
    staging = _make_staging(tmp_path)
    _build_outlet(staging, 'webmap')

    summary = _publish(config, client, staging, V1)
    assert summary['written_outlets'] == ['webmap']
    assert 'console' not in summary['outlet_versions']


def test_the_version_catalog_names_outlets_and_layers(tmp_path):
    client = FakeS3()
    config = _with_outlets(_cloud_config(tmp_path))
    staging = _make_staging(tmp_path)
    _build_outlet(staging, 'webmap')
    _publish(config, client, staging, V1)

    catalog = _doc(client, f'scvfd/catalog/versions/{V1}/catalog.json')
    titles = {l.get('title') for l in catalog['links'] if l['rel'] == 'item'}
    assert titles == {'hydrants', 'roads', 'lidar_basemap', 'webmap'}


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
    staging = _make_staging(tmp_path)
    layer_dir = staging / 'layers' / 'roads'
    (layer_dir / '.htpasswd').write_text('admin:$apr1$redacted')
    (layer_dir / 'unrelated_export.csv').write_text('a,b,c')
    (layer_dir / 'roads.tiff.jpg').write_text('IMG')

    assets = AC.scan_layers(LAYERS, staging / 'layers')
    assert sorted(assets['roads']['files']) == ['roads.geojson', 'roads.tiff.jpg']


def test_a_stray_file_never_reaches_an_item(tmp_path):
    client = FakeS3()
    config = _cloud_config(tmp_path)
    staging = _make_staging(tmp_path)
    (staging / 'layers' / 'roads' / '.htpasswd').write_text('admin:$apr1$redacted')
    _publish(config, client, staging, V1)

    item = _item(client, 'roads', V1)
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
    staging = _make_staging(tmp_path)
    stray_layer = staging / 'layers' / 'lpss'
    stray_layer.mkdir(parents=True)
    (stray_layer / '.htpasswd').write_text('admin:$apr1$redacted')

    layers = LAYERS + [{'name': 'lpss', 'access': ['admin']}]
    assets = AC.scan_layers(layers, staging / 'layers')
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
    staging = _make_staging(tmp_path)
    raster = staging / 'layers' / 'lidar_basemap'
    for f in ('lidar_basemap.tiff.jpg', 'lidar_basemap.tiff.aux.xml',
              'lidar_basemap.tiff.jpg.aux.xml'):
        (raster / f).write_text('x')

    assets = AC.scan_layers(LAYERS, staging / 'layers')
    assert sorted(assets['lidar_basemap']['files']) == [
        'lidar_basemap.tiff', 'lidar_basemap.tiff.jpg']


# --------------------------------------------------------------------------- #
# Rendered raster selection
# --------------------------------------------------------------------------- #

def test_prefers_png_over_jpg(tmp_path):
    d = tmp_path / 'basemap'
    d.mkdir()
    (d / 'basemap.tiff.jpg').write_text('J')
    (d / 'basemap.tiff.png').write_text('P')
    assert AC.rendered_raster_filename(tmp_path, 'basemap') == 'basemap.tiff.png'


def test_falls_back_to_jpg(tmp_path):
    d = tmp_path / 'basemap'
    d.mkdir()
    (d / 'basemap.tiff.jpg').write_text('J')
    assert AC.rendered_raster_filename(tmp_path, 'basemap') == 'basemap.tiff.jpg'


def test_no_rendered_image_is_none_not_a_guess(tmp_path):
    """The regression: an unchecked fallback named a file that isn't there.

    kennedy's lidar_basemap has the source layer declared but no rendered
    image, and the webmap emitted `lidar_basemap.tiff.jpg` regardless — a
    source URL guaranteed to 404, which is how it surfaced on CloudFront.
    """
    (tmp_path / 'lidar_basemap').mkdir()
    assert AC.rendered_raster_filename(tmp_path, 'lidar_basemap') is None


def test_source_tiff_alone_is_not_a_rendered_image(tmp_path):
    # The GeoTIFF is the source, not something a browser can show.
    d = tmp_path / 'basemap'
    d.mkdir()
    (d / 'basemap.tiff').write_text('T')
    assert AC.rendered_raster_filename(tmp_path, 'basemap') is None


def test_missing_layer_directory_is_none(tmp_path):
    assert AC.rendered_raster_filename(tmp_path, 'never_created') is None
