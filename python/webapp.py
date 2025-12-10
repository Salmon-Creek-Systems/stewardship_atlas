from fastapi import FastAPI, HTTPException, BackgroundTasks, logger
from fastapi.middleware.cors import CORSMiddleware
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

import sys
sys.path.insert(0, "/root/stewardship_atlas/python")

# Boring Imports
import sys, os, subprocess, time, json, string, random, math


SWALES_ROOT = "/root/swales"


# our Imports|
import atlas
import dataswale_geojson
import outlets
import versioning   
import deltas_geojson
import vector_inlets
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

class JSONPayload(BaseModel):
    data: Dict[str, Any]

class PinToPOIPayload(BaseModel):
    url: str
    poi_type: str
    asset: str

class SQLQueryPayload(BaseModel):
    query: str
    return_format: str = 'csv'  # Default to CSV format

def extract_coordinates_from_url(url: str) -> tuple[float, float]:
    """Extract latitude and longitude from a Google Maps URL."""
    try:
        # Handle short URLs by following redirects
        if 'goo.gl' in url or 'maps.app.goo.gl' in url:
            response = requests.get(url, allow_redirects=True)
            url = response.url

        # Parse the URL
        parsed = urlparse(url)
        if 'maps.google.com' in parsed.netloc:
            # Handle different URL formats
            if '@' in url:
                # Format: https://www.google.com/maps/@37.7749,-122.4194,15z
                coords = url.split('@')[1].split(',')
                lat, lon = float(coords[0]), float(coords[1])
            else:
                # Format: https://www.google.com/maps?q=37.7749,-122.4194
                query = parse_qs(parsed.query)
                if 'q' in query:
                    coords = query['q'][0].split(',')
                    lat, lon = float(coords[0]), float(coords[1])
                else:
                    raise ValueError("Could not find coordinates in URL")
        else:
            raise ValueError("Not a valid Google Maps URL")

        return lat, lon
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
        ac = json.load(open(f"/root/data/{swale}_atlas_config.json"))
        dc = json.load(open(f"/root/data/{swale}/stage/dataswale_config.json"))
        # res = atlas.asset_materialize(ac, dc, ac['assets'][asset + "_delta"])
        res = atlas.asset_materialize(ac, dc, ac['assets'][asset])
        res_json = {
            "status": "success",
            "message": f"Refreshed layer {asset}: {res}",
            "asset": asset,
            "home": f"https://internal.fireatlas.org/{swale}/html/admin.html"
        }
        print(res_json)
        return res_json
    except Exception as e:
        print(f"ERROR refreshing. {e}")
        traceback_str = ''.join(traceback.format_tb(e.__traceback__))
        print(traceback_str)
        raise HTTPException(status_code=500, detail=str(e))

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
        print(f"publish loading config from {config_path}")
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
                # logging.info(f"Starting publish with ac['dataswale']: {ac['dataswale'].get('versioned_outlets',[])}")

                for outlet_name in ac['dataswale'].get('versioned_outlets', []):
                    print(f"Materializing outlet: {outlet_name}")
                    #logger.info(f"Materializing outlet: {outlet_name}")
                    publish_status["log"].append(  [ (f'Materializing {outlet_name}', datetime.now().isoformat()) ])
                    atlas.materialize(ac, outlet_name,outlets.asset_methods)
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
                atlas.materialize(ac, 'html',outlets.asset_methods)
                return str(res)
            except Exception as e:
                print(f"E! {e}")
                raise HTTPException(status_code=500, detail=str(e))



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

@app.post("/sql_query/{swalename}")
async def execute_sql_query(swalename: str, payload: SQLQueryPayload):
    print("HELLLLO")
    try:
        print(f"SQL Query [{swalename}]: {payload.query}")
        # Load config
        ac = json.load(open(f"/root/swales/{swalename}/staging/atlas_config.json"))
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
