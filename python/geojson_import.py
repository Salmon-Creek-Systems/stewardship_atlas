"""Reading an uploaded GeoJSON into a new atlas — extent and layer routing.

`/create` accepts a GeoJSON file in place of a drawn rectangle. Every feature
contributes to the atlas extent; a feature carrying a `layer` property is also
routed into that layer, so one upload can define the atlas *and* seed its
regions.

Its own module rather than `utils.py` because `utils` imports gspread and
`webapp` imports fastapi, neither of which is installed on a bare checkout —
and this is the half worth testing. Nothing here imports anything but the
standard library.
"""

# A feature carrying this property is routed into that layer; one without it
# contributes to the atlas extent and nothing else.
LAYER_PROPERTY = 'layer'


def iter_coordinates(geometry):
    """Every (x, y) pair in a GeoJSON geometry, at any nesting depth."""
    if not isinstance(geometry, dict):
        return
    if geometry.get('type') == 'GeometryCollection':
        for member in geometry.get('geometries') or []:
            yield from iter_coordinates(member)
        return

    def walk(node):
        if isinstance(node, (list, tuple)):
            if node and isinstance(node[0], (int, float)) and len(node) >= 2:
                yield float(node[0]), float(node[1])
            else:
                for child in node:
                    yield from walk(child)

    yield from walk(geometry.get('coordinates'))


def as_features(obj):
    """A GeoJSON geometry, Feature or FeatureCollection as a list of Features."""
    if not isinstance(obj, dict):
        raise ValueError("not a GeoJSON object")
    kind = obj.get('type')
    if kind == 'FeatureCollection':
        return list(obj.get('features') or [])
    if kind == 'Feature':
        return [obj]
    if kind:
        return [{'type': 'Feature', 'geometry': obj, 'properties': {}}]
    raise ValueError("GeoJSON object has no 'type'")


def boundary_bbox(obj):
    """Envelope of everything in the upload, as an atlas bbox.

    `utils.geojson_to_bbox` assumes a four-corner ring and
    `utils.geojson_multipolygon_to_bbox` reads only the first ring of the first
    polygon; both are load-bearing for `regions_from_geojson`, so this is a new
    function rather than a widening of those.
    """
    xs, ys = [], []
    for feature in as_features(obj):
        for x, y in iter_coordinates(feature.get('geometry') or {}):
            xs.append(x)
            ys.append(y)
    if not xs:
        raise ValueError("GeoJSON carries no coordinates")
    if not (all(-180 <= x <= 180 for x in xs) and all(-90 <= y <= 90 for y in ys)):
        raise ValueError("coordinates are outside lon/lat range — is this projected?")
    return {'west': min(xs), 'east': max(xs), 'north': max(ys), 'south': min(ys)}


def split_features_by_layer(obj, known_layers):
    """Route uploaded features to layers by their `layer` property.

    Returns ``{'routed': {layer: [feature, ...]}, 'extent_only': int,
    'unknown': {name: count}}``. The routing property is stripped from the
    features that carry it, so it is not stored on every feature. Unknown layer
    names are reported rather than dropped: a typo in one property should not
    quietly cost a layer's worth of data.
    """
    known = set(known_layers or ())
    routed, unknown, extent_only = {}, {}, 0

    for feature in as_features(obj):
        properties = dict(feature.get('properties') or {})
        target = properties.pop(LAYER_PROPERTY, None)
        if target is None:
            extent_only += 1
            continue
        target = str(target)
        if target not in known:
            unknown[target] = unknown.get(target, 0) + 1
            continue
        routed.setdefault(target, []).append(
            {'type': 'Feature', 'geometry': feature.get('geometry'),
             'properties': properties})

    return {'routed': routed, 'extent_only': extent_only, 'unknown': unknown}


def summarize(obj, known_layers):
    """What a create would do with this upload — the /create-preview payload."""
    split = split_features_by_layer(obj, known_layers)
    return {
        'bbox': boundary_bbox(obj),
        'routed': {name: len(features) for name, features in split['routed'].items()},
        'extent_only': split['extent_only'],
        'unknown': split['unknown'],
        'total': len(as_features(obj)),
    }
