# Quick Atlas 

Goal: allow quick generation of minimal atlas

* data in cloud storage
* inputs: name and bbox
* data: default general public layers
* transient - option to delete in N days


## Configuration
Generate `atlas_config.json` from name and bbox (or geojson)

Use specific `default_quick_config.json` - includes layers/lifespan/etc

optionally set things like `admin_emails`

## Cloud storage
dataswale hosted in S3

mount with S3fs for now - allows web access etc

## Layers
default layer specification - part of config task

## transient 
Based on config parameter `lifespan` which defaults to 0, set S3 bucket rule to expore `/swales/*` based on age.
