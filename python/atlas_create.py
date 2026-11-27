"""Creating an atlas from a starter: the ``create_atlas`` job (Phase 4, task 7).

``/create_atlas`` answers the quick questions in the request (is the name free,
does the starter exist, what is the extent) and submits a job; the box worker
runs ``create`` here. It was a FastAPI background task, which never runs on
Lambda. It runs on the box because a create runs every starter inlet and builds
every outlet from nothing.

Job params: ``slug``, ``name``, ``starter`` (a key), ``bbox``,
``default_h3_resolution``, ``register_email``, and ``boundary_key`` when a
GeoJSON was uploaded. The upload itself is stored beside the job
(``boundary_key``), so the status the page polls stays small.

``register_email`` adds ``{slug}@fireatlas.org`` to the live SES rule. The API
sets it for a logged-in creator; it is a separate step so an anonymous create
(not allowed yet) can leave it off.

Heavy imports are inside ``create`` so webapp can import this module.
"""
import json
import logging
import math
import os
from pathlib import Path

logger = logging.getLogger(__name__)

STARTER_SUFFIX = '_starter.json'
EMAIL_DOMAIN = 'fireatlas.org'
SES_RULE_SET = 'atlas-email-inlet'
SES_REGION = 'us-east-1'

# H3 average edge length (km) by resolution.
H3_EDGE_KM = {5: 86.745, 6: 32.906, 7: 12.447, 8: 4.642, 9: 1.755,
              10: 0.664, 11: 0.252, 12: 0.095}

# Outlets build in this order: webmap before the console (it checks for the
# webmap), notebook first, console last; anything else in between.
OUTLET_ORDER = {"notebook": 0, "webmap": 1, "webedit": 2, "3dview": 3, "console": 9}


def starter_dir() -> Path:
    """Directory holding {key}_starter.json files in the app checkout."""
    import versioning
    return Path(versioning.atlas_path(local_path='configuration', version='app'))


def load_starter(key: str) -> dict:
    """One starter bundle. FileNotFoundError if there is none by that key."""
    path = starter_dir() / f"{key}{STARTER_SUFFIX}"
    if not path.exists():
        raise FileNotFoundError(f"Unknown starter '{key}'")
    with open(path) as f:
        return json.load(f)


def boundary_key(slug: str, job_id: str) -> str:
    return f"{slug}/jobs/{job_id}.boundary.geojson"


def default_h3_resolution(bbox: dict) -> int:
    """The resolution giving about 10 cells across the short axis of the bbox."""
    lat_center = (bbox["north"] + bbox["south"]) / 2
    width_km = (bbox["east"] - bbox["west"]) * math.cos(math.radians(lat_center)) * 111
    height_km = (bbox["north"] - bbox["south"]) * 111
    target_cell_width_km = max(min(width_km, height_km) / 10.0, 0.001)
    resolution = 8
    for r in range(5, 13):
        if H3_EDGE_KM[r] * 1.732 >= target_cell_width_km:
            resolution = r
    return max(5, min(12, resolution))


def atlas_feature_collection(slug: str, name: str, bbox: dict, h3_resolution: int,
                             starter_props: dict) -> dict:
    """The atlas's region: its bbox as one polygon, with the atlas's properties."""
    w, s, e, n = bbox["west"], bbox["south"], bbox["east"], bbox["north"]
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Polygon",
                         "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]},
            "properties": {
                "name": slug,
                "description": name,
                "app_url": "/api",   # same origin (config_checks.API_PATH)
                "versioned_outlets": [],  # empty = snapshot every outlet (#131 C9)
                "default_h3_resolution": h3_resolution,
                **starter_props,
            },
        }],
    }


def deferred_outlets(assets: dict) -> list:
    """PDF outlets, left for later: a runbook render is slow and memory-hungry,
    and at create time it would run over regions that were only just imported."""
    return [n for n, a in assets.items()
            if a.get('type') == 'outlet' and a.get('config', {}).get('data_type') == 'geopdf']


def outlets_to_build(assets: dict) -> list:
    deferred = deferred_outlets(assets)
    return sorted((n for n, a in assets.items() if a.get('type') == 'outlet' and n not in deferred),
                  key=lambda n: OUTLET_ORDER.get(n, 5))


def expected_steps(starter: dict) -> int:
    """About how many progress lines a create of this starter reports, for the
    page's progress bar. Exact for a starter without an upload; an upload adds a
    line per layer it fills, so the page caps the bar short of full."""
    assets, layers = starter.get('assets', {}), starter.get('layers', {})
    inlets = [n for n, a in assets.items() if a.get('type') == 'inlet']
    vector_layers = [n for n, l in layers.items() if l.get('geometry_type') != 'raster']
    return (2                                   # structure: started, created
            + 1                                 # email
            + 2 * len(inlets)                   # fetching, finished
            + len(vector_layers)                # one refresh line each
            + len(deferred_outlets(assets))
            + 2 * len(outlets_to_build(assets)))


def email_address(slug: str) -> str:
    return f"{slug}@{EMAIL_DOMAIN}"


def register_email_address(slug: str, ses=None) -> str:
    """Add {slug}@fireatlas.org to the SES rule that stores mail for the email
    Lambda. Idempotent. Returns the address."""
    if ses is None:
        import boto3
        ses = boto3.client('ses', region_name=SES_REGION)
    address = email_address(slug)
    rule_set = ses.describe_receipt_rule_set(RuleSetName=SES_RULE_SET)
    for rule in rule_set['Rules']:
        if any('S3Action' in a for a in rule['Actions']):
            recipients = rule.get('Recipients', [])
            if address not in recipients:
                rule['Recipients'] = recipients + [address]
                ses.update_receipt_rule(RuleSetName=SES_RULE_SET, Rule=rule)
            return address
    raise LookupError(f"no S3 rule in SES rule set {SES_RULE_SET!r}")


def create(job: dict, report, wait: float = 0.0) -> str:
    """Run a create_atlas job: build the atlas in a session whose write-back
    seeds ``{slug}/staging/`` in S3."""
    import atlas
    import atlas_session
    import atlas_store
    import atlas_workspace
    import dataswale_geojson
    import deltas_geojson
    import geojson_import

    p = job['params']
    slug = p['slug']
    settings = atlas_store.cloud_settings({})
    s3 = atlas_store._s3()
    # The request checked too, but two creates of one name a few seconds apart
    # both pass that check; the lease stops them overlapping, not following.
    if atlas_workspace.is_seeded(s3, settings['private_bucket'], slug):
        raise FileExistsError(f"Atlas '{slug}' already exists")

    starter = load_starter(p['starter'])
    starter_layers = starter["layers"]   # dict keyed by name
    starter_assets = starter["assets"]
    boundary = None
    if p.get('boundary_key'):
        obj = s3.get_object(Bucket=settings['private_bucket'], Key=p['boundary_key'])
        boundary = json.loads(obj['Body'].read())
    feature_collection = atlas_feature_collection(
        slug, p['name'], p['bbox'], p['default_h3_resolution'], starter.get('properties', {}))

    # require_config=False: the atlas starts with nothing in S3, so there is
    # no staging config to load; the write-back is what seeds it.
    with atlas_session.open_session(slug, purpose=f"job create_atlas ({job['id']})",
                                    wait=wait, require_config=False) as session:
        report(f"Creating atlas structure (starter: {p['starter']})")
        ac = atlas.create(
            layers=[{"name": k, **v} for k, v in starter_layers.items()],
            assets=starter_assets,
            data_root=str(session.workspace_root),
            feature_collection=feature_collection,
        )
        report("Atlas structure created")

        if p.get('register_email'):
            try:
                report(f"Email ingest configured for {register_email_address(slug)}")
            except Exception as ses_err:
                logger.warning(f"Could not configure SES for {slug}: {ses_err}")
                report(f"Warning: email ingest not configured ({ses_err})")
        else:
            report("Email ingest not configured (not requested)")

        # A self-contained single-file atlas.geojson: the starter's layers and
        # assets inline, so later rebuilds (build_atlas_from_geojson) don't
        # depend on the starter file. It is inside staging, so it is seeded.
        atlas_geojson = json.loads(json.dumps(feature_collection))
        atlas_geojson['features'][0]['properties'].update({
            "data_root": os.environ.get('SWALES_ROOT', ''),
            "shared_dir": os.environ.get('ATLAS_SHARED_DIR', '/root/data'),
            "base_url": f"{settings['public_base_url']}/{slug}",
            "port": 9000,   # unused; build_atlas_from_geojson still requires it
            "admin_emails": [],
            "logo_url": "",
            "layers": starter_layers,
            "assets": starter_assets,
        })
        with open(session.staging_dir / "atlas.geojson", 'w') as f:
            json.dump(atlas_geojson, f, indent=2)

        assets = ac['assets']
        raster_layers = {l['name'] for l in ac['dataswale']['layers']
                         if l.get('geometry_type') == 'raster'}

        # The starter's inlets populate the base layers. Eddies are skipped
        # (DEM/hillshade/H3/sim are slow and run on layers that start empty).
        for inlet_name in [n for n, a in assets.items() if a.get('type') == 'inlet']:
            report(f"Fetching {inlet_name}")
            try:
                atlas.materialize(ac, inlet_name)
                out_layer = assets[inlet_name].get('out_layer')
                if out_layer in raster_layers:
                    dataswale_geojson.refresh_raster_layer(ac, out_layer)
                report(f"Finished {inlet_name}")
            except Exception as inlet_err:
                logger.warning(f"Inlet {inlet_name} failed for {slug}: {inlet_err}")
                report(f"Warning: {inlet_name} failed ({inlet_err}) — skipped")

        # Uploaded features are seeded the way an inlet seeds one: as a delta,
        # applied by the refresh below.
        if boundary is not None:
            known = [l['name'] for l in ac['dataswale']['layers']]
            split = geojson_import.split_features_by_layer(boundary, known)
            for layer_name, features in sorted(split['routed'].items()):
                deltas_geojson.write_delta(ac, layer_name,
                                           {"type": "FeatureCollection", "features": features},
                                           "create", "create")
                report(f"Imported {len(features)} feature(s) into {layer_name}")
            for unknown_name, count in sorted(split['unknown'].items()):
                report(f"Warning: {count} feature(s) name layer '{unknown_name}', "
                       f"which this starter does not have — not imported")

        # Apply the deltas, and give inlet-less layers an empty collection so
        # the webmap doesn't 404. Must precede the outlets, which read them.
        for layer_name, action in dataswale_geojson.refresh_all_vector_layers(ac):
            if action.startswith('failed'):
                logger.warning(f"Layer {layer_name} for {slug}: {action}")
                report(f"Warning: layer {layer_name} {action}")
            else:
                report(f"Layer {layer_name} {action}")

        for deferred_name in deferred_outlets(assets):
            report(f"Deferred {deferred_name} (PDF outlets are materialized on request)")
        for outlet_name in outlets_to_build(assets):
            report(f"Materializing {outlet_name}")
            try:
                atlas.materialize(ac, outlet_name)
                report(f"Finished {outlet_name}")
            except Exception as outlet_err:
                logger.exception(f"Outlet {outlet_name} failed for {slug}")
                report(f"Warning: {outlet_name} failed ({outlet_err}) — skipped")

    return f"Atlas '{slug}' created"
