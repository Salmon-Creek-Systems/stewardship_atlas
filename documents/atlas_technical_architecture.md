# Stewardship Atlas Technical Specification

  


> **Note**: the settled conceptual model for layers, assets, deltas, refresh,
> and versions lives in [data_architecture.md](data_architecture.md). Where
> this older spec disagrees with it, that document wins.

# Definitions
Here are some ideas and components we'll work with a lot. We'll delve more deeply into them, but let's start with some high level overview and context.

* ***Dataswale***: Core dataset at the heart of a Stewardship Atlas. This is a store of geospatial and other data ephasizing self consistency and standards. There is no other data in an atlas, just ways to view, process, and export this primary data store. Example: GeoPackage of a county's roads, hills, and creeks.
* ***Layer***: A uniquely named single-typed canonicalized dataset in a Dataswale. Example: Roads as geojson linestrings.
* ***Delta***: A standardized “batch” of pending changes to a Dataswale layer. Deltas accumulate and do not modify the layer until a refresh applies them. Example: a new driveway to be added to existing Roads layer.
* ***Inlet***: A job which generates a Delta from external data sources. Example: OpenStreetMap query
* ***Eddy:*** A job which processes a layer in a Dataswale into a new layer. Example: Add h3 indexes to each feature in Roads layer. 
* ***Outlet***: A job which generates some external artifact or representation from a Dataswale. Example: Export Roads and Hills as GeoDF
* ***Atlas***: a specific configuration and implementation of all the above. Example: A Fire Atlas maintained and used by a locel fire safe council.
* ***Version***: A frozen, timestamped snapshot of the dataswale. `staging` is the one editable copy (the "working copy"); publishing freezes it as-is — no computation runs at publish time. A version may not include every outlet artifact (see `versioned_outlets` copy filter) but always contains everything needed to regenerate them, including the full delta history.
* ***Region***: A named polygon feature (currently required to form a bounding box) which may have defined adjacencies.

# Configuration
Configuration is implemented in JSON. Where appropriate , valid GeoJSON should be used - for example, when defining geographic regions for computation or artifacts, they should be simple Polygon Feature Collections when possible. Some templating and interpolation may be used for generating a Stewardship Atlas, but in the end the configuration for it must be a single “flat vanilla” JSON file and kept with the data. We currently store some aspects of Atlas state in the configuration file (eg, versions) which is probably a Bad Idea.

## Dataswale Configuration Fields
* name
* description
* bounding box
* CRS
* metadata (m/c times, authors, access, etc)
* data_root
* versions (a list of available version_ids)
* layers - list of named layer configs

## Atlas Configuration Fields
* dataswale
* assets - dictionary of named asset configs (inlets, eddies, outlets)
 
## Layer Configuration Fields
* name
* geometry_type [point, area, linestring]
* color RGB
* fill_color
* fill_opacity
* vector_width integer
* symbol
* icon-size
* icon-anchor
* interaction
* access [public, internal, admin]
* add_labels
* editable_columns: required "columns" all records will have. 
 * name
 * type: [radio, string]
 * editable: boolean
 * values: list of dictionaries
 * default: single value or dictionary of linked values

## Asset Configuration Fields
While some assets will have additional configuration data, they all need to have at least  the following:
* asset_type : [inlet, eddy, outlet]
* fetch_type : label mapping to specific implemented method [
* interaction : would a user directly engage with the data (maybe this is in the Atlas Layer conf actually…)
* data_type: vector, raster, (lidar), (document)
* in_layers: list of layer names
* out_layer: single layer name
* feature_id_field: by default, as deltas are ingested into layer data, they will get a UUID4(name, geometry) ID assigned. It is not a great idea, but this field let’s you instead specify some other Property to use.
* inpath_template
* outpath_template

* attribution
  * url
  * description
  * license
  * citation



# Implementation 
## Dataswale
Possible substrates for the Dataswale include:
* geojson in local/cloud files
* GeoParquet in cloud storage 
* local GPKG
* DuckDB/PG

Any implementation should provide some basic shared functionality:
* create/delete a new dataswale
* create new version
* load_asset ( ) -> [feature_set, raster]
* build_asset_from_deltas ( ) ->  [feature_set, raster]
* alter_deltas
* add_deltas
* assign_feature_id

### Current Dataswale Implemention
A dataswale is a (local) directory structure of json, geojson, and geotiff files:
* shared_data_store
* swale name
  * version name	
	* atlas_config.json
	* layers
	* deltas
	* outlets
	

## Delta Queue
A FeatureCollection with at target asset. Each feature has AnnotationProperties and AnnotationGeometry.
There are three operations supported - this is specified by a “annotation_type” Property as one of:

AnnotationProperties is a JSON object. It can have the following “special” fields:
* annotation_type: Annotate (default), Create Delete
* annotation_schema: a simple schema describing the fields of the AnnotationProperties.
* annotation_join: specifies how the AnnoGeom will be spatially joined against the asset record geometries, defaulting to SimpleGeometryIntersection but including at least PropertyMatch.
* annotation_timestamp: what it says on the tin. Annos are applied in this order.
* annotation_property_match: in addition to the Spatial Join it may be useful to allow specification of matches against properties. This can be used to “tune” spatial joins to make them more or less specific.
* 

Note that the methods to apply a queue to a dataswale should be provided by the dataswale, which shouldn’t need to know much about how the DQ was populated - inlet configuration. This implies that the FeatureTransformation should happen outside the dataswale - likely as part of retrieving the deltas as a queue of FC. A DQ implementation  should provide:
* create
* add_deltas
* (validate)
* queue : return an iterator over a FeatureCollection
* transform : apply simple configured transformations to Features, generally once as part of DQ retrieval.

### Current Delta queue Implemention
A directory  for each Layer contains delta files, and a `work` subdir where they are put after being processed into the dataswale. The `work` archive is the layer's edit history; it is never auto-deleted and is retained in published versions.

## Refresh and Publish
The operational model (see [data_architecture.md](data_architecture.md) for the full treatment):

* **Refresh is the only operation that mutates a delta-fed layer.** Inlets, eddies, and accumulated deltas are inputs a refresh consumes. Two modes, chosen at run time (never per-layer config): **update** applies pending deltas onto the current layer (cheap, default); **rebuild** re-runs the layer's direct inlets for a fresh base and applies pending deltas onto an emptied layer (archived `work/` deltas are not replayed). Manual-only layers (no inlet) have nothing to rebuild.
* **Refresh cascades downstream only** — dependent eddies, then outlets — never upstream. A no-cascade option exists for development velocity.
* **Publish is a pure snapshot.** It copies `staging` to a timestamped version and repoints `CURRENT`; it runs no inlet, eddy, or outlet. What the user saw in staging is exactly what publishes. Expensive outlets (PDF runbook, gazetteer) must be built explicitly before publishing — via a refresh cascade, the console Build buttons, or `/materialize_asset`.
* Orchestration is a Dagster asset graph built from the atlas config (`atlas_dagster.py`); edges are ordering-only, so any asset can equally be materialized directly from a notebook.

## Inlets
Inlets take data from somewhere in the world and move it into the Dataswale  as canonically formatted assets. In particular, an `inlet` always generates a Delta associated with a given Asset (or set of assets I suppose). The inlet should do as little processing of the delta as possible so this can be done in one place uniformly when the deltas are applied to their assets.
### Examples:
API calls, local file reads, etc
* `fetch_uri`
* `fetch_overture`
* `fetch_osm`
* `load_ogr`
* `load_tiff`
* fetch_google_sheet
* sql_query


## Eddy
Eddies process layers back into the dataswale - sometimes modifying an input layer in place, sometimes generating a new layer. Because they are entirely internal to the dataswale they can make a lot of simplifying assumptions about their data, its quality, and formatting, so they are where we try to do most computation and heavy lifting.
### Examples
* `resample_raster`
* `alter_properties`
* assign index to features in layers (H3, S2, etc)
* assign a region to all the overlapping features in a layer
* calculate a path distance from another layer


## Outlet
Outlets process the dataswale into some external artifact. This can be a direct translation of the dataswale into some natural format, like a GPKG; a map or representation of some aspect of the data; or some bit of code or tooling.

### Current Outlets
* interactive web map (hosted maplibre)
* RunBook (PDF & web)
* HTML - console view of Atlas, at multiple access levels
* GeoPDF
* gPKG
* API Server - One important outlet for a Stewardship Atlas generates an API service. This is one way to materialize Assets and manage data, generally using web apps and services. Included methods:
  * SQL query - Accept SQL (ddb and st flavored) as text payload.  Read only. Return as any of:
	* CSV
	* (Geo)JSON
	* URL to spreadsheet
	* URL to download

  * submit_delta - Targeting a single layer in the Dataswale, upload GeoJSON annotation FeatureCollection. Optionally process dependencies. action: [create (default), annotate, delete]
  * clear_layer
  * new_version


### Future Outlets
* COG
* map_image
* web_image
* MBTiles
* GPKG
* SQL interface service
  * DuckDB
  * Athena
  * Sedona
* API interface
* PDF
* GeoPDF
* Spreadsheet
* Gazeteer
* RunBook
  * HTML
  * PDF
* Sedona
* Iceberg
* Upstream Update
  * OSM
  * Overture
  * Calfire?
  * Fed/County/State
