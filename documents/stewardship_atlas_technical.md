# Stewardship Atlas Technical Specification

  


# Definitions
* ***Dataswale***: core dataset at the heart of a Stewardship Atlas.
* ***Layer***: A named typed canonicalized dataset in a Dataswale.
* ***Delta***: A standardized “batch” of Geo data to be applied to a Dataswale
* ***Inlet***: A job which generates a Delta from external data sources
* ***Eddy:*** A job which processes a layer in a Dataswale into a new layer
* ***Outlet***: A job which generates some external artifact or representation from a Dataswale
* ***Atlas***: a specific configuration and implementation of all the above.

# Configuration
Configuration is implemented in JSON. Where appropriate , valid GeoJSON should be used - for example, when defining regions for computation or artifacts, they should be simple Polygon Feature Collections. Some templating and interpolation may be used for generating a Stewardship Atlas, but in the end the configuration for it must be a single “flat vanilla” JSON file and kept with the data. We currently store some aspects of Atlas state in the configuration file (eg, versions) which is probably a Bad Idea.
## Dataswale
* name
* description
* bounding box
* CRS
* metadata (m/c times, authors, access, etc)
* data_root
* versions (?)
* assets
* layers


## Atlas
* dataswale
* assets
  * in_layers
  * out_layers
  * ~~asset_type: [inlet, outlet, eddy]~~
  * asset_configuration: [asset_config_name or JSON]
## Asset
While some assets will have additional configuration data, they all need to have at least  the following:
* asset_type : [inlet, eddy, outlet]
* fetch_type : label mapping to specific implemented method
* interaction : would a user directly engage with the data (maybe this is in the Atlas Layer conf actually…)
* data_type: vector, raster, lidar 
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

### Inlets
Inlets take data from somewhere in the world and move it into the Dataswale  as canonically formatted assets. In particular, an `inlet` always generates a Delta associated with a given Asset (or set of assets I suppose). The inlet should do as little processing of the delta as possible so this can be done in one place uniformly when the deltas are applied to their assets.
#### Examples:
* `fetch_uri`
* `fetch_overture`
* `fetch_osm`
* `load_ogr`
* `load_tiff`
* fetch_google_sheet
* sql_query
	

### Eddy
Eddies process assets into new assets within the dataswale. So they can make a lot of simplifying assumptions about their data, its quality, and formatting.
#### Examples
* `resample_raster`
* `alter_properties`
### Outlet
Outlets process the dataswale into some external artifact. This can be a direct translation of the dataswale into some natural format, like a GPKG; a map or representation of some aspect of the data; or some bit of code or tooling.

#### Examples
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

## API
One important outlet for a Stewardship Atlas generates an API service. This is one way to materialize Assets and manage data, generally using web apps and services. All methods include parameters:
* dataswale name
* auth

### SQL query
Accept SQL (ddb and st flavored) as text payload.  Read only.
Return as any of:
* CSV
* (Geo)JSON
* URL to spreadsheet
* URL to download

### submit_delta
Targeting a single layer in the Dataswale, upload GeoJSON annotation FeatureCollection. Optionally process dependencies.

#### Parameters
* action: [create (default), annotate, delete]

### 