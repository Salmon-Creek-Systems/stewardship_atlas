# Stewardship Atlas User Guide

## Overview

A Stewardship Atlas is a data set; a configuration for storing, processing, and sharing that data set; and a set of implementions for doing so.

More concretely it is a set of maps and documents tied to specific types of planning and implemention in a specific geographic area. Examples might include:
* Wildfire Planning and Response in a specific community
* Prioritization of projects and funding with geographic aspects - natural resource organizations, advocacy groups, etc
* Grantwriters needing to gather geospatial data and maps for proposals
* researchers and practitioners looking to work across different platforms, data formats, and toolchains in a consistent low-frction way.

For more about the philosopy and design principles behind it see our [vision.markdown](Vision Statement) and for more low level technical detail see our [atals_architecture.markdown](Architectural OVerview) and [index.html](Code Documentation).


## Examples and Use Cases
* I just want to look at a map
* I want to download a map I can use offline or in an app
* I need to add a road to an existing map
* I need to change the address on a building
* I'd like to export (some parts of) the dataset for use in another program or platform.
* I need a printable version of my Atlas
* I'd like to share a link to a view in my map.

## Access and Data Control
A Stewardship Atlas supports three simple "levels" of access to and control of data:
* Public: anyone in the world with no authentication. Useful to share in the field, not the place for anything senstive. "Read Only"
* Internal: authenticated users can view this, but not change it. "Read Only"
* Admin: only specific users can access. Can edit the data seen by the other two access classes, release new versions of the data, and generate new output artifacts. "Read/Write Access"

Currently the Stewardship does not provide user managment - there are user/password pairs your Admin users can generate and share.

## Viewing Data

### Interactive Web Map

The easiest way to explore your Stewardship Atlas is through the **web map interface**. Access it by navigating to your atlas URL and clicking on "Webmap" (e.g., `https://your-atlas.org/webmap`).

**Features:**
- Pan and zoom to explore your area
- Toggle layers on/off to view different data
- Click features to see their attributes
- Search for specific locations or features
- View data at multiple zoom levels

The web map is available at all access levels (Public, Internal, Admin) depending on how each layer is configured.

### Printable Maps and Documents

For offline use or formal documentation, the atlas generates several types of output:

**Gazetteer**: A grid-based "map book" that covers your entire area systematically. Each page shows a specific grid cell with all configured layers. Perfect for:
- Field reference guides
- Emergency response planning
- Systematic area documentation

**Runbook**: Custom map pages focused on specific regions of interest (fire stations, project sites, hazard zones, etc.). Each page includes:
- Detailed map of the region
- Contextual overview map
- Navigation links to adjacent regions
- Custom annotations and notes

**GeoPDF Output**: Modern PDF maps that preserve:
- Layer structure (toggle layers on/off in Adobe Reader or other PDF viewers)
- Georeferencing (use in GIS applications)
- Vector data (not just images - text stays searchable)

Access these through your atlas outlets (links typically at `/gazetteer` and `/runbook`).

### Jupyter Notebooks

For exploratory analysis and custom workflows, access the **Jupyter notebook interface**. This provides:
- Interactive Python environment
- Direct access to all atlas data
- Pre-configured map visualization
- Ability to run custom analyses
- Export results in various formats

Notebooks are typically restricted to Admin access and available at `/notebook`.

### Files and Exports to Other Platforms

All atlas data can be exported for use in other tools:

**Download Individual Layers:**
- Navigate to webmap → click layer name → "Download Layer"
- Available formats: GeoJSON, Shapefile, GeoPackage, CSV
- Use in QGIS, ArcGIS, Google Earth Pro, or any GIS software

**Access via GeoPackage:**
- Download the complete atlas as a single `.gpkg` file
- Open directly in QGIS, ArcGIS Pro, or other modern GIS applications
- All layers included with styling and attribute data preserved

**SQL Query Interface** (Admin only):
- Run custom SQL queries against the atlas database
- Export query results as CSV or GeoJSON
- Great for generating reports or filtered datasets

**API Access** (for developers):
- Direct file access to layers: `/layers/{layer_name}/{layer_name}.geojson`
- Programmatic access for integration with other systems

## Curating and Editing Data

As a community member with Internal or Admin access, you can help maintain and improve the atlas data.

### Web-Based Editing

The **Edit Map** interface (Admin only) provides simple tools for common tasks:

**Adding Features:**
1. Select the layer you want to edit
2. Choose the drawing tool (point, line, or polygon)
3. Draw on the map
4. Fill in attribute information in the popup form
5. Save your changes

**Editing Existing Features:**
1. Click a feature to select it
2. Click "Edit" in the popup
3. Update attributes or move/reshape the geometry
4. Save changes

**Common Editing Tasks:**
- Update building addresses
- Add new roads or trails
- Mark changed hydrant locations
- Update facility information
- Add incident markers

See detailed instructions: [Editing Guide](help/draw_vector.md)

### Uploading Data

If you have data from another source (GPS tracks, surveys, external datasets):

**Upload Vector Data:**
1. Navigate to Edit Map
2. Select target layer
3. Click "Upload" and choose your file
4. Supported formats: GeoJSON, Shapefile (zipped), KML, GPX
5. Review and confirm the import

This is great for:
- GPS field surveys
- Data from external agencies
- Collaborative data collection
- Migration from other platforms

See: [Upload Guide](help/upload_vector.md)

### Direct Python Access (Advanced)

For bulk operations or complex edits, you can work directly with the data using Python:

```python
from dataswale_geojson import layer_as_featurecollection, write_layer

# Load a layer
config = {...}  # Your atlas configuration
features = layer_as_featurecollection(config, 'roads')

# Make changes
for feature in features['features']:
    # Your logic here
    pass

# Save changes
write_layer(config, 'roads', features)
```

This approach is useful for:
- Batch updates across many features
- Automated data processing
- Complex spatial operations
- Integration with external data sources

### Data Versioning

The atlas maintains versions of your data, so you can:
- Roll back changes if needed
- Track what changed and when
- Test edits before publishing
- Maintain stable "released" versions

Admin users can create new versions and promote them when ready.

## Sharing Data

### Sharing Views and Links

**Share a specific map view:**
1. Navigate to the area of interest in the web map
2. Configure which layers are visible
3. Copy the URL from your browser
4. Share the link - recipients see the same view

The URL encodes the map position and visible layers, making it easy to direct others to specific locations or configurations.

### Sharing Exports

**For collaborators using GIS software:**
1. Download the layer or full GeoPackage
2. Share the file via email, cloud storage, or shared drive
3. Recipients can open directly in their GIS application

**For general audiences:**
1. Export the Gazetteer or Runbook PDFs
2. Share via web link or distribute printed copies
3. GeoPDF format works in free PDF readers

### Access Control

Control who can see what using the three-tier access system:
- **Public layers**: Available to anyone without login
- **Internal layers**: Require authentication but visible to all logged-in users
- **Admin layers**: Restricted to specific admin accounts

Configure access per layer in your layer configuration files.

### Embedding Maps

For websites or presentations, you can embed atlas maps:
- Use the web map URL in an iframe
- Link to specific Gazetteer pages
- Embed exported images from print outputs

## Technical Details

### System Requirements

**For Viewing (Web Map/PDFs):**
- Modern web browser (Chrome, Firefox, Safari, Edge)
- Internet connection for web map
- PDF reader for GeoPDF (Adobe Reader recommended for full features)

**For Editing:**
- Admin account credentials
- Web browser with JavaScript enabled
- Stable internet connection

**For GIS Integration:**
- QGIS 3.x, ArcGIS Pro, or other modern GIS software
- Ability to read GeoJSON, GeoPackage, or Shapefile formats

**For Programming:**
- Python 3.8+
- QGIS Python libraries (for advanced operations)
- Jupyter notebook support (optional)

### Data Formats

The atlas uses open, standard formats:
- **Vector data**: GeoJSON (primary), GeoPackage, Shapefile
- **Raster data**: GeoTIFF
- **Configuration**: JSON
- **Output**: GeoPDF, PNG, HTML

### Getting Help

**Documentation:**
- How-to guides: [help/](help/)
- Technical architecture: [atlas_architecture.md](atlas_architecture.md)

**Common Tasks:**
- [View and download layers](help/export_layer.md)
- [Edit vector data](help/draw_vector.md)
- [Upload new data](help/upload_vector.md)
- [Hide/show layers](help/hide_layers.md)
- [Replace layer data](help/replace_layer.md)

**Support:**
Contact your atlas administrator for:
- Access credentials
- Layer configuration changes
- New data layer requests
- Technical issues
- Training on advanced features

### Performance Tips

**For Web Map:**
- Hide unused layers for faster rendering
- Use appropriate zoom levels (some layers only show at certain scales)
- Clear browser cache if experiencing issues

**For Downloads:**
- Export individual layers when you only need specific data
- Use GeoPackage format for complete datasets
- Consider file sizes when sharing (compress large files)

**For Editing:**
- Make frequent small saves rather than one large edit session
- Test complex changes on a small area first
- Coordinate with other editors to avoid conflicts

### Privacy and Data Handling

- Public layers are accessible to anyone - don't include sensitive information
- Internal/Admin layers require authentication but data may still be cached locally
- Downloaded data should be handled according to your organization's policies
- Consider data licensing and attribution requirements when sharing

### Updates and Maintenance

Your atlas administrator handles:
- Software updates
- New layer additions
- Configuration changes
- Performance optimization
- Backup and recovery

As a community curator, focus on:
- Data accuracy and completeness
- Reporting issues or errors
- Suggesting improvements
- Regular quality checks of your areas of responsibility
