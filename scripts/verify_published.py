#!/usr/bin/env python3
"""Check a published atlas actually serves, from the outside.

    ATLAS_CDN_URL=https://d1y2f6bmf8ds3m.cloudfront.net \
        python3 scripts/verify_published.py kennedy

Every bug in the Phase 3 read-path work was found by running against a real
bucket, and none by unit tests. The fixtures were always simpler than
production in the dimension that mattered — one version, relative URLs where
a wrong directory and a right one are the same empty string, one code path
filtered but not its twin. This checks the things a fixture cannot:

1. every allowlisted outlet's entry point returns 200;
2. no protected role variant (admin/internal/technical) is readable;
3. **every layer URL the published webmap actually emits resolves** — the
   join, not the two halves separately. A URL can be individually plausible
   at both ends and still point at nothing, which is exactly what shipped
   three times;
4. no layer the config declares protected is readable under the public prefix.

Exits non-zero if any check fails.
"""

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

REPO = Path(__file__).resolve().parent.parent

RED, GREEN, YELLOW, BOLD, OFF = '\033[31m', '\033[32m', '\033[33m', '\033[1m', '\033[0m'


def status(url, timeout=30):
    """HTTP status for a URL, or 0 if it could not be reached."""
    try:
        with urlopen(Request(url, method='GET'), timeout=timeout) as response:
            return response.status
    except HTTPError as exc:
        return exc.code
    except (URLError, OSError):
        return 0


def fetch(url, timeout=30):
    try:
        with urlopen(Request(url), timeout=timeout) as response:
            return response.read().decode('utf-8', 'replace')
    except Exception:
        return ''


def load_config(atlas):
    path = REPO / 'configuration' / f'{atlas}.geojson'
    doc = json.loads(path.read_text())
    return doc['features'][0]['properties'] if 'features' in doc else doc


def layer_urls_from(html, base):
    """The layer URLs a published webmap emits, resolved against its own page.

    Read out of the served HTML rather than reconstructed, because the point is
    to test what the browser will actually request.
    """
    match = re.search(r'const MAP_CONFIG = (\{.*?\});\s*\n', html, re.S)
    if not match:
        return None
    try:
        config = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    found = set()
    for source in (config.get('style', {}).get('sources') or {}).values():
        if not isinstance(source, dict):
            continue
        for key in ('data', 'url'):
            value = source.get(key)
            if isinstance(value, str) and value.startswith('../'):
                found.add(urljoin(base, value))
            # COG sources carry a cog:// prefix and a #color: fragment.
            elif isinstance(value, str) and value.startswith('cog://../'):
                rest = value[len('cog://'):].split('#', 1)[0]
                found.add(urljoin(base, rest))
    return sorted(found)


def main():
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} <atlas>", file=sys.stderr)
        return 2
    atlas = sys.argv[1]
    cdn = os.environ.get('ATLAS_CDN_URL', 'https://next.fireatlas.org').rstrip('/')

    props = load_config(atlas)
    allowlist = (props.get('cloud') or {}).get('outlets')
    if allowlist is None:
        print(f"{RED}{atlas} has no cloud.outlets allowlist — it publishes nothing{OFF}")
        return 1

    root = f"{cdn}/{atlas}/current"
    ok = True
    print(f"{BOLD}verifying {atlas} at {cdn}{OFF}")

    # 1. public entry points
    for outlet in allowlist:
        # html and console emit all four role variants as subdirectories and
        # have no root index, so the public entry point is public/.
        entry = f"{outlet}/public" if outlet in ('html', 'console') else outlet
        code = status(f"{root}/outlets/{entry}/")
        mark = GREEN if code == 200 else RED
        print(f"  {mark}{code}{OFF}  outlets/{entry}/")
        ok &= code == 200

    # 2. role variants must never be public
    for outlet in ('html', 'console'):
        if outlet not in allowlist:
            continue
        for variant in ('admin', 'internal', 'technical'):
            code = status(f"{root}/outlets/{outlet}/{variant}/")
            if code == 200:
                print(f"  {RED}LEAK{OFF} outlets/{outlet}/{variant}/ is public")
                ok = False

    # 3. the join: every layer URL the webmap emits
    if 'webmap' in allowlist:
        base = f"{root}/outlets/webmap/"
        urls = layer_urls_from(fetch(base), base)
        if urls is None:
            print(f"  {YELLOW}note{OFF}  could not read MAP_CONFIG from the published webmap")
        elif not urls:
            print(f"  {YELLOW}note{OFF}  webmap emits no relative layer URLs")
        else:
            bad = [(u, status(u)) for u in urls]
            bad = [(u, c) for u, c in bad if c != 200]
            for url, code in bad:
                print(f"  {RED}{code}{OFF}  BROKEN {url[len(cdn) + 1:]}")
            if not bad:
                print(f"  {GREEN}200{OFF}  all {len(urls)} layer source(s) resolve")
            ok &= not bad

    # 4. protected layers must not be readable
    for layer in (props.get('dataswale') or {}).get('layers') or []:
        access = layer.get('access')
        if not access or 'public' in access or 'shareable' in access:
            continue
        name = layer['name']
        code = status(f"{root}/layers/{name}/{name}.geojson")
        if code == 200:
            print(f"  {RED}LEAK{OFF} protected layer {name} is public")
            ok = False

    print(f"{GREEN}{BOLD}verified{OFF}" if ok else f"{RED}{BOLD}FAILED{OFF}")
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
