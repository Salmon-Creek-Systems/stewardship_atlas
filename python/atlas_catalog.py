"""Write a published version's STAC catalog to disk (Phase 3, issue #159).

The pure catalog logic lives in `federation.py`. This module is the thin
filesystem half: find each layer's file, checksum it, load the previous
version's Items so unchanged layers can be *referenced* rather than rewritten,
and serialize the documents.

Deliberately import-light — stdlib plus `federation` — so it stays testable in
the bare local env (issue #153 / the "local test env is bare" constraint). It
does not import duckdb, GDAL, boto3 or anything else heavy, and it must not
grow to.

There is no version directory any more (task 6). A publish reads **staging**
and the previous versions come back from the catalog in S3, so the catalog is
not a description of a snapshot — it *is* the version.

Layout in the bucket, one stable prefix rather than a copy per version:

    {atlas}/catalog/catalog.json                     <- the version index
    {atlas}/catalog/{name}/collection.json           <- layers and outlets alike
    {atlas}/catalog/{name}/{name}-{version}.json     <- the Item, written once
    {atlas}/catalog/versions/{version}/catalog.json  <- the manifest

The root `catalog.json` is the one mutable document: every publish rewrites it
so it keeps naming every version. It is rebuildable from a listing of the
`versions/` prefix, which is what stops it being a single point of loss.
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


# An outlet's entry point. `index.html` at the root is the common case; the
# `html` and `console` outlets have no root index at all, because all four role
# variants are subdirectories and the public entry is `public/index.html`
# (found the hard way — checking `html/` always 403s on CloudFront).
OUTLET_ENTRY_CANDIDATES = ('index.html', 'public/index.html')


def find_outlet_entry(outlet_dir):
    """Relative path of the outlet's entry point, or None if it has no page.

    None means the directory is not a servable outlet — a half-materialized
    build, or an asset that writes files without a page. The caller reports it
    as missing rather than cataloguing an Item whose entry href 404s.
    """
    outlet_dir = Path(outlet_dir)
    for rel in OUTLET_ENTRY_CANDIDATES:
        if (outlet_dir / rel).is_file():
            return rel
    return None


def iter_outlet_files(outlet_dir):
    """(relative posix path, Path) for every archivable file under an outlet.

    Dotfiles and dot-directories are skipped at every level. The one that
    matters is `.htpasswd`, which `atlas.add_htpasswds()` writes into outlet
    directories: it is regenerated by every config build, it has no reader once
    nginx stops serving these, and archiving credentials because they happened
    to be in the directory is how `lpss` published its own (see
    `is_servable_file`).
    """
    outlet_dir = Path(outlet_dir)
    for path in sorted(outlet_dir.rglob('*')):
        if not path.is_file():
            continue
        rel = path.relative_to(outlet_dir).as_posix()
        if any(segment.startswith('.') for segment in rel.split('/')):
            continue
        yield rel, path


def scan_outlet_dir(outlet_dir) -> dict:
    """Describe one built outlet directory, or None if it holds no entry page.

    Every file is hashed, for two reasons at once: the roll-up is what decides
    whether this outlet can be *reused* from the previous version instead of
    re-uploading several hundred objects, and the per-file hashes are what the
    upload planner skips already-present objects with.
    """
    outlet_dir = Path(outlet_dir)
    if not outlet_dir.is_dir():
        return None
    entry = find_outlet_entry(outlet_dir)
    if entry is None:
        return None

    files = {}
    total = 0
    for rel, path in iter_outlet_files(outlet_dir):
        files[rel] = sha256_multihash(path)
        total += path.stat().st_size

    return {
        'entry': entry,
        'files': files,
        'checksum': federation.content_checksum(files),
        'entry_size': (outlet_dir / entry).stat().st_size,
        'file_count': len(files),
        'bytes': total,
    }


def scan_outlets(outlets_root, names) -> dict:
    """`{outlet_name: spec}` for the outlets that were actually built.

    Outlets with no directory, or with a directory holding no entry page, are
    simply absent — the caller reports them as missing. A never-materialized
    outlet must not take a publish down.
    """
    outlets_root = Path(outlets_root)
    found = {}
    for name in names:
        spec = scan_outlet_dir(outlets_root / name)
        if spec is None:
            logger.info(f"atlas_catalog: outlet '{name}' has no built entry page "
                        f"under {outlets_root / name} — not cataloguing it")
            continue
        found[name] = spec
    return found


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


def _read_links(document, rel: str):
    """Hrefs of one link relation, in document order."""
    return [link.get('href', '') for link in (document or {}).get('links', [])
            if link.get('rel') == rel]


def known_versions(root_catalog) -> list:
    """Versions a root Catalog names, sorted. Empty for a first publish.

    This is what replaces `config['dataswale']['versions']` and the directory
    listing behind `atlas.discover_versions()`: the catalog is the index, so
    asking it is asking the one thing that knows.
    """
    found = set()
    for link in (root_catalog or {}).get('links', []):
        if link.get('rel') != 'version-history':
            continue
        title = link.get('title')
        if not title:
            parts = [p for p in link.get('href', '').split('/') if p]
            title = parts[-2] if len(parts) >= 2 else None
        if title:
            found.add(title)
    return sorted(found)


def load_history_from_s3(client, bucket: str, atlas_name: str,
                         base_url: str) -> tuple:
    """Read `({name: [Items, oldest first]}, known_versions)` from the catalog.

    Follows each Collection's `item` links rather than listing the prefix.
    That is load-bearing for the same reason it was on disk: a **reused** entry
    gets no new Item, only a Collection link back to where its Item really
    lives, so a listing loses its history after one hop and the layer is
    needlessly rewritten on the next publish (found on kennedy — three
    publishes with no edits rewrote every layer on the third).

    Returns empty history for a first publish or an unreadable catalog. The
    consequence is that everything is written fresh, which is safe; a corrupt
    previous catalog must never take a publish down, it only costs the reuse
    it would have enabled.
    """
    import atlas_store

    prefix = atlas_store.catalog_prefix(atlas_name)
    root = atlas_store.get_json(bucket, f'{prefix}/catalog.json', client=client)
    if not root:
        return {}, []

    history = {}
    for child_href in _read_links(root, 'child'):
        collection_key = atlas_store.key_from_href(child_href, base_url, prefix)
        if not collection_key:
            continue
        collection = atlas_store.get_json(bucket, collection_key, client=client)
        if not collection:
            logger.warning(f"atlas_catalog: s3://{bucket}/{collection_key} is "
                           f"missing or unreadable — its entries will be rewritten")
            continue
        name = collection.get('id') or Path(collection_key).parent.name

        items = []
        seen = set()
        for item_href in _read_links(collection, 'item'):
            item_key = atlas_store.key_from_href(item_href, base_url, prefix)
            if not item_key:
                continue
            item = atlas_store.get_json(bucket, item_key, client=client)
            if item is None:
                logger.warning(f"atlas_catalog: collection '{name}' references a "
                               f"missing Item {item_key} — that entry will be "
                               f"rewritten")
                continue
            if item.get('type') != 'Feature' or item.get('id') in seen:
                continue
            seen.add(item['id'])
            items.append(item)

        if items:
            items.sort(key=lambda i: i.get('properties', {}).get('datetime') or '')
            history[name] = items

    return history, known_versions(root)


def read_version_items(client, bucket: str, atlas_name: str, version: str,
                       base_url: str) -> list:
    """The Items constituting one published version, or [] if there is no such
    version.

    This is what "a version is a Catalog" buys: the manifest is read once and
    names every layer and outlet in it, each at whichever version actually
    holds its bytes. Nothing has to be derived from a directory listing or from
    the current config.
    """
    import atlas_store

    prefix = atlas_store.catalog_prefix(atlas_name)
    catalog = atlas_store.get_json(
        bucket, f'{prefix}/versions/{version}/catalog.json', client=client)
    if not catalog:
        return []

    items = []
    for href in _read_links(catalog, 'item'):
        key = atlas_store.key_from_href(href, base_url, prefix)
        if not key:
            continue
        item = atlas_store.get_json(bucket, key, client=client)
        if item and item.get('type') == 'Feature':
            items.append(item)
    return items


def item_source_keys(item: dict) -> list:
    """`(bucket, key, filename)` for every asset of a layer Item.

    Read off the `alternate.s3` href the catalog already records, rather than
    rebuilt from `layer_key()`. Rebuilding would have to know which version
    holds the bytes, and getting that wrong is the exact bug this branch hit
    twice — the Item already carries the answer.
    """
    found = []
    for asset in (item.get('assets') or {}).values():
        href = ((asset.get('alternate') or {}).get('s3') or {}).get('href', '')
        if not href.startswith('s3://'):
            continue
        bucket, _, key = href[len('s3://'):].partition('/')
        if bucket and key:
            found.append((bucket, key, Path(key).name))
    return found


def catalog_documents(built: dict, atlas_name: str, version: str) -> dict:
    """`{s3 key: document}` for everything this publish should write.

    **Reused Items are not included.** They already exist at their own keys and
    are immutable; re-uploading them is the duplication the catalog exists to
    avoid. The cost is that `link_version_chain` cannot retro-fit
    successor/latest-version links onto already-published Items, so those go
    stale — which is why a layer's history is authoritative in its *Collection*
    (rewritten every publish) and the chain links are a convenience.
    """
    import atlas_store

    prefix = atlas_store.catalog_prefix(atlas_name)
    documents = {f'{prefix}/catalog.json': built['catalog']}
    for name, collection in built['collections'].items():
        documents[f'{prefix}/{name}/collection.json'] = collection
    for name, item in built['items'].items():
        documents[f'{prefix}/{name}/{item["id"]}.json'] = item
    documents[f'{prefix}/versions/{version}/catalog.json'] = built['version_catalog']
    return documents


def build_outlet_assets(config: dict, outlet_specs: dict, version: str) -> dict:
    """Turn scanned outlet directories into the spec `build_atlas_catalog` wants.

    The entry href names the **archive** — `s3://{private}/…` — rather than a
    public URL. The archive is where this version's copy of the outlet actually
    and permanently lives; the public URL belongs to whichever version is
    current, and baking "current" into an immutable Item would make it a lie
    the moment the next publish lands. `current.json` is what answers that.
    """
    import atlas_store

    settings = atlas_store.cloud_settings(config)
    private = settings.get('private_bucket') or ''
    assets = config.get('assets') or {}
    atlas_name = config['name']

    out = {}
    for name, spec in outlet_specs.items():
        asset = assets.get(name) or {}
        prefix = atlas_store.outlet_prefix(atlas_name, name, version)
        entry = spec['entry']
        out[name] = {
            'entry_href': (f's3://{private}/{prefix}/{entry}' if private
                           else f'{prefix}/{entry}'),
            'prefix': prefix,
            'access': atlas_store.normalize_access(asset.get('access')),
            'checksum': spec['checksum'],
            'entry_size': spec['entry_size'],
            'file_count': spec['file_count'],
            'title': asset.get('title', name),
            'description': asset.get('description', ''),
            # Declared inputs only. Outlets also read things they do not
            # declare (html checks that webmap exists; sqldb reads every
            # layer), and pretending otherwise would put a guess in the
            # published record. The rehearsal's file-open logging is what will
            # replace the guess with a measurement.
            'in_layers': asset.get('in_layers') or [],
        }
    return out


def publish_catalog(config: dict, source_path, version: str, *,
                    client=None, bucket: str = None) -> dict:
    """Build the STAC documents describing a new version of an atlas.

    `source_path` is the **staging** tree — there is no version directory any
    more. A version is not a copy of staging; it is a Catalog naming the
    immutable objects that constitute it, most of which the previous version
    already put in S3.

    Nothing is uploaded here. The caller writes the objects the documents name
    *first*, then the documents, so a catalog can never outlive the bytes it
    describes.
    """
    import atlas_store

    source_path = Path(source_path)
    layers = config.get('dataswale', {}).get('layers', [])
    bbox = config['dataswale']['bbox']
    atlas_id = config['name']

    settings = atlas_store.cloud_settings(config)
    bucket = bucket or settings.get('outlets_bucket') or ''
    base_url = atlas_store.catalog_base_url(config)

    # Relative within the layer tree. `set_layer_hrefs` promotes each asset to
    # its absolute S3/CloudFront URL and keeps this as `alternate.local`, which
    # is how a workspace — or an offline copy — resolves the same layer.
    layer_assets = scan_layers(layers, source_path / 'layers')

    outlet_specs = scan_outlets(source_path / 'outlets',
                                atlas_store.archivable_outlets(config))
    outlet_assets = build_outlet_assets(config, outlet_specs, version)

    history, previous_versions = ({}, [])
    if bucket and client is not None:
        history, previous_versions = load_history_from_s3(
            client, bucket, atlas_id, base_url)

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
        catalog_base_url=base_url,
        outlet_assets=outlet_assets,
        known_versions=previous_versions,
    )

    hrefs = set_layer_hrefs(built, config, version)

    summary = {
        'status': 'ok',
        'version': version,
        'written_layers': built['written'],
        'reused_layers': built['reused'],
        'missing_layers': built['missing'],
        'written_outlets': built['outlets_written'],
        'reused_outlets': built['outlets_reused'],
        'missing_outlets': built['outlets_missing'],
        'asset_hrefs': hrefs,
        # Passed through for the S3 push — computed here so every file is
        # scanned and checksummed exactly once per publish.
        'layer_assets': layer_assets,
        'access_by_layer': access_by_layer,
        'layer_versions': built['versions'],
        'outlet_specs': outlet_specs,
        'outlet_versions': built['outlet_versions'],
        'documents': catalog_documents(built, atlas_id, version),
        'versions': sorted(set(previous_versions) | {version}),
    }
    logger.info(
        f"atlas_catalog: {atlas_id} {version} — "
        f"layers {len(built['written'])} new / {len(built['reused'])} reused / "
        f"{len(built['missing'])} with no data; "
        f"outlets {len(built['outlets_written'])} new / "
        f"{len(built['outlets_reused'])} reused; "
        f"{len(summary['documents'])} document(s)")
    if built['missing']:
        logger.info(f"atlas_catalog: no data file for {built['missing']}")
    return summary


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
        if federation.is_outlet_item(item):
            # An outlet Item's asset is its entry page in the archive, which is
            # already an absolute s3:// URI at the outlet key space. Running it
            # through layer_key() rewrote it to `…/layers/webmap/{version}/
            # index.html` — a key nothing will ever hold — and routed a public
            # outlet's href at the public bucket on the strength of its
            # `atlas:access`, which is exactly the tier confusion #177 was.
            continue
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
