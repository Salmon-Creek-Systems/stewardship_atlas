"""Delta provenance: who or what wrote a delta, and which release it belongs to.

Every delta records where it came from, in two places:

1. **The filename** — ``{source}__{YYYYmmdd_HHMMSS}__{action}.geojson``. The
   first field is a *source token*: the inlet asset that wrote it, or one of
   the interactive sources in ``SOURCES`` (``webedit``, ``email_photo``,
   ``sheet_import``, ...). Before this module, every non-inlet write used the
   placeholder ``assetless``. That name is now refused for new deltas; old files
   that carry it are read as ``source = "unrecorded"``.

2. **A ``provenance`` member** on the delta's FeatureCollection (a GeoJSON
   foreign member, which GDAL/DuckDB ignore). It carries the source, the asset
   (if any), the action, the layer, the feature count, the UTC write time and —
   the part that answers "what changed since the last publish" — the
   ``base_version``: the newest published version at the moment the delta was
   written. A delta with ``base_version == V`` belongs to the release after V;
   ``None`` means the atlas had never been published.

Stdlib only, so the overlook outlet and tests can use it without DuckDB/GDAL.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

# Interactive / API sources and the label a visitor sees for them. Inlet assets
# use their own asset name as the source token and are labelled by it.
SOURCES: Dict[str, str] = {
    'webedit': 'web edits',
    'webedit_upload': 'file upload (web editor)',
    'reshape': 'reshaped in the web editor',
    'console_upload': 'file upload (console)',
    'sheet_import': 'spreadsheet import',
    'gsheet_import': 'spreadsheet import',
    'email_photo': 'field photos (email)',
    'web_photo': 'field photos (web upload)',
    's3_photo_import': 'field photos (batch import)',
    'comment': 'comments',
    'move': 'moved between layers',
    'create': 'atlas creation (boundary import)',
    'api_store': 'API (store)',
    'api': 'API (unlabeled client)',
}

PHOTO_SOURCES = frozenset({'email_photo', 'web_photo', 's3_photo_import'})

# Read-only alias for deltas written before provenance was recorded.
LEGACY_SOURCE = 'assetless'
UNRECORDED = 'unrecorded'

_TOKEN_RE = re.compile(r'^[a-z0-9][a-z0-9_\-]*$')
TIMESTAMP_FORMAT = '%Y%m%d_%H%M%S'
VERSION_FORMAT = '%Y-%m-%d_%H-%M-%S'


def validate_source(source: Optional[str]) -> str:
    """Return ``source`` if it is a usable filename token, else raise ValueError.

    A token may not contain ``__`` (the filename separator), may not be empty,
    and may not be the legacy placeholder — a delta must say where it came from.
    """
    if not source or not isinstance(source, str):
        raise ValueError("A delta must name its source (e.g. 'webedit', an inlet asset name)")
    if source == LEGACY_SOURCE:
        raise ValueError("'assetless' is not a source; name the real one")
    if '__' in source or not _TOKEN_RE.match(source):
        raise ValueError(f"Invalid delta source token: {source!r}")
    return source


def latest_version(config: Dict[str, Any]) -> Optional[str]:
    """Newest published version named in the config, or None if never published."""
    versions = (config.get('dataswale') or {}).get('versions') or []
    parsed = [v for v in versions if parse_version(v) is not None]
    return max(parsed, key=parse_version) if parsed else None


def parse_version(version: str) -> Optional[datetime]:
    try:
        return datetime.strptime(version, VERSION_FORMAT)
    except (TypeError, ValueError):
        return None


def parse_timestamp(ts: str) -> Optional[datetime]:
    """Parse a delta filename timestamp; tolerates a ``_N`` collision suffix."""
    try:
        return datetime.strptime(ts[:15], TIMESTAMP_FORMAT)
    except (TypeError, ValueError):
        return None


def record(config: Dict[str, Any], *, source: str, action: str, layer: str,
           feature_count: int, asset: Optional[str] = None,
           detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build the provenance member for a new delta."""
    rec = {
        'source': validate_source(source),
        'asset': asset,
        'action': action,
        'layer': layer,
        'feature_count': feature_count,
        'written_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'base_version': latest_version(config),
    }
    if detail:
        rec['detail'] = detail
    return rec


def parse_filename(path: Path) -> Dict[str, Any]:
    """Split ``{source}__{ts}__{action}.ext`` into its parts."""
    parts = Path(path).stem.split('__')
    if len(parts) != 3:
        return {'source': UNRECORDED, 'timestamp': None, 'action': None}
    source, ts, action = parts
    return {
        'source': UNRECORDED if source == LEGACY_SOURCE else source,
        'timestamp': parse_timestamp(ts),
        'action': action,
    }


def read(path: Path, fc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Provenance for a delta file: the embedded member, completed from the filename.

    ``fc`` may be passed when the caller has already loaded the file.
    """
    info = parse_filename(path)
    if fc is None:
        try:
            with open(path) as f:
                fc = json.load(f)
        except (OSError, ValueError):
            fc = {}
    member = fc.get('provenance') if isinstance(fc, dict) else None
    out = dict(info)
    if isinstance(member, dict):
        out.update({k: v for k, v in member.items() if v is not None})
    out.setdefault('base_version', None)
    out['recorded'] = isinstance(member, dict)
    return out


def label(source: str, asset: Optional[str] = None) -> str:
    """Visitor-facing label for a source token."""
    if source in SOURCES:
        return SOURCES[source]
    if source == UNRECORDED:
        return 'edits (source not recorded)'
    return f"{(asset or source).replace('_', ' ')}"


def in_release_after(prov: Dict[str, Any], since_version: Optional[str]) -> bool:
    """True when a delta belongs to the release after ``since_version``.

    Uses the recorded ``base_version`` when there is one: the delta was written
    against that version, so it belongs after it. Older deltas fall back to the
    filename timestamp compared with the version timestamp.
    """
    if since_version is None:
        return True
    if prov.get('recorded') and 'base_version' in prov:
        base = prov.get('base_version')
        if base is None:
            return False  # written before the very first publish
        b, s = parse_version(base), parse_version(since_version)
        if b is not None and s is not None:
            return b >= s
    ts, s = prov.get('timestamp'), parse_version(since_version)
    return ts is not None and s is not None and ts > s


def applied_deltas(deltas_root: Path, layers: Optional[List[str]] = None) -> Iterator[Dict[str, Any]]:
    """Yield ``{layer, path, provenance, fc}`` for every archived (applied) delta.

    Applied deltas live in ``deltas/{layer}/work/``; pending ones (not yet in
    the layer file) are skipped on purpose.
    """
    deltas_root = Path(deltas_root)
    if not deltas_root.exists():
        return
    for layer_dir in sorted(p for p in deltas_root.iterdir() if p.is_dir()):
        if layers is not None and layer_dir.name not in layers:
            continue
        work = layer_dir / 'work'
        if not work.is_dir():
            continue
        for path in sorted(work.glob('*.geojson')):
            try:
                with open(path) as f:
                    fc = json.load(f)
            except (OSError, ValueError):
                continue
            yield {'layer': layer_dir.name, 'path': path, 'fc': fc,
                   'provenance': read(path, fc)}
