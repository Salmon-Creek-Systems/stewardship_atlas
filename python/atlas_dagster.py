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

Dependencies are ordering-only (`deps=`), never data-passing (`ins=`): all
assets communicate through the dataswale filesystem, so Dagster must not
require its own stored outputs to exist before running a downstream asset.
This keeps direct materialization (notebooks, webapp) and Dagster runs
interchangeable — disk is the single source of truth.

Known limitation: s3_geojson inlets write to layers/{asset_name}/ rather than
layers/{out_layer}/ (pre-existing config inconsistency). The dependency graph
will reflect the out_layer name, which may not match the actual data path.

Usage:
    cd python
    SWALES_ROOT=/root/swales_dev dagster dev -f atlas_definitions.py

    # or in-process, from a notebook / the webapp:
    refresh_layer(config, 'roads')                    # update + cascade
    refresh_layer(config, 'roads', mode='rebuild')    # fresh base from inlets
    refresh_layer(config, 'roads', cascade=False)     # layer only, no outlets
"""
import os
from collections import defaultdict
from typing import Dict, List, Optional

from dagster import (
    AssetKey,
    AssetSelection,
    Config,
    DagsterInstance,
    Definitions,
    asset,
    materialize,
)

import atlas as atlas_module
import dataswale_geojson
import deltas_geojson
import raster_inlets as raster_inlets_module
import vector_inlets as vector_inlets_module

_VECTOR_FETCH_TYPES = frozenset(vector_inlets_module.asset_methods)
_RASTER_FETCH_TYPES = frozenset(raster_inlets_module.asset_methods)

# Vector geometry types that can carry deltas (see shared_layers_config.json
# for the vocabulary; 'raster' and 'document' layers are not delta-fed).
_DELTA_GEOMETRY_TYPES = frozenset({'point', 'linestring', 'polygon'})


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


def _find_layer_producer(config: Dict, layer_name: str) -> Optional[str]:
    for name, entry in config['assets'].items():
        if _get_out_layer(entry) == layer_name:
            return name
    return None


def _make_materializer_asset(atlas_slug: str, asset_name: str, config: Dict, deps: List[AssetKey]):
    @asset(
        key=AssetKey([atlas_slug, asset_name]),
        deps=deps,
        group_name=atlas_slug,
    )
    def _fn(context):
        atlas_module.materialize(config, asset_name)

    return _fn


class LayerRefreshConfig(Config):
    """Per-run refresh mode (issue #131, C5: runtime parameter, not layer config).

    update  — apply pending deltas on top of the current layer file (cheap).
    rebuild — apply pending deltas onto an emptied layer file; pair with
              re-running the direct inlets for a fresh base (refresh_layer
              handles that selection). Archived work/ deltas are NOT replayed.
    """
    mode: str = "update"


def _make_layer_refresh_asset(atlas_slug: str, layer_name: str, atlas_config: Dict, deps: List[AssetKey]):
    @asset(
        key=AssetKey([atlas_slug, f"layer_{layer_name}"]),
        deps=deps,
        group_name=atlas_slug,
        description=f"Apply accumulated deltas to {layer_name}",
    )
    def _fn(context, config: LayerRefreshConfig):
        if config.mode == "rebuild":
            dataswale_geojson.refresh_vector_layer(
                atlas_config, layer_name, deltas_geojson.apply_deltas_overwrite)
        elif config.mode == "update":
            dataswale_geojson.refresh_vector_layer(atlas_config, layer_name)
        else:
            raise ValueError(f"Unknown refresh mode: {config.mode!r}")

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

    # Manual-only vector layers (no producing asset at all) also get a
    # layer_ asset: they are pure delta consumers (webedit, photo ingest)
    # and refresh is the only thing that materializes them.
    for layer_def in config.get('dataswale', {}).get('layers', []):
        layer_name = layer_def.get('name')
        if not layer_name or layer_name in by_layer:
            continue
        if layer_def.get('geometry_type') not in _DELTA_GEOMETRY_TYPES:
            continue
        if _find_layer_producer(config, layer_name):
            continue  # eddy- or raster-produced: that asset owns the layer
        by_layer[layer_name] = []

    # Inlet assets — no upstream Dagster dependencies.
    for asset_name, entry in assets_cfg.items():
        if entry.get('type') != 'inlet':
            continue
        result.append(_make_materializer_asset(atlas_slug, asset_name, config, []))

    # Synthesized layer_ assets — fan-in from all vector inlets producing that layer.
    for layer_name, inlet_names in by_layer.items():
        deps = [AssetKey([atlas_slug, name]) for name in inlet_names]
        result.append(_make_layer_refresh_asset(atlas_slug, layer_name, config, deps))

    # Eddy and outlet assets.
    # Build deps incrementally and guard against cycles with a reachability check.
    # Cycles arise from write-back eddies (e.g. biochar_simulation writes to 'photos',
    # which processing_sites_h3 also reads as its primary source from the delta system).
    upstream: Dict[str, set] = {}  # asset_name -> set of direct upstream asset names

    def _reachable(start: str, target: str) -> bool:
        """True if target is reachable from start through current upstream edges."""
        visited: set = set()
        stack = list(upstream.get(start, set()))
        while stack:
            node = stack.pop()
            if node == target:
                return True
            if node not in visited:
                visited.add(node)
                stack.extend(upstream.get(node, set()))
        return False

    for asset_name, entry in assets_cfg.items():
        if entry.get('type') == 'inlet':
            upstream[asset_name] = set()
            continue

        deps = []
        asset_upstream: set = set()
        for layer in _get_in_layers(entry):
            if layer in by_layer:
                # Vector layer: depend on the synthesized layer_ asset.
                key = f"layer_{layer}"
                deps.append(AssetKey([atlas_slug, key]))
                asset_upstream.add(key)
            else:
                # Raster or eddy-produced layer: depend directly on the producing asset.
                producer = _find_layer_producer(config, layer)
                # Skip self-deps: in-place eddies (e.g. ssurgo_enrich) have in_layer == out_layer.
                if not producer or producer == asset_name:
                    continue
                # Skip if this edge would create a cycle (write-back pattern, e.g.
                # biochar_simulation writes to 'photos' which processing_sites_h3 reads).
                if _reachable(producer, asset_name):
                    import logging
                    logging.getLogger(__name__).warning(
                        f"{atlas_slug}/{asset_name}: skipping dep on {producer} "
                        f"(would create cycle via layer '{layer}')"
                    )
                    continue
                deps.append(AssetKey([atlas_slug, producer]))
                asset_upstream.add(producer)

        upstream[asset_name] = asset_upstream
        result.append(_make_materializer_asset(atlas_slug, asset_name, config, deps))

    return result


def build_definitions(configs: List[Dict]) -> Definitions:
    """Build a single Dagster Definitions object covering all atlases."""
    all_assets = []
    for config in configs:
        all_assets.extend(build_atlas_assets(config))
    return Definitions(assets=all_assets)


def refresh_layer(config: Dict, layer_name: str, mode: str = "update", cascade: bool = True):
    """
    Refresh one layer through the Dagster asset graph (issue #131, T2).

    mode="update"  — apply pending deltas onto the current layer (cheap).
    mode="rebuild" — re-run the layer's direct inlets for a fresh base, then
                     apply pending deltas onto an emptied layer. Archived
                     work/ deltas are not replayed. Manual-only layers (no
                     inlet) have nothing to rebuild: mode is coerced to
                     "update" rather than allowed to empty the layer.
    cascade=True   — also re-derive everything downstream (eddies, outlets).

    Blocks until the run completes; returns the Dagster ExecuteInProcessResult.
    Uses the persistent DagsterInstance when DAGSTER_HOME is set (so runs
    appear in the Dagster UI), an ephemeral one otherwise.
    """
    if mode not in ("update", "rebuild"):
        raise ValueError(f"Unknown refresh mode: {mode!r}")

    atlas_slug = config['name']
    assets = build_atlas_assets(config)
    layer_key = AssetKey([atlas_slug, f"layer_{layer_name}"])

    layer_asset = next((a for a in assets if layer_key in a.keys), None)
    if layer_asset is None:
        raise ValueError(
            f"No refreshable layer asset for '{layer_name}' in atlas '{atlas_slug}' "
            f"(eddy- and raster-produced layers are refreshed by materializing their producer)")

    # Manual-only layer: no inlet upstream, nothing to rebuild (C5).
    has_inlets = bool(layer_asset.asset_deps.get(layer_key))
    if mode == "rebuild" and not has_inlets:
        mode = "update"

    selection = AssetSelection.keys(layer_key)
    if cascade:
        selection = selection.downstream()  # includes the layer asset itself
    run_config = None
    if mode == "rebuild":
        # Direct inlets only (depth=1) — never the whole ancestry (C6).
        selection = selection | AssetSelection.keys(layer_key).upstream(depth=1)
        run_config = {
            "ops": {f"{atlas_slug}__layer_{layer_name}": {"config": {"mode": "rebuild"}}}
        }

    instance = DagsterInstance.get() if os.environ.get('DAGSTER_HOME') else None
    return materialize(
        assets,
        selection=selection,
        run_config=run_config,
        instance=instance,
    )


def materialize_asset(config: Dict, asset_name: str):
    """
    Materialize one asset through the Dagster asset graph (issue #131, T6).

    No cascade, no upstream: exactly this asset, against whatever is on
    disk. This is the explicit "build this outlet now" operation (e.g.
    rebuilding a runbook or gazetteer before publish).

    Blocks until the run completes; returns the Dagster result. Uses the
    persistent DagsterInstance when DAGSTER_HOME is set.
    """
    atlas_slug = config['name']
    assets = build_atlas_assets(config)
    key = AssetKey([atlas_slug, asset_name])
    if not any(key in a.keys for a in assets):
        raise ValueError(f"No asset '{asset_name}' in atlas '{atlas_slug}'")

    instance = DagsterInstance.get() if os.environ.get('DAGSTER_HOME') else None
    return materialize(assets, selection=[key], instance=instance)
