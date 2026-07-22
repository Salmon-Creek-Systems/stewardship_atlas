"""
Tests for the Dagster asset graph builder (atlas_dagster.py).

Requires dagster, which is a server-side dependency — these tests skip
locally when dagster isn't installed.

The core property under test: dependencies are ordering-only. Any single
asset must be materializable in isolation (fresh ephemeral instance, no
upstream ever materialized in Dagster), running against whatever data is
on disk. This is what keeps notebook/webapp materialization and Dagster
runs interchangeable.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

pytest.importorskip("dagster")

from dagster import AssetKey, materialize

import atlas_dagster


def make_config():
    """Minimal atlas config exercising inlet fan-in, eddy, outlet, and the
    layer taxonomy: inlet-fed (roads), eddy-produced (road_mileage),
    manual-only (culverts), raster (elevation)."""
    return {
        'name': 'testatlas',
        'dataswale': {
            'layers': [
                {'name': 'roads', 'geometry_type': 'linestring'},
                {'name': 'road_mileage', 'geometry_type': 'linestring'},
                {'name': 'culverts', 'geometry_type': 'point'},
                {'name': 'elevation', 'geometry_type': 'raster'},
            ],
        },
        'assets': {
            'roads_osm': {
                'type': 'inlet',
                'config': {'fetch_type': 'fetch_osm', 'out_layer': 'roads'},
            },
            'roads_overture': {
                'type': 'inlet',
                'config': {'fetch_type': 'overture_duckdb', 'out_layer': 'roads'},
            },
            'mileage': {
                'type': 'eddy',
                'config': {'in_layer': 'roads', 'out_layer': 'road_mileage'},
            },
            'webmap': {
                'type': 'outlet',
                'config': {'in_layers': ['roads', 'road_mileage']},
            },
        },
    }


def assets_by_key(assets):
    return {a.key: a for a in assets}


def test_graph_shape():
    assets = atlas_dagster.build_atlas_assets(make_config())
    by_key = assets_by_key(assets)

    layer_roads = AssetKey(['testatlas', 'layer_roads'])
    mileage = AssetKey(['testatlas', 'mileage'])
    webmap = AssetKey(['testatlas', 'webmap'])

    assert set(by_key) == {
        AssetKey(['testatlas', 'roads_osm']),
        AssetKey(['testatlas', 'roads_overture']),
        AssetKey(['testatlas', 'layer_culverts']),
        layer_roads, mileage, webmap,
    }

    # Inlet fan-in: both inlets feed the synthesized layer asset.
    assert by_key[layer_roads].asset_deps[layer_roads] == {
        AssetKey(['testatlas', 'roads_osm']),
        AssetKey(['testatlas', 'roads_overture']),
    }
    # Eddy depends on the layer asset, not the inlets.
    assert by_key[mileage].asset_deps[mileage] == {layer_roads}
    # Outlet depends on the layer asset and the eddy that produces road_mileage.
    assert by_key[webmap].asset_deps[webmap] == {layer_roads, mileage}

    # Manual-only layer: dep-less layer_ asset; eddy-produced road_mileage
    # and raster elevation get none (their producer owns them).
    culverts = AssetKey(['testatlas', 'layer_culverts'])
    assert by_key[culverts].asset_deps[culverts] == set()
    assert AssetKey(['testatlas', 'layer_road_mileage']) not in by_key
    assert AssetKey(['testatlas', 'layer_elevation']) not in by_key


def test_midgraph_asset_materializes_in_isolation(monkeypatch):
    """An eddy/outlet must run alone on a fresh instance — no upstream
    Dagster materializations, no IO-manager input loading."""
    calls = []
    monkeypatch.setattr(
        atlas_dagster.atlas_module, 'materialize',
        lambda config, name: calls.append(name),
    )

    config = make_config()
    assets = atlas_dagster.build_atlas_assets(config)
    result = materialize(
        assets,
        selection=[AssetKey(['testatlas', 'mileage'])],
    )

    assert result.success
    assert calls == ['mileage']


def test_layer_asset_materializes_in_isolation(monkeypatch):
    """The synthesized layer_ asset must run without its inlets having
    been materialized through Dagster."""
    refreshed = []
    monkeypatch.setattr(
        atlas_dagster.dataswale_geojson, 'refresh_vector_layer',
        lambda config, name: refreshed.append(name),
    )

    config = make_config()
    assets = atlas_dagster.build_atlas_assets(config)
    result = materialize(
        assets,
        selection=[AssetKey(['testatlas', 'layer_roads'])],
    )

    assert result.success
    assert refreshed == ['roads']


@pytest.fixture
def recorders(monkeypatch):
    """Patch the two work functions with recorders; keep the run ephemeral."""
    monkeypatch.delenv('DAGSTER_HOME', raising=False)
    materialized = []
    refreshed = []
    monkeypatch.setattr(
        atlas_dagster.atlas_module, 'materialize',
        lambda config, name: materialized.append(name),
    )
    monkeypatch.setattr(
        atlas_dagster.dataswale_geojson, 'refresh_vector_layer',
        lambda config, name, delta_queue_builder=None:
            refreshed.append((name, delta_queue_builder)),
    )
    return materialized, refreshed


def test_refresh_layer_update_cascades_downstream_only(recorders):
    materialized, refreshed = recorders
    result = atlas_dagster.refresh_layer(make_config(), 'roads')

    assert result.success
    # Default builder (pending deltas onto current layer), no inlets re-run.
    assert refreshed == [('roads', None)]
    assert set(materialized) == {'mileage', 'webmap'}


def test_refresh_layer_update_no_cascade(recorders):
    materialized, refreshed = recorders
    result = atlas_dagster.refresh_layer(make_config(), 'roads', cascade=False)

    assert result.success
    assert refreshed == [('roads', None)]
    assert materialized == []


def test_refresh_layer_rebuild_reruns_direct_inlets(recorders):
    materialized, refreshed = recorders
    result = atlas_dagster.refresh_layer(make_config(), 'roads', mode='rebuild')

    assert result.success
    # Fresh base from both direct inlets, overwrite builder, then cascade.
    assert set(materialized) == {'roads_osm', 'roads_overture', 'mileage', 'webmap'}
    assert refreshed == [('roads', atlas_dagster.deltas_geojson.apply_deltas_overwrite)]


def test_refresh_layer_rebuild_coerced_to_update_for_manual_layer(recorders):
    materialized, refreshed = recorders
    result = atlas_dagster.refresh_layer(make_config(), 'culverts', mode='rebuild')

    assert result.success
    # No inlet upstream: nothing to rebuild, must not empty the layer (C5).
    assert refreshed == [('culverts', None)]
    assert materialized == []


def test_refresh_layer_rejects_unknown_layer_and_mode(recorders):
    with pytest.raises(ValueError):
        atlas_dagster.refresh_layer(make_config(), 'road_mileage')
    with pytest.raises(ValueError):
        atlas_dagster.refresh_layer(make_config(), 'roads', mode='sideways')


def test_materialize_asset_runs_exactly_one_asset(recorders):
    materialized, refreshed = recorders
    result = atlas_dagster.materialize_asset(make_config(), 'webmap')

    assert result.success
    # No cascade, no upstream — exactly the requested asset.
    assert materialized == ['webmap']
    assert refreshed == []


def test_materialize_asset_rejects_unknown_asset(recorders):
    with pytest.raises(ValueError):
        atlas_dagster.materialize_asset(make_config(), 'nope')
