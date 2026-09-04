"""Write a published version's STAC catalog to disk (Phase 3, issue #159).

The pure catalog logic lives in `federation.py`. This module is the thin
filesystem half: find each layer's file, checksum it, load the previous
version's Items so unchanged layers can be *referenced* rather than rewritten,
and serialize the documents.

Deliberately import-light — stdlib plus `federation` — so it stays testable in
the bare local env (issue #153 / the "local test env is bare" constraint). It
does not import duckdb, GDAL, boto3 or anything else heavy, and it must not
grow to.

**Additive by design.** Publishing a catalog changes nothing about what nginx
or CloudFront serves; it writes a new `stac/` directory inside the version
snapshot and nothing reads it yet. Wiring the catalog into the *copy* decision
(so unchanged rasters stop being duplicated) is the slice after this one.

On-disk layout inside a published version:

    {version}/stac/catalog.json
    {version}/stac/{layer}/collection.json
    {version}/stac/{layer}/{layer}-{version}.json      <- the Item
    {version}/stac/versions/{version}/catalog.json     <- the manifest
"""

from pathlib import Path
import hashlib
import json
import logging

import federation

logger = logging.getLogger(__name__)

CATALOG_DIRNAME = 'stac'

# Preference order when a layer directory holds more than one candidate file.
# GeoJSON first because it is today's vector format; parquet ahead of the
# raster suffixes so a converted layer wins over a leftover source tiff.
LAYER_FILE_SUFFIXES = ('.geojson', '.parquet', '.tiff', '.tif', '.pmtiles', '.gpkg')


def sha256_multihash(path, chunk_size: int = 1 << 20) -> str:
    """Multihash-encoded sha2-256 of a file, as STAC's `file:` extension wants.

    `file:checksum` is a multihash hex string, not a bare digest: '1220' is the
    sha2-256 code (0x12) and length (0x20 = 32 bytes), followed by the digest.
    Streamed so a 154 MB lidar basemap does not land in memory.
    """
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(chunk_size), b''):
            digest.update(block)
    return '1220' + digest.hexdigest()


def find_layer_file(layers_root, name):
    """The primary file for one layer, or None if the layer has no data yet.

    Convention is `layers/{name}/{name}.{ext}` (see `dataswale_geojson`), so
    the name-matched file wins. Falls back to the largest file in the directory
    so an oddly-named raster is still catalogued rather than silently dropped.
    """
    layer_dir = Path(layers_root) / name
    if not layer_dir.is_dir():
        return None

    for suffix in LAYER_FILE_SUFFIXES:
        candidate = layer_dir / f'{name}{suffix}'
        if candidate.is_file():
            return candidate

    files = [p for p in layer_dir.iterdir() if p.is_file() and p.name != 'stats.json']
    if not files:
        return None
    largest = max(files, key=lambda p: p.stat().st_size)
    logger.info(f"atlas_catalog: layer '{name}' has no {name}.* file; "
                f"cataloguing largest file {largest.name}")
    return largest


def scan_layers(layers, layers_root, data_base_url: str = '') -> dict:
    """Build the `layer_assets` mapping `build_atlas_catalog()` expects.

    Only layers named in the config are looked at — a directory on disk that
    the config does not declare is not catalogued (see #173). Layers with no
    file are simply absent from the result and come back as `missing`.
    """
    layers_root = Path(layers_root)
    assets = {}
    for layer in layers:
        name = layer.get('name')
        if not name:
            continue
        path = find_layer_file(layers_root, name)
        if path is None:
            continue
        assets[name] = {
            'path': path,
            'href': f'{data_base_url}{name}/{path.name}',
            'size': path.stat().st_size,
            'checksum': sha256_multihash(path),
        }
    return assets


def load_history(stac_dir) -> dict:
    """Read `{layer: [Items, oldest first]}` from a previous version's catalog.

    Returns an empty dict for a first publish or an unreadable catalog — the
    consequence is that everything is written fresh, which is safe. A corrupt
    previous catalog must never take a publish down.
    """
    stac_dir = Path(stac_dir)
    if not stac_dir.is_dir():
        return {}

    history = {}
    for layer_dir in sorted(p for p in stac_dir.iterdir() if p.is_dir()):
        if layer_dir.name == 'versions':
            continue
        items = []
        for item_path in sorted(layer_dir.glob('*.json')):
            if item_path.name == 'collection.json':
                continue
            try:
                with open(item_path) as handle:
                    item = json.load(handle)
            except (OSError, ValueError) as exc:
                logger.warning(f"atlas_catalog: skipping unreadable Item "
                               f"{item_path}: {exc}")
                continue
            if item.get('type') == 'Feature':
                items.append(item)
        if items:
            items.sort(key=lambda i: i.get('properties', {}).get('datetime') or '')
            history[layer_dir.name] = items
    return history


def write_catalog(built: dict, stac_dir, version: str) -> list:
    """Serialize the documents from `build_atlas_catalog()`. Returns paths written."""
    stac_dir = Path(stac_dir)
    stac_dir.mkdir(parents=True, exist_ok=True)
    written = []

    def _dump(obj, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w') as handle:
            json.dump(obj, handle, indent=2)
        written.append(path)

    _dump(built['catalog'], stac_dir / 'catalog.json')
    for name, collection in built['collections'].items():
        _dump(collection, stac_dir / name / 'collection.json')
    for name, item in built['items'].items():
        _dump(item, stac_dir / name / f"{item['id']}.json")
    _dump(built['version_catalog'], stac_dir / 'versions' / version / 'catalog.json')
    return written


def publish_catalog(config: dict, version_path, version: str,
                    previous_version_path=None) -> dict:
    """Write the STAC catalog for a just-created version snapshot.

    Called from `versioning.publish_new_version()` after the snapshot exists.
    Returns a summary dict; raises nothing the caller has to handle — see the
    call site, which treats a catalog failure as non-fatal exactly as
    `publish_public_outlets` does. A missing catalog is a missing index; a
    failed publish is a customer-visible outage.
    """
    version_path = Path(version_path)
    layers = config.get('dataswale', {}).get('layers', [])
    bbox = config['dataswale']['bbox']
    atlas_id = config['name']

    # Mirrors the href convention already used by outlet_stac_catalog().
    base_url = config.get('base_url', '')
    catalog_base_url = f'{base_url}/{version}/{CATALOG_DIRNAME}/' if base_url else ''
    data_base_url = f'{base_url}/{version}/layers/' if base_url else ''

    layer_assets = scan_layers(layers, version_path / 'layers', data_base_url)
    history = load_history(Path(previous_version_path) / CATALOG_DIRNAME) \
        if previous_version_path else {}

    built = federation.build_atlas_catalog(
        atlas_id=atlas_id,
        atlas_description=config.get(
            'description', f'Stewardship atlas {atlas_id}'),
        layers=layers,
        bbox=bbox,
        version=version,
        layer_assets=layer_assets,
        history=history,
        catalog_base_url=catalog_base_url,
    )

    paths = write_catalog(built, version_path / CATALOG_DIRNAME, version)

    summary = {
        'status': 'ok',
        'version': version,
        'written_layers': built['written'],
        'reused_layers': built['reused'],
        'missing_layers': built['missing'],
        'documents': len(paths),
    }
    logger.info(
        f"atlas_catalog: {atlas_id} {version} — {len(built['written'])} new Item(s), "
        f"{len(built['reused'])} reused, {len(built['missing'])} layer(s) with no data, "
        f"{len(paths)} document(s)")
    if built['missing']:
        logger.info(f"atlas_catalog: no data file for {built['missing']}")
    return summary
