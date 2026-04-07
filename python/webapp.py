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
import vector_inlets
import email_inlet
import atlas_logs
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


class JSONPayload(BaseModel):
    data: Dict[str, Any]

class PinToPOIPayload(BaseModel):
    url: str
    poi_type: str
    asset: str

class SQLQueryPayload(BaseModel):
    query: str
    return_format: str = 'csv'  # Default to CSV format

class BBox(BaseModel):
    west: float
    east: float
    north: float
    south: float

class CreateAtlasRequest(BaseModel):
    name: str   # display name (e.g. "Salmon Creek VFD")
    slug: str   # atlas ID / directory name (e.g. "scvfd")
    bbox: BBox

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

@app.post("/delta_upload/{swalename}")
async def json_upload(payload: JSONPayload, swalename: str):
    try:
        delta_package = payload.data
        fc = delta_package #['features']
        layer = delta_package['layer']
        action = delta_package['action']
        print(f"delta_upoad.=: {action} for {layer}: delta_package")
        config_path = Path(SWALES_ROOT) / swalename / "staging" / "atlas_config.json"
        print(f"loading config from {config_path}")
        ac = json.load(open(config_path))
        delta_path = deltas_geojson.delta_path_from_layer(ac, layer, action)
        with open(delta_path, "w") as f:
            json.dump(fc, f)

        print(f"refreshing {layer} after {action}")
        res = dataswale_geojson.refresh_vector_layer(ac, layer)
        
        
        return {
            "status": "success",
            "message": f"Data stored successfully, refreshed: {res}",
            "filename": os.path.basename(delta_path),
            "path": delta_path}
        return {"status": "success"}
    except Exception as e:
        print(f"ERROR in json_upload. {e}")
        traceback_str = ''.join(traceback.format_tb(e.__traceback__))
        print(traceback_str)
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

            
        async def finish_publishing():
            try:
                print(f"Starting publish with ac: {ac['dataswale'].get('versioned_outlets',[])}\n-----\n{ac}\n---")
                publish_status["log"].append(  [ (f"Starting publish with ac: {ac['dataswale'].get('versioned_outlets',[])}\n-----\n---", datetime.now().isoformat()) ])
                # logging.info(f"Starting publish with ac['dataswale']: {ac['dataswale'].get('versioned_outlets',[])}")

                for outlet_name in ac['dataswale'].get('versioned_outlets', []):
                    print(f"Materializing outlet: {outlet_name}")
                    logging.info(f"LOG Materializing outlet: {outlet_name}")
                    publish_status["log"].append(  [ (f'Materializing {outlet_name}', datetime.now().isoformat()) ])
                    atlas.materialize(ac, outlet_name)
                    publish_status["log"].append(  [ (f'Finished materializing {outlet_name}', datetime.now().isoformat()) ])
                # res = versioning.publish_new_version(ac)
                publish_status["log"].append(  [ ('Publishing new version', datetime.now().isoformat()) ])

                res = versioning.publish_new_version(ac)
                publish_status["log"].append(  [ ('Finished publishing new version', datetime.now().isoformat()) ])
                # res = atlas.asset_materialize(ac, dc, ac['assets']['gazetteer'])
                publish_status["finished_at"] = datetime.now().isoformat()
                publish_status["publishing"] = False
                # publish_status["log"] = []
                #logger.info(res_json)
                # let's always refresh the HTML after all this.
                atlas.materialize(ac, 'html')
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


@app.post("/create_atlas")
async def create_atlas_endpoint(payload: CreateAtlasRequest, background_tasks: BackgroundTasks):
    slug = payload.slug.strip()
    if not re.match(r'^[a-zA-Z][a-zA-Z0-9_]*$', slug):
        raise HTTPException(status_code=400, detail="slug must start with a letter and contain only letters, numbers, and underscores")
    if (Path(SWALES_ROOT) / slug).exists():
        raise HTTPException(status_code=409, detail=f"Atlas '{slug}' already exists")

    create_statuses[slug] = {
        "creating": True,
        "started_at": datetime.now().isoformat(),
        "finished_at": None,
        "log": [["Starting atlas creation", datetime.now().isoformat()]]
    }

    bbox = {"west": payload.bbox.west, "east": payload.bbox.east,
            "north": payload.bbox.north, "south": payload.bbox.south}

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
                "versioned_outlets": ["html", "webmap"]
            }
        }]
    }

    layers_path = str(versioning.atlas_path(local_path='configuration/starter_layers.json', version='app'))
    assets_path = str(versioning.atlas_path(local_path='configuration/starter_assets.json', version='app'))

    async def finish_creating():
        try:
            create_statuses[slug]["log"].append(["Creating atlas structure", datetime.now().isoformat()])
            atlas.create(
                layers_path=layers_path,
                assets_path=assets_path,
                data_root=SWALES_ROOT,
                shared_dir=Path(ATLAS_SHARED_DIR),
                feature_collection=feature_collection
            )
            create_statuses[slug]["log"].append(["Atlas structure created", datetime.now().isoformat()])

            ac = json.load(open(Path(SWALES_ROOT) / slug / "staging" / "atlas_config.json"))
            for inlet_name in ["public_roads", "public_creeks", "public_landmarks"]:
                create_statuses[slug]["log"].append([f"Fetching {inlet_name}", datetime.now().isoformat()])
                try:
                    atlas.materialize(ac, inlet_name)
                    create_statuses[slug]["log"].append([f"Finished {inlet_name}", datetime.now().isoformat()])
                except Exception as inlet_err:
                    logging.warning(f"Inlet {inlet_name} failed for {slug}: {inlet_err}")
                    create_statuses[slug]["log"].append([f"Warning: {inlet_name} failed ({inlet_err}) — skipped", datetime.now().isoformat()])
            for outlet_name in ["html", "webmap", "webedit"]:
                create_statuses[slug]["log"].append([f"Materializing {outlet_name}", datetime.now().isoformat()])
                atlas.materialize(ac, outlet_name)
                create_statuses[slug]["log"].append([f"Finished {outlet_name}", datetime.now().isoformat()])

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

        # Validate sender
        admin_emails = ac.get("admin_emails", [])
        sender_addr = payload.sender.strip().lower()
        if not any(sender_addr == e.strip().lower() or sender_addr.endswith(f"<{e.strip().lower()}>")
                   for e in admin_emails):
            raise HTTPException(status_code=403, detail=f"Sender not authorised: {payload.sender}")

        # Parse subject
        default_layer = ac.get("email_photo_default_layer", "poi")
        layer_name, title = email_inlet.parse_subject(payload.subject, default_layer)

        # Validate layer exists
        layer_names = [l["name"] for l in ac["dataswale"]["layers"]]
        if layer_name not in layer_names:
            raise HTTPException(status_code=400, detail=f"Unknown layer: {layer_name}")

        # Decode image and extract GPS
        image_bytes = base64.b64decode(payload.image_data)
        gps = email_inlet.extract_gps(image_bytes)
        if gps is None:
            atlas_addr = f"{payload.atlas_name}@fireatlas.org"
            _send_bounce(
                from_addr=atlas_addr,
                to_addrs=[payload.sender] + admin_emails,
                subject=f"Re: {payload.subject or '(no subject)'}",
                body=(
                    f"Hi,\n\n"
                    f"Your photo was received but could not be added to the atlas because "
                    f"it doesn't contain GPS location data.\n\n"
                    f"To fix this, please enable location access for the Camera app:\n"
                    f"  iOS: Settings → Privacy & Security → Location Services → Camera → While Using\n\n"
                    f"Once enabled, retake the photo and resend it to {atlas_addr}.\n\n"
                    f"— {payload.atlas_name} Atlas"
                ),
            )
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

        # Build extra properties from EXIF
        extra_props = {k: v for k, v in gps.items() if k not in ("lat", "lon")}

        # Build feature and write delta
        feature = email_inlet.build_feature(
            lat=gps["lat"],
            lon=gps["lon"],
            title=title,
            sender=payload.sender,
            timestamp=payload.received_at,
            image_url=image_url,
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
