#!/bin/bash
# Run the 3D view's route helpers (buildRouteLegs, sampleAlong, smoothedAlong, easeBearing)
# in node, without a browser. Extracts _ROUTE_JS from python/3dview.py with placeholders filled.
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT=$(mktemp -t route_js).js
python3 - "$OUT" <<'PY'
import ast, sys
# Read the literals with ast: importing 3dview pulls in utils (gspread, etc.).
consts = {}
for node in ast.parse(open('python/3dview.py').read()).body:
    if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', '') in ('_ROUTE_JS', '_ROUTE_DEFAULTS'):
        consts[node.targets[0].id] = ast.literal_eval(node.value)
d = consts['_ROUTE_DEFAULTS']
js = (consts['_ROUTE_JS'].replace('__ROUTE_URLS__', '{}')
      .replace('__DEF_HEIGHT__', str(d['height']))
      .replace('__DEF_ANGLE__', str(d['angle']))
      .replace('__DEF_SECS__', str(d['secs'])))
open(sys.argv[1], 'w').write('async function wrapper(map, maplibregl) {' + js + '}\n')
PY
node scripts/js_checks/route_unit.js "$OUT"
rm -f "$OUT"
