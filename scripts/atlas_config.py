#!/usr/bin/env python3
"""Read and write an atlas's source config, which lives in S3 (decided 2026-11-17).

The source is ``{atlas}/staging/atlas.geojson`` in the private bucket. The repo's
``configuration/{atlas}.geojson`` copies are retired at cutover; ``shared_*.json``
and the starters stay in git (they are code).

    python3 scripts/atlas_config.py pull kennedy                  # -> ./kennedy.geojson
    python3 scripts/atlas_config.py pull kennedy -o /tmp/k.geojson
    python3 scripts/atlas_config.py push kennedy kennedy.geojson -m "add culverts layer"
    python3 scripts/atlas_config.py push kennedy kennedy.geojson -m "..." --rebuild --yes
    python3 scripts/atlas_config.py check kennedy.geojson         # validate only

``push`` validates (``config_checks``, the same rules the tests apply), takes the
atlas's lock through a session that hydrates only the config files, prints a diff
against S3, writes the file, appends a line to ``staging/config_changes.log`` and,
with ``--rebuild``, rebuilds ``atlas_config.json`` (``config_only``). It refuses an
atlas that is not seeded yet: seed first, then push its config.

Needs boto3 and AWS credentials for the bucket (the box's role, or ``AWS_PROFILE``).
``ATLAS_WORKSPACE_ROOT`` defaults to a temporary directory.
"""
import argparse
import difflib
import getpass
import hashlib
import json
import os
import socket
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "python"))
import config_checks  # noqa: E402

SOURCE_REL = 'atlas.geojson'
LOG_REL = 'config_changes.log'
CONFIG_REL = 'atlas_config.json'
CONFIG_DIR = Path(__file__).parent.parent / 'configuration'


def problems_in(text: str) -> list:
    try:
        props = json.loads(text)['features'][0]['properties']
    except (ValueError, KeyError, IndexError, TypeError) as e:
        return [f"not a single-feature atlas GeoJSON: {e}"]
    return config_checks.atlas_config_problems(props, config_checks.load_shared(CONFIG_DIR))


def _bucket():
    import atlas_store
    return atlas_store.cloud_settings({})['private_bucket']


def read_source(atlas: str, client=None) -> str:
    """The current source text in S3, byte for byte (formatting kept)."""
    import atlas_store
    import atlas_workspace
    client = client or atlas_store._s3()
    key = atlas_workspace.staging_prefix(atlas) + SOURCE_REL
    body = client.get_object(Bucket=_bucket(), Key=key)['Body'].read()
    return body.decode('utf-8')


def log_line(atlas: str, message: str, old: str, new: str) -> str:
    def h(t):
        return hashlib.sha256(t.encode('utf-8')).hexdigest()[:12] if t else '-'
    who = f"{os.environ.get('SUDO_USER') or getpass.getuser()}@{socket.gethostname()}"
    when = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    return f"{when}\t{who}\t{atlas}\t{h(old)} -> {h(new)}\t{message}\n"


def push(atlas: str, path: Path, message: str, rebuild: bool, yes: bool) -> int:
    new = path.read_text()
    found = problems_in(new)
    if found:
        print("refusing to push — config problems:", *found, sep="\n  ", file=sys.stderr)
        return 1

    import atlas_session
    wanted = {SOURCE_REL, CONFIG_REL, LOG_REL}
    with atlas_session.open_session(atlas, purpose=f"atlas_config push: {message}",
                                    wanted=lambda rel: rel in wanted) as session:
        src = session.staging_dir / SOURCE_REL
        old = src.read_text() if src.exists() else ''
        if old == new:
            print(f"{atlas}: no change")
            return 0
        diff = difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                    f"s3:{atlas}/staging/{SOURCE_REL}", str(path))
        sys.stdout.writelines(diff)
        if not yes and input(f"\nwrite this to {atlas}? [y/N] ").strip().lower() != 'y':
            raise SystemExit("aborted; nothing written")  # raising discards the session
        src.write_text(new)
        with open(session.staging_dir / LOG_REL, 'a') as f:
            f.write(log_line(atlas, message, old, new))
        if rebuild:
            import atlas as atlas_module  # `atlas` is the atlas name here
            atlas_module.build_atlas_from_geojson(src, config_only=True,
                                                  data_root=session.workspace_root)
    print(f"{atlas}: pushed{' and rebuilt atlas_config.json' if rebuild else ''}; "
          f"written back {session.written}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('pull', help="copy the S3 source to a local file")
    p.add_argument('atlas')
    p.add_argument('-o', '--out', type=Path)
    p = sub.add_parser('push', help="validate a local file and write it to S3")
    p.add_argument('atlas')
    p.add_argument('path', type=Path)
    p.add_argument('-m', '--message', required=True, help="one line for config_changes.log")
    p.add_argument('--rebuild', action='store_true', help="also run a config_only rebuild")
    p.add_argument('--yes', action='store_true', help="don't ask before writing")
    p = sub.add_parser('check', help="validate a local file only")
    p.add_argument('path', type=Path)
    args = ap.parse_args()

    if args.cmd == 'check':
        found = problems_in(args.path.read_text())
        print("ok" if not found else "\n".join(found))
        return 1 if found else 0

    os.environ.setdefault('ATLAS_WORKSPACE_ROOT', tempfile.mkdtemp(prefix='atlas_config_'))
    if args.cmd == 'pull':
        out = args.out or Path(f"{args.atlas}.geojson")
        out.write_text(read_source(args.atlas))
        print(out)
        return 0
    return push(args.atlas, args.path, args.message, args.rebuild, args.yes)


if __name__ == '__main__':
    sys.exit(main())
