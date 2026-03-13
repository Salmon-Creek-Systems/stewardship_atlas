#!/usr/bin/env python3
"""
Local read-only atlas server for demos and offline use.

Serves pre-generated atlas outputs (webmap, runbook, gazetteer, console)
from a locally synced directory. Write operations return 503 read-only responses.

Usage:
    python scripts/serve_local.py --data-dir ~/atlas_demo --atlas scvfd --port 8080

Then open: http://localhost:8080/staging/outlets/html/admin/
"""

import argparse
import json
import re
import sys
from pathlib import Path

try:
    from fastapi import FastAPI, Response
    from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
    from fastapi.staticfiles import StaticFiles
    import uvicorn
except ImportError:
    print("ERROR: fastapi and uvicorn are required.")
    print("  pip3 install --user fastapi uvicorn")
    sys.exit(1)


def build_app(data_dir: Path, atlas: str, port: int) -> FastAPI:
    staging_dir = data_dir / atlas / "staging"
    if not staging_dir.exists():
        print(f"ERROR: {staging_dir} does not exist.")
        print(f"  Run: scripts/sync_atlas_local.sh {atlas} {data_dir}")
        sys.exit(1)

    app = FastAPI(title=f"{atlas} — local demo server")

    READ_ONLY = {"error": "read-only mode", "message": "This atlas is running in read-only demo mode. Write operations are disabled."}

    # ── Read-only API stubs ───────────────────────────────────────────────

    @app.get("/status")
    def status():
        return {"status": "ok", "mode": "read-only"}

    @app.get("/publish-status")
    def publish_status(swale: str = ""):
        return {"status": "idle", "mode": "read-only"}

    # Write operations — return 503
    WRITE_PATHS = [
        "/publish", "/refresh", "/clear_layer/{swalename}/{layer_name}",
        "/reset-staging/{swalename}", "/set-current-version/{swalename}",
        "/delta_upload/{swalename}", "/store/{swalename}",
        "/save_config/{swalename}", "/import_sheet/{swalename}/{layer_name}",
        "/import_gsheet/{swalename}/{layer_name}", "/sql_query/{swalename}",
        "/export_gsheet/{swalename}/{layer_name}",
    ]

    def make_readonly_handler(path):
        async def handler(**kwargs):
            return JSONResponse(READ_ONLY, status_code=503)
        handler.__name__ = f"readonly_{path.replace('/', '_').replace('{', '').replace('}', '')}"
        return handler

    for path in WRITE_PATHS:
        for method in ("get", "post"):
            getattr(app, method)(path)(make_readonly_handler(path))

    # ── Console HTML rewriting ────────────────────────────────────────────
    # The console HTML has baseurl/atlasappport baked in pointing at production.
    # Rewrite them to localhost so the console's API calls come back to us.

    CONSOLE_PATTERN = re.compile(
        r'"baseurl"\s*:\s*"[^"]*"',
    )
    PORT_PATTERN = re.compile(
        r'"atlasappport"\s*:\s*\d+',
    )

    def rewrite_console_html(html: str) -> str:
        html = CONSOLE_PATTERN.sub(f'"baseurl": "http://localhost"', html)
        html = PORT_PATTERN.sub(f'"atlasappport": {port}', html)
        return html

    # Serve console HTML with URL rewriting
    for role in ("admin", "internal", "public"):
        console_path = staging_dir / "outlets" / "html" / role / "index.html"

        def make_console_handler(path=console_path):
            @app.get(f"/staging/outlets/html/{path.parent.name}/")
            @app.get(f"/staging/outlets/html/{path.parent.name}/index.html")
            def serve_console():
                if not path.exists():
                    return Response(status_code=404)
                html = path.read_text()
                return HTMLResponse(rewrite_console_html(html))
            return serve_console

        make_console_handler()

    # ── Static file serving ───────────────────────────────────────────────
    # Mount the staging directory so all other paths resolve from the filesystem.
    # This covers: outlets/webmap, outlets/gazetteer, outlets/runbook,
    # layers/*.geojson, local/css, local/logo, etc.

    app.mount("/staging", StaticFiles(directory=str(staging_dir), html=True), name="staging")

    @app.get("/")
    def root():
        return HTMLResponse(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>{atlas} — local demo</title>
<style>body{{font-family:system-ui;max-width:600px;margin:60px auto;padding:0 20px}}</style>
</head><body>
<h2>{atlas} — local demo</h2>
<ul>
  <li><a href="/staging/outlets/html/admin/">Admin console</a></li>
  <li><a href="/staging/outlets/webmap/">Webmap</a></li>
  <li><a href="/staging/outlets/gazetteer/">Gazetteer</a></li>
  <li><a href="/staging/outlets/runbook/">Runbook</a></li>
</ul>
<p style="color:#888;font-size:14px">Read-only mode. Write operations are disabled.</p>
</body></html>""")

    return app


def main():
    parser = argparse.ArgumentParser(description="Local read-only atlas server")
    parser.add_argument("--data-dir", required=True, type=Path, help="Directory containing synced atlas data")
    parser.add_argument("--atlas", required=True, help="Atlas name (e.g. scvfd)")
    parser.add_argument("--port", type=int, default=8080, help="Local port (default: 8080)")
    args = parser.parse_args()

    app = build_app(args.data_dir.expanduser(), args.atlas, args.port)

    print(f"\n  Atlas:  {args.atlas}")
    print(f"  Data:   {args.data_dir.expanduser() / args.atlas}")
    print(f"  URL:    http://localhost:{args.port}/")
    print(f"  Console: http://localhost:{args.port}/staging/outlets/html/admin/\n")

    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
