"""What a job reads from staging, so a session can hydrate only that (Phase 4c).

On the box a session hydrates the whole atlas into a persistent workspace, and
only changed files download. On Lambda there is no persistent disk, so every
cold start would be a full copy (westport is ~800 MB). Declaring a job's inputs
lets the session download just those.

**Declaring inputs is an optimization, never a correctness rule.** Anything not
covered here returns ``None``, which means "hydrate everything" — slower, never
wrong. A declared set that misses a file is wrong, which is what the
differential check in ``scripts/check_declared_inputs.py`` exists to catch: it
runs each asset in a full and in a declared workspace and compares the outputs.

Inputs are staging-relative path *prefixes*. ``wanted(prefixes)`` turns them
into the predicate ``atlas_session.open_session(wanted=...)`` takes.

Stdlib only.
"""

# Always read: the config, and the source it is rebuilt from.
ALWAYS = ('atlas_config.json', 'atlas.geojson')

# Outlets and eddies that read (nearly) everything. Full hydrate.
FULL = frozenset({
    'sqldb',          # atlas.db holds every layer
    'overlook',       # every public layer, every outlet dir, delta provenance
    'stac',
    'spreadsheet_export',
    'notebook', 'jupyter_notebook',
})

# Reads an asset makes beyond the layers it names, found by reading the code
# (and, from now on, by the differential check).
EXTRA = {
    # outlet_webmap: the Regions dropdown reads layers/regions/ for every map.
    'webmap': ('layers/regions/',),
    'webedit': ('layers/regions/',),
    # make_swale_html: the console links the webmap only if it was built, the
    # SQL page introspects the sqldb outlet's atlas.db, and every layer page's
    # attribution.html reads the attributions layer.
    'console': ('outlets/webmap/index.html', 'outlets/sqldb/atlas.db',
                'layers/attributions/'),
}

# Layer-list fields an asset may name, top level or under 'config'.
_LAYER_FIELDS = ('in_layer', 'in_layers', 'out_layer', 'layers', 'regions_layer',
                 'grid_layers', 'visible_layers')


def _field(asset: dict, key: str):
    if key in asset:
        return asset[key]
    return (asset.get('config') or {}).get(key)


def _named_layers(asset: dict) -> list:
    names = []
    for key in _LAYER_FIELDS:
        value = _field(asset, key)
        if isinstance(value, str):
            names.append(value)
        elif isinstance(value, list):
            names.extend(v for v in value if isinstance(v, str))
    roles = _field(asset, 'roles')          # game_kit: {role: [layer, ...]}
    if isinstance(roles, dict):
        for value in roles.values():
            names.extend(value if isinstance(value, list) else [value])
    return names


def _kind(asset: dict) -> str:
    return asset.get('type') or _field(asset, 'asset_type') or ''


def asset_inputs(config: dict, asset_name: str):
    """Staging prefixes materializing ``asset_name`` reads, or None for everything."""
    asset = (config.get('assets') or {}).get(asset_name)
    if asset is None:
        return None
    fetch_type = _field(asset, 'fetch_type') or ''
    if asset_name in FULL or fetch_type in FULL or asset.get('config_def') in FULL:
        return None

    prefixes = list(ALWAYS)
    for layer in _named_layers(asset):
        prefixes.append(f'layers/{layer}/')
    kind = _kind(asset)
    if kind == 'inlet':
        # An inlet writes deltas; the unique-name check globs the pending ones.
        out = _field(asset, 'out_layer') or asset_name
        prefixes.append(f'deltas/{out}/')
    if kind == 'outlet':
        # Its own previous output: state such as slides' gslides.json.
        prefixes.append(f'outlets/{asset_name}/')
    for key in (asset_name, fetch_type, asset.get('config_def')):
        prefixes.extend(EXTRA.get(key, ()))
    return sorted(set(prefixes))


def publish_inputs():
    """Publish promotes the staging catalog in S3; it needs only the config."""
    return list(ALWAYS)


def layer_refresh_inputs(layer_name: str):
    """A delta apply: the layer, its pending deltas, and the config."""
    return sorted(set(ALWAYS) | {f'layers/{layer_name}/', f'deltas/{layer_name}/'})


def wanted(prefixes):
    """The ``open_session(wanted=...)`` predicate for a prefix list; None stays None."""
    if prefixes is None:
        return None
    exact = {p for p in prefixes if not p.endswith('/')}
    dirs = tuple(p for p in prefixes if p.endswith('/'))
    return lambda rel: rel in exact or rel.startswith(dirs)
