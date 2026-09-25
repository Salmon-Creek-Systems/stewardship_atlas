"""Pure planners for whole-layer operations on an atlas's config.

Standard library only, so the rules can be tested on a bare checkout without
stubbing atlas.py's heavy imports into sys.modules (#153).
"""

# Asset fields that list layers. Deleting a layer removes it from each.
LAYER_LIST_FIELDS = ('in_layers', 'hidden_layers', 'grid_layers', 'h3_layers', 'layers')

# List fields an asset cannot work with empty: an eddy or outlet left with no
# inputs is broken, not smaller. hidden_layers/grid_layers may be empty.
REQUIRED_LIST_FIELDS = ('in_layers', 'layers', 'h3_layers')

# Asset fields that name exactly one layer another asset depends on.
LAYER_REF_FIELDS = ('regions_layer', 'lrs_zones_layer')

# Top-level atlas properties that name a layer.
ATLAS_LAYER_FIELDS = ('email_photo_default_layer',)


def _resolved(asset):
    """An asset as it runs: its shared template merged with its overrides
    (atlas.create_config puts that in asset['config']), else the asset itself."""
    return asset.get('config', asset) if isinstance(asset, dict) else {}


def is_producer(asset, layer):
    """Whether the asset writes this layer: an inlet or eddy whose output it
    is, or an in-place eddy/outlet that names only it (e.g. tiff_to_cog)."""
    r = _resolved(asset)
    out_layer = r.get('out_layer')
    return out_layer == layer or (out_layer is None and r.get('in_layer') == layer)


def plan_layer_delete(props, assets, layer):
    """Decide what deleting `layer` changes, without changing anything.

    props:  the atlas's top-level properties (for email_photo_default_layer
            and the source `layers` dict)
    assets: the RESOLVED assets (config['assets']), so a field set only by a
            shared template is still seen

    Returns a dict:
      remove_assets: asset keys that produce the layer, removed with it
      list_edits:    [(asset_key, field)] lists the name is removed from
      blockers:      reasons the delete must not happen; non-empty means refuse
    """
    blockers = []
    if layer not in (props.get('layers') or {}):
        blockers.append(f"No layer '{layer}' in this atlas's layers.")

    remove_assets = sorted(k for k, a in assets.items() if is_producer(a, layer))

    list_edits = []
    for key, asset in sorted(assets.items()):
        if key in remove_assets:
            continue
        r = _resolved(asset)
        for field in LAYER_LIST_FIELDS:
            values = r.get(field)
            if isinstance(values, list) and layer in values:
                if field in REQUIRED_LIST_FIELDS and not [v for v in values if v != layer]:
                    blockers.append(f"Asset '{key}' uses only this layer ({field}); "
                                    f"it would be left with none.")
                else:
                    list_edits.append((key, field))
        if r.get('in_layer') == layer:
            blockers.append(f"Asset '{key}' reads this layer (in_layer) to build "
                            f"'{r.get('out_layer')}'.")
        for field in LAYER_REF_FIELDS:
            if r.get(field) == layer:
                blockers.append(f"Asset '{key}' uses this layer as its {field}.")

    for field in ATLAS_LAYER_FIELDS:
        if props.get(field) == layer:
            blockers.append(f"The atlas's {field} is this layer.")

    return {'layer': layer, 'remove_assets': remove_assets,
            'list_edits': list_edits, 'blockers': blockers}


def apply_layer_delete(props, resolved_assets, plan):
    """Apply a blocker-free plan to the SOURCE properties, in place.

    Lists are edited where the source asset sets them; a list that only the
    shared template sets is written as an override without the layer, since
    overrides replace a template key wholesale.
    """
    if plan['blockers']:
        raise ValueError('; '.join(plan['blockers']))
    layer = plan['layer']
    source_assets = props['assets']
    props['layers'].pop(layer, None)
    for key in plan['remove_assets']:
        source_assets.pop(key, None)
    for key, field in plan['list_edits']:
        source = source_assets.get(key)
        if source is None:
            continue
        if isinstance(source.get(field), list):
            source[field] = [v for v in source[field] if v != layer]
        else:
            template_list = _resolved(resolved_assets[key]).get(field) or []
            source[field] = [v for v in template_list if v != layer]
    return props
