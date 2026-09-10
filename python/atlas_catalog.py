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
if not logger.handlers:  # match versioning.py: visible under uvicorn
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)

CATALOG_DIRNAME = 'stac'

# Preference order when a layer directory holds more than one candidate file.
# GeoJSON first because it is today's vector format; parquet ahead of the
# raster suffixes so a converted layer wins over a leftover source tiff.
LAYER_FILE_SUFFIXES = ('.geojson', '.parquet', '.tiff', '.tif', '.pmtiles', '.gpkg')

# Extensions a consumer actually reads. Covers the vector and raster sources
# plus the rendered images a webmap requests ({layer}.tiff.jpg / .tiff.png --
# see the candidate list in atlas_store.plan_current_layers). Anything else in a layer
# directory is tooling residue: GDAL statistics sidecars, exports, lock files.
SERVABLE_SUFFIXES = frozenset(LAYER_FILE_SUFFIXES) | {
    '.png', '.jpg', '.jpeg', '.webp'}


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

    # The fallback must apply the same allowlist as the sibling scan. It did
    # not, and kennedy's `lpss` — a layer directory holding no lpss.* file at
    # all — selected `.htpasswd` as its primary asset and published it. A
    # denylist filtered in one place and not the other is worse than no filter,
    # because the gap only shows on the one layer that takes the odd path.
    files = [p for p in layer_dir.iterdir()
             if p.is_file() and is_servable_file(p.name, name)]
    if not files:
        return None
    largest = max(files, key=lambda p: p.stat().st_size)
    logger.info(f"atlas_catalog: layer '{name}' has no {name}.* file; "
                f"cataloguing largest servable file {largest.name}")
    return largest


def is_servable_file(filename: str, layer_name: str) -> bool:
    """Is this file part of the layer's published data?

    A layer directory is not a curated set of files. Kennedy's held `.htpasswd`
    (role credentials), plus `biochar_summary.csv` and `burns_simulation.geojson`
    left by other work. An earlier version of this took everything except
    `stats.json` and published all of it — including the credentials, to a
    world-readable bucket.

    So the rule is an allowlist by *name*, not a denylist by exception: a
    layer's servable files are the ones named after it. That matches the actual
    convention (`{layer}.geojson`, `{layer}.tiff.jpg`, `{layer}.pmtiles` — see
    the candidates in `atlas_store.plan_current_layers`) and anything unexpected in
    directory is excluded by default rather than published by default.
    """
    if filename.startswith('.'):
        return False
    if not (filename == layer_name or filename.startswith(f'{layer_name}.')):
        return False
    # Name-prefixing alone still admits GDAL's sidecars — basemap.tiff.aux.xml
    # rides along beside basemap.tiff. So the extension is an allowlist too:
    # servable means a format something actually reads, not merely a file the
    # layer's tooling happened to leave behind.
    return Path(filename).suffix.lower() in SERVABLE_SUFFIXES


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
                          if p.is_file() and is_servable_file(p.name, name))
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

    access_by_layer = {l['name']: federation.layer_access(l)
                       for l in layers if l.get('name')}

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

    hrefs = set_layer_hrefs(built, config, version)
    paths = write_catalog(built, version_path / CATALOG_DIRNAME, version)

    summary = {
        'status': 'ok',
        'version': version,
        'written_layers': built['written'],
        'reused_layers': built['reused'],
        'missing_layers': built['missing'],
        'documents': len(paths),
        'asset_hrefs': hrefs,
        # Passed through for the S3 push in versioning — computed here so the
        # layer files are scanned and checksummed exactly once per publish.
        'layer_assets': layer_assets,
        'access_by_layer': access_by_layer,
        'layer_versions': built['versions'],
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


# A webmap shows a non-COG raster as an `image` source pointing at a rendered
# picture of it, not at the source GeoTIFF. `.tiff.png` is preferred when it
# exists (transparency), otherwise `.tiff.jpg`.
RENDERED_RASTER_SUFFIXES = ('.tiff.png', '.tiff.jpg')


def rendered_raster_filename(layers_root, layer_name: str):
    """The rendered image a webmap should request for a raster layer, or None.

    None means the layer has no picture to show. Returning a filename anyway —
    which is what an unchecked `.tiff.jpg` fallback amounts to — produces a
    source URL that is guaranteed to 404, and the failure surfaces as a broken
    map rather than as anything pointing at the missing data.

    This is not the same case as a vector layer with no features. An empty
    FeatureCollection is a truthful statement that renders cleanly (#135); an
    empty raster is not a thing, so the layer is dropped instead.
    """
    layer_dir = Path(layers_root) / layer_name
    for suffix in RENDERED_RASTER_SUFFIXES:
        if (layer_dir / f'{layer_name}{suffix}').is_file():
            return f'{layer_name}{suffix}'
    return None


def layer_data_url(layer_name: str, filename: str) -> str:
    """URL a webmap should use for one of its layer files.

    Always relative, and deliberately so. A published outlet lives at
    ``{atlas}/current/outlets/{name}/`` on S3 and at
    ``{atlas}/{version}/outlets/{name}/`` on the box, and in both places
    ``../../layers/{layer}/{file}`` lands on the layer data that belongs to
    that copy — the S3 mirror in one case, the local layer tree in the other.
    One built artifact therefore works unchanged from either host, which is
    what lets publish stay a pure snapshot (#131): there is no URL to rewrite
    at publish time and no second materialize.

    This replaces three earlier variants — baked (``data/{file}``), pinned
    (``../../../{version}/layers/...``) and shared. Baking copied the same
    bytes into every outlet that referenced them and was the mechanism behind
    #177; pinning addressed a sibling *version* directory, which has no
    counterpart under the published ``current/`` prefix and would 404 on
    CloudFront. The mirror in ``atlas_store.plan_current_layers`` is what makes
    the single shared form correct everywhere.

    Absolute URLs are still required for range-read formats (PMTiles, COG), but
    those are resolved in the browser against ``window.location.href`` rather
    than baked in — same reason, one artifact, many hosts.
    """
    return f"../../layers/{layer_name}/{filename}"


ALTERNATE_EXTENSION = "https://stac-extensions.github.io/alternate-assets/v1.2.0/schema.json"


def set_layer_hrefs(built: dict, config: dict, version: str) -> int:
    """Point each Item's assets at S3, where the layer data now actually lives.

    This inverts the arrangement slice 4 shipped, which recorded S3 as an
    `alternate` and left the repo-relative path as the primary href. That was
    right while the push was best-effort: keys are deterministic, so writing
    one as primary before a failed upload leaves the catalog naming an object
    that does not exist. The premise is gone — the push now raises and takes
    the publish with it, so the catalog cannot outlive the objects it names.

    Primary href by tier:

    * **public** — the CloudFront URL. A public layer has a reader, and an
      HTTPS href is the one thing a STAC client, a browser and a webmap can
      all use.
    * **protected** — the `s3://` URI. These have no HTTPS reader until the
      Phase 4 authenticated path exists, and naming a URL that 403s would be
      less honest than naming the bucket.

    The previous relative href is kept as a `local` alternate rather than
    dropped: it is how the box resolves a layer from its own disk, and what an
    offline copy of an outlet still needs.
    """
    import atlas_store
    settings = atlas_store.cloud_settings(config)
    atlas_name = config['name']
    base_url = settings['public_base_url']

    touched = 0
    for layer_name, item in built['items'].items():
        access = (item.get('properties') or {}).get('atlas:access')
        bucket = atlas_store.layer_bucket(access, settings)
        if not bucket:
            continue
        public = bucket == settings['outlets_bucket']
        for asset in item.get('assets', {}).values():
            local_href = asset.get('href')
            filename = Path(local_href).name
            key = atlas_store.layer_key(atlas_name, layer_name, version, filename)
            s3_uri = f's3://{bucket}/{key}'
            asset['href'] = f'{base_url}/{key}' if public else s3_uri
            alternate = asset.setdefault('alternate', {})
            alternate['s3'] = {'href': s3_uri}
            if local_href:
                alternate['local'] = {'href': local_href}
            touched += 1
        exts = item.setdefault('stac_extensions', [])
        if ALTERNATE_EXTENSION not in exts:
            exts.append(ALTERNATE_EXTENSION)
    return touched
