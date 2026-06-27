# Stewardship Atlas User Guide

## Contents

- [Overview](#overview)
- [Examples and Use Cases](#examples-and-use-cases)
- [Access and Data Control](#access-and-data-control)
- [Viewing Data](#viewing-data)
  - [Interactive Web Map](#interactive-web-map)
  - [Printable Maps and Documents](#printable-maps-and-documents)
  - [Jupyter Notebooks](#jupyter-notebooks)
  - [Files and Exports to Other Platforms](#files-and-exports-to-other-platforms)
- [Curating and Editing Data](#curating-and-editing-data)
  - [Web-Based Editing](#web-based-editing)
  - [Submitting Photos by Email](#submitting-photos-by-email)
  - [Uploading Data](#uploading-data)
  - [Direct Python Access](#direct-python-access-advanced)
  - [Data Versioning](#data-versioning)
- [Sharing Data](#sharing-data)
- [Advanced Topics](#advanced-topics)
  - [Creating a New Atlas](#creating-a-new-atlas)
  - [Batch-Ingesting Photos from S3](#batch-ingesting-photos-from-s3)
  - [Adding Private S3 Data Layers](#adding-private-s3-data-layers)
  - [Layer Zoom Visibility](#layer-zoom-visibility)
  - [Custom Layer Icons (Sprite System)](#custom-layer-icons-sprite-system)
  - [Layer Interaction Options](#layer-interaction-options)
- [Technical Details](#technical-details)
  - [System Requirements](#system-requirements)
  - [Data Formats](#data-formats)
  - [Getting Help](#getting-help)
  - [Performance Tips](#performance-tips)
  - [Privacy and Data Handling](#privacy-and-data-handling)
  - [Diagnosing Email Photo Submissions](#diagnosing-email-photo-submissions)
  - [Live Email Log in the Technical Console](#live-email-log-in-the-technical-console)
  - [Adding a New Authorised Sender](#adding-a-new-authorised-sender)
  - [Inspecting and Reprocessing Stuck Emails](#inspecting-and-reprocessing-stuck-emails)
  - [Testing the Platform](#testing-the-platform)
  - [Updates and Maintenance](#updates-and-maintenance)

---

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

### Submitting Photos by Email

The fastest way to add a point to the atlas from the field is to email a geotagged photo directly from your smartphone. No browser, no login — just take the photo and send it.

**Requirements:**
- Location services must be enabled in your phone's camera app (see below)
- Your email address must be on the atlas admin list

**How it works:**
1. Take a photo outdoors so your phone has a GPS fix
2. Email it to the atlas address (e.g. `scvfd@fireatlas.org`) with the photo as an attachment
3. Write a subject line describing what you're documenting:
   - `Locked gate on Miller Road` — goes to the default `poi` layer
   - `hydrants: New hydrant at Ridgeline staging area` — goes to the `hydrants` layer
   - `private_notes: Erosion on north slope, check after rain` — goes to `private_notes`
4. Send — the feature appears on the map within a minute or two

The system extracts the GPS coordinates from the photo's EXIF data, creates a point feature in the layer you named, and stores the photo itself with a link from the feature. You can click the feature on the map to see the photo (modifier-click on desktop, or the feature popup on mobile).

**If something goes wrong:** You will receive an automatic reply from the atlas address explaining what happened. The atlas administrators are also notified. Common issues are described below.

**Tips:**
- Take photos outdoors with a clear sky for the best GPS accuracy
- The default iPhone and Android mail apps preserve GPS data in attachments; some third-party apps (e.g. WhatsApp, Slack) strip it — attach directly from the Photos app or Camera Roll
- Layer names in the subject are case-insensitive; `POI` and `poi` both work
- If you omit the colon and layer name, the feature goes to `poi` automatically

#### Making sure your phone has location services enabled

The most common reason a photo submission fails is that the Camera app does not have permission to record your location. This can happen silently — the photo looks normal, but has no GPS coordinates embedded in it.

**How to check on iPhone:**
1. Open **Settings → Privacy & Security → Location Services**
2. Scroll down to **Camera** and tap it
3. It should say **While Using** — if it says Never or Ask Next Time, tap to change it

**How to tell before you send:**
- Open the photo in the **Photos app** and swipe up (or tap the info icon ⓘ). If a map thumbnail appears showing where the photo was taken, GPS is present.
- If no map appears, the photo has no location data and the submission will fail.

**Android:**
- Go to **Settings → Apps → Camera → Permissions → Location** and ensure it is set to "Allow while using the app"
- Similarly, open the photo in Google Photos and check if a location is shown in the details

If location was off when you took the photo, you'll need to retake it with location enabled — there is no way to add GPS data to an existing photo after the fact.

---

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

The atlas uses a **staging and publish** model for managing data changes:

**How It Works:**
- **Staging**: All edits happen in a working "staging" area. This is your draft workspace.
- **Published Versions**: When you're ready, you "publish" to create a permanent timestamped snapshot (e.g., `2024-01-15_14-30-00`).

**Why Versioning Matters:**
- **Safety**: Published versions can't be accidentally modified
- **History**: Each publish creates a permanent record of your data at that point
- **Sharing**: Share links to specific versions for consistency
- **Recovery**: If something goes wrong in staging, published versions remain intact

**The Publishing Workflow:**
1. Make edits in the staging environment (webmap, edit map, spreadsheet import)
2. Review changes to ensure they're correct
3. Click "Publish Atlas" in the Admin Console
4. A new version is created and becomes available at its own URL
5. Continue editing in staging for the next set of changes

**Accessing Versions:**
- Current staging: `/staging/outlets/...`
- Published version: `/{version-name}/outlets/...`
- The Admin Console shows all available versions with links

**Best Practices:**
- Publish after completing a logical set of changes
- Publish before making risky modifications (as a backup)
- Use version URLs when sharing data that shouldn't change
- Review staging changes before publishing

Admin users can create new versions through the Admin Console. See [Publishing Guide](help/publish_version.md) for detailed instructions.

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

## Advanced Topics

### Creating a New Atlas

A new atlas can be created entirely through the web interface — no server access or configuration files required.

1. Go to [fireatlas.org/create](https://fireatlas.org/create)
2. **Draw the atlas area**: use the draw tool to sketch a polygon covering the geographic region of interest. The bounding box of your drawing becomes the atlas extent.
3. **Name it**: enter a display name (e.g. *Salmon Creek VFD*). An atlas ID is generated automatically from the name (e.g. `scvfd`) — you can edit it before submitting.
4. Click **Create Atlas**. A progress log shows what's happening. Creation typically takes under a minute.
5. When complete you are redirected to the new atlas's public console.

**What you get out of the box:**
- Interactive web map with OpenMapTiles Terrain basemap
- Web editing interface for roads, creeks, and landmarks layers
- Admin and public consoles

**First steps after creation:**

The atlas starts with empty layers. To populate it with publicly available data, open the Admin Console and trigger the data inlets:

- **Roads** — fetches road network from Overture Maps for your area
- **Creeks** — fetches waterways from the USGS National Hydrography Dataset
- **Landmarks** — fetches points of interest from OpenStreetMap

Each fetch runs as a background job; refresh the console to see progress. Data quality varies by region — Overture and NHD have excellent coverage across the United States; OSM coverage is strong in populated areas.

**Default credentials:**

New atlases use shared default credentials: `admin` / `admin` and `internal` / `internal`. These are intentionally simple placeholders. Contact your platform administrator to set up credentials specific to your atlas before sharing access with your community.

---

### Batch-Ingesting Photos from S3

If you have a collection of geotagged photos already stored in S3 that need to be added to an atlas layer, use `scripts/ingest_s3_photos.py`. It applies the same GPS extraction and delta-writing pipeline as the email inlet.

```bash
# Preview — no writes
SWALES_ROOT=/root/swales_dev \
python3 scripts/ingest_s3_photos.py scvfd s3://my-bucket/field-photos/2026-04/ --dry-run

# Ingest into a specific layer
SWALES_ROOT=/root/swales_dev \
python3 scripts/ingest_s3_photos.py scvfd s3://my-bucket/field-photos/2026-04/ --layer poi
```

Photos without GPS EXIF data are skipped and reported on stdout. All valid photos from the run are written as a single delta batch. The `--layer` flag defaults to the atlas's configured `email_photo_default_layer`.

Photos must be publicly readable at their S3 URL for the atlas web map to display them inline.

---

### Adding Private S3 Data Layers

If your organisation has data in a private S3 bucket — aerial imagery, lidar-derived rasters, species observation exports — you can pull it directly into an atlas layer without making it publicly accessible.

#### Raster layers (GeoTIFF from S3)

Add an entry to `{atlas}_assets.json`:

```json
"canopy_density": {
    "type": "inlet",
    "out_layer": "canopy_density",
    "config_def": "s3_geotiff",
    "s3_bucket": "my-org-private",
    "s3_key": "site/canopy_density_2m.tif"
}
```

Add a matching entry in `{atlas}_layers.json`:

```json
{"name": "canopy_density", "geometry_type": "raster", "vis": {"layout": {"visibility": "none"}}, "paint": {"raster-opacity": 0.7}}
```

On `atlas.materialize(config, 'canopy_density')` the TIFF is fetched from S3, warped to the atlas CRS, and clipped to the bounding box. It is then served as a georeferenced image overlay in the webmap. Single-band float rasters (e.g. canopy density, fuel load) receive a percentile-stretched grayscale PNG with transparent nodata pixels so they composite cleanly over the basemap.

AWS credentials are handled automatically: the EC2 IAM role is used on the server, the `atlas` AWS profile is used for local development. No credentials appear in configuration files.

#### Vector GeoJSON layers from S3

```json
"inaturalist": {
    "type": "inlet",
    "out_layer": "inaturalist",
    "config_def": "s3_geojson_inlet",
    "s3_bucket": "my-org-private",
    "s3_key": "site/observations.geojson"
}
```

Features are filtered to the atlas bounding box automatically. If features carry an `image_url` property it is copied to `URL`, enabling the existing webmap click-to-open-image behaviour. The `name` field is used for map labels — set it on features, or the inlet will derive it from `common_name` if present (useful for iNaturalist exports).

After running the inlet and applying deltas, include the layer name in the webmap asset's `in_layers` list and re-materialise the webmap.

---

### Layer Zoom Visibility

By default layers are visible at all zoom levels. Two options in the layer config let you control when geometry and labels appear independently.

**`vis`** — merged directly into the MapLibre layer spec. Use it to set `minzoom` or `maxzoom` on the geometry layer (circles, lines, fills):

```json
{"name": "inaturalist", ..., "vis": {"minzoom": 8}}
```

The same `vis` values are also applied to the label layer unless explicitly overridden (see below).

**`label_minzoom` / `label_maxzoom`** — override zoom thresholds for the label/icon layer only, independently of the geometry layer. Useful when you want markers visible at a wider zoom but labels or icons only when the user is closer in:

```json
{"name": "inaturalist", ..., "vis": {"minzoom": 8}, "label_minzoom": 12}
```

This shows dot markers from zoom 8 but defers text labels and flower icons to zoom 12, avoiding clutter at regional scales.

---

### Custom Layer Icons (Sprite System)

Point layers display as plain circles by default. To use a custom PNG icon instead, add a `symbol` entry to the layer config:

```json
{
    "name": "inaturalist",
    "geometry_type": "point",
    "symbol": {"png": "flower.png", "icon": "flower"},
    "icon-size": 1.0,
    ...
}
```

Place the PNG file in one of two locations:

- **Atlas-specific**: the atlas's `local/` directory (symlinked from `staging/local/`) — use for icons that belong to one atlas only.
- **Shared**: `templates/icons/` in the repo — checked in to git and available to all atlases. The sprite loader checks `local/` first and falls back to `templates/icons/` automatically.

Icons are compiled into a sprite sheet (sprite.png + sprite.json) at webmap build time. The `icon` value in the `symbol` key is the sprite symbol name that MapLibre references; it must match the PNG filename without extension.

---

### Layer Interaction Options

These layer config fields control how a layer behaves in the webmap and admin console. They go in `{atlas}_layers.json`.

#### Click popups (`show_attributes` + `editable_columns`)

To make clicking a feature open a popup showing its properties, set **both**:

```json
{
    "name": "hydrants",
    "geometry_type": "point",
    "show_attributes": true,
    "editable_columns": [
        {"name": "capacity", "type": "string", "default": ""},
        {"name": "access",   "type": "string", "default": "no"}
    ]
}
```

`show_attributes` alone is not enough — `editable_columns` must also be present and list the fields to display. Fields listed here appear in both the webmap popup and the web editor. The layer also needs to be in the `webedit` asset's `in_layers` for editing to work; `show_attributes` in the webmap popup works independently of that.

#### Conversations (`conversations_enabled`)

Enables a comment/annotation thread on individual features. Clicking a feature opens a popup with the comment history and a form to add a new comment. Comments are stored as a delta on the feature.

```json
{"name": "photos", ..., "conversations_enabled": true}
```

A speech-bubble badge is automatically overlaid on the feature's icon when comments exist. `conversations_enabled` must be present on the layer; it is also propagated to badge overlay layers automatically.

#### Admin console list (`interaction`)

Controls whether the layer appears in the admin console's layer list (and therefore the web editor layer picker):

```json
{"name": "roads", ..., "interaction": "interface"}
```

Layers without `interaction: "interface"` are invisible in the console UI even if they are present in the atlas. Most editable layers should have this set.

#### Initially hidden in webmap (`hidden_layers`)

A layer can be included in the webmap but hidden by default — still accessible via the legend toggle. Configure this in the **asset** config (not the layer config), under the `webmap` or `private_webmap` asset:

```json
"webmap": {
    "type": "outlet",
    "in_layers": ["roads", "roads_lrs", "lrs_markers", ...],
    "hidden_layers": ["roads_lrs", "lrs_markers"],
    ...
}
```

Hidden layers are fully rendered and clickable once toggled on; they just start invisible. Useful for supplementary or technical layers that shouldn't clutter the default view.

#### Summary table

| Field | Where | Effect |
|---|---|---|
| `show_attributes: true` | layer config | Enables click popup (requires `editable_columns`) |
| `editable_columns: [...]` | layer config | Fields shown in popup and web editor |
| `conversations_enabled: true` | layer config | Enables per-feature comment threads |
| `interaction: "interface"` | layer config | Layer appears in admin console list and editor |
| `hidden_layers: [...]` | asset config | Layer present in webmap but hidden by default |

All of these require re-running `build_atlas.py config_only` and then rematerialising the relevant outlet (`webmap`, `webedit`, or `html`) before they take effect.

---

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

**Admin Tasks:**
- [Publish a new version](help/publish_version.md)
- [Edit atlas configuration](help/edit_config.md)
- [Export/import spreadsheets](help/spreadsheet_workflow.md)
- [Add private notes](help/add_private_note.md)

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

### TLS Certificate Expired / `certbot renew` Fails

**Symptoms:** Browsers warn the certificate has expired and API calls (port `:9000`) stop working entirely — `curl` reports `SSL certificate problem: certificate has expired` with `http_code=000` (the TLS handshake never completes, so nothing the API does matters). Running `sudo certbot renew` fails with:

```
Failed to renew certificate fireatlas.org-0001 with error: The manual plugin is not
working ... An authentication script must be provided with --manual-auth-hook when
using the manual plugin non-interactively.
```

**Cause:** The cert is (or was) a **wildcard** `*.fireatlas.org`. Wildcards can only be validated by **DNS-01**, and this lineage was issued with certbot's **manual** plugin (`authenticator = manual` in `/etc/letsencrypt/renewal/fireatlas.org-0001.conf`). `certbot renew` *always* runs non-interactively (it's built for cron), so it refuses to prompt for the manual DNS step regardless of you sitting at a terminal — it needs a `--manual-auth-hook`. There isn't one, so auto-renewal has silently failed every cycle since the switch to a wildcard. It only surfaces when the 90-day cert actually expires. This is **not** a terminal/`TERM` problem, and not caused by nginx config edits.

**Confirm it:**
```bash
echo | openssl s_client -servername fireatlas.org -connect fireatlas.org:443 2>/dev/null \
  | openssl x509 -noout -subject -dates          # look for CN=*.fireatlas.org and a past notAfter
sudo cat /etc/letsencrypt/renewal/fireatlas.org-0001.conf   # authenticator = manual
```

**Cure (drop the wildcard → explicit SANs over HTTP-01, which auto-renews):** We only use the apex plus the `scvfd` and `westportvfd` subdomains, and they all point at this host, so HTTP-01 works. Reuse `--cert-name` so the files stay at `/etc/letsencrypt/live/fireatlas.org-0001/` — **nginx and the `:9000` launch flags don't change**, only the cert contents and authenticator:

```bash
sudo certbot certonly --nginx \
  --cert-name fireatlas.org-0001 \
  -d fireatlas.org -d scvfd.fireatlas.org -d westportvfd.fireatlas.org
# confirm 'yes' when it warns the domain set changed (dropping *.fireatlas.org)
```

Then reload/restart both services that serve the cert:

```bash
sudo nginx -t && sudo systemctl reload nginx
# HARD restart the :9000 uvicorn screen session — it loads --ssl-certfile at launch,
# so --reload will NOT pick up the new cert. Kill the screen session and relaunch it.
```

This restores service **and** brings back unattended renewal (`authenticator` becomes `nginx`, which `certbot renew` can run on its own). Caveat: `--nginx` needs a `server_name` matching each `-d` name; if certbot can't find one for a subdomain, add it to the server block and rerun.

**Alternative — keep the wildcard:** if you need arbitrary `*.fireatlas.org` subdomains, install the `dns-route53` plugin (our DNS is in Route 53, personal AWS account) and reissue with `--dns-route53`. Also fully automated, but requires the plugin plus AWS credentials with `route53:ChangeResourceRecordSets` available on the box.

### Diagnosing Email Photo Submissions

If a photo email doesn't appear in the atlas within a few minutes, use the `trace_email.py` script to follow it through the pipeline and find where it stopped.

```bash
# Show the 3 most recent email submissions and their status (default)
python scripts/trace_email.py --profile atlas

# Show more or all submissions (--n 0 fetches everything via paginated CloudWatch queries)
python scripts/trace_email.py --n 10 --profile atlas
python scripts/trace_email.py --n 0 --profile atlas

# Trace a specific email by its S3 key (found in the ingress bucket)
python scripts/trace_email.py incoming/mvjamh5c7rtsvpb3qejlkaa0mhavb8uaj76luo81 --profile atlas
```

**Quick check — which atlas addresses are active:** Stage 1 of the trace lists the
recipients SES will accept. To query SES directly without running the full trace:

```bash
aws ses describe-receipt-rule-set --rule-set-name atlas-email-inlet \
  --profile atlas --region us-east-1 \
  --query 'Rules[].{rule:Name,enabled:Enabled,recipients:Recipients}'
```

Each `{slug}@fireatlas.org` is appended to this rule automatically when an atlas
is created, so this is the authoritative list of live ingest addresses.

The script checks each stage in order:

1. **SES receipt rule** — confirms the rule set is active and configured for the right recipient address
2. **S3 ingress bucket** — shows any emails still sitting unprocessed; a successfully processed email is deleted from here automatically
3. **Lambda invocation log** — shows what the Lambda did with the email and what the webapp returned
4. **Feature in layer** — confirms the point was created and shows its coordinates

Example output for a successful submission:

```
Email Photo Inlet — Pipeline Trace
Atlas: scvfd  |  Region: us-east-1  |  Profile: atlas

──────────────────────────────────────────────────────────
  Stage 1 — SES receipt rule config
──────────────────────────────────────────────────────────
  ✓  Rule set 'atlas-email-inlet' is ACTIVE
  ✓  Rule '...' (enabled): recipients=['scvfd@fireatlas.org'], actions=['S3Action']

──────────────────────────────────────────────────────────
  Stage 2 — S3 ingress bucket
──────────────────────────────────────────────────────────
  ✓  No unprocessed emails in ingress bucket

──────────────────────────────────────────────────────────
  Stage 3 — Lambda invocation log
──────────────────────────────────────────────────────────
  ✓  Invocation 32bc97bc… — SUCCESS (2026-03-10 21:41:44 UTC, 4.6s)
     S3 key: incoming/mvjamh5c7rtsvpb3qejlkaa0mhavb8uaj76luo81
     Webapp: HTTP 200  {"status":"ok","layer":"poi","lat":40.2055667,"lon":-123.9355917}

──────────────────────────────────────────────────────────
  Stage 4 — Feature in layer
──────────────────────────────────────────────────────────
  ✓  Feature created in layer 'poi' at (40.2055667, -123.9355917)
```

If an email never reached S3 at all (e.g. sent to the wrong address at the right domain), the script cannot currently diagnose that — it will only show that nothing arrived. More detailed SES receipt logging is planned (see [GitHub issue #17](https://github.com/Salmon-Creek-Systems/stewardship_atlas/issues/17)).

### Live Email Log in the Technical Console

The **Technical Console** (accessible to admin users) shows a live view of recent email submissions in its main panel. The panel fetches the latest log data each time the page loads — no regeneration needed. Each entry shows the sender, subject, processing status, duration, and a direct link to the stored photo if the submission succeeded.

This is the quickest way to monitor incoming photos day-to-day without running a command-line script.

### Adding a New Authorised Sender

Only addresses listed in `admin_emails` in the atlas configuration are accepted. To add a new sender:

> **Note:** `admin_emails` currently serves double duty — it controls both who can submit photos via email *and* who Google Sheets exports are shared with. These are often different people (field crews vs. administrators). See issue #98 for planned split into `email_authorized_senders` and `spreadsheet_share_emails`.

1. Add the email address to `admin_emails` in `configuration/{atlas}.geojson`
2. Commit and push, then pull on the server and rebuild the atlas config:
   ```bash
   python scripts/build_atlas.py config_only
   ```

New submissions from that address will be accepted immediately after the config rebuild.

### Inspecting and Reprocessing Stuck Emails

If an email failed, the raw email stays in the S3 ingress bucket. Use `reprocess_email.py` to list, inspect, and retrigger stuck emails:

```bash
# See what's currently stuck in the ingress bucket
python scripts/reprocess_email.py --list --profile atlas

# Inspect a specific email — shows sender, subject, and whether GPS data is present
python scripts/reprocess_email.py --inspect incoming/63hfbll9b84j2... --profile atlas

# Retrigger a specific email by its S3 key
python scripts/reprocess_email.py incoming/63hfbll9b84j2... --profile atlas
```

The `--inspect` option downloads the raw email from S3 and shows:
- **From / Subject / To** — confirms what was actually sent
- **Camera make and model** — useful to verify it came from an iPhone vs another device
- **GPS status** — whether GPS coordinates are present in the image EXIF, with coordinates if found

Example output for a photo missing GPS data:
```
  From:    "Stinkin' Feathers" <planephun@gmail.com>
  Subject: (empty)
  Camera:  Apple iPhone 15 Pro
  GPS:     ✗  No GPS data in EXIF — submission will fail.
           Check iOS Settings → Privacy → Location Services → Camera.
```

Reprocessing copies the S3 object with a metadata change, which fires a new `ObjectCreated` event and causes the Lambda to run through the full pipeline again. Check the Technical Console after a few seconds to confirm the result.

**Note:** if the original failure was due to missing GPS EXIF data, reprocessing the same email will fail again — the photo itself needs to be resent with location services enabled. GPS is stripped from the image when iOS Camera does not have location permission, which is set in **iOS Settings → Privacy & Security → Location Services → Camera** (should be "While Using").

### Testing the Platform

The platform has three levels of tests. All commands are run from the repo root unless noted.

#### Unit Tests (no server required)

Fast tests covering core Python modules (`utils`, `versioning`, `deltas`, etc.). Run locally without a server or QGIS.

```bash
cd python
python -m pytest tests/ -v
```

Exclude the e2e tests when running unit tests only:

```bash
cd python
python -m pytest tests/ --ignore=tests/test_kennedy_e2e.py -v
```

#### End-to-End Tests (read-only, against live server)

Playwright-based browser tests that exercise the deployed platform: webmap rendering, console pages, layer GeoJSON endpoints, and the log API. These make no writes.

```bash
cd python
ATLAS_NAME=kennedy \
ATLAS_BASE_URL=https://fireatlas.org \
ATLAS_API_URL=https://fireatlas.org:9000 \
ATLAS_USER=admin ATLAS_PASSWORD=admin \
pytest tests/test_kennedy_e2e.py -v
```

Change `ATLAS_NAME` to run against a different atlas (e.g. `scvfd`). Credentials must match that atlas's admin htpasswd.

First-time setup (once per machine):

```bash
pip3 install -r python/requirements-dev.txt --break-system-packages
playwright install chromium
```

#### Manual Smoke Tests

Quick checks you can run from the server to verify a specific atlas is healthy after a config change or redeploy:

```bash
# Check the log API
curl https://fireatlas.org:9000/log/kennedy

# Verify a layer GeoJSON is present and non-empty
curl -s https://fireatlas.org/kennedy/staging/layers/roads/roads.geojson | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d['features']), 'features')"

# Reprocess a stuck email by S3 key
python3 scripts/reprocess_email.py <s3-key>
```

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
