"""Version-aware STAC catalog tests (Phase 3, issue #159).

The catalog shape is Catalog(atlas) -> Collection(layer) -> Item(version), with
a thin per-version Catalog acting as the manifest. These functions are pure and
stdlib-only, so this suite runs in the bare local env with no GDAL/QGIS/S3.

Imports only the real `federation` and `atlas_store` modules — no sys.modules
stubbing, so nothing leaks into other suites (see issue #153).

    cd python && python -m pytest tests/test_federation_versions.py -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import federation as F


BBOX = {'north': 38.6, 'south': 38.4, 'east': -122.9, 'west': -123.1}
BASE = 'https://example.org/scvfd/stac/'

V1 = '2026-07-02'
V2 = '2026-08-25'
T1 = '2026-07-02T12:00:00+00:00'
T2 = '2026-08-25T12:00:00+00:00'


def _item(layer, version, dt, checksum='aaaa', href=None):
    assets = {'data': F.stac_asset(
        href or f'https://example.org/scvfd/versions/{version}/layers/{layer}.parquet',
        roles=['data'], size=1234, checksum=checksum)}
    return F.build_layer_item('scvfd', layer, version, BBOX, assets,
                              datetime_iso=dt, catalog_base_url=BASE)


def _rels(obj, rel):
    return [l['href'] for l in obj['links'] if l.get('rel') == rel]


# --------------------------------------------------------------------------- #
# geometry + assets
# --------------------------------------------------------------------------- #

def test_bbox_to_geometry_closes_the_ring():
    geom = F.bbox_to_geometry(F._bbox_to_stac(BBOX))
    ring = geom['coordinates'][0]
    assert geom['type'] == 'Polygon'
    assert len(ring) == 5
    assert ring[0] == ring[-1], 'polygon ring must close'
    assert ring[0] == [BBOX['west'], BBOX['south']]


def test_stac_asset_infers_geo_media_types():
    assert F.stac_asset('x/hydrants.parquet')['type'] == 'application/vnd.apache.parquet'
    assert F.stac_asset('x/hydrants.geojson')['type'] == 'application/geo+json'
    assert F.stac_asset('x/hillshade.pmtiles')['type'] == 'application/octet-stream'


def test_stac_asset_carries_file_extension_fields():
    asset = F.stac_asset('x/h.parquet', roles=['data'], title='Hydrants',
                         size=99, checksum='deadbeef')
    assert asset['file:size'] == 99
    assert asset['file:checksum'] == 'deadbeef'
    assert asset['roles'] == ['data']
    assert asset['title'] == 'Hydrants'


def test_stac_asset_omits_absent_optional_fields():
    asset = F.stac_asset('x/h.geojson')
    for absent in ('file:size', 'file:checksum', 'roles', 'title'):
        assert absent not in asset


# --------------------------------------------------------------------------- #
# access: fail-closed, unlike the outlet default
# --------------------------------------------------------------------------- #

def test_layer_without_access_is_not_public():
    """The outlet default is fail-open; the layer default must not be."""
    assert F.layer_access({'name': 'private_notes'}) == ['internal']
    assert F.is_public_layer({'name': 'private_notes'}) is False


def test_explicit_access_is_respected_and_normalized():
    assert F.layer_access({'name': 'x', 'access': 'admin'}) == ['admin']
    assert F.layer_access({'name': 'x', 'access': ['public', 'internal']}) == \
        ['public', 'internal']
    assert F.is_public_layer({'name': 'x', 'access': ['public']}) is True


def test_shareable_promotes_to_public():
    layer = {'name': 'hydrants', 'shareable': {'enabled': True}}
    assert F.layer_access(layer) == ['public']
    assert F.is_public_layer(layer) is True


def test_shareable_disabled_does_not_promote():
    assert F.layer_access({'name': 'x', 'shareable': {'enabled': False}}) == ['internal']


# --------------------------------------------------------------------------- #
# Items
# --------------------------------------------------------------------------- #

def test_item_is_a_valid_looking_stac_feature():
    item = _item('hydrants', V1, T1)
    assert item['type'] == 'Feature'
    assert item['stac_version'] == F.STAC_VERSION
    assert item['id'] == 'hydrants-2026-07-02'
    assert item['collection'] == 'hydrants'
    assert item['bbox'] == F._bbox_to_stac(BBOX)
    assert item['geometry']['type'] == 'Polygon'
    assert item['properties']['datetime'] == T1
    assert item['properties']['version'] == V1
    assert 'data' in item['assets']


def test_item_declares_the_extensions_it_uses():
    item = _item('hydrants', V1, T1)
    assert F.FILE_EXTENSION in item['stac_extensions']
    assert F.VERSION_EXTENSION in item['stac_extensions']


def test_item_links_point_at_its_collection():
    item = _item('hydrants', V1, T1)
    assert _rels(item, 'self') == [f'{BASE}hydrants/hydrants-{V1}.json']
    assert _rels(item, 'collection') == [f'{BASE}hydrants/collection.json']
    assert _rels(item, 'parent') == [f'{BASE}hydrants/collection.json']
    assert _rels(item, 'root') == [f'{BASE}catalog.json']


def test_derived_from_records_outlet_lineage():
    """An outlet Item names the layer Items it was built from (in_layers)."""
    assets = {'index': F.stac_asset('https://example.org/outlets/webmap/index.html',
                                    roles=['outlet', 'entry'])}
    item = F.build_layer_item(
        'scvfd', 'webmap', V2, BBOX, assets, datetime_iso=T2, catalog_base_url=BASE,
        derived_from=[f'{BASE}hydrants/hydrants-{V2}.json',
                      f'{BASE}roads/roads-{V1}.json'])
    assert _rels(item, 'derived_from') == [
        f'{BASE}hydrants/hydrants-{V2}.json',
        f'{BASE}roads/roads-{V1}.json',
    ]
    assert item['assets']['index']['type'] == 'text/html'


def test_extra_properties_merge_without_dropping_datetime():
    item = F.build_layer_item('scvfd', 'roads', V1, BBOX, {}, datetime_iso=T1,
                              properties={'feature_count': 412})
    assert item['properties']['feature_count'] == 412
    assert item['properties']['datetime'] == T1


# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #

def test_collection_spans_its_items_in_time():
    items = [_item('hydrants', V1, T1), _item('hydrants', V2, T2, checksum='bbbb')]
    coll = F.build_layer_collection('scvfd', {'name': 'hydrants', 'title': 'Hydrants'},
                                    BBOX, items, catalog_base_url=BASE)
    assert coll['type'] == 'Collection'
    assert coll['id'] == 'hydrants'
    assert coll['extent']['temporal']['interval'] == [[T1, T2]]
    assert coll['version'] == V2, 'collection version names the newest item'
    assert len(_rels(coll, 'item')) == 2


def test_collection_with_no_items_is_still_well_formed():
    coll = F.build_layer_collection('scvfd', {'name': 'empty'}, BBOX, [],
                                    catalog_base_url=BASE)
    assert coll['extent']['temporal']['interval'] == [[None, None]]
    assert 'version' not in coll
    assert _rels(coll, 'item') == []


def test_collection_version_is_readable_by_the_existing_consumer():
    """Phase 2's consumer side reads it; the new builder must stay compatible."""
    items = [_item('hydrants', V1, T1)]
    coll = F.build_layer_collection('scvfd', {'name': 'hydrants'}, BBOX, items)
    assert F.source_version_from_collection(coll) == V1


# --------------------------------------------------------------------------- #
# Version catalog — the manifest
# --------------------------------------------------------------------------- #

def test_version_catalog_links_every_constituent_item():
    items = [_item('hydrants', V2, T2), _item('roads', V1, T1)]
    cat = F.build_version_catalog('scvfd', V2, items, datetime_iso=T2,
                                  catalog_base_url=BASE)
    assert cat['type'] == 'Catalog'
    assert cat['id'] == f'scvfd-{V2}'
    assert cat['published'] == T2
    assert _rels(cat, 'self') == [f'{BASE}versions/{V2}/catalog.json']
    assert _rels(cat, 'item') == [
        f'{BASE}hydrants/hydrants-{V2}.json',
        f'{BASE}roads/roads-{V1}.json',
    ]


def test_version_catalog_references_an_older_item_for_an_unchanged_layer():
    """The whole point: version 2 names version 1's roads Item, no copy."""
    cat = F.build_version_catalog('scvfd', V2,
                                  [_item('hydrants', V2, T2), _item('roads', V1, T1)],
                                  catalog_base_url=BASE)
    roads = [h for h in _rels(cat, 'item') if 'roads' in h]
    assert roads == [f'{BASE}roads/roads-{V1}.json'], \
        'unchanged layer must be referenced at its own version, not re-stamped'


# --------------------------------------------------------------------------- #
# Version chain
# --------------------------------------------------------------------------- #

def test_version_chain_links_neighbours_and_latest():
    items = F.link_version_chain([
        _item('hydrants', V1, T1),
        _item('hydrants', V2, T2, checksum='bbbb'),
    ])
    first, last = items
    assert _rels(first, 'successor') == [f'{BASE}hydrants/hydrants-{V2}.json']
    assert _rels(first, 'predecessor') == []
    assert _rels(first, 'latest-version') == [f'{BASE}hydrants/hydrants-{V2}.json']
    assert _rels(last, 'predecessor') == [f'{BASE}hydrants/hydrants-{V1}.json']
    assert _rels(last, 'successor') == []
    assert _rels(last, 'latest-version') == [], 'latest does not link to itself'


def test_version_chain_is_a_noop_for_a_single_item():
    item, = F.link_version_chain([_item('hydrants', V1, T1)])
    for rel in ('predecessor', 'successor', 'latest-version'):
        assert _rels(item, rel) == []


def test_version_chain_is_idempotent():
    items = [_item('hydrants', V1, T1), _item('hydrants', V2, T2)]
    once = F.link_version_chain(items)
    counts = [len(i['links']) for i in once]
    twice = F.link_version_chain(once)
    assert [len(i['links']) for i in twice] == counts, 'must not accumulate links'


def test_version_chain_handles_empty_list():
    assert F.link_version_chain([]) == []


# --------------------------------------------------------------------------- #
# Reuse — what stops publish duplicating an unchanged layer
# --------------------------------------------------------------------------- #

def test_item_checksum_reads_the_primary_asset():
    assert F.item_checksum(_item('hydrants', V1, T1, checksum='abc123')) == 'abc123'
    assert F.item_checksum({'assets': {}}) is None
    assert F.item_checksum({}) is None


def test_unchanged_layer_is_reused_and_changed_layer_is_written():
    previous = {'hydrants': _item('hydrants', V1, T1, checksum='same'),
                'roads': _item('roads', V1, T1, checksum='old')}
    reuse, write = F.select_reusable_items(
        previous, {'hydrants': 'same', 'roads': 'new'})
    assert list(reuse) == ['hydrants']
    assert reuse['hydrants']['properties']['version'] == V1
    assert write == ['roads']


def test_new_layer_with_no_history_is_written():
    reuse, write = F.select_reusable_items({}, {'culverts': 'abc'})
    assert reuse == {}
    assert write == ['culverts']


def test_missing_checksum_fails_closed_on_both_sides():
    """An absent checksum must mean 'write', never 'assume unchanged'."""
    previous_no_sum = {'x': F.build_layer_item('scvfd', 'x', V1, BBOX,
                                               {'data': F.stac_asset('x.parquet')},
                                               datetime_iso=T1)}
    _, write = F.select_reusable_items(previous_no_sum, {'x': 'abc'})
    assert write == ['x'], 'previous Item has no checksum -> must rewrite'

    _, write = F.select_reusable_items(
        {'x': _item('x', V1, T1, checksum='abc')}, {'x': None})
    assert write == ['x'], 'unknown current checksum -> must rewrite'


def test_write_list_is_sorted_for_deterministic_publishes():
    _, write = F.select_reusable_items({}, {'zebra': 'a', 'alpha': 'b', 'middle': 'c'})
    assert write == ['alpha', 'middle', 'zebra']
