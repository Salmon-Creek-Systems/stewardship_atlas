"""
Dagster asset orchestration for Stewardship Atlas.

Dynamically builds a Dagster asset graph from a resolved atlas_config.json.

Two-tier design:
  inlets  →  (synthesized) layer_* assets  →  eddies / outlets

The synthesized layer_* assets call refresh_vector_layer(), which applies all
accumulated deltas for that layer in a single batch. This preserves the
multi-inlet fan-in semantics: all inlets feeding a layer complete before it
is refreshed, avoiding intermediate-state races.

Raster inlets write their layer directly (no delta accumulation), so they
feed downstream eddies/outlets without an intermediate layer_ asset.

Known limitation: s3_geojson inlets write to layers/{asset_name}/ rather than
layers/{out_layer}/ (pre-existing config inconsistency). The dependency graph
will reflect the out_layer name, which may not match the actual data path.

Usage:
    cd python
    SWALES_ROOT=/root/swales_dev dagster dev -f atlas_definitions.py
"""
from collections import defaultdict
from typing import Dict, List, Optional

from dagster import AssetIn, AssetKey, Definitions, asset

import atlas as atlas_module
import dataswale_geojson
import raster_inlets as raster_inlets_module
import vector_inlets as vector_inlets_module

_VECTOR_FETCH_TYPES = frozenset(vector_inlets_module.asset_methods)
_RASTER_FETCH_TYPES = frozenset(raster_inlets_module.asset_methods)


def _cfg(asset_entry: Dict) -> Dict:
    return asset_entry.get('config', asset_entry)


def _get_in_layers(asset_entry: Dict) -> List[str]:
    c = _cfg(asset_entry)
    layers = []
    if 'in_layer' in c:
        layers.append(c['in_layer'])
    if 'in_layers' in c:
        layers.extend(c['in_layers'])
    return layers


def _get_out_layer(asset_entry: Dict) -> Optional[str]:
    return _cfg(asset_entry).get('out_layer')


def _sanitize(name: str) -> str:
    return name.replace('-', '_').replace('.', '_')


def _find_layer_producer(config: Dict, layer_name: str) -> Optional[str]:
    for name, entry in config['assets'].items():
        if _get_out_layer(entry) == layer_name:
            return name
    return None


def _make_materializer_asset(atlas_slug: str, asset_name: str, config: Dict, ins: Dict):
    @asset(
        key=AssetKey([atlas_slug, asset_name]),
        ins=ins,
        group_name=atlas_slug,
    )
    def _fn(context, **kwargs):
        atlas_module.materialize(config, asset_name)

    return _fn


def _make_layer_refresh_asset(atlas_slug: str, layer_name: str, config: Dict, ins: Dict):
    @asset(
        key=AssetKey([atlas_slug, f"layer_{layer_name}"]),
        ins=ins,
        group_name=atlas_slug,
        description=f"Apply accumulated deltas to {layer_name}",
    )
    def _fn(context, **kwargs):
        dataswale_geojson.refresh_vector_layer(config, layer_name)

    return _fn


def build_atlas_assets(config: Dict) -> List:
    """
    Build Dagster asset definitions for one atlas from its resolved atlas_config.json.
    """
    atlas_slug = config['name']
    assets_cfg = config['assets']
    result = []

    # Which layers are produced by vector inlets — these get a synthesized layer_ asset.
    # Fan-in: multiple inlets may feed the same layer (e.g. OSM + Overture → roads).
    by_layer: Dict[str, List[str]] = defaultdict(list)
    for asset_name, entry in assets_cfg.items():
        if entry.get('type') == 'inlet':
            fetch_type = _cfg(entry).get('fetch_type', '')
            out_layer = _get_out_layer(entry)
            if out_layer and fetch_type in _VECTOR_FETCH_TYPES:
                by_layer[out_layer].append(asset_name)

    # Inlet assets — no upstream Dagster dependencies.
    for asset_name, entry in assets_cfg.items():
        if entry.get('type') != 'inlet':
            continue
        result.append(_make_materializer_asset(atlas_slug, asset_name, config, {}))

    # Synthesized layer_ assets — fan-in from all vector inlets producing that layer.
    for layer_name, inlet_names in by_layer.items():
        ins = {
            _sanitize(name): AssetIn(key=AssetKey([atlas_slug, name]))
            for name in inlet_names
        }
        result.append(_make_layer_refresh_asset(atlas_slug, layer_name, config, ins))

    # Eddy and outlet assets.
    for asset_name, entry in assets_cfg.items():
        if entry.get('type') == 'inlet':
            continue

        ins = {}
        for layer in _get_in_layers(entry):
            if layer in by_layer:
                # Vector layer: depend on the synthesized layer_ asset.
                key = f"layer_{layer}"
                ins[_sanitize(key)] = AssetIn(key=AssetKey([atlas_slug, key]))
            else:
                # Raster or eddy-produced layer: depend directly on the producing asset.
                producer = _find_layer_producer(config, layer)
                if producer:
                    ins[_sanitize(producer)] = AssetIn(key=AssetKey([atlas_slug, producer]))

        result.append(_make_materializer_asset(atlas_slug, asset_name, config, ins))

    return result


def build_definitions(configs: List[Dict]) -> Definitions:
    """Build a single Dagster Definitions object covering all atlases."""
    all_assets = []
    for config in configs:
        all_assets.extend(build_atlas_assets(config))
    return Definitions(assets=all_assets)
