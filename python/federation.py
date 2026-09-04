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


# --------------------------------------------------------------------------- #
# Phase 3: version-aware catalog
#
# The catalog above describes one snapshot of the *shareable* layers. It hangs
# each layer's asset straight off its Collection, which is fine while only
# CURRENT exists and is exactly why it cannot express versions: there is
# nowhere to put a second one. This section adds the missing level.
#
#     Catalog     {atlas}              the atlas
#       Collection  {layer}            a layer, spanning all of its versions
#         Item        {layer}-{ver}    one published version; holds the assets
#
# A version is then a thin Catalog linking to the Items that constitute it —
# which is the "manifest" Phase 3 needs, in a format other tools already read.
# Issue #159 records why this shape rather than Collection-per-version.
#
# Pure and stdlib-only like the rest of this module: these build dicts. They do
# not touch the filesystem, S3, or the network. Wiring into publish is a
# separate slice.
# --------------------------------------------------------------------------- #

# STAC extension schemas. `file:` carries size and checksum (what makes an Item
# reusable across versions); `version` carries the predecessor/successor chain.
FILE_EXTENSION = "https://stac-extensions.github.io/file/v2.1.0/schema.json"
VERSION_EXTENSION = "https://stac-extensions.github.io/version/v1.2.0/schema.json"


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string. Injectable so tests are stable."""
    return datetime.now(timezone.utc).isoformat()


def bbox_to_geometry(stac_bbox: List[float]) -> Dict[str, Any]:
    """[west, south, east, north] -> a closed GeoJSON Polygon ring.

    A STAC Item is a GeoJSON Feature and so needs a geometry. Atlas layers
    cover the whole atlas footprint, so the bbox rectangle is the honest
    answer; a tighter hull would imply a precision we do not have.
    """
    west, south, east, north = stac_bbox
    return {
        'type': 'Polygon',
        'coordinates': [[
            [west, south], [east, south], [east, north], [west, north], [west, south],
        ]],
    }


def stac_asset(href: str, *, media_type: Optional[str] = None,
               roles: Optional[List[str]] = None, title: Optional[str] = None,
               size: Optional[int] = None,
               checksum: Optional[str] = None) -> Dict[str, Any]:
    """Build one STAC asset entry.

    `media_type` defaults to atlas_store's table, which knows the geo formats
    mimetypes gets wrong. `size`/`checksum` become `file:` extension fields —
    the checksum is what lets a later version reuse this exact asset instead of
    writing a duplicate object.
    """
    if media_type is None:
        import atlas_store  # import-light (stdlib only); no cycle back to here
        media_type = atlas_store.content_type_for(href)

    asset: Dict[str, Any] = {'href': href, 'type': media_type}
    if roles:
        asset['roles'] = list(roles)
    if title:
        asset['title'] = title
    if size is not None:
        asset['file:size'] = size
    if checksum:
        asset['file:checksum'] = checksum
    return asset


def layer_access(layer: Dict[str, Any]) -> List[str]:
    """Access tiers for a *layer*, defaulting fail-closed.

    Deliberately the opposite of the outlet default. `atlas.py` does
    `.get('access', ['public'])` for outlets, and that fail-open default is a
    known trap — it is how `sqldb`, whose atlas.db holds every layer, reads as
    public on the strength of a missing field. A layer with no explicit tier is
    treated as internal here; publishing one is then an act of commission.

    `shareable.enabled` is an explicit decision to hand a layer to another
    atlas, so it promotes to public.
    """
    access = layer.get('access')
    if access:
        import atlas_store  # import-light (stdlib only); no cycle back to here
        return atlas_store.normalize_access(access)
    share = layer.get('shareable')
    if isinstance(share, dict) and share.get('enabled'):
        return ['public']
    return ['internal']


def is_public_layer(layer: Dict[str, Any]) -> bool:
    """True if this layer may appear in a world-readable catalog."""
    return 'public' in layer_access(layer)


def item_id(layer_name: str, version: str) -> str:
    """Stable Item id for one layer at one version.

    Hyphen-joined rather than slash-joined: an id travels into filenames and
    URLs, and STAC ids with path separators in them break naive resolvers.
    """
    return f'{layer_name}-{version}'


def build_layer_item(atlas_id: str, layer_name: str, version: str,
                     bbox: Dict[str, float], assets: Dict[str, Dict[str, Any]],
                     *, datetime_iso: Optional[str] = None,
                     catalog_base_url: str = '',
                     properties: Optional[Dict[str, Any]] = None,
                     derived_from: Optional[List[str]] = None) -> Dict[str, Any]:
    """A STAC Item describing one layer as published in one version.

    `datetime_iso` is the *publish* time. A layer like `hydrants` is a
    continuously-edited register rather than an observation, so publish time is
    the honest answer to "when was this true".

    `derived_from` hrefs record lineage — for an outlet Item, the layer Items it
    was built from. That turns `in_layers` from a build-time config field into
    a property of the published artifact.
    """
    stac_bbox = _bbox_to_stac(bbox)
    iid = item_id(layer_name, version)
    catalog_self = catalog_base_url + 'catalog.json'
    collection_href = f'{catalog_base_url}{layer_name}/collection.json'

    props = {'datetime': datetime_iso or utc_now_iso(), 'version': version}
    if properties:
        props.update(properties)

    links = [
        {'rel': 'root', 'href': catalog_self},
        {'rel': 'parent', 'href': collection_href},
        {'rel': 'collection', 'href': collection_href},
        {'rel': 'self', 'href': f'{catalog_base_url}{layer_name}/{iid}.json'},
    ]
    for href in (derived_from or []):
        links.append({'rel': 'derived_from', 'href': href})

    return {
        'stac_version': STAC_VERSION,
        'stac_extensions': [FILE_EXTENSION, VERSION_EXTENSION],
        'type': 'Feature',
        'id': iid,
        'collection': layer_name,
        'geometry': bbox_to_geometry(stac_bbox),
        'bbox': stac_bbox,
        'properties': props,
        'assets': dict(assets),
        'links': links,
    }


def build_layer_collection(atlas_id: str, layer: Dict[str, Any],
                           bbox: Dict[str, float], items: List[Dict[str, Any]],
                           *, catalog_base_url: str = '') -> Dict[str, Any]:
    """A Collection for one layer, spanning every version of it.

    `items` are that layer's Items, oldest first. The temporal extent runs from
    the first publish to the most recent one; the Collection's `version` member
    names the newest, which is what `source_version_from_collection()` already
    reads on the consumer side.
    """
    name = layer['name']
    stac_bbox = _bbox_to_stac(bbox)
    catalog_self = catalog_base_url + 'catalog.json'
    datetimes = [i['properties']['datetime'] for i in items]

    collection = {
        'stac_version': STAC_VERSION,
        'stac_extensions': [VERSION_EXTENSION],
        'type': 'Collection',
        'id': name,
        'title': layer.get('title', name),
        'description': layer.get('description', ''),
        'license': layer.get('license', 'other'),
        'extent': {
            'spatial': {'bbox': [stac_bbox]},
            'temporal': {'interval': [[
                datetimes[0] if datetimes else None,
                datetimes[-1] if datetimes else None,
            ]]},
        },
        'links': [
            {'rel': 'root', 'href': catalog_self},
            {'rel': 'parent', 'href': '../catalog.json'},
            {'rel': 'self', 'href': f'{catalog_base_url}{name}/collection.json'},
        ],
    }
    if items:
        collection['version'] = items[-1]['properties']['version']
    for item in items:
        collection['links'].append(
            {'rel': 'item', 'href': f"{catalog_base_url}{name}/{item['id']}.json"})
    return collection


def build_version_catalog(atlas_id: str, version: str,
                          items: List[Dict[str, Any]],
                          *, datetime_iso: Optional[str] = None,
                          catalog_base_url: str = '') -> Dict[str, Any]:
    """The thin Catalog naming everything that constitutes one version.

    This is the manifest. Its links point at Items that may live under *other*
    versions — an unchanged layer is referenced, not copied, which is what
    keeps a version cheap. Nothing here duplicates asset bytes.
    """
    catalog_self = catalog_base_url + 'catalog.json'
    catalog = {
        'stac_version': STAC_VERSION,
        'type': 'Catalog',
        'id': f'{atlas_id}-{version}',
        'description': f'Atlas {atlas_id}, published version {version}',
        'links': [
            {'rel': 'root', 'href': catalog_self},
            {'rel': 'self',
             'href': f'{catalog_base_url}versions/{version}/catalog.json'},
        ],
    }
    catalog['published'] = datetime_iso or utc_now_iso()
    for item in items:
        layer_name = item.get('collection') or item['id']
        catalog['links'].append(
            {'rel': 'item',
             'href': f"{catalog_base_url}{layer_name}/{item['id']}.json",
             'title': layer_name})
    return catalog


def link_version_chain(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Add `predecessor`/`successor`/`latest-version` links across one layer's
    Items, oldest first. Mutates and returns the list.

    This is the STAC `version` extension's answer to history, and it is why the
    catalog does not need a separate pointer file per layer: the newest Item is
    reachable from any of them.
    """
    if not items:
        return items
    latest = items[-1]
    for idx, item in enumerate(items):
        links = [l for l in item.get('links', [])
                 if l.get('rel') not in ('predecessor', 'successor', 'latest-version')]
        self_href = next((l['href'] for l in item['links'] if l.get('rel') == 'self'), None)
        if idx > 0:
            prev_self = next((l['href'] for l in items[idx - 1]['links']
                              if l.get('rel') == 'self'), None)
            if prev_self:
                links.append({'rel': 'predecessor', 'href': prev_self})
        if idx < len(items) - 1:
            next_self = next((l['href'] for l in items[idx + 1]['links']
                              if l.get('rel') == 'self'), None)
            if next_self:
                links.append({'rel': 'successor', 'href': next_self})
        latest_self = next((l['href'] for l in latest['links']
                            if l.get('rel') == 'self'), None)
        if latest_self and latest_self != self_href:
            links.append({'rel': 'latest-version', 'href': latest_self})
        item['links'] = links
    return items


def item_checksum(item: Dict[str, Any], asset_key: str = 'data') -> Optional[str]:
    """The `file:checksum` of an Item's primary asset, if it carries one."""
    asset = (item.get('assets') or {}).get(asset_key)
    if not isinstance(asset, dict):
        return None
    return asset.get('file:checksum')


def select_reusable_items(previous_items: Dict[str, Dict[str, Any]],
                          current_checksums: Dict[str, Optional[str]],
                          asset_key: str = 'data') -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """Split a new version's layers into (reuse, write).

    `previous_items` maps layer name -> the Item from the previous version.
    `current_checksums` maps layer name -> the checksum staging now holds.

    A layer whose checksum matches its previous Item is **reused**: the new
    version's catalog links to the existing Item and no new object is written.
    That is the mechanism that stops publish duplicating a layer tree it did
    not change.

    Fail-closed on unknowns: a missing checksum on either side means write.
    Trusting an absent checksum would silently publish a stale asset, which is
    far worse than writing an object we did not have to.
    """
    reuse: Dict[str, Dict[str, Any]] = {}
    write: List[str] = []
    for name, checksum in current_checksums.items():
        previous = previous_items.get(name)
        if previous is None or not checksum:
            write.append(name)
            continue
        if item_checksum(previous, asset_key) == checksum:
            reuse[name] = previous
        else:
            write.append(name)
    return reuse, sorted(write)


# --------------------------------------------------------------------------- #
# Assembling a whole atlas catalog for one published version
#
# Built from the **config's layer list**, never from directory contents. That
# is deliberate: a catalog built by walking the filesystem would faithfully
# preserve orphans forever (see #173, where a superseded pipeline's output sat
# in every published version for months). Config is the declaration of intent;
# anything on disk it does not name is, by construction, garbage.
# --------------------------------------------------------------------------- #

def classify_layer(layer: Dict[str, Any]) -> str:
    """'raster' or 'vector' for one layer config.

    Decides whether a layer is *copied* into each version (vector: measured at
    ~5% of scvfd's layer bytes and ~0.6% of fhe's, so copying is free and keeps
    the version prefix traversable by DuckDB/Athena) or *referenced* at its own
    stamped key (raster and tiles: where all the duplication savings are).
    """
    if layer.get('cog'):
        return 'raster'
    return 'raster' if layer.get('geometry_type') == 'raster' else 'vector'


def build_atlas_catalog(atlas_id: str, atlas_description: str,
                        layers: List[Dict[str, Any]], bbox: Dict[str, float],
                        version: str, layer_assets: Dict[str, Dict[str, Any]],
                        *, history: Optional[Dict[str, List[Dict[str, Any]]]] = None,
                        catalog_base_url: str = '',
                        datetime_iso: Optional[str] = None) -> Dict[str, Any]:
    """Assemble every document describing one published version.

    Args:
        layers: the config's layer list — the source of truth for what exists.
        layer_assets: {layer_name: {'href', 'size', 'checksum', 'media_type',
            'roles'}} for layers that actually have a file in this version.
        history: {layer_name: [Items, oldest first]} from previous versions.
            Empty or absent for a first publish.

    Returns a dict with:
        catalog          the atlas root Catalog
        collections      {layer_name: Collection}
        items            {layer_name: Item} — only the *newly written* ones
        version_catalog  the thin Catalog that is this version's manifest
        reused           layer names referenced at an older version
        written          layer names that got a new Item
        missing          layers declared in config with no file in this version

    Nothing here writes anything; the caller decides what to persist.
    """
    history = {k: list(v) for k, v in (history or {}).items()}
    when = datetime_iso or utc_now_iso()
    catalog_self = catalog_base_url + 'catalog.json'

    previous_items = {name: items[-1] for name, items in history.items() if items}
    current_checksums = {name: layer_assets[name].get('checksum')
                         for name in layer_assets}
    reuse, _ = select_reusable_items(previous_items, current_checksums)

    collections: Dict[str, Dict[str, Any]] = {}
    new_items: Dict[str, Dict[str, Any]] = {}
    version_items: List[Dict[str, Any]] = []
    reused: List[str] = []
    written: List[str] = []
    missing: List[str] = []

    for layer in layers:
        name = layer.get('name')
        if not name:
            continue
        spec = layer_assets.get(name)
        if spec is None:
            missing.append(name)
            continue

        if name in reuse:
            item = reuse[name]
            reused.append(name)
        else:
            assets = {'data': stac_asset(
                spec['href'],
                media_type=spec.get('media_type'),
                roles=spec.get('roles') or ['data'],
                title=layer.get('title', name),
                size=spec.get('size'),
                checksum=spec.get('checksum'))}
            item = build_layer_item(
                atlas_id, name, version, bbox, assets,
                datetime_iso=when, catalog_base_url=catalog_base_url,
                properties={'atlas:layer_type': classify_layer(layer),
                            'atlas:access': layer_access(layer)},
                derived_from=spec.get('derived_from'))
            history.setdefault(name, []).append(item)
            new_items[name] = item
            written.append(name)

        version_items.append(item)
        collections[name] = build_layer_collection(
            atlas_id, layer, bbox, link_version_chain(history.get(name, [])),
            catalog_base_url=catalog_base_url)

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
    for name in collections:
        catalog['links'].append({'rel': 'child', 'href': f'./{name}/collection.json'})
    catalog['links'].append(
        {'rel': 'version-history',
         'href': f'./versions/{version}/catalog.json', 'title': version})

    return {
        'catalog': catalog,
        'collections': collections,
        'items': new_items,
        'version_catalog': build_version_catalog(
            atlas_id, version, version_items,
            datetime_iso=when, catalog_base_url=catalog_base_url),
        'reused': reused,
        'written': written,
        'missing': missing,
    }
