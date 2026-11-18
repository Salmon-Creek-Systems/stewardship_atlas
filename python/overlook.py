"""Overlook — the atlas's public progress page.

A static, public outlet that shows what the atlas holds, what changed since the
last published release, where field photos are coming in, and the state of
every product the atlas builds. Design: ``documents/development/homepage_design.md``.

Pure Python (stdlib + the light atlas_store/federation/delta_provenance
modules): no QGIS, GDAL or DuckDB, so it runs in the bare test environment.

Output, under ``outlets/{name}/``::

    index.html               page shell, summary inlined
    overlook.json            the full computed summary
    data/changes.geojson     change-map features (allowlisted properties)
    data/photos.geojson      photo points (allowlisted properties; never `sender`)
    css/overlook.css, js/overlook.js

Privacy rules (each has a test in tests/test_overlook.py):

1. Only layers passing ``atlas_store.is_public(federation.layer_access(layer))``
   are read. Undeclared access means internal, which means excluded.
2. Derived GeoJSON carries only the properties in ``CHANGE_PROPS`` / ``PHOTO_PROPS``.
3. No URL to a protected outlet, a role-variant subdirectory, or ``staging/``.
"""

from __future__ import annotations

import html
import json
import logging
import math
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import atlas_store
import delta_provenance
import federation
import versioning

logger = logging.getLogger(__name__)

CHANGE_PROPS = ('layer', 'action', 'date', 'name', 'kind', 'source')
PHOTO_PROPS = ('name', 'timestamp', 'layer', 'image_url', 'map_url')

DEFAULTS = {
    'bulk_threshold': 500,
    'max_change_features': 5000,
    'photo_strip_count': 12,
    'headlines': [],
}

ACTION_WORDS = {
    'create': 'added',
    'reshape': 'reshaped',
    'match': 'updated',
    'annotate': 'annotated',
    'delete': 'removed (areas)',
}

ACCESS_LABELS = {'public': 'Public', 'internal': 'Members', 'admin': 'Admins',
                 'technical': 'Technical'}

# Outlets that are pipeline plumbing rather than products a person opens.
PIPELINE_FETCH_TYPES = frozenset({'s3_upload'})

EARTH_RADIUS_M = 6371008.8
M_PER_MILE = 1609.344
M2_PER_ACRE = 4046.8564224


# ── geometry (spherical approximations; good to well under 1% at atlas scale) ──

def _haversine(a, b) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def _line_length(coords) -> float:
    return sum(_haversine(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def _ring_area(coords) -> float:
    """Spherical polygon area of one ring (m², unsigned)."""
    if len(coords) < 4:
        return 0.0
    total = 0.0
    for i in range(len(coords) - 1):
        lon1, lat1 = map(math.radians, coords[i][:2])
        lon2, lat2 = map(math.radians, coords[i + 1][:2])
        total += (lon2 - lon1) * (2 + math.sin(lat1) + math.sin(lat2))
    return abs(total * EARTH_RADIUS_M ** 2 / 2)


def length_m(geom) -> float:
    if not geom:
        return 0.0
    t, c = geom.get('type'), geom.get('coordinates')
    if t == 'LineString':
        return _line_length(c)
    if t == 'MultiLineString':
        return sum(_line_length(l) for l in c)
    if t == 'GeometryCollection':
        return sum(length_m(g) for g in geom.get('geometries', []))
    return 0.0


def area_m2(geom) -> float:
    if not geom:
        return 0.0
    t, c = geom.get('type'), geom.get('coordinates')
    if t == 'Polygon':
        return max(0.0, _ring_area(c[0]) - sum(_ring_area(r) for r in c[1:]))
    if t == 'MultiPolygon':
        return sum(area_m2({'type': 'Polygon', 'coordinates': p}) for p in c)
    if t == 'GeometryCollection':
        return sum(area_m2(g) for g in geom.get('geometries', []))
    return 0.0


def _positions(geom) -> Iterable:
    if not geom:
        return
    t = geom.get('type')
    if t == 'GeometryCollection':
        for g in geom.get('geometries', []):
            yield from _positions(g)
        return
    stack = [geom.get('coordinates')]
    while stack:
        c = stack.pop()
        if not c:
            continue
        if isinstance(c[0], (int, float)):
            yield c
        else:
            stack.extend(c)


def representative_point(geom) -> Optional[Tuple[float, float]]:
    """Mean of the vertices — cheap, and inside enough for a 'view on map' link."""
    pts = list(_positions(geom))
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def bbox_of(features) -> Optional[List[float]]:
    xs, ys = [], []
    for f in features:
        for p in _positions(f.get('geometry')):
            xs.append(p[0])
            ys.append(p[1])
    if not xs:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


# ── config / layers ──────────────────────────────────────────────────────────

def outlet_settings(config: Dict[str, Any], outlet_name: str) -> Dict[str, Any]:
    asset = (config.get('assets') or {}).get(outlet_name) or {}
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in (asset.get('config') or {}).items() if k in DEFAULTS})
    merged.update({k: v for k, v in asset.items() if k in DEFAULTS})
    return merged


def public_layers(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Layer configs the overlook may read, keyed by name (fail-closed)."""
    out = {}
    for layer in (config.get('dataswale') or {}).get('layers') or []:
        name = layer.get('name')
        if name and atlas_store.is_public(federation.layer_access(layer)):
            out[name] = layer
    return out


def _layers_dir(config) -> Path:
    return versioning.atlas_path(config, 'layers')


def load_layer(config: Dict[str, Any], name: str) -> Optional[List[Dict[str, Any]]]:
    path = _layers_dir(config) / name / f'{name}.geojson'
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f).get('features') or []
    except (OSError, ValueError):
        logger.warning(f"overlook: could not read {path}")
        return None


def _newest_mtime(path: Path) -> Optional[float]:
    if not path.exists():
        return None
    if path.is_file():
        return path.stat().st_mtime
    newest = None
    for root, _dirs, files in os.walk(path):
        for fn in files:
            try:
                m = (Path(root) / fn).stat().st_mtime
            except OSError:
                continue
            newest = m if newest is None or m > newest else newest
    return newest


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _version_iso(version: Optional[str]) -> Optional[str]:
    dt = delta_provenance.parse_version(version) if version else None
    return dt.strftime('%Y-%m-%dT%H:%M:%S') if dt else None


# ── headlines ───────────────────────────────────────────────────────────────

def _matches_filter(feature, flt) -> bool:
    if not flt:
        return True
    props = feature.get('properties') or {}
    values = flt.get('values')
    if values is None and 'value' in flt:
        values = [flt['value']]
    return props.get(flt.get('property')) in (values or [])


def measure(features, how: str, flt=None) -> float:
    feats = [f for f in features if _matches_filter(f, flt)]
    if how == 'count':
        return len(feats)
    if how == 'length_mi':
        return sum(length_m(f.get('geometry')) for f in feats) / M_PER_MILE
    if how == 'area_acres':
        return sum(area_m2(f.get('geometry')) for f in feats) / M2_PER_ACRE
    raise ValueError(f"unknown measure: {how}")


def headlines(specs, layer_features: Dict[str, Optional[list]]) -> List[Dict[str, Any]]:
    """Stat tiles. A tile is omitted if its layer is missing, non-public or zero.

    ``layer_features`` must contain only public layers, so a non-public layer
    simply never resolves.
    """
    out = []
    for spec in specs or []:
        feats = layer_features.get(spec.get('layer'))
        if not feats:
            continue
        value = measure(feats, spec.get('measure', 'count'), spec.get('filter'))
        if value <= 0:
            continue
        rounded = int(round(value)) if value >= 10 or spec.get('measure', 'count') == 'count' else round(value, 1)
        out.append({'label': spec.get('label') or spec['layer'], 'value': rounded,
                    'layer': spec['layer'], 'measure': spec.get('measure', 'count')})
    return out


def is_photo(feature) -> bool:
    props = feature.get('properties') or {}
    return props.get('source') == 'email' and bool(props.get('image_url'))


# ── change scan ────────────────────────────────────────────────────────────

def scan_changes(config: Dict[str, Any], layer_names: Iterable[str],
                 since_version: Optional[str], bulk_threshold: int) -> List[Dict[str, Any]]:
    """Applied deltas, in public layers, that belong to the release after ``since_version``.

    Each entry: layer, action, source, asset, label, count, date, bulk, fc.
    """
    deltas_root = versioning.atlas_path(config, 'deltas')
    entries = []
    for d in delta_provenance.applied_deltas(deltas_root, layers=set(layer_names)):
        prov = d['provenance']
        if not delta_provenance.in_release_after(prov, since_version):
            continue
        feats = d['fc'].get('features') or []
        source = prov.get('source') or delta_provenance.UNRECORDED
        # Legacy (pre-provenance) photo deltas are recognizable by their features.
        if source == delta_provenance.UNRECORDED and feats and all(is_photo(f) for f in feats):
            source = 'email_photo'
        ts = prov.get('timestamp')
        written = prov.get('written_at')
        date = (written[:10] if written else (ts.strftime('%Y-%m-%d') if ts else None))
        count = prov.get('feature_count', len(feats))
        entries.append({
            'layer': d['layer'],
            'action': prov.get('action') or 'create',
            'source': source,
            'asset': prov.get('asset'),
            'label': delta_provenance.label(source, prov.get('asset')),
            'count': count,
            'date': date,
            'bulk': count > bulk_threshold,
            'fc': d['fc'],
        })
    return entries


def summarize_changes(entries: List[Dict[str, Any]],
                      regenerated: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group change entries by layer, newest first."""
    by_layer: Dict[str, Dict[str, Any]] = {}
    for e in entries:
        g = by_layer.setdefault(e['layer'], {'layer': e['layer'], 'actions': {},
                                             'sources': {}, 'rebuilds': [], 'dates': []})
        if e['date']:
            g['dates'].append(e['date'])
        if e['bulk']:
            g['rebuilds'].append({'label': e['label'], 'count': e['count']})
            continue
        g['actions'][e['action']] = g['actions'].get(e['action'], 0) + e['count']
        g['sources'][e['label']] = g['sources'].get(e['label'], 0) + e['count']
    for r in regenerated:
        if r['layer'] not in by_layer:
            by_layer[r['layer']] = {'layer': r['layer'], 'actions': {}, 'sources': {},
                                    'rebuilds': [], 'dates': [r['date']] if r['date'] else [],
                                    'regenerated': True}
    out = []
    for g in by_layer.values():
        dates = sorted(g.pop('dates'))
        g['first'] = dates[0] if dates else None
        g['last'] = dates[-1] if dates else None
        g['parts'] = [f"{n:,} {ACTION_WORDS.get(a, a)}" for a, n in sorted(g['actions'].items(), key=lambda kv: -kv[1])]
        g['parts'] += [f"rebuilt from {r['label']} ({r['count']:,} features)" for r in g['rebuilds']]
        if g.get('regenerated'):
            g['parts'].append('regenerated')
        out.append(g)
    out.sort(key=lambda g: (g['last'] or ''), reverse=True)
    return out


def regenerated_layers(config, layer_names, since_version, layers_with_deltas) -> List[Dict[str, Any]]:
    """Public layers rewritten since the last release without any delta (eddies, rasters)."""
    since = delta_provenance.parse_version(since_version) if since_version else None
    out = []
    for name in sorted(layer_names):
        if name in layers_with_deltas:
            continue
        m = _newest_mtime(_layers_dir(config) / name)
        if m is None:
            continue
        if since is None or datetime.fromtimestamp(m) > since:
            out.append({'layer': name, 'date': _iso(m)[:10]})
    return out


def change_features(entries, layer_features, max_features: int) -> Tuple[List[Dict[str, Any]], bool]:
    """Map features for the change map, property-allowlisted. Bulk deltas excluded."""
    feats: List[Dict[str, Any]] = []
    truncated = False
    index_cache: Dict[str, Dict[str, Any]] = {}

    def by_id(layer):
        if layer not in index_cache:
            index_cache[layer] = {
                (f.get('properties') or {}).get('atlas_id'): f
                for f in (layer_features.get(layer) or [])
                if (f.get('properties') or {}).get('atlas_id')}
        return index_cache[layer]

    for e in entries:
        if e['bulk']:
            continue
        action = e['action']
        for f in e['fc'].get('features') or []:
            geom = f.get('geometry')
            props = f.get('properties') or {}
            kind = 'feature'
            name = props.get('name')
            if action == 'match':
                current = by_id(e['layer']).get(props.get('atlas_id'))
                if current is None:
                    continue
                geom = current.get('geometry')
                name = (current.get('properties') or {}).get('name')
            elif action in ('annotate', 'delete'):
                kind = 'footprint'
                name = None
            if not geom:
                continue
            if len(feats) >= max_features:
                truncated = True
                break
            feats.append({'type': 'Feature', 'geometry': geom, 'properties': {
                'layer': e['layer'], 'action': action, 'date': e['date'],
                'name': name if isinstance(name, str) else None, 'kind': kind,
                'source': e['label']}})
        if truncated:
            break
    return feats, truncated


def photo_features(layer_features) -> List[Dict[str, Any]]:
    """Photo points with only PHOTO_PROPS. ``sender`` is never copied."""
    out = []
    for layer, feats in layer_features.items():
        for f in feats or []:
            if not is_photo(f):
                continue
            geom = f.get('geometry')
            pt = representative_point(geom)
            if pt is None:
                continue
            props = f.get('properties') or {}
            out.append({'type': 'Feature',
                        'geometry': {'type': 'Point', 'coordinates': [pt[0], pt[1]]},
                        'properties': {
                            'name': props.get('name') if isinstance(props.get('name'), str) else None,
                            'timestamp': str(props.get('timestamp') or ''),
                            'layer': layer,
                            'image_url': props.get('image_url'),
                            'map_url': f"../webmap/?lat={pt[1]:.6f}&lng={pt[0]:.6f}&zoom=17",
                        }})
    out.sort(key=lambda f: f['properties']['timestamp'], reverse=True)
    return out


# ── products ────────────────────────────────────────────────────────────────

def _outlet_inputs(asset) -> List[str]:
    names = list(asset.get('in_layers') or [])
    if asset.get('in_layer'):
        names.append(asset['in_layer'])
    return names


def _outlet_href(outlet_dir: Path, name: str, fetch_type: str) -> Optional[str]:
    if fetch_type in ('console', 'html'):
        return f"../{name}/public/" if (outlet_dir / 'public').is_dir() else None
    if (outlet_dir / 'index.html').exists():
        return f"../{name}/"
    pdfs = sorted(outlet_dir.glob('*.pdf'))
    if pdfs:
        return f"../{name}/{pdfs[0].name}"
    return None


def products(config: Dict[str, Any], self_name: str) -> List[Dict[str, Any]]:
    assets = config.get('assets') or {}
    outlets_dir = versioning.atlas_path(config, 'outlets')
    layers_dir = _layers_dir(config)
    published = set(atlas_store.publishable_outlets(config))
    rows = []
    for name, asset in sorted(assets.items()):
        if asset.get('type') != 'outlet' or name == self_name:
            continue
        fetch_type = (asset.get('config') or {}).get('fetch_type') or ''
        if fetch_type in PIPELINE_FETCH_TYPES:
            continue
        access = atlas_store.normalize_access(
            asset.get('access', (asset.get('config') or {}).get('access')))
        public = atlas_store.is_public(access)
        # Most restrictive tier names who can see it.
        tier = 'public' if public else next((t for t in ('internal', 'admin', 'technical') if t in access), access[0])
        outlet_dir = outlets_dir / name
        built = _newest_mtime(outlet_dir)
        inputs = _outlet_inputs(asset)
        if built is None:
            status = 'not built'
        elif not inputs:
            status = 'built'
        else:
            newest_input = max((m for m in (_newest_mtime(layers_dir / l) for l in inputs) if m is not None),
                               default=None)
            status = 'needs rebuild' if newest_input and newest_input > built else 'current'
        href = None
        if public and name in published and built is not None:
            href = _outlet_href(outlet_dir, name, fetch_type)
        rows.append({'name': name, 'title': asset.get('title') or name.replace('_', ' '),
                     'access': ACCESS_LABELS.get(tier, tier.title()), 'built': _iso(built),
                     'status': status, 'href': href})
    return rows


# ── assemble ───────────────────────────────────────────────────────────────

def build_summary(config: Dict[str, Any], outlet_name: str) -> Tuple[Dict[str, Any], list, list]:
    """Return (summary, change_features, photo_features)."""
    settings = outlet_settings(config, outlet_name)
    publics = public_layers(config)
    vector_names = [n for n, l in publics.items() if l.get('geometry_type') != 'raster']
    layer_features = {n: load_layer(config, n) for n in vector_names}
    layer_features = {n: f for n, f in layer_features.items() if f is not None}

    versions = (config.get('dataswale') or {}).get('versions') or []
    since = delta_provenance.latest_version(config)

    entries = scan_changes(config, vector_names, since, settings['bulk_threshold'])
    regen = regenerated_layers(config, publics.keys(), since, {e['layer'] for e in entries})
    changes = summarize_changes(entries, regen)
    cfeats, truncated = change_features(entries, layer_features, settings['max_change_features'])
    photos = photo_features(layer_features)

    tiles = headlines(settings['headlines'], layer_features)
    n_edits = sum(e['count'] for e in entries if not e['bulk'])
    system_tiles = [
        {'label': 'Field photos', 'value': len(photos)},
        {'label': 'Edits since last release', 'value': n_edits},
        {'label': 'Releases published', 'value': len(versions)},
    ]
    tiles += [t for t in system_tiles if t['value']]

    name = config.get('name', '')
    summary = {
        'atlas': name,
        'title': config.get('title') or name.replace('_', ' ').title(),
        'tagline': config.get('tagline'),
        'about': config.get('about') or config.get('description'),
        'logo_url': config.get('logo_url'),
        'contact_email': config.get('contact_email'),
        'photo_email': config.get('photo_email') or f"{name}@fireatlas.org",
        'built_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'since_version': since,
        'since_date': _version_iso(since),
        'headlines': tiles,
        'changes': changes,
        'change_map_truncated': truncated,
        'change_bbox': bbox_of(cfeats) or _atlas_bbox(config),
        'atlas_bbox': _atlas_bbox(config),
        'photos': [f['properties'] | {'coordinates': f['geometry']['coordinates']}
                   for f in photos[:settings['photo_strip_count']]],
        'photo_count': len(photos),
        'products': products(config, outlet_name),
        'context_layers': _context_layers(config, publics),
    }
    return summary, cfeats, photos


def _atlas_bbox(config) -> Optional[List[float]]:
    b = (config.get('dataswale') or {}).get('bbox')
    if not b:
        return None
    return [b['west'], b['south'], b['east'], b['north']]


def _context_layers(config, publics) -> List[Dict[str, str]]:
    """Public boundary-type layers drawn under the change map, by relative URL."""
    import atlas_catalog
    out = []
    for name in ('boundary', 'regions'):
        if name in publics and (_layers_dir(config) / name / f'{name}.geojson').exists():
            out.append({'layer': name, 'url': atlas_catalog.layer_data_url(name, f'{name}.geojson')})
    return out


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, separators=(',', ':'), default=str)


def outlet_overlook(config: Dict[str, Any], outlet_name: str) -> Path:
    """Materialize the overlook outlet. Uniform asset signature."""
    outpath = versioning.atlas_path(config, 'outlets') / outlet_name
    outpath.mkdir(parents=True, exist_ok=True)
    summary, cfeats, photos = build_summary(config, outlet_name)

    _write_json(outpath / 'overlook.json', summary)
    _write_json(outpath / 'data' / 'changes.geojson', {'type': 'FeatureCollection', 'features': cfeats})
    _write_json(outpath / 'data' / 'photos.geojson', {'type': 'FeatureCollection', 'features': photos})

    templates = versioning.atlas_path(config, version='app') / 'templates'
    (outpath / 'css').mkdir(exist_ok=True)
    (outpath / 'js').mkdir(exist_ok=True)
    shutil.copy(templates / 'css' / 'overlook.css', outpath / 'css' / 'overlook.css')
    shutil.copy(templates / 'js' / 'overlook.js', outpath / 'js' / 'overlook.js')

    page = (templates / 'overlook.html').read_text()
    # `</` is escaped so no string in the data can close the <script> block.
    inline = json.dumps(summary, default=str).replace('</', '<\\/')
    page = page.replace('{title}', html.escape(summary['title']))
    page = page.replace('{summary_json}', inline)
    (outpath / 'index.html').write_text(page)
    logger.info(f"overlook: wrote {outpath} ({len(cfeats)} change features, {len(photos)} photos)")
    return outpath / 'index.html'


asset_methods = {'overlook': outlet_overlook}
