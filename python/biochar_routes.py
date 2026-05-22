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
        'implemented': False,
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
        / 'biochar_properties_pnw' / 'biochar_properties_pnw.geojson'
    )
    if not layer_path.exists():
        raise FileNotFoundError(f"Biochar layer not found: {layer_path}")
    fc = json.loads(layer_path.read_text())
    return [f['properties'] for f in fc.get('features', []) if f.get('properties')]


class SuitabilityRequest(BaseModel):
    mukey: Optional[str] = None
    soil_ph: float
    soil_om: Optional[float] = None
    goal: str = 'raise_ph'
    target_ph: Optional[float] = None


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
        goal=req.goal,
        biochar_records=biochar_records,
        target_ph=req.target_ph,
    )

    return {
        'mukey': req.mukey,
        'soil_ph': req.soil_ph,
        'soil_om': req.soil_om,
        'goal': req.goal,
        'goal_label': goal_def['label'],
        'goal_implemented': goal_def['implemented'],
        'results': ranked,
    }


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
