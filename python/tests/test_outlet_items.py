"""Outlets as STAC Items (Phase 3 Step 3 task 6, issue #159).

A published version is a Catalog and everything in it is an Item — outlets
included. An outlet Item names where the outlet starts, what tier it is, and
which layer Items it was built from; it does not enumerate the outlet's files.

Pure logic plus a tmpdir outlet tree. No S3, GDAL or QGIS, so it runs in the
bare local env.

    cd python && python -m pytest tests/test_outlet_items.py -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import atlas_catalog as AC
import atlas_store
import federation as F


BBOX = {'north': 38.6, 'south': 38.4, 'east': -122.9, 'west': -123.1}
V1 = '2026-07-02'
V2 = '2026-08-25'
BASE = 'https://example.org/scvfd/catalog/'

LAYERS = [
    {'name': 'hydrants', 'title': 'Hydrants', 'access': ['public']},
    {'name': 'roads', 'title': 'Roads', 'access': ['public']},
]


def _layer_assets(hydrants='H1', roads='R1'):
    return {
        'hydrants': {'href': 'hydrants/hydrants.geojson', 'size': 2,
                     'checksum': f'1220{hydrants}', 'files': ['hydrants.geojson']},
        'roads': {'href': 'roads/roads.geojson', 'size': 2,
                  'checksum': f'1220{roads}', 'files': ['roads.geojson']},
    }


def _build(version, outlet_assets, *, history=None, layer_assets=None):
    return F.build_atlas_catalog(
        atlas_id='scvfd', atlas_description='test',
        layers=LAYERS, bbox=BBOX, version=version,
        layer_assets=layer_assets or _layer_assets(),
        history=history or {}, catalog_base_url=BASE,
        outlet_assets=outlet_assets)


def _history(built):
    """{name: [Item]} the way `load_history` hands the next publish its past."""
    return {name: [item] for name, item in built['items'].items()}


def _webmap_spec(checksum='1220WM', **overrides):
    spec = {
        'entry_href': f's3://private/scvfd/outlets/webmap/{V1}/index.html',
        'prefix': f'scvfd/outlets/webmap/{V1}',
        'access': ['public'],
        'checksum': checksum,
        'entry_size': 120,
        'file_count': 3,
        'title': 'Web map',
        'in_layers': ['hydrants', 'roads'],
    }
    spec.update(overrides)
    return spec


# --------------------------------------------------------------------------- #
# content_checksum — a directory's identity as one value
# --------------------------------------------------------------------------- #

def test_content_checksum_is_order_independent():
    a = F.content_checksum({'index.html': 'aa', 'data/x.geojson': 'bb'})
    b = F.content_checksum({'data/x.geojson': 'bb', 'index.html': 'aa'})
    assert a == b


def test_content_checksum_moves_on_content():
    a = F.content_checksum({'index.html': 'aa'})
    b = F.content_checksum({'index.html': 'ab'})
    assert a != b


def test_content_checksum_moves_on_rename():
    """A renamed file with identical bytes is a different outlet: the entry
    href or a data URL may now point at nothing."""
    a = F.content_checksum({'index.html': 'aa'})
    b = F.content_checksum({'index.htm': 'aa'})
    assert a != b


def test_content_checksum_is_multihash_encoded():
    value = F.content_checksum({'index.html': 'aa'})
    assert value.startswith('1220')
    assert len(value) == 68


# --------------------------------------------------------------------------- #
# Scanning a built outlet directory
# --------------------------------------------------------------------------- #

def _write(root, rel, content='x'):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_scan_outlet_dir_finds_a_root_index(tmp_path):
    outlet = tmp_path / 'webmap'
    _write(outlet, 'index.html', '<html>')
    _write(outlet, 'data/hydrants.geojson', '{}')

    spec = AC.scan_outlet_dir(outlet)
    assert spec['entry'] == 'index.html'
    assert spec['file_count'] == 2
    assert set(spec['files']) == {'index.html', 'data/hydrants.geojson'}


def test_scan_outlet_dir_falls_back_to_the_public_variant(tmp_path):
    """`html` and `console` have no root index — all four role variants are
    subdirectories and the public entry is `public/index.html`."""
    outlet = tmp_path / 'html'
    _write(outlet, 'public/index.html', '<html>')
    _write(outlet, 'admin/index.html', '<html>')

    assert AC.scan_outlet_dir(outlet)['entry'] == 'public/index.html'


def test_scan_outlet_dir_skips_dotfiles(tmp_path):
    """`.htpasswd` is written into outlet directories by add_htpasswds(). It is
    regenerated every config build and must not be archived."""
    outlet = tmp_path / 'html'
    _write(outlet, 'public/index.html', '<html>')
    _write(outlet, 'admin/.htpasswd', 'admin:$apr1$x')

    spec = AC.scan_outlet_dir(outlet)
    assert set(spec['files']) == {'public/index.html'}


def test_scan_outlet_dir_returns_none_without_an_entry(tmp_path):
    outlet = tmp_path / 'sqldb'
    _write(outlet, 'atlas.db', 'binary')
    assert AC.scan_outlet_dir(outlet) is None


def test_scan_outlets_skips_what_was_never_built(tmp_path):
    _write(tmp_path / 'webmap', 'index.html', '<html>')
    found = AC.scan_outlets(tmp_path, ['webmap', 'runbook'])
    assert list(found) == ['webmap']


def test_scan_outlet_dir_checksum_tracks_a_nested_change(tmp_path):
    outlet = tmp_path / 'webmap'
    _write(outlet, 'index.html', '<html>')
    _write(outlet, 'data/hydrants.geojson', '{"features":[]}')
    before = AC.scan_outlet_dir(outlet)['checksum']

    _write(outlet, 'data/hydrants.geojson', '{"features":[1]}')
    assert AC.scan_outlet_dir(outlet)['checksum'] != before


# --------------------------------------------------------------------------- #
# The Item itself
# --------------------------------------------------------------------------- #

def test_outlet_item_carries_entry_and_prefix():
    item = F.build_outlet_item(
        'scvfd', 'webmap', V1, BBOX,
        entry_href='s3://private/scvfd/outlets/webmap/2026-07-02/index.html',
        prefix='scvfd/outlets/webmap/2026-07-02',
        access=['public'], checksum='1220WM', file_count=312,
        catalog_base_url=BASE)

    assert item['id'] == f'webmap-{V1}'
    assert item['collection'] == 'webmap'
    assert item['assets']['index']['roles'] == ['outlet', 'entry']
    assert item['properties'][F.PREFIX_PROP] == 'scvfd/outlets/webmap/2026-07-02'
    assert item['properties'][F.ITEM_TYPE_PROP] == 'outlet'
    assert item['properties']['atlas:access'] == ['public']
    assert item['properties']['atlas:file_count'] == 312
    assert F.is_outlet_item(item)


def test_outlet_item_keeps_the_directory_checksum_off_the_entry_asset():
    """The roll-up is not index.html's checksum. Writing it as `file:checksum`
    would be a lie a `file:` reader would act on."""
    item = F.build_outlet_item(
        'scvfd', 'webmap', V1, BBOX, entry_href='s3://b/i.html',
        prefix='p', checksum='1220WM', catalog_base_url=BASE)

    assert 'file:checksum' not in item['assets']['index']
    assert F.item_content_checksum(item) == '1220WM'


# --------------------------------------------------------------------------- #
# Lineage
# --------------------------------------------------------------------------- #

def test_derived_from_names_the_layer_items_in_this_version():
    built = _build(V1, {'webmap': _webmap_spec()})
    item = built['items']['webmap']

    derived = [l['href'] for l in item['links'] if l['rel'] == 'derived_from']
    assert derived == [
        f'{BASE}hydrants/hydrants-{V1}.json',
        f'{BASE}roads/roads-{V1}.json',
    ]


def test_derived_from_follows_a_reused_layer_to_its_own_version():
    """`roads` did not change, so V2 reuses its V1 Item — and the outlet's
    lineage must point at where that Item actually lives, not at a V2 path
    that holds nothing."""
    first = _build(V1, {'webmap': _webmap_spec()})
    second = _build(V2, {'webmap': _webmap_spec(checksum='1220WM2')},
                    history=_history(first),
                    layer_assets=_layer_assets(hydrants='H2'))

    assert second['reused'] == ['roads']
    derived = [l['href'] for l in second['items']['webmap']['links']
               if l['rel'] == 'derived_from']
    assert derived == [
        f'{BASE}hydrants/hydrants-{V2}.json',
        f'{BASE}roads/roads-{V1}.json',
    ]


def test_derived_from_skips_a_layer_with_no_data():
    built = _build(V1, {'webmap': _webmap_spec(in_layers=['hydrants', 'ghost'])})
    derived = [l['href'] for l in built['items']['webmap']['links']
               if l['rel'] == 'derived_from']
    assert derived == [f'{BASE}hydrants/hydrants-{V1}.json']


# --------------------------------------------------------------------------- #
# Reuse across publishes
# --------------------------------------------------------------------------- #

def test_an_unchanged_outlet_is_reused_not_rewritten():
    first = _build(V1, {'webmap': _webmap_spec()})
    second = _build(V2, {'webmap': _webmap_spec()}, history=_history(first))

    assert second['outlets_reused'] == ['webmap']
    assert second['outlets_written'] == []
    assert 'webmap' not in second['items']
    # The version that actually holds the bytes stays V1 — nothing is uploaded.
    assert second['outlet_versions']['webmap'] == V1


def test_a_changed_outlet_is_rewritten_at_the_new_version():
    first = _build(V1, {'webmap': _webmap_spec()})
    second = _build(V2, {'webmap': _webmap_spec(checksum='1220CHANGED')},
                    history=_history(first))

    assert second['outlets_written'] == ['webmap']
    assert second['outlet_versions']['webmap'] == V2


def test_an_outlet_with_no_previous_checksum_is_rewritten():
    """Fail-closed: an unknown checksum on either side means write."""
    first = _build(V1, {'webmap': _webmap_spec()})
    second = _build(V2, {'webmap': _webmap_spec(checksum=None)},
                    history=_history(first))
    assert second['outlets_written'] == ['webmap']


def test_version_catalog_links_layers_and_outlets_alike():
    built = _build(V1, {'webmap': _webmap_spec()})
    titles = {l.get('title') for l in built['version_catalog']['links']
              if l['rel'] == 'item'}
    assert titles == {'hydrants', 'roads', 'webmap'}


def test_a_reused_outlet_still_appears_in_the_new_version_catalog():
    first = _build(V1, {'webmap': _webmap_spec()})
    second = _build(V2, {'webmap': _webmap_spec()}, history=_history(first))

    hrefs = [l['href'] for l in second['version_catalog']['links']
             if l.get('title') == 'webmap']
    assert hrefs == [f'{BASE}webmap/webmap-{V1}.json']


def test_outlet_gets_its_own_collection():
    built = _build(V1, {'webmap': _webmap_spec()})
    assert built['collections']['webmap']['id'] == 'webmap'
    assert built['collections']['webmap']['version'] == V1


def test_an_outlet_named_like_a_layer_is_skipped():
    """Layers and outlets share one Collection namespace, so a collision would
    hand one of them the other's Collection."""
    built = _build(V1, {'roads': _webmap_spec()})
    assert built['outlets_missing'] == ['roads']
    assert built['collections']['roads']['title'] == 'Roads'


def test_layer_versions_exclude_outlets():
    """`plan_layer_uploads` iterates `versions`; an outlet in there would be
    looked for in the layer tree."""
    built = _build(V1, {'webmap': _webmap_spec()})
    assert set(built['versions']) == {'hydrants', 'roads'}
    assert set(built['outlet_versions']) == {'webmap'}


# --------------------------------------------------------------------------- #
# What gets archived, and where
# --------------------------------------------------------------------------- #

def _archive_config(tmp_path, **cloud):
    settings = {'private_bucket': 'private-b', 'outlets_bucket': 'public-b'}
    settings.update(cloud)
    return {
        'name': 'scvfd',
        'data_root': str(tmp_path),
        'cloud': settings,
        'dataswale': {'bbox': BBOX, 'layers': LAYERS},
        'assets': {
            'webmap': {'type': 'outlet', 'access': ['public']},
            'console': {'type': 'outlet', 'access': ['admin']},
            'hydrants': {'type': 'inlet'},
        },
    }


def test_archivable_outlets_includes_protected_ones(tmp_path):
    """The archive is private, so it is not filtered by tier or by the public
    `cloud.outlets` allowlist."""
    assert atlas_store.archivable_outlets(_archive_config(tmp_path)) == [
        'console', 'webmap']


def test_archivable_outlets_respects_versioned_outlets(tmp_path):
    config = _archive_config(tmp_path)
    config['dataswale']['versioned_outlets'] = ['webmap']
    assert atlas_store.archivable_outlets(config) == ['webmap']


def test_outlet_archive_always_goes_to_the_private_bucket(tmp_path):
    """One outlet directory can hold several tiers (#177), so routing the
    archive by the outlet's own access would publish the admin console."""
    outlets = tmp_path / 'outlets'
    _write(outlets / 'console', 'public/index.html', '<html>')
    _write(outlets / 'console', 'admin/index.html', '<admin>')

    specs = AC.scan_outlets(outlets, ['console'])
    plan = atlas_store.plan_outlet_uploads(
        _archive_config(tmp_path), outlets, specs, {'console': V1})

    assert {bucket for _, bucket, _, _ in plan} == {'private-b'}
    assert sorted(key for _, _, key, _ in plan) == [
        f'scvfd/outlets/console/{V1}/admin/index.html',
        f'scvfd/outlets/console/{V1}/public/index.html',
    ]


def test_outlet_upload_plan_uses_the_version_that_holds_the_bytes(tmp_path):
    """A reused outlet is planned at the older version's keys — which is how
    the reconcile step finds them already present and uploads nothing."""
    outlets = tmp_path / 'outlets'
    _write(outlets / 'webmap', 'index.html', '<html>')

    specs = AC.scan_outlets(outlets, ['webmap'])
    plan = atlas_store.plan_outlet_uploads(
        _archive_config(tmp_path), outlets, specs, {'webmap': V1})

    assert [key for _, _, key, _ in plan] == [
        f'scvfd/outlets/webmap/{V1}/index.html']


def test_outlet_upload_plan_skips_a_file_that_vanished(tmp_path):
    outlets = tmp_path / 'outlets'
    _write(outlets / 'webmap', 'index.html', '<html>')
    specs = AC.scan_outlets(outlets, ['webmap'])
    specs['webmap']['files']['gone.js'] = '1220ff'

    plan = atlas_store.plan_outlet_uploads(
        _archive_config(tmp_path), outlets, specs, {'webmap': V1})
    assert [key for _, _, key, _ in plan] == [
        f'scvfd/outlets/webmap/{V1}/index.html']
