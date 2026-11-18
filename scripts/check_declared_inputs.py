#!/usr/bin/env python3
"""Differential check of declared inputs (Phase 4c): does an asset build the same
outputs from only the files asset_inputs says it reads?

    ./scripts/check_declared_inputs.py kennedy                 # its light outlets
    ./scripts/check_declared_inputs.py kennedy webmap console  # these assets
    ./scripts/check_declared_inputs.py kennedy layer:hydrants  # a delta apply

For each asset, in two read-only sessions (no lock, nothing written back):

1. **full**: the whole atlas hydrated; materialize; hash every file it wrote.
2. **declared**: only ``asset_inputs.asset_inputs()`` hydrated; materialize; hash.

The outputs must match file for file. A difference — or a materialize that
fails only in the declared workspace — means the declaration misses an input.
That catches reads by any library, GDAL and QGIS included, which an audit hook
cannot see; the hook is kept only to *name* missing files Python itself opened.

Run on the box against the staging substrate (same env as
staging_rehearsal.sh). Exit status 1 if any asset differs.
"""
import argparse
import hashlib
import os
import shutil
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "python"))
import asset_inputs  # noqa: E402

# Assets a Lambda would run with declared inputs. QGIS, slides and heavy
# inlets/eddies run on the box worker with a full hydrate, so they need none.
DEFAULT_KINDS = ('webmap', 'webedit', '3dview', 'console')

# Files whose bytes legitimately differ run to run (build stamps, logs).
IGNORE_SUFFIXES = ('.log',)


def snapshot(root: Path) -> dict:
    out = {}
    for dirpath, _dirs, files in os.walk(root, followlinks=False):
        for name in files:
            p = Path(dirpath) / name
            if p.is_symlink():
                continue
            st = p.stat()
            out[p.relative_to(root).as_posix()] = (st.st_size, st.st_mtime_ns)
    return out


def written(root: Path, before: dict) -> dict:
    """{rel: sha256} for every file created or modified since `before`."""
    result = {}
    for rel, stat in snapshot(root).items():
        if before.get(rel) != stat and not rel.endswith(IGNORE_SUFFIXES):
            result[rel] = hashlib.sha256((root / rel).read_bytes()).hexdigest()
    return result


class MissingOpens:
    """Audit hook: files under the workspace Python tried to open that were not there."""

    def __init__(self):
        self.root = None
        self.missing = set()
        sys.addaudithook(self)

    def __call__(self, event, args):
        if self.root is None or event != 'open' or not args:
            return
        path = args[0]
        if not isinstance(path, (str, bytes, os.PathLike)):
            return
        path = os.fsdecode(path)
        if path.startswith(self.root) and not os.path.exists(path):
            mode = args[1] if len(args) > 1 else 'r'
            if not isinstance(mode, str) or 'r' in mode:
                self.missing.add(os.path.relpath(path, self.root))


def run(atlas_name: str, asset: str, workspace: Path, wanted, hook: MissingOpens):
    import atlas
    import atlas_session
    with atlas_session.open_session(atlas_name, purpose=f"check_declared_inputs {asset}",
                                    workspace_root=workspace, wanted=wanted,
                                    read_only=True) as session:
        before = snapshot(session.staging_dir)
        hook.root = str(session.staging_dir) + os.sep
        hook.missing = set()
        error = None
        try:
            if asset.startswith('layer:'):
                import dataswale_geojson
                dataswale_geojson.refresh_vector_layer(session.config, asset[len('layer:'):])
            else:
                atlas.materialize(session.config, asset)
        except Exception:
            error = traceback.format_exc(limit=3)
        finally:
            hook.root = None
        return written(session.staging_dir, before), error, sorted(hook.missing)


def default_assets(config: dict) -> list:
    names = []
    for name, asset in (config.get('assets') or {}).items():
        fetch = (asset.get('config') or {}).get('fetch_type') or asset.get('config_def')
        if asset.get('type') == 'outlet' and (name in DEFAULT_KINDS or fetch in DEFAULT_KINDS):
            names.append(name)
    return sorted(names)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('atlas')
    ap.add_argument('assets', nargs='*')
    ap.add_argument('--work', type=Path, default=Path('/root/check_declared_inputs'),
                    help="scratch root; two workspaces are made under it")
    args = ap.parse_args()

    full_ws, part_ws = args.work / 'full', args.work / 'declared'
    hook = MissingOpens()

    import atlas_session
    with atlas_session.open_session(args.atlas, purpose='check_declared_inputs config',
                                    workspace_root=full_ws, read_only=True) as session:
        config = session.config
    assets = args.assets or default_assets(config)

    failures = 0
    for asset in assets:
        prefixes = (asset_inputs.layer_refresh_inputs(asset[len('layer:'):])
                    if asset.startswith('layer:') else asset_inputs.asset_inputs(config, asset))
        if prefixes is None:
            print(f"  skip  {asset}: full hydrate by declaration")
            continue
        shutil.rmtree(part_ws, ignore_errors=True)   # cold, like a Lambda
        full, full_err, _ = run(args.atlas, asset, full_ws, None, hook)
        part, part_err, missing = run(args.atlas, asset, part_ws,
                                      asset_inputs.wanted(prefixes), hook)
        diffs = sorted(rel for rel in set(full) | set(part) if full.get(rel) != part.get(rel))
        ok = not diffs and (part_err is None or full_err is not None)
        print(f"  {'ok  ' if ok else 'FAIL'}  {asset}: {len(full)} file(s) written, "
              f"declared {len(prefixes)} prefix(es)")
        if not ok:
            failures += 1
            for rel in diffs[:20]:
                state = ('only in full' if rel not in part else
                         'only in declared' if rel not in full else 'differs')
                print(f"          {state}: {rel}")
            if part_err and not full_err:
                print("          fails only with declared inputs:\n" + part_err)
            for rel in missing:
                print(f"          opened but not hydrated: {rel}")
        if full_err:
            print(f"          (fails in the full workspace too — not a declaration problem)\n{full_err}")
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
