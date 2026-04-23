# uvicorn --port 9999 --host 0.0.0.0 json_upload:app --reload --log-level trace

SWALES_ROOT=/root/swales_dev ATLAS_DATA_BUCKET=scs-atlas-data /usr/bin/python3 /usr/local/bin/uvicorn --port 9000 --host 0.0.0.0 webapp:app \
    --reload --log-level trace \
    --ssl-certfile /etc/letsencrypt/live/fireatlas.org-0001/fullchain.pem \
    --ssl-keyfile /etc/letsencrypt/live/fireatlas.org-0001/privkey.pem
