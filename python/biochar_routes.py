"""
FastAPI router for biochar suitability endpoints (/api/biochar/*).

Wires the dst_match_point calculation to HTTP for the abi_demo atlas.
Add to webapp.py with: app.include_router(biochar_router)
"""

import json
import os
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import sys
sys.path.insert(0, str(Path(__file__).parent))
from eddies import dst_match_point

logger = logging.getLogger(__name__)

SWALES_ROOT = os.environ.get('SWALES_ROOT', '/root/swales_dev')
BIOCHAR_ATLAS = 'abi_demo'

biochar_router = APIRouter(prefix="/api/biochar", tags=["biochar"])

GOALS = [
    {
        'id': 'raise_ph',
        'label': 'Raise soil pH',
        'description': 'Increase pH toward neutral using biochar liming potential.',
        'implemented': True,
    },
    {
        'id': 'water_retention',
        'label': 'Improve water retention',
        'description': 'Increase soil water holding capacity.',
        'implemented': True,
    },
    {
        'id': 'carbon_sequestration',
        'label': 'Sequester carbon',
        'description': 'Maximize stable carbon storage in soil.',
        'implemented': False,
    },
    {
        'id': 'provision_pk',
        'label': 'Provision P or K',
        'description': 'Supply phosphorus or potassium to nutrient-deficient soils.',
        'implemented': False,
    },
]


def _load_biochar_records() -> list:
    """Load biochar properties from the abi_demo staging layer."""
    layer_path = (
        Path(SWALES_ROOT) / BIOCHAR_ATLAS / 'staging' / 'layers'
        / 'seed_biochar' / 'seed_biochar.geojson'
    )
    if not layer_path.exists():
        raise FileNotFoundError(f"Biochar layer not found: {layer_path}")
    fc = json.loads(layer_path.read_text())
    print(f"Loaded biochar records: {fc}")
    return [f['properties'] for f in fc.get('features', []) if f.get('properties')]


LANDSCAPE_TARGET_PH = 6.5  # standard agronomic target for OR


def _load_ssurgo_features() -> list:
    """Load SSURGO polygon features from the abi_demo staging layer."""
    layer_path = (
        Path(SWALES_ROOT) / BIOCHAR_ATLAS / 'staging' / 'layers'
        / 'ssurgo_polk_or' / 'ssurgo_polk_or.geojson'
    )
    if not layer_path.exists():
        raise FileNotFoundError(f"SSURGO layer not found: {layer_path}")
    fc = json.loads(layer_path.read_text())
    return [f['properties'] for f in fc.get('features', []) if f.get('properties')]


class SuitabilityRequest(BaseModel):
    mukey: Optional[str] = None
    soil_ph: Optional[float] = None
    soil_om: Optional[float] = None
    soil_sand: Optional[float] = None
    goal: str = 'raise_ph'
    target_ph: Optional[float] = None


class LandscapeRequest(BaseModel):
    goal: str = 'raise_ph'
    biochar_id: str


@biochar_router.post('/suitability')
def biochar_suitability(req: SuitabilityRequest):
    """Return ranked biochar suitability for a soil query.

    Input: soil_ph (required), soil_om (optional), goal, target_ph (optional).
    Returns top-5 ranked biochars with suitability_score, rate, predicted ΔpH.
    """
    try:
        biochar_records = _load_biochar_records()
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))

    goal_def = next((g for g in GOALS if g['id'] == req.goal), None)
    if not goal_def:
        raise HTTPException(status_code=400, detail=f"Unknown goal: {req.goal!r}")

    ranked = dst_match_point(
        soil_ph=req.soil_ph,
        soil_om=req.soil_om,
        soil_sand=req.soil_sand,
        goal=req.goal,
        biochar_records=biochar_records,
        target_ph=req.target_ph,
    )

    return {
        'mukey': req.mukey,
        'soil_ph': req.soil_ph,
        'soil_om': req.soil_om,
        'soil_sand': req.soil_sand,
        'goal': req.goal,
        'goal_label': goal_def['label'],
        'goal_implemented': goal_def['implemented'],
        'results': ranked,
    }


@biochar_router.get('/debug_ssurgo')
def debug_ssurgo():
    """Return field names and a sample of the first SSURGO feature for diagnosis."""
    try:
        features = _load_ssurgo_features()
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))
    if not features:
        return {'count': 0, 'fields': [], 'sample': None}
    sample = features[0]
    return {'count': len(features), 'fields': sorted(sample.keys()), 'sample': sample}


@biochar_router.post('/landscape')
def biochar_landscape(req: LandscapeRequest):
    """Compute suitability for a single biochar across every SSURGO polygon.

    Returns a mukey→score dict for driving a choropleth map. Uses a fixed
    target_ph of 6.5 so scores vary meaningfully with each polygon's soil_ph.
    """
    try:
        biochar_records = _load_biochar_records()
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))

    try:
        ssurgo_features = _load_ssurgo_features()
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))

    biochar_record = next(
        (r for r in biochar_records if str(r.get('biochar_id')) == str(req.biochar_id)),
        None,
    )
    if biochar_record is None:
        raise HTTPException(status_code=404, detail=f"Biochar id {req.biochar_id!r} not found")

    if ssurgo_features:
        logger.info(f"landscape: {len(ssurgo_features)} SSURGO features, sample keys: {sorted(ssurgo_features[0].keys())}")

    scores = {}
    skipped_no_mukey = skipped_no_ph = skipped_no_sand = 0
    for props in ssurgo_features:
        mukey = props.get('mukey') or props.get('MUKEY')
        if not mukey:
            skipped_no_mukey += 1
            continue

        soil_ph_raw = props.get('soil_ph') or props.get('SOIL_PH') or props.get('ph1to1h2o_r')
        soil_ph = None
        if soil_ph_raw is not None:
            try:
                soil_ph = float(soil_ph_raw)
            except (TypeError, ValueError):
                pass

        soil_sand_raw = props.get('soil_sand') or props.get('SOIL_SAND') or props.get('sandtotal_r')
        soil_sand = None
        if soil_sand_raw is not None:
            try:
                soil_sand = float(soil_sand_raw)
            except (TypeError, ValueError):
                pass

        if req.goal == 'raise_ph' and soil_ph is None:
            skipped_no_ph += 1
            continue
        if req.goal == 'water_retention' and soil_sand is None:
            skipped_no_sand += 1
            continue

        soil_om_raw = props.get('soil_om') or props.get('SOIL_OM') or props.get('om_r')
        soil_om = float(soil_om_raw) if soil_om_raw is not None else None

        # Pass all biochars so normalization works; find the selected one by id
        ranked = dst_match_point(
            soil_ph=soil_ph,
            soil_om=soil_om,
            soil_sand=soil_sand,
            goal=req.goal,
            biochar_records=biochar_records,
            target_ph=LANDSCAPE_TARGET_PH,
            top_n=None,
        )
        match = next((r for r in ranked if str(r.get('biochar_id')) == str(req.biochar_id)), None)
        if match and not match.get('placeholder'):
            scores[str(mukey)] = match['suitability_score']

    logger.info(
        f"landscape: scored {len(scores)}, skipped no_mukey={skipped_no_mukey} "
        f"no_ph={skipped_no_ph} no_sand={skipped_no_sand}"
    )

    if scores:
        min_score = min(scores.values())
        max_score = max(scores.values())
    else:
        min_score = max_score = 0.0

    return {'scores': scores, 'min_score': min_score, 'max_score': max_score,
            'debug': {'total': len(ssurgo_features), 'scored': len(scores),
                      'skipped_no_mukey': skipped_no_mukey, 'skipped_no_ph': skipped_no_ph,
                      'skipped_no_sand': skipped_no_sand}}


@biochar_router.get('/goals')
def biochar_goals():
    """Return the list of supported biochar goals."""
    return {'goals': GOALS}


@biochar_router.get('/biochar_db')
def biochar_db():
    """Return full biochar properties database (for UI dropdowns / debug)."""
    try:
        records = _load_biochar_records()
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {'count': len(records), 'records': records}
