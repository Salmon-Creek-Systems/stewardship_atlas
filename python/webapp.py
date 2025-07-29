from fastapi import FastAPI, HTTPException, BackgroundTasks, logger
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import json
from datetime import datetime
import os
from typing import Dict, Any
import asyncio
import logging
import traceback
import requests
import re
from urllib.parse import urlparse, parse_qs
from pathlib import Path

import sys
sys.path.insert(0, "/root/internal/python")

# Boring Imports
import sys, os, subprocess, time, json, string, random, math


SWALES_ROOT = "/root/swales"


# our Imports|
import atlas
import dataswale_geojson
import atlas_outlets
import versioning   
import deltas_geojson
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
    logging.error("HELLO WORLD")
    try:
        # Check if already publishing
        if publish_status["publishing"]:
            return {
                "status": "error",
                "message": "Publishing already in progress",
                "publishing": True,
                "started_at": publish_status["started_at"]
            }
        
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
        async def test_finish_publishing():
            await asyncio.sleep(5)
            publish_status["log"].append(  [ ('update', datetime.now().isoformat()) ])

            await asyncio.sleep(5)
            publish_status["log"].append(  [ ('update', datetime.now().isoformat()) ])

            await asyncio.sleep(5)
            publish_status["log"].append(  [ ('update', datetime.now().isoformat()) ])

            await asyncio.sleep(5)
            publish_status["log"].append(  [ ('done', datetime.now().isoformat()) ])

            publish_status["finished_at"] = datetime.now().isoformat()
            publish_status["publishing"] = False
            publish_status["log"] = []
            
        async def finish_publishing():
            try:
                ac = json.load(open(f"../atlases/{swale}_atlas_config.json"))
                dc = json.load(open(f"/root/data/{swale}/dataswale_config.json"))
                # res = atlas.asset_materialize(ac, dc, ac['assets'][asset + "_delta"])

                res = atlas.asset_materialize(ac, dc, ac['assets']['gazetteer'])
                publish_status["finished_at"] = datetime.now().isoformat()
                publish_status["publishing"] = False
                publish_status["log"] = []
                print(res_json)
                return res_json
            except Exception as e:
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
        #ac = json.load(open(f"/root/data/{swalename}_atlas_config.json"))
        config_path = versioning.atlas_path(swalename, "atlas_config.json")
        ac = json.load(open(config_path))

        # Execute query using outlets.sql_query
        result = atlas_outlets.sql_query(
            config=ac,
            outlet_name=swalename,
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
