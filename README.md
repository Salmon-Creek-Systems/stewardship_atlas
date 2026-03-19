# Stewardship Atlas

Stewardship Atlas is an open platform for building and maintaining place-based geospatial atlases — combining public, community, and private datasets into a managed, shareable resource. It is cloud-native and requires no installation, works with other tools and platforms via open standards, and can generate offline-usable artifacts.

[![Screenshot 1](documents/images/screenshot1_thumb.png)](documents/images/screenshot1.png)

[![Screenshot 2](documents/images/screenshot2_thumb.png)](documents/images/screenshot2.png)

## Use Cases

**Community organizations and emergency services** — A volunteer fire department, fire safe council, or similar org can maintain a living atlas of their area: correcting inaccurate public data, managing sensitive internal records, coordinating field projects, and contributing verified data back to regional or national sources. Field staff access it from a phone browser — no app required.

**Researchers and data practitioners** — Anyone working with geospatial datasets can use Atlas as a processing and publication layer: import from standard formats and external sources, apply transformations and spatial analysis, then export or publish as maps, narratives, or structured datasets.

**Developers and platform builders** — Atlas provides a geospatial data backend with a REST API and standard format I/O, making it straightforward to add geospatial capability to an existing application. Cloud-native by design: S3-backed data, containerizable compute, scale-to-zero when idle.

**Emergency response and incident coordination** — During an active incident, a current atlas provides the situational awareness layer: up-to-date infrastructure, access routes, hazard data, and resource locations — the payoff for keeping data current day-to-day.

**Regional aggregation and planning** — A county agency, regional planning body, or multi-org network can pull together data from multiple contributing organizations into a unified, current picture that no single contributor maintains alone.

## Requirements and Optional Dependencies

**Requirements**

- Python 3.10+
- `geojson`, `shapely`, `requests`

**Optional dependencies**

Install only what your use case needs.

| Capability | Dependency |
|---|---|
| Web API | `fastapi`, `uvicorn` |
| Spatial queries and joins | `duckdb` |
| GeoDataFrame operations | `geopandas` |
| OpenStreetMap inlets | `overpass` |
| Raster processing and tile generation | GDAL (`gdal_translate`, `gdalwarp`, `gdal2tiles`) |
| Cloud raster sources (STAC / Planetary Computer) | `pystac-client`, `planetary-computer`, `mercantile` |
| Print atlas and PDF generation | QGIS with PyQGIS |
| Advanced spatial analysis | GRASS GIS |
| Image processing (map sprites) | `Pillow` |
| AWS cloud deployment | `boto3`, AWS account |

**Experimental and edge cases**

These integrations exist in the codebase or are planned but are not part of the standard workflow:

- **Mapnik** — alternative map rendering pipeline (partial implementation)
- **Leaflet** — alternative to the default MapLibre webmap outlet
- **PostGIS** — relational spatial backend, as an alternative to file-based GeoJSON storage
- **Apache Sedona** — distributed spatial processing for large-scale datasets

## Documentation

- [User Guide](documents/user_guide.md)
- [Data Interaction Guide](documents/data_interaction_guide.md)
- [Technical Architecture](documents/atlas_technical_architecture.md)
- [Help](documents/help/)
