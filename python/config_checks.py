"""Checks every active atlas's source config must pass.

One function, used by ``tests/test_json_configs.py`` for the repo copies and by
``scripts/atlas_config.py push`` before a config is written to S3 — so the
rules cannot drift between "what the tests check" and "what can be deployed".

Stdlib only: it runs in the local test environment and in the Lambda image.
"""
import json
from pathlib import Path

# Outlets whose page fetches its layers in the browser, so every layer they
# list must be in the public mirror (atlas_store.CLIENT_FETCHED_OUTLETS).
PUBLIC_MAP_OUTLETS = ('webmap', '3dview')


def load_shared(config_dir) -> dict:
    """The shared templates the checks resolve `layer_def`/`config_def` against."""
    config_dir = Path(config_dir)
    with open(config_dir / 'shared_layers_config.json') as f:
        layers = json.load(f)
    with open(config_dir / 'shared_outlets_config.json') as f:
        outlets = json.load(f)
    return {'layers': layers, 'outlets': outlets}


def layer_access(layer: dict, shared_layers: dict):
    """A layer's declared access, from itself or its shared `layer_def`; None if neither."""
    if layer.get('access') is not None:
        return layer['access']
    if layer.get('layer_def'):
        return (shared_layers.get(layer['layer_def']) or {}).get('access')
    return None


def outlet_access(asset: dict, shared_outlets: dict):
    """An outlet's declared access, from itself or its shared `config_def`."""
    if asset.get('access') is not None:
        return asset['access']
    if asset.get('config_def'):
        return (shared_outlets.get(asset['config_def']) or {}).get('access')
    return None


def unpublishable_map_layers(props: dict, shared_layers: dict) -> list:
    """`outlet:layer=access` for every layer a public map loads that is not
    explicitly declared public.

    A missing `access` reads as public in atlas.py but as internal in the
    publish guardrail (#175), so the map 403s on S3 while working on the box.
    Declaring it is the only state both agree on.
    """
    layers = props.get('layers') or {}
    allow = (props.get('cloud') or {}).get('outlets')
    found = []
    for outlet in PUBLIC_MAP_OUTLETS:
        if allow is not None and outlet not in allow:
            continue
        asset = (props.get('assets') or {}).get(outlet) or {}
        if 'public' not in (asset.get('access') or ['public']):
            continue
        for name in asset.get('in_layers') or []:
            layer = layers.get(name)
            if layer is None:
                continue
            access = layer_access(layer, shared_layers)
            if not access or 'public' not in access:
                found.append(f"{outlet}:{name}={access}")
    return found


def atlas_config_problems(props: dict, shared: dict) -> list:
    """Every reason this atlas's source properties are not deployable; [] if none.

    `props` is ``features[0].properties`` of ``{atlas}.geojson``.
    """
    problems = []
    for key in ('layers_path', 'assets_path'):
        if key in props:
            problems.append(f"{key} present: active atlases are single-file")
    layers = props.get('layers')
    assets = props.get('assets')
    if not isinstance(layers, dict) or not isinstance(assets, dict):
        return problems + ["layers and assets must be inline dicts"]

    if 'console' not in assets:
        problems.append("no console asset")
    if 'html' in assets:
        problems.append("the retired html console is still an asset")
    # versioned_outlets filters the publish copy; a stale ["html", "webmap"]
    # would leave the console out (#131 C9).
    if props.get('versioned_outlets', []) != []:
        problems.append(f"versioned_outlets must be [], is {props.get('versioned_outlets')}")

    for name, asset in assets.items():
        if asset.get('config_def') != 'webmap':
            continue
        if not asset.get('show_user_location'):
            problems.append(f"{name} lacks show_user_location")
        for lname in asset.get('in_layers', []):
            if lname not in layers and lname not in shared['layers']:
                problems.append(f"{name} in_layers references undefined '{lname}'")

    outlets = (props.get('cloud') or {}).get('outlets')
    if outlets is None:
        problems.append("no cloud.outlets allowlist")
    else:
        for o in outlets:
            if o not in assets:
                problems.append(f"cloud.outlets names '{o}', which is not an asset")
            elif outlet_access(assets[o], shared['outlets']) is None:
                problems.append(f"allowlisted outlet '{o}' declares no access (#175)")

    problems += [f"public map loads a non-public layer: {x}"
                 for x in unpublishable_map_layers(props, shared['layers'])]
    return problems
