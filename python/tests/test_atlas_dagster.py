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
    """Minimal atlas config exercising inlet fan-in, eddy, and outlet."""
    return {
        'name': 'testatlas',
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
