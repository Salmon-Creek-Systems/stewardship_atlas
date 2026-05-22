#!/usr/bin/env python3
"""
Ingest the PNW Biochar Atlas CSV into a tabular GeoJSON layer.

Source: BiocharData_PNWComputed.csv from github.com/phillipsclaire/pnwbiochar
        (public domain federal work product)

Writes: configuration/seed_data/biochar_properties_pnw.geojson

Usage:
    python scripts/ingest_biochar_pnw.py /path/to/BiocharData_PNWComputed.csv
    python scripts/ingest_biochar_pnw.py /path/to/csv --output /path/to/output.geojson
"""

import sys
import csv
import json
import re
import argparse
from pathlib import Path

# Map original CSV column names → canonical snake_case names.
# Covers both likely raw column names and computed/derived variants.
# Any column not in this map is passed through with light normalization.
COLUMN_MAP = {
    # Identity
    'SampleID': 'sample_id',
    'sampleid': 'sample_id',
    'Sample ID': 'sample_id',
    # Source / feedstock
    'Feedstock': 'feedstock',
    'feedstock': 'feedstock',
    'FeedstockType': 'feedstock_type',
    'feedstock_type': 'feedstock_type',
    # Production
    'PyrolysisTemp': 'production_temp_c',
    'pyrolysis_temp': 'production_temp_c',
    'Pyrolysis Temperature': 'production_temp_c',
    'ProductionTemp': 'production_temp_c',
    'production_temp': 'production_temp_c',
    'HoldTime': 'hold_time_min',
    'hold_time': 'hold_time_min',
    # Chemistry
    'pH': 'biochar_ph',
    'ph': 'biochar_ph',
    'biochar_ph': 'biochar_ph',
    'AshContent': 'ash_content_pct',
    'ash_content': 'ash_content_pct',
    'Ash': 'ash_content_pct',
    'Carbon': 'carbon_pct',
    'carbon': 'carbon_pct',
    'C': 'carbon_pct',
    'Hydrogen': 'hydrogen_pct',
    'hydrogen': 'hydrogen_pct',
    'H': 'hydrogen_pct',
    'CH_Ratio': 'c_h_ratio',
    'ch_ratio': 'c_h_ratio',
    'C:H': 'c_h_ratio',
    'C/H': 'c_h_ratio',
    'Nitrogen': 'nitrogen_pct',
    'nitrogen': 'nitrogen_pct',
    'N': 'nitrogen_pct',
    'CEC': 'cec_cmol_kg',
    'cec': 'cec_cmol_kg',
    'SurfaceArea': 'surface_area_m2_g',
    'surface_area': 'surface_area_m2_g',
    # Physical
    'ParticleSize': 'particle_size_mm',
    'particle_size': 'particle_size_mm',
    'BulkDensity': 'bulk_density_g_cm3',
    'bulk_density': 'bulk_density_g_cm3',
    # Computed / suitability
    'LimingScore': 'liming_score',
    'liming_score': 'liming_score',
    'LimingPotential': 'liming_potential',
    'liming_potential': 'liming_potential',
}


def normalize_col(name: str) -> str:
    """Light normalization for columns not in COLUMN_MAP."""
    s = re.sub(r'[^a-zA-Z0-9]+', '_', name.strip()).lower().strip('_')
    return s or 'col'


def coerce_number(val: str):
    """Return float, int, or original string."""
    if val is None or val.strip() == '' or val.strip().lower() in ('na', 'n/a', 'null', 'none', '-'):
        return None
    try:
        f = float(val)
        return int(f) if f == int(f) else f
    except ValueError:
        return val.strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('csv_path', help='Path to BiocharData_PNWComputed.csv')
    parser.add_argument('--output', default=None)
    args = parser.parse_args()

    csv_path = Path(args.csv_path)
    if not csv_path.exists():
        print(f"ERROR: file not found: {csv_path}")
        sys.exit(1)

    repo_root = Path(__file__).parent.parent
    out_path = Path(args.output) if args.output else (
        repo_root / 'configuration' / 'seed_data' / 'biochar_properties_pnw.geojson'
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    features = []
    with open(csv_path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        raw_cols = reader.fieldnames or []
        col_mapping = {c: COLUMN_MAP.get(c, normalize_col(c)) for c in raw_cols}
        print(f"Columns: {len(raw_cols)}")
        print("  Mapping:")
        for orig, mapped in col_mapping.items():
            marker = '*' if orig in COLUMN_MAP else ' '
            print(f"    {marker} {orig!r:30s} → {mapped!r}")

        for i, row in enumerate(reader):
            props = {}
            for orig_col, val in row.items():
                mapped = col_mapping.get(orig_col, normalize_col(orig_col))
                props[mapped] = coerce_number(val)
            # Assign a stable integer id
            props['biochar_id'] = i + 1
            features.append({'type': 'Feature', 'geometry': None, 'properties': props})

    fc = {'type': 'FeatureCollection', 'features': features}
    with open(out_path, 'w') as f:
        json.dump(fc, f, indent=2)

    print(f"\nWrote {len(features)} biochar records → {out_path}")
    # Quick sanity check
    ph_vals = [feat['properties'].get('biochar_ph') for feat in features
               if isinstance(feat['properties'].get('biochar_ph'), (int, float))]
    if ph_vals:
        print(f"Biochar pH range: {min(ph_vals):.1f} – {max(ph_vals):.1f}")


if __name__ == '__main__':
    main()
