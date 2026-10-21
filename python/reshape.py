"""
Reshape deltas: replace the geometry of existing features, keyed by atlas_id.

Standard library only, so it is testable on a bare checkout; deltas_geojson
applies it to the layer file.

A reshape changes geometry and nothing else. Properties stay as they were, and
the geometry type must not change — a reshaped point is still a point. Matching
is by atlas_id, which refresh_vector_layer stamps on every feature and the layer
file then carries: an update applies deltas to the existing layer, and a rebuild
never replays archived deltas, so an id cannot drift out from under a reshape.
"""
import logging

logger = logging.getLogger(__name__)


def apply_reshape(layer_features, delta_features, shape_fn=None):
    """Replace geometries in layer_features with those in delta_features.

    layer_features: the layer's features, modified in place.
    delta_features: features carrying the new geometry and properties.atlas_id.
    shape_fn: optional feature -> feature applied to each reshaped feature, so a
              layer's polygon_shape holds for reshaped polygons too.

    Returns (reshaped_ids, skipped) where skipped is a list of (atlas_id, reason).
    """
    by_id = {}
    for i, feature in enumerate(layer_features):
        fid = (feature.get('properties') or {}).get('atlas_id')
        if fid:
            by_id[fid] = i

    reshaped, skipped = [], []
    for delta in delta_features:
        fid = (delta.get('properties') or {}).get('atlas_id')
        new_geom = delta.get('geometry')
        if not fid:
            skipped.append((None, 'no atlas_id'))
            continue
        if fid not in by_id:
            skipped.append((fid, 'no feature with this atlas_id'))
            continue
        if not new_geom:
            skipped.append((fid, 'no geometry'))
            continue
        target = layer_features[by_id[fid]]
        old_type = (target.get('geometry') or {}).get('type')
        if old_type != new_geom.get('type'):
            skipped.append((fid, f"geometry type {new_geom.get('type')} != {old_type}"))
            continue
        target['geometry'] = new_geom
        if shape_fn:
            layer_features[by_id[fid]] = shape_fn(target)
        reshaped.append(fid)

    for fid, reason in skipped:
        logger.warning(f"reshape skipped {fid}: {reason}")
    return reshaped, skipped
