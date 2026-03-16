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

### Submitting Photos by Email

The fastest way to add a point to the atlas from the field is to email a geotagged photo directly from your smartphone. No browser, no login — just take the photo and send it.

**Requirements:**
- Location services must be enabled in your phone's camera app
- Your email address must be on the atlas admin list

**How it works:**
1. Take a photo outdoors so your phone has a GPS fix
2. Email it to the atlas address (e.g. `scvfd@fireatlas.org`) with the photo as an attachment
3. Format your subject line as `<layer> | <title>`:
   - `poi | Locked gate on Miller Road`
   - `hydrants | New hydrant at Ridgeline staging area`
   - `private_notes | Erosion on north slope, check after rain`
4. Send — the feature appears on the map within a minute or two

The system extracts the GPS coordinates from the photo's EXIF data, creates a point feature in the layer you named, and stores the photo itself with a link from the feature. You can click the feature on the map to see the photo.

**Tips:**
- Take photos outdoors with a clear sky for the best GPS accuracy
- The default iPhone and Android mail apps preserve GPS data in attachments; some third-party apps strip it
- Layer names are case-insensitive; `POI` and `poi` both work
- If you omit the `|` and title, the feature is created with the title "Photo submission"

See: [Email Photo Submission](help/email_photo_submission.md)

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

### Diagnosing Email Photo Submissions

If a photo email doesn't appear in the atlas within a few minutes, use the `trace_email.py` script to follow it through the pipeline and find where it stopped.

```bash
# Show the 3 most recent email submissions and their status
python scripts/trace_email.py --profile atlas

# Trace a specific email by its S3 key (found in the ingress bucket)
python scripts/trace_email.py incoming/mvjamh5c7rtsvpb3qejlkaa0mhavb8uaj76luo81 --profile atlas
```

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
