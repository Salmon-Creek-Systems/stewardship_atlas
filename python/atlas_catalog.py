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
        siblings = sorted(p.name for p in path.parent.iterdir()
                          if p.is_file() and p.name != 'stats.json')
        assets[name] = {
            'path': path,
            'href': f'{data_base_url}{name}/{path.name}',
            'size': path.stat().st_size,
            'checksum': sha256_multihash(path),
            # Every servable file in the layer dir, not just the primary one. A
            # webmap asks for `basemap.tiff.jpg` while the layer's primary file
            # is `basemap.tiff`, so recording one file per layer is not enough
            # to answer "does this version hold what the caller wants".
            'files': siblings,
            'file_hrefs': {n: f'{data_base_url}{name}/{n}' for n in siblings},
        }
    return assets


def _resolve_item_href(href: str, base_url: str, atlas_root):
    """Map a catalog href back to a local path, or None if it is not ours.

    All versions of an atlas are sibling directories under the atlas root, and
    hrefs are `{base_url}/{version}/stac/{layer}/{item}.json`, so stripping the
    base URL yields a path relative to that root.
    """
    if not (base_url and atlas_root):
        return None
    prefix = base_url.rstrip('/') + '/'
    if not href.startswith(prefix):
        return None
    return Path(atlas_root) / href[len(prefix):]


def load_history(stac_dir, base_url: str = '', atlas_root=None) -> dict:
    """Read `{layer: [Items, oldest first]}` from a previous version's catalog.

    Follows each Collection's `item` links rather than listing the directory.
    That matters because a **reused** layer has no Item file in the version
    that reused it — only a Collection linking back to where the Item really
    lives. Globbing the directory therefore loses the layer's history after a
    single hop, and the layer gets needlessly rewritten on the next publish
    (found on kennedy: three publishes with no edits rewrote every layer on the
    third). Item files that are local to this version are still picked up, so
    a first publish and a hop both work.

    Returns an empty dict for a first publish or an unreadable catalog — the
    consequence is that everything is written fresh, which is safe. A corrupt
    previous catalog must never take a publish down.
    """
    stac_dir = Path(stac_dir)
    if not stac_dir.is_dir():
        return {}

    def _read_item(path):
        try:
            with open(path) as handle:
                item = json.load(handle)
        except (OSError, ValueError) as exc:
            logger.warning(f"atlas_catalog: skipping unreadable Item {path}: {exc}")
            return None
        return item if item.get('type') == 'Feature' else None

    history = {}
    for layer_dir in sorted(p for p in stac_dir.iterdir() if p.is_dir()):
        if layer_dir.name == 'versions':
            continue

        paths = []
        collection_path = layer_dir / 'collection.json'
        if collection_path.is_file():
            try:
                with open(collection_path) as handle:
                    collection = json.load(handle)
            except (OSError, ValueError) as exc:
                logger.warning(f"atlas_catalog: unreadable collection "
                               f"{collection_path}: {exc}")
                collection = {}
            for link in collection.get('links', []):
                if link.get('rel') != 'item':
                    continue
                resolved = _resolve_item_href(link.get('href', ''), base_url, atlas_root)
                if resolved is None:
                    resolved = layer_dir / Path(link.get('href', '')).name
                paths.append(resolved)

        # Fall back to (and top up with) Item files sitting in this directory,
        # so a catalog written without resolvable hrefs still yields history.
        for local in sorted(layer_dir.glob('*.json')):
            if local.name != 'collection.json' and local not in paths:
                paths.append(local)

        items = []
        seen = set()
        for path in paths:
            if not path.is_file():
                logger.warning(f"atlas_catalog: history references a missing Item "
                               f"{path} — that layer will be rewritten")
                continue
            item = _read_item(path)
            if item is None or item.get('id') in seen:
                continue
            seen.add(item['id'])
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
    history = load_history(
        Path(previous_version_path) / CATALOG_DIRNAME,
        base_url=base_url,
        atlas_root=Path(previous_version_path).parent,
    ) if previous_version_path else {}

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


def item_version(item: dict):
    """The version an Item describes, from the `version` extension property."""
    return (item.get('properties') or {}).get('version')


def resolve_pinned_layers(layers, staging_layers_root, previous_stac_dir,
                          base_url: str = '', atlas_root=None) -> dict:
    """`{layer_name: version}` for layers a publish would *not* rewrite.

    A layer whose staging bytes match the newest Item in the previous version's
    catalog will be reused rather than re-stamped, so an outlet built now should
    address it at the version that actually holds it — not at `../../layers/`,
    which assumes every layer is copied into every version. Removing that
    assumption is the point: it has no counterpart in an object store, and
    propping it up with hard/symlinks would be work to undo at the S3 cutover.

    Layers absent from the result are changed (or new), will be written into the
    version being built, and keep addressing it locally.

    Costs one sha256 per layer file, so callers should only ask when the feature
    is on — see `outlets._pinned_layers()`.
    """
    history = load_history(previous_stac_dir, base_url=base_url,
                           atlas_root=atlas_root)
    if not history:
        return {}

    staging_layers_root = Path(staging_layers_root)
    pinned = {}
    for layer in layers:
        name = layer.get('name')
        items = history.get(name) if name else None
        if not items:
            continue
        path = find_layer_file(staging_layers_root, name)
        if path is None:
            continue
        version = item_version(items[-1])
        if version and item_checksum_matches(items[-1], sha256_multihash(path)):
            pinned[name] = {'version': version, 'files': item_filenames(items[-1])}
    return pinned


def item_checksum_matches(item: dict, checksum: str) -> bool:
    """True only on a real match — an absent checksum on either side is False.

    Fail-closed: treating 'unknown' as 'unchanged' would pin an outlet at a
    version whose bytes we never verified.
    """
    if not checksum:
        return False
    import federation
    return federation.item_checksum(item) == checksum


def layer_data_url(bake: bool, layer_name: str, filename: str,
                   pinned: dict = None) -> str:
    """URL a webmap should use for a layer file: baked, pinned, or shared.

    Lives here rather than in `outlets` because `outlets` imports duckdb and
    cannot be imported in the bare local env — the same reason `federation`
    holds the federation logic. `outlets._layer_data_url` delegates.

    `pinned` maps layer name -> the version that actually holds the data, for
    layers a publish would reuse rather than copy. Staging and every published
    version are sibling directories under the atlas root, so
    `../../../{version}/...` resolves identically from a staging outlet and
    from a published one. That is what lets an outlet stop assuming its layers
    were copied alongside it — an assumption with no counterpart in an object
    store.

    Empty `pinned` reproduces the previous behaviour exactly.
    """
    if bake:
        return f"data/{filename}"
    entry = (pinned or {}).get(layer_name)
    if entry and filename in entry.get('files', ()):
        return f"../../../{entry['version']}/layers/{layer_name}/{filename}"
    return f"../../layers/{layer_name}/{filename}"


def item_filenames(item: dict) -> set:
    """Every filename an Item records, across all of its assets.

    Pinning a layer to another version is only safe for files that version
    actually holds. Resolving the *version* from the catalog and then
    reconstructing the filename by convention is half a mechanism: it works
    while `{layer}.geojson` holds and fails silently when it does not — a
    raster whose webmap wants `{layer}.tiff.jpg`, or a layer whose file is
    named something else. Unknown filename means fall back to `../../layers/`.
    """
    names = set()
    for asset in (item.get('assets') or {}).values():
        href = asset.get('href') if isinstance(asset, dict) else None
        if href:
            names.add(Path(href).name)
    return names
