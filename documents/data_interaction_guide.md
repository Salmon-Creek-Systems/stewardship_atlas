# Data Interaction Guide

This guide covers the various ways to view, edit, and manage data in a Stewardship Atlas.

---

## Viewing Data

### Web Map Interface

The interactive web map provides a browser-based view of all atlas layers with controls for toggling visibility, switching basemaps, and sharing locations.

**Use cases:**
- Quickly viewing current data without installing software
- Sharing specific locations with team members via URL
- Checking layer data during field work on mobile devices
- Verifying edits before publishing

**How to use:**
1. Navigate to the atlas webmap URL (e.g., `https://youratlas.fireatlas.org/webmap/`)
2. Use the layer toggles to show/hide data layers
3. Click features to view their properties
4. Use the location sharing dropdown to copy coordinates or Google Maps links

See: [Webmap Help](webmap_help.md)

---

### PDF Runbook

The PDF runbook provides printable, high-resolution maps organized by region. These are designed for field use, emergency response, and offline reference.

**Use cases:**
- Printing maps for vehicles or field kits
- Providing maps to personnel without internet access
- Creating official documentation or reports
- Archiving point-in-time snapshots of atlas data

**How to use:**
1. From the console, navigate to the Downloads section
2. Download the complete runbook PDF or individual region pages
3. Print at appropriate scale for field use

See: LINK TO HELP PAGE ON PDF RUNBOOK

---

### Data Packages

Data packages allow downloading atlas layers in standard GIS formats (GeoPackage, GeoJSON, etc.) for use in external applications.

**Use cases:**
- Loading atlas data into QGIS or ArcGIS for analysis
- Backing up layer data locally
- Sharing data with external partners
- Integrating with other GIS workflows

**How to use:**
1. From the admin console, locate the layer in the Downloads section
2. Click the download link for the desired format
3. Import into your GIS application

See: LINK TO HELP PAGE ON DATA PACKAGES

---

## Editing Data

### Web Map Edit Interface

The edit map interface allows drawing new features directly in the browser. Edits are stored as deltas and applied to the staging layer immediately, so they appear on the edit map right away. Derived products (webmap, PDFs) pick them up on the next layer refresh or explicit build.

**Use cases:**
- Adding new points of interest from field observations
- Marking locations identified from aerial imagery
- Quick corrections to feature locations
- Adding private notes or annotations

**How to use:**
1. From the admin console, click the "annotate" link for the layer you want to edit
2. Draw features using the drawing tools
3. Fill in property values in the control panel
4. Click "Save" to submit to staging

See: [Add Private Notes](add_private_note.md), [Editing Layer Data](editing_layer_data.md)

---

### Google Sheets

Google Sheets integration provides a spreadsheet interface for bulk editing feature properties. This is ideal for tabular data entry and corrections.

**Use cases:**
- Bulk updating property values across many features
- Collaborative editing with multiple team members
- Fixing typos or standardizing names
- Adding or correcting attribute data

**How to use:**
1. From the admin console, click "Export to Sheet" for the layer
2. Edit the data in Google Sheets
3. Click "Import from Sheet" to bring changes back to staging

See: LINK TO HELP PAGE ON GOOGLE SHEETS WORKFLOW

---

### Email Photo Submission

Geotagged photos taken on a smartphone can be submitted directly to the atlas by email. GPS coordinates are extracted from the photo and a point feature is created in the specified layer automatically.

**Use cases:**
- Field crews adding observations without opening a browser
- Quickly marking a location during site visits
- Capturing photo evidence tied to a geographic location

**How to use:**
1. Take a photo with your phone (location services must be on)
2. Email it to the atlas address (e.g. `scvfd@fireatlas.org`) from your registered admin email
3. Set the subject to `<layer> | <title>` (e.g. `poi | Locked gate on Miller Road`)
4. The feature appears in the atlas within a minute or two

See: [Email Photo Submission](help/email_photo_submission.md)

---

### External Applications (File Upload/Download)

For complex edits, data can be downloaded, modified in external applications (QGIS, Excel, Python scripts), and re-uploaded.

**Use cases:**
- Complex geometry edits requiring GIS tools
- Bulk transformations using scripts
- Merging data from external sources
- Advanced spatial analysis and corrections

**How to use:**
1. Download the layer data in GeoJSON or GeoPackage format
2. Edit in your application of choice
3. Use the "Upload GeoJSON" button in the edit map interface to import

See: [Editing Layer Data](editing_layer_data.md)

---

### Jupyter Notebook

Jupyter notebooks provide a Python environment for programmatic data manipulation, analysis, and atlas management.

**Use cases:**
- Scripted bulk updates to layer data
- Data validation and quality checks
- Complex spatial analysis
- Automating repetitive tasks
- Prototyping new features

**How to use:**
1. Open the atlas notebook (e.g., `StewardshipAtlas-YourAtlas.ipynb`)
2. Load the atlas configuration
3. Use the atlas Python modules to read and modify data
4. Run cells to execute changes

See: LINK TO HELP PAGE ON JUPYTER NOTEBOOKS

---

### SQL Interface

The SQL query interface allows direct querying of atlas data using SQL syntax, useful for ad-hoc queries and data exploration.

**Use cases:**
- Finding features matching specific criteria
- Generating reports and statistics
- Exploring data relationships
- Debugging data issues

**How to use:**
1. From the admin console, open the SQL Query interface
2. Write your SQL query against the layer tables
3. Execute and view results

See: LINK TO HELP PAGE ON SQL INTERFACE

---

### QGIS Desktop

QGIS project files can be opened in QGIS Desktop for advanced editing, styling, and analysis using full GIS capabilities.

**Use cases:**
- Precise geometry editing with snapping and digitizing tools
- Advanced spatial analysis (buffers, intersections, etc.)
- Creating custom map layouts
- Bulk attribute editing with field calculator

**How to use:**
1. Download the QGIS project file from the data packages
2. Open in QGIS Desktop
3. Edit layers as needed
4. Export modified layers and upload back to the atlas

See: LINK TO HELP PAGE ON QGIS WORKFLOW

---

## Administration

### Refreshing Layers

A layer is defined by its sources (inlets) plus its accumulated deltas; **refresh** is the operation that materializes it. Each layer in the admin console has a Refresh menu:

- **Update (apply edits)** — applies pending deltas onto the current layer. Cheap; this is the everyday choice. Also re-derives everything downstream (dependent layers and outputs).
- **Rebuild from source** — re-pulls the layer's source data (e.g. a fresh OpenStreetMap query) and applies pending edits onto the fresh base. Only offered for layers that have an external source. **Edits applied in past refreshes are not replayed** — a rebuild reflects fresh source data plus currently-pending edits.

**Use cases:**
- Making accumulated edits visible in the webmap and other products (Update)
- Picking up upstream changes, e.g. new OSM roads (Rebuild)
- Reconciling a layer before publishing (Rebuild)

---

### Building Outputs

Publishing no longer rebuilds outputs — outputs are built by refresh cascades or explicitly. Each entry in the admin console's Maps and Downloads sections has a **Build** button that regenerates that output from current staging data. Build the slow ones (PDF runbook, gazetteer) explicitly before publishing if their data has changed.

Programmatic equivalent: `GET /materialize_asset?swale={atlas}&asset={name}`, polled via `GET /materialize-asset-status`.

---

### Admin Console

The admin console is the central hub for managing atlas versions, publishing changes, and accessing editing interfaces.

**Use cases:**
- Publishing staging changes to a new version
- Rolling back to a previous version
- Resetting staging to match the current published version
- Monitoring layer status and accessing edit interfaces

**How to use:**
1. Navigate to the admin console URL
2. Use "Publish Atlas" to create a new version from staging. Publish is a pure snapshot: what you see in staging is exactly what gets published, and nothing is recomputed. Build any stale outputs first (see Building Outputs above).
3. Use "Rollback Version" to revert CURRENT to the previous version
4. Use "Reset Staging" to discard staging changes and start fresh

The Versions list shows `staging` (the working copy) on top, then published versions newest-first, with "(current)" marking the version visitors currently see.

See: LINK TO HELP PAGE ON ADMIN CONSOLE

---

### REST API

The atlas webapp exposes REST endpoints for programmatic access to atlas functions, enabling integration with external systems.

**Use cases:**
- Automating publishing workflows
- Integrating with CI/CD pipelines
- Building custom applications on top of atlas data
- Scripted data uploads from external systems

**Key endpoints:**
- `POST /delta_upload/{atlas}` - Upload feature changes; auto-applies to the layer unless `apply: false` in the payload
- `GET /refresh_layer?swale={atlas}&layer={name}&mode=update|rebuild&cascade=true|false` - Refresh a layer (background; poll `/refresh-layer-status`)
- `GET /materialize_asset?swale={atlas}&asset={name}` - Build one output (background; poll `/materialize-asset-status`)
- `GET /publish?swale={atlas}` - Snapshot staging as a new version (background; poll `/publish-status`)
- `GET /reset-staging/{atlas}` - Reset staging
- `POST /dereference_url` - Resolve shortened URLs

See: LINK TO HELP PAGE ON REST API
