"""The staging catalog: STAC Items describing what an atlas's staging holds.

Published versions have always been STAC (``{atlas}/catalog/``). Staging — the
one editable copy — had no index, so publish walked a hydrated directory and
hashed every file to discover it. Decided 2026-11-18: staging gets a catalog of
its own, in the same place as the version catalogs, and publish *promotes* it.

Layout (outlets bucket, beside the versions; readable listings of admin layer
names were accepted — the hrefs are private objects)::

    {atlas}/catalog/staging/catalog.json         links to every Item
    {atlas}/catalog/staging/layer-{name}.json    one Item per layer dir
    {atlas}/catalog/staging/outlet-{name}.json   one Item per outlet dir

One Item per ``layers/{name}/`` or ``outlets/{name}/`` directory, one asset per
file, keyed by its path inside that directory. Each asset carries the file's
``file:checksum`` (sha2-256 multihash), ``file:size`` and the S3 ``atlas:etag``,
and its href is the private staging object. Config files, deltas and anything
else outside those two trees are not catalogued: they are not published content.

**Who writes it:** only session write-back, which holds the atlas lock, so there
are no concurrent writers. The seed (and repair) rebuilds it from a listing.
It is mutable, like the root catalog, so it is written ``Cache-Control:
no-cache``; code reads it through the S3 API.
"""
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

STAC_VERSION = '1.0.0'
FILE_EXTENSION = 'https://stac-extensions.github.io/file/v2.1.0/schema.json'
KINDS = {'layers': 'layer', 'outlets': 'outlet'}
WORKING_COPY_REL = 'working-copy'


# ---------------------------------------------------------------------------
# Pure
# ---------------------------------------------------------------------------

def item_of(rel: str):
    """``(kind, name, path inside the item)`` for a staging-relative path, or None."""
    parts = rel.split('/')
    if len(parts) < 3 or parts[0] not in KINDS or not parts[1]:
        return None
    return KINDS[parts[0]], parts[1], '/'.join(parts[2:])


def staging_rel(kind: str, name: str, inner: str) -> str:
    return f"{'layers' if kind == 'layer' else 'outlets'}/{name}/{inner}"


def catalog_prefix(atlas_name: str) -> str:
    return f"{atlas_name}/catalog/staging"


def item_id(kind: str, name: str) -> str:
    return f"{kind}-{name}"


def item_key(atlas_name: str, kind: str, name: str) -> str:
    return f"{catalog_prefix(atlas_name)}/{item_id(kind, name)}.json"


def multihash(sha256_hex: str) -> str:
    return '1220' + sha256_hex


def sha256_of(asset: dict) -> str:
    checksum = asset.get('file:checksum') or ''
    return checksum[4:] if checksum.startswith('1220') else checksum


def empty_item(atlas_name: str, kind: str, name: str) -> dict:
    return {
        'type': 'Feature', 'stac_version': STAC_VERSION,
        'stac_extensions': [FILE_EXTENSION],
        'id': item_id(kind, name),
        # Staging is a working copy; its extent is the atlas's, carried by the
        # version Items. Null geometry is valid STAC and needs no config here.
        'geometry': None,
        'properties': {'datetime': None, 'atlas:atlas': atlas_name,
                       'atlas:kind': kind, 'atlas:name': name},
        'links': [], 'assets': {},
    }


def apply_changes(item: dict, changes: dict, private_bucket: str, atlas_name: str) -> dict:
    """Return ``item`` with ``changes`` applied: ``{inner: entry or None}``.

    ``entry`` is ``{'sha256', 'size', 'etag'}``; None removes the asset.
    """
    kind = item['properties']['atlas:kind']
    name = item['properties']['atlas:name']
    assets = dict(item.get('assets') or {})
    for inner, entry in changes.items():
        if entry is None:
            assets.pop(inner, None)
            continue
        rel = staging_rel(kind, name, inner)
        assets[inner] = {
            'href': f"s3://{private_bucket}/{atlas_name}/staging/{rel}",
            'file:checksum': multihash(entry['sha256']),
            'file:size': entry['size'],
            'atlas:etag': entry['etag'],
        }
    out = dict(item, assets=dict(sorted(assets.items())))
    out['properties'] = dict(item['properties'],
                             datetime=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
    return out


def group_changes(changes: dict) -> dict:
    """``{(kind, name): {inner: entry or None}}`` from ``{rel: entry or None}``."""
    grouped = {}
    for rel, entry in changes.items():
        found = item_of(rel)
        if found is None:
            continue
        kind, name, inner = found
        grouped.setdefault((kind, name), {})[inner] = entry
    return grouped


def root_catalog(atlas_name: str, item_ids: list) -> dict:
    return {
        'type': 'Catalog', 'stac_version': STAC_VERSION,
        'id': f"{atlas_name}-staging",
        'description': f"Working copy of {atlas_name} — what the next publish would promote.",
        'links': [{'rel': 'item', 'href': f"./{i}.json", 'type': 'application/geo+json'}
                  for i in sorted(item_ids)],
    }


def tree_from_items(items: list) -> dict:
    """``{staging rel: {'sha256', 'size', 'etag'}}`` — what publish reads."""
    tree = {}
    for item in items:
        props = item['properties']
        for inner, asset in (item.get('assets') or {}).items():
            tree[staging_rel(props['atlas:kind'], props['atlas:name'], inner)] = {
                'sha256': sha256_of(asset), 'size': asset.get('file:size'),
                'etag': asset.get('atlas:etag')}
    return tree


# ---------------------------------------------------------------------------
# S3
# ---------------------------------------------------------------------------

def _put(client, bucket: str, key: str, doc: dict) -> None:
    client.put_object(Bucket=bucket, Key=key, Body=json.dumps(doc, indent=1).encode('utf-8'),
                      ContentType='application/json', CacheControl='no-cache')


def _get(client, bucket: str, key: str):
    import atlas_store
    return atlas_store.get_json(bucket, key, client=client)


def load_items(client, bucket: str, atlas_name: str) -> list:
    """Every staging Item, by following the root catalog's links."""
    prefix = catalog_prefix(atlas_name)
    root = _get(client, bucket, f"{prefix}/catalog.json") or {'links': []}
    items = []
    for link in root.get('links', []):
        if link.get('rel') != 'item':
            continue
        item = _get(client, bucket, f"{prefix}/{link['href'].lstrip('./')}")
        if item is not None:
            items.append(item)
    return items


def load_tree(client, bucket: str, atlas_name: str) -> dict:
    return tree_from_items(load_items(client, bucket, atlas_name))


def update(client, bucket: str, private_bucket: str, atlas_name: str, changes: dict) -> dict:
    """Apply ``{rel: entry or None}`` to the staging catalog. Under the atlas lock.

    Reads only the Items the changes touch; rewrites the root catalog only when
    an Item appears or disappears. Returns ``{'items_written', 'items_removed'}``.
    """
    grouped = group_changes(changes)
    if not grouped:
        return {'items_written': 0, 'items_removed': 0}
    prefix = catalog_prefix(atlas_name)
    root = _get(client, bucket, f"{prefix}/catalog.json")
    ids = {l['href'].lstrip('./')[:-len('.json')] for l in (root or {}).get('links', [])
           if l.get('rel') == 'item'}
    before = set(ids)
    written = removed = 0
    for (kind, name), item_changes in sorted(grouped.items()):
        key = item_key(atlas_name, kind, name)
        item = _get(client, bucket, key) or empty_item(atlas_name, kind, name)
        item = apply_changes(item, item_changes, private_bucket, atlas_name)
        if item['assets']:
            _put(client, bucket, key, item)
            ids.add(item['id'])
            written += 1
        else:
            client.delete_object(Bucket=bucket, Key=key)
            ids.discard(item['id'])
            removed += 1
    if ids != before or root is None:
        _put(client, bucket, f"{prefix}/catalog.json", root_catalog(atlas_name, list(ids)))
    return {'items_written': written, 'items_removed': removed}


def rebuild(client, bucket: str, private_bucket: str, atlas_name: str) -> dict:
    """Rebuild the whole staging catalog from a listing of staging. Seed and repair.

    Reads each object's ``sha256`` metadata (write-back and the seed both set
    it); an object without it is downloaded and hashed. Replaces every Item.
    """
    import hashlib
    import atlas_workspace as ws
    listing = ws.list_staging(client, private_bucket, atlas_name)
    changes, hashed = {}, 0
    for rel, obj in sorted(listing.items()):
        if item_of(rel) is None:
            continue
        key = ws.staging_prefix(atlas_name) + rel
        head = client.head_object(Bucket=private_bucket, Key=key)
        sha = (head.get('Metadata') or {}).get('sha256')
        if not sha:
            body = client.get_object(Bucket=private_bucket, Key=key)['Body'].read()
            sha = hashlib.sha256(body).hexdigest()
            hashed += 1
        changes[rel] = {'sha256': sha, 'size': obj['size'], 'etag': obj['etag']}

    prefix = catalog_prefix(atlas_name)
    old = {l['href'].lstrip('./')[:-len('.json')]
           for l in (_get(client, bucket, f"{prefix}/catalog.json") or {}).get('links', [])
           if l.get('rel') == 'item'}
    ids = set()
    for (kind, name), item_changes in sorted(group_changes(changes).items()):
        item = apply_changes(empty_item(atlas_name, kind, name), item_changes,
                             private_bucket, atlas_name)
        _put(client, bucket, item_key(atlas_name, kind, name), item)
        ids.add(item['id'])
    for stale in old - ids:
        client.delete_object(Bucket=bucket, Key=f"{prefix}/{stale}.json")
    _put(client, bucket, f"{prefix}/catalog.json", root_catalog(atlas_name, list(ids)))
    logger.info(f"staging_catalog: rebuilt {atlas_name}: {len(ids)} item(s), "
                f"{len(changes)} file(s), {hashed} hashed for lack of metadata")
    return {'items': len(ids), 'files': len(changes), 'hashed': hashed}
