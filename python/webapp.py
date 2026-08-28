from fastapi import FastAPI, HTTPException, BackgroundTasks, logger
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import json
from datetime import datetime
import os
import shutil
from typing import Dict, Any
import asyncio
import logging
import traceback
import requests
import re
from urllib.parse import urlparse, parse_qs
from pathlib import Path
import base64
import boto3

import sys
#webapp_conf = json.load(open("webapp_conf.json"))

SWALES_ROOT = os.environ['SWALES_ROOT']
ATLAS_SHARED_DIR = os.environ.get('ATLAS_SHARED_DIR', '/root/data')
print(f"Serving all atlases from {SWALES_ROOT}")

# Boring Imports
import sys, os, subprocess, time, json, string, random, math


# our Imports|
import atlas
import dataswale_geojson
import outlets
import versioning
import deltas_geojson
import utils
import vector_inlets
import email_inlet
import atlas_logs
from biochar_routes import biochar_router
app = FastAPI()
logger.logger.setLevel(0)

# CORS middleware configuration
app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Configure storage directory
STORAGE_DIR = "/root/data/uploads/"
# os.makedirs(f"{STORAGE_DIR}/roads_deltas", exist_ok=True)

# Serve templates directory for the create-atlas UI
_templates_dir = versioning.atlas_path(local_path='templates', version='app')
app.mount("/templates", StaticFiles(directory=str(_templates_dir)), name="templates")
app.include_router(biochar_router)


class JSONPayload(BaseModel):
    data: Dict[str, Any]

class PinToPOIPayload(BaseModel):
    url: str
    poi_type: str
    asset: str

class SQLQueryPayload(BaseModel):
    query: str
    return_format: str = 'csv'  # Default to CSV format

class NLSQLPayload(BaseModel):
    natural_language: str

class BBox(BaseModel):
    west: float
    east: float
    north: float
    south: float

class CreateAtlasRequest(BaseModel):
    name: str   # display name (e.g. "Salmon Creek VFD")
    slug: str   # atlas ID / directory name (e.g. "scvfd")
    bbox: BBox
    starter: str = "simple"   # starter bundle key (configuration/{starter}_starter.json)

class MovePayload(BaseModel):
    source_layer: str
    target_layer: str
    selection: Dict[str, Any]  # GeoJSON Feature or FeatureCollection of selection polygon(s)

class CommentPayload(BaseModel):
    text: str
    author: str
    atlas_id: str
    existing_conversations: list = []

def extract_coordinates_from_url(url: str) -> tuple[float, float]:
    """Extract latitude and longitude from a Google Maps URL."""
    import re
    try:
        # Handle short URLs by following redirects
        if 'goo.gl' in url or 'maps.app.goo.gl' in url:
            response = requests.get(url, allow_redirects=True)
            url = response.url
            print(f"Dereferenced URL to: {url}")

        # Parse the URL
        parsed = urlparse(url)
        print(f"Parsing URL - netloc: {parsed.netloc}, path: {parsed.path}, query: {parsed.query}")
        
        # Handle both old maps.google.com and new www.google.com/maps formats
        if 'google.com' in parsed.netloc and ('/maps' in parsed.path or 'maps.google.com' in parsed.netloc):
            
            # Try multiple extraction methods
            
            # Method 1: @lat,lng format (most common after redirect)
            if '@' in url:
                # Format: https://www.google.com/maps/@37.7749,-122.4194,15z
                # or: https://www.google.com/maps/place/Name/@37.7749,-122.4194,15z
                match = re.search(r'@(-?\d+\.?\d*),(-?\d+\.?\d*)', url)
                if match:
                    lat, lon = float(match.group(1)), float(match.group(2))
                    print(f"Extracted from @ format: {lat}, {lon}")
                    return lat, lon
            
            # Method 2: ?q=lat,lng format
            query = parse_qs(parsed.query)
            if 'q' in query:
                q_value = query['q'][0]
                # Try to parse as coordinates
                coord_match = re.match(r'(-?\d+\.?\d*),\s*(-?\d+\.?\d*)', q_value)
                if coord_match:
                    lat, lon = float(coord_match.group(1)), float(coord_match.group(2))
                    print(f"Extracted from ?q= format: {lat}, {lon}")
                    return lat, lon
            
            # Method 3: /place/lat,lng or /search/lat,lng format (coordinates in path)
            # Handle optional + or space before longitude
            path_match = re.search(r'/(-?\d+\.?\d*),\+?(-?\d+\.?\d*)', parsed.path)
            if path_match:
                lat, lon = float(path_match.group(1)), float(path_match.group(2))
                print(f"Extracted from path: {lat}, {lon}")
                return lat, lon
            
            # Method 4: ll= parameter
            if 'll' in query:
                coords = query['ll'][0].split(',')
                lat, lon = float(coords[0]), float(coords[1])
                print(f"Extracted from ll= format: {lat}, {lon}")
                return lat, lon
            
            raise ValueError(f"Could not find coordinates in URL: {url}")
        else:
            raise ValueError(f"Not a valid Google Maps URL: {url}")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error extracting coordinates: {str(e)}")

@app.get("/status")
async def get_status():
    print("STATUS GET")
    return {
        "status": "success",
        "message": "Getting Status via API",
        "filename": "None",
        "path": "none"}

@app.post("/dereference_url")
async def dereference_url(payload: dict):
    """
    Dereference a shortened Google Maps URL and extract coordinates.
    Accepts: {"url": "https://goo.gl/maps/..."}
    Returns: {"lat": 37.7749, "lng": -122.4194, "full_url": "https://..."}
    """
    try:
        url = payload.get('url', '').strip()
        if not url:
            raise HTTPException(status_code=400, detail="URL is required")
        
        # Check if it's a shortened URL
        if 'goo.gl' not in url and 'maps.app.goo.gl' not in url:
            # Not a shortened URL, try to extract coordinates directly
            lat, lon = extract_coordinates_from_url(url)
            return {
                "status": "success",
                "lat": lat,
                "lng": lon,
                "full_url": url
            }
        
        # Follow redirects for shortened URLs
        response = requests.get(url, allow_redirects=True, timeout=10)
        full_url = response.url
        
        # Extract coordinates from the full URL
        lat, lon = extract_coordinates_from_url(full_url)
        
        return {
            "status": "success",
            "lat": lat,
            "lng": lon,
            "full_url": full_url
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error dereferencing URL: {str(e)}")

    
@app.post("/import_gsheet/{swalename}/{layer_name}")
async def import_gsheet(swalename: str, layer_name: str):
    try:
        config_path = Path(SWALES_ROOT) / swalename / "staging" / "atlas_config.json"
        ac = json.load(open(config_path))
        layer_fc = outlets.import_gsheet(ac, 'spreadsheet_import', layer_name)


        # store the geojson
        outpath = deltas_geojson.delta_path_from_layer(ac, layer_name, "create")
        with open(outpath, "w") as f:
            json.dump(layer_fc, f)

        res = dataswale_geojson.refresh_vector_layer(ac, layer_name)
        return {
            "status": "success",
            "message": f"Data stored successfully, refreshed: {res}",
            "filename": os.path.basename(outpath),
            "path": outpath}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/export_gsheet/{swalename}/{layer_name}")
async def export_gsheet(swalename: str, layer_name: str):
    print("HIIYYEEEEEEE")
    try:
        config_path = Path(SWALES_ROOT) / swalename / "staging" / "atlas_config.json"
        ac = json.load(open(config_path))
        ac['assets']['spreadsheet_export']['in_layers'] = [layer_name]
        layer_fc = outlets.gsheet_export(ac, 'spreadsheet_export', layer_name)


        # store the config since we may have updated spreadsheet URLs
        #outpath = deltas_geojson.delta_path_from_layer(ac, layer_name, "create")
        with open(config_path, "w") as f:
            json.dump(ac, f)


        return {
            "status": "success",
            "message": f"Data stored successfully, refreshed: {ac['spreadsheets']}",
            "filename": os.path.basename(config_path),
            "path": config_path}
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/import_sheet/{swalename}/{layer_name}")
async def import_sheet_endpoint(swalename: str, layer_name: str):
    """Import data from Google Sheet and refresh the layer."""
    try:
        config_path = Path(SWALES_ROOT) / swalename / "staging" / "atlas_config.json"
        ac = json.load(open(config_path))
        
        # Call vector_inlets.import_sheet to get delta paths
        delta_paths = vector_inlets.import_sheet(ac, layer_name)
        
        # Refresh the layer to overwrite with new data
        res = dataswale_geojson.refresh_vector_layer(ac, layer_name, deltas_geojson.apply_deltas_overwrite)
        
        return {
            "status": "success",
            "message": f"Sheet imported and layer refreshed: {res}",
            "delta_paths": delta_paths,
            "layer": layer_name
        }
    except Exception as e:
        logging.error(f"Error importing sheet for {layer_name}: {str(e)}")
        logging.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

    
@app.get("/clear_layer/{swalename}/{layer_name}")
async def clear_layer(swalename: str, layer_name: str):
    try:
        config_path = Path(SWALES_ROOT) / swalename / "staging" / "atlas_config.json"
        ac = json.load(open(config_path))
        dataswale_geojson.clear_vector_layer(ac, layer_name)
        return {
            "status": "success",
            "message": f"Layer cleared successfully",
            "layer_name": layer_name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/copy_layer/{swalename}/{layer_name}")
async def copy_layer(swalename: str, layer_name: str, new_name: str):
    try:
        config_path = Path(SWALES_ROOT) / swalename / "staging" / "atlas_config.json"
        ac = json.load(open(config_path))
        atlas.copy_layer(ac, layer_name, new_name)
        return {
            "status": "success",
            "message": (f"Layer '{layer_name}' copied to '{new_name}'. "
                        f"Rematerialize webmap/webedit/html on the server for it to appear."),
            "old_name": layer_name,
            "new_name": new_name}
    except (ValueError, FileExistsError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/add_layer/{swalename}/{layer_name}")
async def add_layer(swalename: str, layer_name: str, s3_url: str = None,
                    s3_key: str = None, s3_bucket: str = "scs-internal",
                    color: str = None, geometry: str = "point"):
    """Add a new S3-backed vector layer (issue: layer import). The GeoJSON must
    already be uploaded to S3 (default s3://scs-internal/{atlas}/imports/
    {layer}.geojson). s3_url accepts a full 's3://bucket/key' or a bare key;
    color is a hex string like '#FFAA33'. Registers layer + inlet, wires
    consumers, rebuilds config, materializes. Synchronous — can take a while."""
    try:
        # s3_url convenience: split 's3://bucket/key', else treat as a bare key.
        if s3_url:
            if s3_url.startswith("s3://"):
                bucket, _, key = s3_url[len("s3://"):].partition("/")
                if bucket:
                    s3_bucket = bucket
                s3_key = key
            else:
                s3_key = s3_url
        config_path = Path(SWALES_ROOT) / swalename / "staging" / "atlas_config.json"
        ac = json.load(open(config_path))
        atlas.add_layer(ac, layer_name, s3_key=s3_key, s3_bucket=s3_bucket,
                        color=color, geometry_type=geometry)
        return {
            "status": "success",
            "message": f"Layer '{layer_name}' added and materialized.",
            "layer": layer_name}
    except (ValueError, FileExistsError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logging.error(f"add_layer failed for {swalename}/{layer_name}: {e}")
        logging.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/delta_upload/{swalename}")
async def json_upload(payload: JSONPayload, swalename: str):
    try:
        delta_package = payload.data
        fc = delta_package #['features']
        layer = delta_package['layer']
        action = delta_package['action']
        # Auto-apply is keyed on the source (issue #131, C7): interactive
        # sources default to immediate apply; bulk sources send apply=False
        # to accumulate. Transport flag only — not persisted in the delta.
        apply_now = bool(delta_package.pop('apply', True))
        print(f"delta_upoad.=: {action} for {layer}: delta_package (apply={apply_now})")
        config_path = Path(SWALES_ROOT) / swalename / "staging" / "atlas_config.json"
        print(f"loading config from {config_path}")
        ac = json.load(open(config_path))
        delta_path = deltas_geojson.delta_path_from_layer(ac, layer, action)
        with open(delta_path, "w") as f:
            json.dump(fc, f)

        if apply_now:
            # Cheap update-refresh of the target layer only, no cascade.
            print(f"refreshing {layer} after {action}")
            res = dataswale_geojson.refresh_vector_layer(ac, layer)
            message = f"Data stored successfully, refreshed: {res}"
        else:
            print(f"stored delta for {layer} ({action}); apply deferred")
            message = "Data stored successfully, apply deferred"

        return {
            "status": "success",
            "message": message,
            "applied": apply_now,
            "filename": os.path.basename(delta_path),
            "path": delta_path}
    except Exception as e:
        print(f"ERROR in json_upload. {e}")
        traceback_str = ''.join(traceback.format_tb(e.__traceback__))
        print(traceback_str)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/move_features/{swalename}")
async def move_features(payload: MovePayload, swalename: str):
    try:
        config_path = Path(SWALES_ROOT) / swalename / "staging" / "atlas_config.json"
        ac = json.load(open(config_path))

        source_path = versioning.atlas_path(ac, "layers") / payload.source_layer / f"{payload.source_layer}.geojson"
        target_path = versioning.atlas_path(ac, "layers") / payload.target_layer / f"{payload.target_layer}.geojson"

        source_snapshot = source_path.read_text()
        target_snapshot = target_path.read_text() if target_path.exists() else None

        sel = payload.selection
        if sel.get("type") == "Feature":
            sel_fc = {"type": "FeatureCollection", "features": [sel]}
        elif sel.get("type") == "FeatureCollection":
            sel_fc = sel
        else:
            raise HTTPException(status_code=400, detail="selection must be a GeoJSON Feature or FeatureCollection")

        moved = deltas_geojson.extract_intersecting_features(source_path, sel_fc)
        if not moved:
            return {"moved": 0, "source_remaining": len(json.loads(source_snapshot)["features"])}

        delta_files_written = []
        try:
            create_fc = {"type": "FeatureCollection", "layer": payload.target_layer, "action": "create", "features": [dict(f) for f in moved]}
            target_delta = deltas_geojson.delta_path_from_layer(ac, payload.target_layer, "create")
            with open(target_delta, "w") as f:
                json.dump(create_fc, f, default=utils.json_serial)
            delta_files_written.append(Path(target_delta))
            dataswale_geojson.refresh_vector_layer(ac, payload.target_layer)

            delete_fc = dict(sel_fc)
            delete_fc["layer"] = payload.source_layer
            delete_fc["action"] = "delete"
            source_delta = deltas_geojson.delta_path_from_layer(ac, payload.source_layer, "delete")
            with open(source_delta, "w") as f:
                json.dump(delete_fc, f, default=utils.json_serial)
            delta_files_written.append(Path(source_delta))
            dataswale_geojson.refresh_vector_layer(ac, payload.source_layer)

            source_remaining = len(json.loads(source_path.read_text())["features"])
            return {"moved": len(moved), "source_remaining": source_remaining}

        except Exception as e:
            for p in delta_files_written:
                if p.exists():
                    p.unlink()
            source_path.write_text(source_snapshot)
            if target_snapshot is not None:
                target_path.write_text(target_snapshot)
            raise HTTPException(status_code=500, detail=str(e))

    except HTTPException:
        raise
    except Exception as e:
        traceback_str = ''.join(traceback.format_tb(e.__traceback__))
        print(f"ERROR in move_features: {e}\n{traceback_str}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/comment/{swalename}/{layer_name}")
async def add_comment(payload: CommentPayload, swalename: str, layer_name: str):
    try:
        config_path = Path(SWALES_ROOT) / swalename / "staging" / "atlas_config.json"
        ac = json.load(open(config_path))

        existing = payload.existing_conversations
        if isinstance(existing, str):
            try:
                existing = json.loads(existing)
            except Exception:
                existing = []

        new_comment = {
            "text": payload.text,
            "author": payload.author,
            "ts": datetime.utcnow().isoformat() + "Z"
        }
        updated = list(existing) + [new_comment]

        delta_fc = {
            "type": "FeatureCollection",
            "layer": layer_name,
            "action": "match",
            "features": [{
                "type": "Feature",
                "geometry": None,
                "properties": {
                    "atlas_id": payload.atlas_id,
                    "conversations": json.dumps(updated)
                }
            }]
        }
        delta_path = deltas_geojson.delta_path_from_layer(ac, layer_name, "match")
        with open(delta_path, "w") as f:
            json.dump(delta_fc, f)

        dataswale_geojson.refresh_vector_layer(ac, layer_name)

        return {"status": "success", "conversations": updated}
    except HTTPException:
        raise
    except Exception as e:
        traceback_str = ''.join(traceback.format_tb(e.__traceback__))
        print(f"ERROR in add_comment: {e}\n{traceback_str}")
        raise HTTPException(status_code=500, detail=str(e))


# TODO do we use this anywhere? And why is delta_upload different?
@app.post("/store/{swalename}")
async def store_json(swalename: str, payload: JSONPayload):
    try:
        logger.logger.info("Storing JSON payload")
        # Get layer and version from payload
        layer = payload.data.get('layer', 'unknown')
        print(f"storing delta for  {layer} in {swalename}")
        # version = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Create versioned path

        #outpath_template = os.path.join(STORAGE_DIR, swalename,  "{layer}", "data_{version}.json")
        #outpath = outpath_template.format(layer=layer, version=version)

        config_path = Path(SWALES_ROOT) / swalename / "staging" / "atlas_config.json"
        print(f"loading config from {config_path}")
        ac = json.load(open(config_path))
        outpath = deltas_geojson.delta_path_from_layer(ac, layer, "create")
        print(f"writing delta to  {outpath}")
        
        #outpath = versioning.atlas_path(swalename, "deltas") / layer / f"data_{version}.json"
        # Ensure directory exists
        #os.makedirs(os.path.dirname(outpath), exist_ok=True)
        #logger.logger.debug(f"Created directory: {os.path.dirname(outpath)}")
        
        # Store the JSON data
        with open(outpath, 'w') as f:
            json.dump(payload.data, f, indent=2)
        logger.logger.info(f"Successfully stored JSON data at: {outpath}")
        print(f"Successfully stored JSON data at: {outpath}")
        

        #dc = json.load(open(versioning.atlas_path(swalename, "dataswale_config.json")))
        #res = atlas.asset_materialize(ac,  ac['assets'][layer])
        res = dataswale_geojson.refresh_vector_layer(ac, layer)

        # Refresh parent layer if it exists
        #res2 = "No Res2"
        #if layer.endswith("_deltas"):
        #    print(f"refreshing {layer}")
       #     parent = layer.split("_deltas")[0]
       #     print(f"Also refershing parent: {parent}")
       #     res2 = atlas.asset_materialize(ac, dc, ac['assets'][parent])
        
        return {
            "status": "success",
            "message": f"Data stored successfully, refreshed: {res}",
            "filename": os.path.basename(outpath),
            "path": outpath
        }
    except Exception as e:
        # logger.error(f"Error storing JSON data: {str(e)}")
        print(f"Error storing JSON data: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    
@app.get("/files")
async def list_files():
    try:
        files = os.listdir(STORAGE_DIR)
        return {
            "files": files
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/refresh")
async def refresh(swale: str, asset: str):
    try:
        config_path = Path(SWALES_ROOT) / swale / "staging" / "atlas_config.json"
        ac = json.load(open(config_path))
        res = atlas.materialize(ac, asset)
        res_json = {
            "status": "success",
            "message": f"Refreshed asset {asset}: {res}",
            "asset": asset,
        }
        print(res_json)
        return res_json
    except Exception as e:
        print(f"ERROR refreshing. {e}")
        traceback_str = ''.join(traceback.format_tb(e.__traceback__))
        print(traceback_str)
        raise HTTPException(status_code=500, detail=str(e))


# Global variable to track layer refresh status.
# Single-flight: one running refresh per webapp process.
refresh_layer_status = {
    "running": False,
    "swale": None,
    "layer": None,
    "mode": None,
    "cascade": None,
    "started_at": None,
    "finished_at": None,
    "success": None,
    "error": None,
}


@app.get("/refresh_layer")
async def refresh_layer_endpoint(swale: str, layer: str, background_tasks: BackgroundTasks,
                                 mode: str = "update", cascade: bool = True):
    """Refresh one layer through the Dagster asset graph (issue #131, T5).

    mode=update|rebuild, cascade=true|false — see atlas_dagster.refresh_layer.
    Runs in the background; poll /refresh-layer-status for completion.
    """
    if mode not in ("update", "rebuild"):
        raise HTTPException(status_code=400, detail=f"Unknown refresh mode: {mode!r}")
    if refresh_layer_status["running"]:
        return {
            "status": "already_running",
            "swale": refresh_layer_status["swale"],
            "layer": refresh_layer_status["layer"],
            "mode": refresh_layer_status["mode"],
            "started_at": refresh_layer_status["started_at"],
        }

    config_path = Path(SWALES_ROOT) / swale / "staging" / "atlas_config.json"
    try:
        ac = json.load(open(config_path))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No atlas config for '{swale}'")
    layer_names = [l.get('name') for l in ac.get('dataswale', {}).get('layers', [])]
    if layer not in layer_names:
        raise HTTPException(status_code=400, detail=f"No layer '{layer}' in atlas '{swale}'")

    refresh_layer_status.update(
        running=True, swale=swale, layer=layer, mode=mode, cascade=cascade,
        started_at=datetime.now().isoformat(), finished_at=None, success=None, error=None)

    def run_refresh():
        # Lazy import: keeps webapp importable where dagster isn't installed.
        import atlas_dagster
        try:
            result = atlas_dagster.refresh_layer(ac, layer, mode=mode, cascade=cascade)
            refresh_layer_status["success"] = bool(result.success)
        except Exception as e:
            logging.error(f"refresh_layer failed for {swale}/{layer}: {e}")
            logging.error(traceback.format_exc())
            refresh_layer_status["success"] = False
            refresh_layer_status["error"] = str(e)
        finally:
            refresh_layer_status["finished_at"] = datetime.now().isoformat()
            refresh_layer_status["running"] = False

    # Plain def: FastAPI runs sync background tasks in the threadpool, so
    # the blocking dagster.materialize doesn't stall the event loop.
    background_tasks.add_task(run_refresh)
    return {
        "status": "started",
        "swale": swale,
        "layer": layer,
        "mode": mode,
        "cascade": cascade,
        "started_at": refresh_layer_status["started_at"],
    }


@app.get("/refresh-layer-status")
async def get_refresh_layer_status():
    return refresh_layer_status


# Global variable to track single-asset materialize status.
# Single-flight: one running build per webapp process.
materialize_status = {
    "running": False,
    "swale": None,
    "asset": None,
    "started_at": None,
    "finished_at": None,
    "success": None,
    "error": None,
}


@app.get("/materialize_asset")
async def materialize_asset_endpoint(swale: str, asset: str, background_tasks: BackgroundTasks):
    """Build one asset (typically an outlet, e.g. runbook) through the
    Dagster graph (issue #131, T6). No cascade — exactly this asset.
    Runs in the background; poll /materialize-asset-status for completion.
    """
    if materialize_status["running"]:
        return {
            "status": "already_running",
            "swale": materialize_status["swale"],
            "asset": materialize_status["asset"],
            "started_at": materialize_status["started_at"],
        }

    config_path = Path(SWALES_ROOT) / swale / "staging" / "atlas_config.json"
    try:
        ac = json.load(open(config_path))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No atlas config for '{swale}'")
    if asset not in ac.get('assets', {}):
        raise HTTPException(status_code=400, detail=f"No asset '{asset}' in atlas '{swale}'")

    materialize_status.update(
        running=True, swale=swale, asset=asset,
        started_at=datetime.now().isoformat(), finished_at=None, success=None, error=None)

    def run_materialize():
        # Lazy import: keeps webapp importable where dagster isn't installed.
        import atlas_dagster
        try:
            result = atlas_dagster.materialize_asset(ac, asset)
            materialize_status["success"] = bool(result.success)
        except Exception as e:
            logging.error(f"materialize_asset failed for {swale}/{asset}: {e}")
            logging.error(traceback.format_exc())
            materialize_status["success"] = False
            materialize_status["error"] = str(e)
        finally:
            materialize_status["finished_at"] = datetime.now().isoformat()
            materialize_status["running"] = False

    background_tasks.add_task(run_materialize)
    return {
        "status": "started",
        "swale": swale,
        "asset": asset,
        "started_at": materialize_status["started_at"],
    }


@app.get("/materialize-asset-status")
async def get_materialize_asset_status():
    return materialize_status


# Global dict to track atlas creation status, keyed by slug
create_statuses: Dict[str, Any] = {}

# Global variable to track publish status
publish_status = {
    "publishing": False,
    "started_at": None,
    "finished_at": None,
    "log": [ ('start', datetime.now().isoformat()) ]
}

@app.get("/publish")
async def publish(swale: str, background_tasks: BackgroundTasks):
    logging.info("Starting publish...")
    try:
        # Check if already publishing
        if publish_status["publishing"]:
            return {
                "status": "error",
                "message": "Publishing already in progress",
                "publishing": True,
                "started_at": publish_status["started_at"]
            }

        config_path = Path(SWALES_ROOT) / swale / "staging" / "atlas_config.json"
        logging.info(f"publish loading config from {config_path}")
        ac = {}
        with open(config_path, 'r') as f:
            ac = json.load(f)
        # ac = json.load(open(config_path))
        print(f"publish loaded config: {ac}")
        # Start new publishing task
        publish_status["publishing"] = True
        publish_status["started_at"] = datetime.now().isoformat()
        publish_status["finished_at"] = None
        
        # Send immediate response
        response = {
            "status": "success",
            "publishing": True,
            "started_at": publish_status["started_at"]
        }
        
        # Add the delayed task to background

            
        def finish_publishing():
            # Publish is a pure snapshot (issue #131, C8): no inlet, eddy, or
            # outlet materialization here. Outlets are kept current by refresh
            # cascades before publish. Plain def so the copytree runs in the
            # threadpool and /publish-status stays responsive.
            try:
                publish_status["log"].append(  [ ('Publishing new version', datetime.now().isoformat()) ])

                res = versioning.publish_new_version(ac)
                publish_status["log"].append(  [ ('Finished publishing new version', datetime.now().isoformat()) ])
                publish_status["finished_at"] = datetime.now().isoformat()
                publish_status["publishing"] = False
                return str(res)
            except Exception as e:
                error_msg = f"Error in publish background task: {e}"
                logging.error(error_msg)
                logging.error(traceback.format_exc())
                publish_status["log"].append([(f'ERROR: {e}', datetime.now().isoformat())])
                publish_status["publishing"] = False
                publish_status["finished_at"] = datetime.now().isoformat()
                # Don't raise HTTPException in background task - response already sent



        background_tasks.add_task(finish_publishing)
        
        return response
    except Exception as e:
        # Reset status on error
        publish_status["publishing"] = False
        publish_status["finished_at"] = datetime.now().isoformat()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/publish-status")
async def publish_status_check(swale: str):
    try:
        return {
            "status": "success",
            "publishing": publish_status["publishing"],
            "started_at": publish_status["started_at"],
            "finished_at": publish_status["finished_at"],
            "log": publish_status["log"]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/create")
async def create_page():
    create_html = versioning.atlas_path(local_path='templates/create_atlas.html', version='app')
    return FileResponse(str(create_html))


def _starter_dir() -> Path:
    """Directory holding {key}_starter.json files in the app checkout."""
    return Path(versioning.atlas_path(local_path='configuration', version='app'))


def _load_starter(key: str) -> dict:
    """Load a single starter bundle; raises 400 if missing/invalid."""
    path = _starter_dir() / f"{key}_starter.json"
    if not path.exists():
        raise HTTPException(status_code=400, detail=f"Unknown starter '{key}'")
    with open(path) as f:
        return json.load(f)


@app.get("/create-starters")
async def list_starters():
    """List available starter bundles for the /create dropdown.

    Routed under the /create prefix so the existing nginx location block
    (^/(create|create_atlas|create-status|templates/)) proxies it to the
    webapp; a bare /starters path would fall through to the atlas-slug
    redirect and 302 instead.

    Returns [{key, label, description}], with 'simple' first when present.
    """
    starters = []
    for path in sorted(_starter_dir().glob("*_starter.json")):
        key = path.name[:-len("_starter.json")]
        try:
            with open(path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logging.warning(f"Skipping unreadable starter {path.name}: {e}")
            continue
        starters.append({
            "key": key,
            "label": data.get("label", key),
            "description": data.get("description", ""),
        })
    starters.sort(key=lambda s: (s["key"] != "simple", s["label"]))
    return {"status": "success", "starters": starters}


@app.post("/create_atlas")
async def create_atlas_endpoint(payload: CreateAtlasRequest, background_tasks: BackgroundTasks):
    slug = payload.slug.strip()
    if not re.match(r'^[a-zA-Z][a-zA-Z0-9_]*$', slug):
        raise HTTPException(status_code=400, detail="slug must start with a letter and contain only letters, numbers, and underscores")
    if (Path(SWALES_ROOT) / slug).exists():
        raise HTTPException(status_code=409, detail=f"Atlas '{slug}' already exists")

    starter = _load_starter(payload.starter)
    starter_props = starter.get("properties", {})
    starter_layers = starter["layers"]   # dict keyed by name
    starter_assets = starter["assets"]
    # create_config/create expect layers as a list with 'name' injected
    starter_layers_list = [{"name": k, **v} for k, v in starter_layers.items()]

    create_statuses[slug] = {
        "creating": True,
        "started_at": datetime.now().isoformat(),
        "finished_at": None,
        "log": [["Starting atlas creation", datetime.now().isoformat()]]
    }

    bbox = {"west": payload.bbox.west, "east": payload.bbox.east,
            "north": payload.bbox.north, "south": payload.bbox.south}

    # Compute H3 resolution that gives ~10 cells across the short axis of the bbox.
    # H3 approximate average edge lengths (km) by resolution.
    _H3_EDGE_KM = {5: 86.745, 6: 32.906, 7: 12.447, 8: 4.642, 9: 1.755, 10: 0.664, 11: 0.252, 12: 0.095}
    _lat_center = (bbox["north"] + bbox["south"]) / 2
    _width_km = (bbox["east"] - bbox["west"]) * math.cos(math.radians(_lat_center)) * 111
    _height_km = (bbox["north"] - bbox["south"]) * 111
    _short_axis_km = min(_width_km, _height_km)
    _target_cell_width_km = max(_short_axis_km / 10.0, 0.001)
    _default_h3_resolution = 8
    for _r in range(5, 13):
        if _H3_EDGE_KM[_r] * 1.732 >= _target_cell_width_km:
            _default_h3_resolution = _r
    _default_h3_resolution = max(5, min(12, _default_h3_resolution))

    feature_collection = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [bbox["west"], bbox["south"]],
                    [bbox["east"], bbox["south"]],
                    [bbox["east"], bbox["north"]],
                    [bbox["west"], bbox["north"]],
                    [bbox["west"], bbox["south"]]
                ]]
            },
            "properties": {
                "name": slug,
                "description": payload.name,
                "app_url": "https://fireatlas.org:9000",
                "versioned_outlets": ["html", "webmap"],
                "default_h3_resolution": _default_h3_resolution,
                **starter_props,
            }
        }]
    }

    async def finish_creating():
        try:
            create_statuses[slug]["log"].append([f"Creating atlas structure (starter: {payload.starter})", datetime.now().isoformat()])
            await asyncio.to_thread(atlas.create,
                layers=starter_layers_list,
                assets=starter_assets,
                data_root=SWALES_ROOT,
                shared_dir=Path(ATLAS_SHARED_DIR),
                feature_collection=feature_collection
            )
            create_statuses[slug]["log"].append(["Atlas structure created", datetime.now().isoformat()])

            # Register email ingest address in SES receipt rule
            try:
                email_addr = f"{slug}@fireatlas.org"
                ses = boto3.client('ses', region_name='us-east-1')
                rule_set = ses.describe_receipt_rule_set(RuleSetName='atlas-email-inlet')
                for rule in rule_set['Rules']:
                    if any('S3Action' in a for a in rule['Actions']):
                        recipients = rule.get('Recipients', [])
                        if email_addr not in recipients:
                            rule['Recipients'] = recipients + [email_addr]
                            ses.update_receipt_rule(RuleSetName='atlas-email-inlet', Rule=rule)
                        break
                create_statuses[slug]["log"].append([f"Email ingest configured for {email_addr}", datetime.now().isoformat()])
            except Exception as ses_err:
                logging.warning(f"Could not configure SES for {slug}: {ses_err}")
                create_statuses[slug]["log"].append([f"Warning: email ingest not configured ({ses_err})", datetime.now().isoformat()])

            # Write a self-contained single-file atlas.geojson: the starter's
            # layers/assets are embedded inline so future config rebuilds
            # (build_atlas_from_geojson) don't depend on the starter file.
            atlas_geojson_path = Path(SWALES_ROOT) / slug / "staging" / "atlas.geojson"
            atlas_geojson = json.loads(json.dumps(feature_collection))
            atlas_geojson['features'][0]['properties'].update({
                "data_root": SWALES_ROOT,
                "shared_dir": ATLAS_SHARED_DIR,
                "base_url": f"https://fireatlas.org/{slug}",
                "port": 9000,
                "admin_emails": [],
                "logo_url": "",
                "layers": starter_layers,
                "assets": starter_assets,
            })
            with open(atlas_geojson_path, 'w') as f:
                json.dump(atlas_geojson, f, indent=2)

            ac = json.load(open(Path(SWALES_ROOT) / slug / "staging" / "atlas_config.json"))
            assets = ac['assets']
            raster_layers = {l['name'] for l in ac['dataswale']['layers']
                             if l.get('geometry_type') == 'raster'}

            # Materialize the starter's inlets to populate base layers. Eddies
            # are intentionally skipped at create time (DEM/hillshade/H3/sim can
            # be slow and run on layers that start empty) — run them later.
            inlet_names = [n for n, a in assets.items() if a.get('type') == 'inlet']
            for inlet_name in inlet_names:
                create_statuses[slug]["log"].append([f"Fetching {inlet_name}", datetime.now().isoformat()])
                try:
                    await asyncio.to_thread(atlas.materialize, ac, inlet_name)
                    out_layer = assets[inlet_name].get('out_layer')
                    if out_layer in raster_layers:
                        await asyncio.to_thread(dataswale_geojson.refresh_raster_layer, ac, out_layer)
                    create_statuses[slug]["log"].append([f"Finished {inlet_name}", datetime.now().isoformat()])
                except Exception as inlet_err:
                    logging.warning(f"Inlet {inlet_name} failed for {slug}: {inlet_err}")
                    create_statuses[slug]["log"].append([f"Warning: {inlet_name} failed ({inlet_err}) — skipped", datetime.now().isoformat()])

            # Inlets only wrote deltas; apply them so the layers actually hold
            # data, and give inlet-less layers an empty FeatureCollection so the
            # webmap doesn't 404 on a missing file. Must precede the outlets,
            # which read these files at materialize time.
            for layer_name, action in await asyncio.to_thread(
                    dataswale_geojson.refresh_all_vector_layers, ac):
                if action.startswith('failed'):
                    logging.warning(f"Layer {layer_name} for {slug}: {action}")
                    create_statuses[slug]["log"].append([f"Warning: layer {layer_name} {action}", datetime.now().isoformat()])
                else:
                    create_statuses[slug]["log"].append([f"Layer {layer_name} {action}", datetime.now().isoformat()])

            # Outlets: webmap must precede html (console HTML checks for the
            # webmap output); notebook first, html last.
            _outlet_order = {"notebook": 0, "webmap": 1, "webedit": 2, "3dview": 3, "html": 9}
            outlet_names = sorted(
                (n for n, a in assets.items() if a.get('type') == 'outlet'),
                key=lambda n: _outlet_order.get(n, 5)
            )
            for outlet_name in outlet_names:
                create_statuses[slug]["log"].append([f"Materializing {outlet_name}", datetime.now().isoformat()])
                try:
                    await asyncio.to_thread(atlas.materialize, ac, outlet_name)
                    create_statuses[slug]["log"].append([f"Finished {outlet_name}", datetime.now().isoformat()])
                except Exception as outlet_err:
                    logging.error(f"Outlet {outlet_name} failed for {slug}: {outlet_err}")
                    logging.error(traceback.format_exc())
                    create_statuses[slug]["log"].append([f"Warning: {outlet_name} failed ({outlet_err}) — skipped", datetime.now().isoformat()])

            create_statuses[slug]["log"].append(["Done", datetime.now().isoformat()])
            create_statuses[slug]["creating"] = False
            create_statuses[slug]["finished_at"] = datetime.now().isoformat()
        except Exception as e:
            logging.error(f"Error creating atlas {slug}: {e}")
            logging.error(traceback.format_exc())
            create_statuses[slug]["log"].append([f"ERROR: {e}", datetime.now().isoformat()])
            create_statuses[slug]["creating"] = False
            create_statuses[slug]["finished_at"] = datetime.now().isoformat()

    background_tasks.add_task(finish_creating)
    return {"status": "success", "creating": True, "atlas_slug": slug,
            "started_at": create_statuses[slug]["started_at"]}


@app.get("/create-status")
async def create_status_check(atlas_slug: str):
    if atlas_slug not in create_statuses:
        raise HTTPException(status_code=404, detail=f"No creation in progress for '{atlas_slug}'")
    s = create_statuses[atlas_slug]
    return {"status": "success", "creating": s["creating"],
            "started_at": s["started_at"], "finished_at": s["finished_at"], "log": s["log"]}


@app.post("/sql_query/{swalename}")
async def execute_sql_query(swalename: str, payload: SQLQueryPayload):
    print("HELLLLO")
    try:
        print(f"SQL Query [{swalename}]: {payload.query}")
        # Load config
        ac = json.load(open(Path(SWALES_ROOT) / swalename / "staging" / "atlas_config.json"))
        #config_path = versioning.atlas_path(ac, "atlas_config.json")
        #ac = json.load(open(config_path))

        # Execute query using outlets.sql_query
        result = outlets.sql_query(
            config=ac,
            outlet_name='sqlquery',
            query=payload.query,
            return_format=payload.return_format
        )
        
        return {
            "status": "success",
            "result": result
        }
    except Exception as e:
        print(f"ERROR executing SQL query. {e}")
        traceback_str = ''.join(traceback.format_tb(e.__traceback__))
        print(traceback_str)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/sql_generate/{swalename}")
async def generate_sql_from_nl(swalename: str, payload: NLSQLPayload):
    """Generate SQL from natural language using Claude."""
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise HTTPException(status_code=503, detail="NL SQL generation not configured")
    try:
        ac = json.load(open(Path(SWALES_ROOT) / swalename / "staging" / "atlas_config.json"))

        # Find the db used by the sqlquery outlet (mirrors outlets.outlet_sqlquery logic)
        sqlquery_cfg = ac.get('assets', {}).get('sqlquery', {})
        db_outlet_name = sqlquery_cfg.get('config', sqlquery_cfg).get('db_outlet', 'sqldb')
        db_path = versioning.atlas_path(ac, "outlets") / db_outlet_name / "atlas.db"

        # Build schema description for the prompt
        schema_lines = []
        if db_path.exists():
            import duckdb as _duckdb
            with _duckdb.connect(str(db_path), read_only=True) as conn:
                tables = [r[0] for r in conn.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
                ).fetchall()]
                for table in sorted(tables):
                    cols = [r[0] for r in conn.execute(
                        f"SELECT column_name FROM information_schema.columns WHERE table_name='{table}'"
                    ).fetchall()]
                    schema_lines.append(f"  {table}({', '.join(cols)})")
        schema_text = '\n'.join(schema_lines) if schema_lines else '  (schema unavailable)'

        prompt = f"""You are a SQL assistant for a geospatial fire atlas database (DuckDB with spatial extension).

Available tables:
{schema_text}

Write a DuckDB SQL query for the following request. Return ONLY the SQL query, no explanation or markdown.

Request: {payload.natural_language}"""

        import anthropic as _anthropic
        client = _anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}]
        )
        sql = message.content[0].text.strip()

        return {"sql": sql, "original_nl": payload.natural_language}
    except HTTPException:
        raise
    except Exception as e:
        print(f"ERROR generating SQL: {e}")
        traceback_str = ''.join(traceback.format_tb(e.__traceback__))
        print(traceback_str)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/save_config/{swalename}")
async def save_config(swalename: str, payload: JSONPayload):
    """Save the atlas configuration file."""
    try:
        config_data = payload.data
        
        # Validate basic structure
        if not config_data.get('name'):
            raise ValueError("Configuration must have a 'name' property")
        if not config_data.get('dataswale'):
            raise ValueError("Configuration must have a 'dataswale' property")
        
        # Construct config path
        config_path = Path(SWALES_ROOT) / swalename / "staging" / "atlas_config.json"
        
        # Create backup of existing config
        if config_path.exists():
            backup_path = config_path.parent / f"atlas_config.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            shutil.copy(config_path, backup_path)
            logging.info(f"Created backup at {backup_path}")
        
        # Save the new configuration
        with open(config_path, 'w') as f:
            json.dump(config_data, f, indent=2)
        
        logging.info(f"Saved configuration to {config_path}")
        
        return {
            "status": "success",
            "message": "Configuration saved successfully",
            "path": str(config_path)
        }
        
    except Exception as e:
        logging.error(f"Error saving configuration: {str(e)}")
        traceback_str = ''.join(traceback.format_tb(e.__traceback__))
        logging.error(traceback_str)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/set-current-version/{swalename}")
async def set_current_version(swalename: str):
    """
    Update the CURRENT symlink to point to the second most recent version (rollback).
    
    Returns information about the version that was set as current.
    """
    try:
        # Load config from staging
        
        config_path = f"{SWALES_ROOT}/{swalename}/CURRENT/atlas_config.json"
        with open(config_path) as f:
            config = json.load(f)
        print(f"Webapp config from: {config_path}")
        # Get versions list, excluding 'staging'
        versions = [v for v in config['dataswale']['versions'] if v != 'staging']
        
        if len(versions) < 2:
            raise HTTPException(
                status_code=400, 
                detail=f"Not enough versions to rollback. Found {len(versions)} version(s), need at least 2."
            )
        
        # Get second most recent version (rollback target)
        rollback_version = versions[-2]
        
        # Get paths using versioning.atlas_path
        current_path = versioning.atlas_path(config, version='CURRENT')
        rollback_path = versioning.atlas_path(config, version=rollback_version)


        print(f"Rolling back. Current: {current_path} Selected: {rollback_path} from {rollback_version} in  {versions}")
        # Remove existing CURRENT symlink if it exists
        # Check is_symlink() first because broken symlinks return False for exists()
        if current_path.is_symlink() or current_path.exists():
            current_path.unlink()
            logging.info(f"Removed existing CURRENT symlink at {current_path}")
        
        # Create new symlink with absolute path
        current_path.symlink_to(rollback_path, target_is_directory=True)
        logging.info(f"Created CURRENT symlink: {current_path} -> {rollback_path}")
        
        return {
            "status": "success",
            "current_version": rollback_version,
            "current_path": str(current_path),
            "target_path": str(rollback_path),
            "message": f"CURRENT now points to {rollback_version}"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error setting current version: {str(e)}")
        traceback_str = ''.join(traceback.format_tb(e.__traceback__))
        logging.error(traceback_str)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/reset-staging/{swalename}")
async def reset_staging(swalename: str):
    """
    Reset staging to match CURRENT version.
    Backs up existing staging before replacing.
    """
    try:
        # Load config from CURRENT
        config_path = f"{SWALES_ROOT}/{swalename}/CURRENT/atlas_config.json"
        with open(config_path) as f:
            config = json.load(f)
        
        print(f"Resetting staging for {swalename}")
        
        # Call versioning function
        result = versioning.reset_staging(config)
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error resetting staging: {str(e)}")
        traceback_str = ''.join(traceback.format_tb(e.__traceback__))
        logging.error(traceback_str)
        raise HTTPException(status_code=500, detail=str(e))


class EmailPhotoPayload(BaseModel):
    subject: str
    sender: str
    atlas_name: str
    image_data: str       # base64-encoded image bytes
    filename: str
    received_at: str
    body_text: str | None = None


class WebPhotoPayload(BaseModel):
    atlas_name: str
    layer_name: str
    image_data: str       # base64-encoded image bytes
    filename: str
    fallback_lat: float | None = None
    fallback_lon: float | None = None


def _send_bounce(from_addr: str, to_addrs, subject: str, body: str):
    """Send a bounce/notification email via SES. Silently logs on failure.

    to_addrs may be a single address string or a list of address strings.
    Handles "Name <email>" format automatically.
    """
    def extract(addr):
        addr = addr.strip()
        return addr.split("<")[1].rstrip(">") if "<" in addr else addr

    if isinstance(to_addrs, str):
        to_addrs = [to_addrs]
    recipients = [extract(a) for a in to_addrs if a]

    try:
        import boto3 as _boto3
        ses = _boto3.client("ses", region_name="us-east-1")
        ses.send_email(
            Source=from_addr,
            Destination={"ToAddresses": recipients},
            Message={
                "Subject": {"Data": subject},
                "Body": {"Text": {"Data": body}},
            },
        )
        logging.info(f"Bounce sent to {recipients}")
    except Exception as e:
        logging.warning(f"Could not send bounce email: {e}")


@app.get("/log/{swalename}")
async def get_log(swalename: str, n: int = 3):
    """Return recent pipeline log entries as use_cases for the technical console."""
    invocations = atlas_logs.fetch_email_invocations(n=n)
    return {"useCases": atlas_logs.format_as_use_cases(invocations)}


@app.post("/ingest/email_photo")
async def ingest_email_photo(payload: EmailPhotoPayload):
    try:
        config_path = Path(SWALES_ROOT) / payload.atlas_name / "staging" / "atlas_config.json"
        ac = json.load(open(config_path))

        # No sender restriction — anyone can submit photos via email.
        # See issue #61 for splitting photo-submission auth from admin_emails.
        admin_emails = ac.get("admin_emails", [])

        # Parse subject — now just the title; layer comes from body or config default
        default_layer = ac.get("email_photo_default_layer", "processing_sites")
        title = email_inlet.parse_subject(payload.subject)

        layer_names = [l["name"] for l in ac["dataswale"]["layers"]]

        # Pre-scan body: if the first non-empty line names a layer, use that
        # layer's editable_columns; otherwise fall back to the default layer's.
        layer_set_lower = {n.lower() for n in layer_names}
        body_lines = [l.strip() for l in payload.body_text.splitlines() if l.strip()]
        prescan_layer = body_lines[0].lower() if body_lines and body_lines[0].lower() in layer_set_lower else None
        editable_columns_source = prescan_layer or default_layer
        editable_layer_cfg = next(
            (l for l in ac["dataswale"]["layers"] if l["name"] == editable_columns_source), {}
        )
        editable_columns = editable_layer_cfg.get("editable_columns", [])
        body = email_inlet.parse_body(payload.body_text, layer_names, editable_columns)

        # Layer resolution: body > config default
        layer_name = body["layer"] or default_layer
        if layer_name not in layer_names:
            logging.warning(
                f"Unknown layer '{layer_name}' for atlas '{payload.atlas_name}'; "
                f"falling back to '{default_layer}'"
            )
            layer_name = default_layer
        if layer_name not in layer_names:
            raise HTTPException(status_code=400, detail=f"Unknown layer: {layer_name}")

        # Decode image and extract GPS
        image_bytes = base64.b64decode(payload.image_data)
        gps = email_inlet.extract_gps(image_bytes)
        if gps is None:
            atlas_addr = f"{payload.atlas_name}@fireatlas.org"
            # _send_bounce disabled — was causing cascade spam during no-GPS quarantine loop
            # _send_bounce(
            #     from_addr=atlas_addr,
            #     to_addrs=[payload.sender] + admin_emails,
            #     subject=f"Re: {payload.subject or '(no subject)'}",
            #     body=(
            #         f"Hi,\n\n"
            #         f"Your photo was received but could not be added to the atlas because "
            #         f"it doesn't contain GPS location data.\n\n"
            #         f"To fix this, please enable location access for the Camera app:\n"
            #         f"  iOS: Settings → Privacy & Security → Location Services → Camera → While Using\n\n"
            #         f"Once enabled, retake the photo and resend it to {atlas_addr}.\n\n"
            #         f"— {payload.atlas_name} Atlas"
            #     ),
            # )
            raise HTTPException(status_code=400, detail="No GPS data found in image EXIF")

        # Upload image to S3
        import boto3
        s3 = boto3.client("s3")
        bucket = os.environ.get("ATLAS_DATA_BUCKET")
        if not bucket:
            raise HTTPException(status_code=500, detail="ATLAS_DATA_BUCKET env var not set")
        timestamp_str = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        s3_key = f"{payload.atlas_name}/media/email_photos/{timestamp_str}_{payload.filename}"
        import mimetypes
        content_type = mimetypes.guess_type(payload.filename)[0] or "application/octet-stream"
        s3.put_object(Bucket=bucket, Key=s3_key, Body=image_bytes,
                      ContentType=content_type, ContentDisposition="inline")
        image_url = f"https://{bucket}.s3.amazonaws.com/{s3_key}"
        logging.info(f"S3 upload: {s3_key} content_type={content_type} content_disposition=inline")

        extra_props = {k: v for k, v in gps.items() if k not in ("lat", "lon")}

        body_props = dict(body["props"])
        if body["raw"]:
            body_props["email_body"] = body["raw"]

        # Build feature and write delta
        feature = email_inlet.build_feature(
            lat=gps["lat"],
            lon=gps["lon"],
            title=title,
            sender=payload.sender,
            timestamp=payload.received_at,
            image_url=image_url,
            body_props=body_props,
            extra_props=extra_props,
        )
        fc = {"type": "FeatureCollection", "features": [feature]}
        deltas_geojson.add_deltas_from_features(ac, None, fc, "create", layer_name=layer_name)

        # Refresh layer
        dataswale_geojson.refresh_vector_layer(ac, layer_name)

        return {
            "status": "ok",
            "layer": layer_name,
            "lat": gps["lat"],
            "lon": gps["lon"],
            "image_url": image_url,
            "content_type": content_type,
        }

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error in ingest_email_photo: {e}")
        traceback_str = "".join(traceback.format_tb(e.__traceback__))
        logging.error(traceback_str)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ingest/web_photo")
async def ingest_web_photo(payload: WebPhotoPayload):
    try:
        config_path = Path(SWALES_ROOT) / payload.atlas_name / "staging" / "atlas_config.json"
        ac = json.load(open(config_path))

        # Validate layer exists
        layer_names = [l["name"] for l in ac["dataswale"]["layers"]]
        if payload.layer_name not in layer_names:
            raise HTTPException(status_code=400, detail=f"Unknown layer: {payload.layer_name}")

        # Decode image and extract GPS
        image_bytes = base64.b64decode(payload.image_data)
        gps = email_inlet.extract_gps(image_bytes)

        if gps is None:
            if payload.fallback_lat is None or payload.fallback_lon is None:
                raise HTTPException(status_code=400, detail="No GPS data found in image and no fallback location provided")
            lat = payload.fallback_lat
            lon = payload.fallback_lon
            extra_props = {}
        else:
            lat = gps["lat"]
            lon = gps["lon"]
            extra_props = {k: v for k, v in gps.items() if k not in ("lat", "lon")}

        # Upload image to S3
        import boto3
        s3 = boto3.client("s3")
        bucket = os.environ.get("ATLAS_DATA_BUCKET")
        if not bucket:
            raise HTTPException(status_code=500, detail="ATLAS_DATA_BUCKET env var not set")
        timestamp_str = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        s3_key = f"{payload.atlas_name}/media/email_photos/web_{timestamp_str}_{payload.filename}"
        import mimetypes
        content_type = mimetypes.guess_type(payload.filename)[0] or "application/octet-stream"
        s3.put_object(Bucket=bucket, Key=s3_key, Body=image_bytes,
                      ContentType=content_type, ContentDisposition="inline")
        image_url = f"https://{bucket}.s3.amazonaws.com/{s3_key}"

        title = Path(payload.filename).stem
        feature = email_inlet.build_feature(
            lat=lat,
            lon=lon,
            title=title,
            sender="web_upload",
            timestamp=datetime.utcnow().isoformat(),
            image_url=image_url,
            extra_props=extra_props,
        )
        fc = {"type": "FeatureCollection", "features": [feature]}
        deltas_geojson.add_deltas_from_features(ac, None, fc, "create", layer_name=payload.layer_name)
        dataswale_geojson.refresh_vector_layer(ac, payload.layer_name)

        return {
            "status": "ok",
            "layer": payload.layer_name,
            "lat": lat,
            "lon": lon,
            "image_url": image_url,
        }

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error in ingest_web_photo: {e}")
        traceback_str = "".join(traceback.format_tb(e.__traceback__))
        logging.error(traceback_str)
        raise HTTPException(status_code=500, detail=str(e))
