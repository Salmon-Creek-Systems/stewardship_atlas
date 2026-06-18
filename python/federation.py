"""Federation core logic — static STAC publishing and consumption.

Federation reuses the existing Dataswale/Inlet/Eddy/Outlet model:
  - a source atlas publishes shareable layers as a static STAC catalog (an outlet);
  - a consumer atlas pulls a published layer at build time (an inlet).

This module holds the *pure* logic — property masking, STAC catalog/collection
construction, collection resolution, and bbox filtering — with no heavy
dependencies (stdlib only). The eddy/outlet/inlet materializers in eddies.py,
outlets.py and vector_inlets.py are thin wrappers around these functions, which
keeps the logic unit-testable locally without GDAL/QGIS/shapely installed.

See documents/federation-overview.md and
documents/development/federation-claude-code-handoff.md.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

STAC_VERSION = "1.1.0"


# --------------------------------------------------------------------------- #
# Source-side property masking (used by the mask_properties eddy)
# --------------------------------------------------------------------------- #

def mask_features(fc: Dict[str, Any], allow: Optional[List[str]] = None,
                  block: Optional[List[str]] = None) -> Dict[str, Any]:
    """Return a copy of a FeatureCollection with feature properties masked.

    Exactly one of `allow` / `block` may be supplied (or neither):
      - allow: keep only these property keys on each feature.
      - block: drop these property keys from each feature.
    Geometry is left untouched. With neither, properties pass through unchanged.

    This is the source-side mask for federation: withheld values are never
    written into the output, so they never cross the wire. The input
    FeatureCollection is not mutated.
    """
    if allow and block:
        raise ValueError("mask_features: 'allow' and 'block' are mutually exclusive")

    allow_set = set(allow) if allow else None
    block_set = set(block) if block else None

    out_features = []
    for feature in fc.get('features', []):
        props = feature.get('properties') or {}
        if allow_set is not None:
            new_props = {k: v for k, v in props.items() if k in allow_set}
        elif block_set is not None:
            new_props = {k: v for k, v in props.items() if k not in block_set}
        else:
            new_props = dict(props)
        out_features.append({**feature, 'properties': new_props})

    return {**fc, 'type': 'FeatureCollection', 'features': out_features}


# --------------------------------------------------------------------------- #
# Source-side: discover shareable layers and build the static STAC catalog
# --------------------------------------------------------------------------- #

def iter_shareable_layers(layers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """From the runtime layer list (config['dataswale']['layers']), return the
    catalog-relevant metadata for each layer whose `shareable.enabled` is truthy.

    Each returned dict: {name, title, description, license}.
    """
    shareable = []
    for layer in layers:
        share = layer.get('shareable')
        if not (isinstance(share, dict) and share.get('enabled')):
            continue
        shareable.append({
            'name': layer['name'],
            'title': share.get('title', layer['name']),
            'description': share.get('description', ''),
            'license': share.get('license', 'other'),
        })
    return shareable


def _bbox_to_stac(bbox: Dict[str, float]) -> List[float]:
    """{north,south,east,west} -> STAC [west, south, east, north]."""
    return [bbox['west'], bbox['south'], bbox['east'], bbox['north']]


def build_stac_catalog(atlas_id: str, atlas_description: str,
                       shareable_layers: List[Dict[str, Any]],
                       bbox: Dict[str, float],
                       catalog_base_url: str,
                       data_base_url: str) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    """Build a static STAC catalog for a set of shareable layers.

    Args:
        atlas_id / atlas_description: catalog identity.
        shareable_layers: output of iter_shareable_layers().
        bbox: atlas bbox dict {north,south,east,west}.
        catalog_base_url: absolute URL of the dir holding catalog.json,
            with trailing slash (e.g. ".../CURRENT/outlets/stac/").
        data_base_url: absolute URL of the published layers dir, with trailing
            slash (e.g. ".../CURRENT/layers/"). Each layer's GeoJSON asset is
            f"{data_base_url}{name}/{name}.geojson".

    Returns (catalog_dict, {layer_name: collection_dict}).
    """
    catalog_self = catalog_base_url + 'catalog.json'
    stac_bbox = _bbox_to_stac(bbox)

    catalog = {
        'stac_version': STAC_VERSION,
        'type': 'Catalog',
        'id': atlas_id,
        'description': atlas_description,
        'links': [
            {'rel': 'root', 'href': catalog_self},
            {'rel': 'self', 'href': catalog_self},
        ],
    }

    collections: Dict[str, Dict[str, Any]] = {}
    for layer in shareable_layers:
        name = layer['name']
        catalog['links'].append({'rel': 'child', 'href': f'./{name}/collection.json'})

        asset_href = f'{data_base_url}{name}/{name}.geojson'
        collections[name] = {
            'stac_version': STAC_VERSION,
            'type': 'Collection',
            'id': name,
            'title': layer.get('title', name),
            'description': layer.get('description', ''),
            'license': layer.get('license', 'other'),
            'extent': {
                'spatial': {'bbox': [stac_bbox]},
                'temporal': {'interval': [[None, None]]},
            },
            'links': [
                {'rel': 'root', 'href': catalog_self},
                {'rel': 'parent', 'href': '../catalog.json'},
                {'rel': 'self', 'href': f'{catalog_base_url}{name}/collection.json'},
            ],
            'assets': {
                'data': {
                    'href': asset_href,
                    'type': 'application/geo+json',
                    'roles': ['data'],
                    'title': layer.get('title', name),
                },
            },
        }

    return catalog, collections


# --------------------------------------------------------------------------- #
# Consumer-side: resolve a collection from a fetched catalog, find its data
# --------------------------------------------------------------------------- #

def resolve_collection_href(catalog: Dict[str, Any], collection_id: str) -> str:
    """Return the href of the child collection matching `collection_id`.

    Static-catalog convention: a child collection lives at
    `{collection_id}/collection.json`, so we match the child link whose href
    ends in that path. Raises ValueError with context if not found.
    """
    suffix = f'{collection_id}/collection.json'
    for link in catalog.get('links', []):
        if link.get('rel') == 'child' and str(link.get('href', '')).rstrip('/').endswith(suffix):
            return link['href']
    available = [l.get('href') for l in catalog.get('links', []) if l.get('rel') == 'child']
    raise ValueError(
        f"federation: collection '{collection_id}' not found in catalog "
        f"(id={catalog.get('id')!r}); available children: {available}")


def data_href_from_collection(collection: Dict[str, Any]) -> str:
    """Return the 'data' asset href from a collection. Raises if absent."""
    try:
        return collection['assets']['data']['href']
    except (KeyError, TypeError):
        raise ValueError(
            f"federation: collection {collection.get('id')!r} has no 'data' asset")


def source_version_from_collection(collection: Dict[str, Any]) -> Optional[str]:
    """Best-effort published-version identifier from a collection, if present.

    Checks the STAC `version` member, then a `version` property. None if absent
    (caller falls back to fetch timestamp).
    """
    if collection.get('version'):
        return str(collection['version'])
    props = collection.get('properties') or {}
    if props.get('version'):
        return str(props['version'])
    return None


# --------------------------------------------------------------------------- #
# Consumer-side: optional bbox filtering (pure python, no shapely)
# --------------------------------------------------------------------------- #

def _iter_coords(coords):
    """Yield [lon, lat] pairs from arbitrarily nested GeoJSON coordinates."""
    if not coords:
        return
    if isinstance(coords[0], (int, float)):
        yield coords
        return
    for c in coords:
        yield from _iter_coords(c)


def feature_in_bbox(feature: Dict[str, Any], bbox: Dict[str, float]) -> bool:
    """True if the feature overlaps the bbox {north,south,east,west}.

    Points test for containment; other geometries test for any overlap between
    the geometry's coordinate bounds and the bbox. Features without geometry are
    kept (cannot be excluded on spatial grounds).
    """
    geom = feature.get('geometry')
    if not geom or 'coordinates' not in geom:
        return True

    if geom.get('type') == 'Point':
        lon, lat = geom['coordinates'][0], geom['coordinates'][1]
        return (bbox['west'] <= lon <= bbox['east']
                and bbox['south'] <= lat <= bbox['north'])

    lons = [c[0] for c in _iter_coords(geom['coordinates'])]
    lats = [c[1] for c in _iter_coords(geom['coordinates'])]
    if not lons:
        return True
    # overlap test
    return not (min(lons) > bbox['east'] or max(lons) < bbox['west']
                or min(lats) > bbox['north'] or max(lats) < bbox['south'])


def filter_features_to_bbox(fc: Dict[str, Any], bbox: Dict[str, float]) -> Dict[str, Any]:
    """Return a copy of fc keeping only features overlapping bbox."""
    kept = [f for f in fc.get('features', []) if feature_in_bbox(f, bbox)]
    return {**fc, 'type': 'FeatureCollection', 'features': kept}


# --------------------------------------------------------------------------- #
# Consumer-side: provenance (folded into the attribution system)
# --------------------------------------------------------------------------- #

def build_provenance(source_catalog: str, collection_id: str,
                     source_version: Optional[str],
                     consumer_version: Optional[str] = None) -> Dict[str, Any]:
    """Provenance record for a federated pull, written to
    layers/{out_layer}/provenance.json and folded into attribution by
    generate_attributions.py."""
    fetched_at = datetime.now(timezone.utc).isoformat()
    return {
        'source_catalog': source_catalog,
        'collection_id': collection_id,
        'source_version': source_version or fetched_at,
        'fetched_at': fetched_at,
        'consumer_version': consumer_version,
    }


def fold_provenance_into_attribution(attribution: Dict[str, Any],
                                     provenance: Dict[str, Any]) -> Dict[str, Any]:
    """Merge a federated-pull provenance record into a layer attribution dict so
    the console '(source)' link reflects where and when the data was federated.

    Returns a new dict; does not mutate the input. Fills `url` (source catalog)
    and `description` only if empty, and appends a human-readable note to
    `metadata`.
    """
    out = dict(attribution)
    note = (f"Federated from {provenance.get('source_catalog')} "
            f"(collection {provenance.get('collection_id')}, "
            f"version {provenance.get('source_version')}, "
            f"fetched {provenance.get('fetched_at')}).")
    if not out.get('url'):
        out['url'] = provenance.get('source_catalog', '')
    if not out.get('description'):
        out['description'] = note
    existing = out.get('metadata') or ''
    out['metadata'] = (existing + ' ' + note).strip()
    return out
