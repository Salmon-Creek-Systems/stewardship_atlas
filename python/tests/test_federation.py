"""Federation v1 round-trip tests.

Federation logic lives in python/federation.py with no heavy dependencies, so
this exercises the real source->catalog->consumer path locally without
GDAL/QGIS/shapely. It mirrors what the thin eddy/outlet/inlet materializers do:
the source masks + builds a static STAC catalog and writes the (masked) layer
GeoJSON; a fake fetcher maps URLs to those files; the consumer resolves the
collection, fetches its data asset, and optionally filters by bbox.

Mirrors the end-to-end acceptance scenario in
documents/development/federation-claude-code-handoff.md §5.

    cd python && python -m pytest tests/test_federation.py -v
"""

import json
import os
import sys
from pathlib import Path
from urllib.parse import urljoin

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import federation as F


BBOX = {'north': 38.6, 'south': 38.4, 'east': -122.9, 'west': -123.1}


def _feature(name, lon, lat, **props):
    return {'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
            'properties': {'name': name, **props}}


# --------------------------------------------------------------------------- #
# mask_features (the mask_properties eddy's core)
# --------------------------------------------------------------------------- #

def test_mask_block_strips_only_blocked():
    fc = {'type': 'FeatureCollection', 'features': [
        _feature('gate1', -123.0, 38.5, gate_combination='1234', material='steel')]}
    out = F.mask_features(fc, block=['gate_combination'])
    props = out['features'][0]['properties']
    assert 'gate_combination' not in props
    assert props['name'] == 'gate1' and props['material'] == 'steel'
    assert out['features'][0]['geometry'] == fc['features'][0]['geometry']


def test_mask_allow_keeps_only_allowed():
    fc = {'type': 'FeatureCollection', 'features': [
        _feature('t1', -123.0, 38.5, secret='x', public='y')]}
    out = F.mask_features(fc, allow=['name', 'public'])
    props = out['features'][0]['properties']
    assert set(props) == {'name', 'public'}


def test_mask_does_not_mutate_input():
    fc = {'type': 'FeatureCollection', 'features': [
        _feature('t1', -123.0, 38.5, secret='x')]}
    F.mask_features(fc, block=['secret'])
    assert 'secret' in fc['features'][0]['properties']


def test_mask_allow_block_mutually_exclusive():
    fc = {'type': 'FeatureCollection', 'features': []}
    with pytest.raises(ValueError):
        F.mask_features(fc, allow=['a'], block=['b'])


# --------------------------------------------------------------------------- #
# bbox filtering (the inlet's optional spatial filter)
# --------------------------------------------------------------------------- #

def test_bbox_filter_excludes_out_of_box_points():
    fc = {'type': 'FeatureCollection', 'features': [
        _feature('inside', -123.0, 38.5),
        _feature('outside', -100.0, 10.0)]}
    out = F.filter_features_to_bbox(fc, BBOX)
    assert [f['properties']['name'] for f in out['features']] == ['inside']


def test_bbox_filter_polygon_overlap():
    poly = {'type': 'Feature',
            'geometry': {'type': 'Polygon', 'coordinates': [[
                [-123.05, 38.45], [-122.95, 38.45],
                [-122.95, 38.55], [-123.05, 38.55], [-123.05, 38.45]]]},
            'properties': {'name': 'overlapping'}}
    far = {'type': 'Feature',
           'geometry': {'type': 'Polygon', 'coordinates': [[
               [0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
           'properties': {'name': 'far'}}
    out = F.filter_features_to_bbox({'type': 'FeatureCollection', 'features': [poly, far]}, BBOX)
    assert [f['properties']['name'] for f in out['features']] == ['overlapping']


# --------------------------------------------------------------------------- #
# Full round-trip: source publishes -> consumer pulls (handoff §5)
# --------------------------------------------------------------------------- #

# Source layers: water_tanks (shareable, no mask), gates (masked), culverts (not shareable).
SOURCE_LAYERS_RAW = {
    'water_tanks': {'type': 'FeatureCollection', 'features': [
        _feature('Tank A', -123.0, 38.5, capacity=5000)]},
    'gates': {'type': 'FeatureCollection', 'features': [
        _feature('Gate 1', -123.0, 38.5, gate_combination='1234'),
        _feature('Gate 2', -100.0, 10.0, gate_combination='9999')]},  # one out of bbox
    'culverts': {'type': 'FeatureCollection', 'features': [
        _feature('Culvert 1', -123.0, 38.5, diameter=24)]},
}

# Shareable layer configs (post-eddy world: gates__shared is the masked sibling).
SHAREABLE_CONFIG = [
    {'name': 'water_tanks',
     'shareable': {'enabled': True, 'title': 'Water tanks', 'license': 'CC-BY-4.0'}},
    {'name': 'gates__shared', 'hidden': True,
     'shareable': {'enabled': True, 'title': 'Gates', 'license': 'CC-BY-4.0'}},
    {'name': 'culverts'},  # not shareable
]

BASE = 'http://src/'
CATALOG_BASE = BASE + 'outlets/stac/'
DATA_BASE = BASE + 'layers/'
CATALOG_URL = CATALOG_BASE + 'catalog.json'


def _publish_source(served: Path):
    """Simulate the source build: mask_properties eddy -> stac outlet.

    Writes the published (masked) layer GeoJSON and the static STAC catalog into
    `served`, laid out so URLs under BASE map to files by stripping the prefix.
    Returns the list of published layer GeoJSON for assertion convenience.
    """
    # eddy: gates -> gates__shared with gate_combination withheld
    published = dict(SOURCE_LAYERS_RAW)
    published['gates__shared'] = F.mask_features(
        SOURCE_LAYERS_RAW['gates'], block=['gate_combination'])

    # Write only the published layers referenced by the catalog
    for name in ('water_tanks', 'gates__shared'):
        layer_dir = served / 'layers' / name
        layer_dir.mkdir(parents=True, exist_ok=True)
        (layer_dir / f'{name}.geojson').write_text(json.dumps(published[name]))

    # stac outlet: build + write catalog and collections
    shareable = F.iter_shareable_layers(SHAREABLE_CONFIG)
    catalog, collections = F.build_stac_catalog(
        atlas_id='kennedy', atlas_description='Shareable layers',
        shareable_layers=shareable, bbox=BBOX,
        catalog_base_url=CATALOG_BASE, data_base_url=DATA_BASE)

    stac_dir = served / 'outlets' / 'stac'
    stac_dir.mkdir(parents=True, exist_ok=True)
    (stac_dir / 'catalog.json').write_text(json.dumps(catalog))
    for name, collection in collections.items():
        (stac_dir / name).mkdir(parents=True, exist_ok=True)
        (stac_dir / name / 'collection.json').write_text(json.dumps(collection))
    return catalog


def _consume(served: Path, collection_id, use_bbox=False):
    """Simulate federation_inlet: resolve collection, fetch data, optional bbox.

    Returns (features, provenance) just like the inlet would write.
    """
    def fetch(url):
        rel = url[len(BASE):]  # URLs all live under BASE in this test
        return json.loads((served / rel).read_text())

    catalog = fetch(CATALOG_URL)
    child_href = F.resolve_collection_href(catalog, collection_id)
    collection_url = urljoin(CATALOG_URL, child_href)
    collection = fetch(collection_url)
    data_url = urljoin(collection_url, F.data_href_from_collection(collection))
    fc = fetch(data_url)

    if use_bbox:
        fc = F.filter_features_to_bbox(fc, BBOX)

    provenance = F.build_provenance(
        CATALOG_URL, collection_id,
        F.source_version_from_collection(collection))
    return fc['features'], provenance


def test_catalog_lists_only_shareable_layers(tmp_path):
    catalog = _publish_source(tmp_path)
    children = [l['href'] for l in catalog['links'] if l['rel'] == 'child']
    assert any('water_tanks' in h for h in children)
    assert any('gates__shared' in h for h in children)
    assert not any('culvert' in h for h in children)


def test_consumer_pulls_full_props_for_unmasked_layer(tmp_path):
    _publish_source(tmp_path)
    features, prov = _consume(tmp_path, 'water_tanks')
    assert len(features) == 1
    assert features[0]['properties']['capacity'] == 5000
    assert prov['source_catalog'] == CATALOG_URL
    assert prov['collection_id'] == 'water_tanks'


def test_consumer_never_sees_masked_property(tmp_path):
    _publish_source(tmp_path)
    features, _ = _consume(tmp_path, 'gates__shared')
    assert features, "expected gate features"
    for f in features:
        assert 'gate_combination' not in f['properties']
        assert f['properties']['name']  # location/identity intact


def test_culverts_not_federatable(tmp_path):
    _publish_source(tmp_path)
    with pytest.raises(ValueError):
        _consume(tmp_path, 'culverts')


def test_bbox_filter_in_roundtrip(tmp_path):
    _publish_source(tmp_path)
    all_feats, _ = _consume(tmp_path, 'gates__shared', use_bbox=False)
    boxed, _ = _consume(tmp_path, 'gates__shared', use_bbox=True)
    assert len(all_feats) == 2
    assert [f['properties']['name'] for f in boxed] == ['Gate 1']


def test_provenance_folds_into_attribution(tmp_path):
    _publish_source(tmp_path)
    _, prov = _consume(tmp_path, 'water_tanks')
    attribution = F.fold_provenance_into_attribution(
        {'title': 'Federated source: kennedy_water_tanks'}, prov)
    assert attribution['url'] == CATALOG_URL
    assert 'Federated from' in attribution['metadata']
