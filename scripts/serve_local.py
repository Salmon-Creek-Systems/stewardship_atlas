#!/usr/bin/env python3
"""
Serve an offline atlas bundle for a read-only demo.

    python3 scripts/serve_local.py ~/atlas_demo [--port 8080]

Serves <demo_dir> at the root, so /<atlas>/staging/... and /local/... match
production. Supports HTTP Range requests (COG and PMTiles readers need them).
Anything under /_api/ — where offline_bundle.py points the API — and any
non-GET request gets a 503 "offline demo" response. Stdlib only.

The port must match the one given to offline_bundle.py (URLs are baked in).
"""

import argparse
import json
import os
import re
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

OFFLINE = json.dumps({"error": "offline demo",
                      "detail": "Offline demo: changes are not saved."}).encode()


class DemoHandler(SimpleHTTPRequestHandler):

    def _refuse(self):
        self.send_response(503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(OFFLINE)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(OFFLINE)

    do_POST = do_PUT = do_DELETE = do_PATCH = _refuse

    def do_GET(self):
        if self.path.startswith("/_api/"):
            return self._refuse()
        match = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range", ""))
        path = self.translate_path(self.path)
        if not match or not os.path.isfile(path):
            return super().do_GET()

        size = os.path.getsize(path)
        first, last = match.groups()
        if first:
            start, end = int(first), min(int(last) if last else size - 1, size - 1)
        else:  # suffix range: last N bytes
            start, end = max(size - int(last or 0), 0), size - 1
        if start > end:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            self.wfile.write(f.read(end - start + 1))

    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("demo_dir", type=Path)
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()

    root = args.demo_dir.expanduser().resolve()
    atlases = sorted(p.parent.name for p in root.glob("*/staging"))
    print(f"Serving {root} on http://localhost:{args.port}/  (Ctrl-C to stop)")
    for a in atlases:
        print(f"  console: http://localhost:{args.port}/{a}/staging/outlets/console/public/")
        print(f"  webmap:  http://localhost:{args.port}/{a}/staging/outlets/webmap/")
        print(f"  3D:      http://localhost:{args.port}/{a}/staging/outlets/3dview/")
    handler = partial(DemoHandler, directory=str(root))
    ThreadingHTTPServer(("127.0.0.1", args.port), handler).serve_forever()


if __name__ == "__main__":
    main()
