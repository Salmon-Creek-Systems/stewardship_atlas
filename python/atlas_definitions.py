"""
Dagster definitions entry point for Stewardship Atlas.

Discovers all atlases under SWALES_ROOT and builds a unified Dagster
asset graph with each atlas namespaced by its slug.

Run locally (Dagster UI on http://localhost:3000):
    cd python
    SWALES_ROOT=/root/swales_dev dagster dev -f atlas_definitions.py

Run headless (webserver only):
    SWALES_ROOT=/root/swales_dev dagster-webserver -f atlas_definitions.py

To run a full atlas refresh from the CLI:
    dagster asset materialize --select "kennedy/*" -f atlas_definitions.py

DAGSTER_HOME controls where Dagster stores run metadata (defaults to ~/.dagster).
Set it to a path under SWALES_ROOT to keep everything co-located:
    export DAGSTER_HOME=/root/swales_dev/.dagster
"""
import json
import os
import sys
from pathlib import Path

# Ensure the python/ directory is on sys.path regardless of Dagster's working directory.
sys.path.insert(0, str(Path(__file__).parent))

from atlas_dagster import build_definitions

swales_root = Path(os.environ['SWALES_ROOT'])

configs = []
for atlas_dir in sorted(swales_root.iterdir()):
    config_path = atlas_dir / 'staging' / 'atlas_config.json'
    if config_path.is_file():
        try:
            configs.append(json.load(open(config_path)))
        except Exception as e:
            print(f"Warning: skipping {atlas_dir.name} — could not load config: {e}")

defs = build_definitions(configs)
