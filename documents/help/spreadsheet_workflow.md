# Export, Edit, and Import Spreadsheets

The spreadsheet workflow allows you to edit layer attributes in Google Sheets, making bulk edits easier than using the map interface. This is ideal for:

- Updating many feature attributes at once
- Collaborative data review
- Working with tabular data in a familiar interface
- Bulk corrections or additions

## Overview

The workflow has three steps:
1. **Export** layer data to Google Sheets
2. **Edit** the data in the spreadsheet
3. **Import** changes back to the atlas

## Step 1: Export to Google Sheets

1. Go to the **Admin Console** (`/staging/outlets/html/admin`)
2. Find your layer in the **Layer Operations** panel
3. Click **"(export sheet)"** next to the layer name
4. Wait for the export to complete (status shows "exporting...")
5. A new browser tab opens with the Google Sheet

The exported sheet contains:
- All features from the layer
- All attribute columns
- Geometry data (as WKT or coordinates)
- A unique feature ID for each row

## Step 2: Edit in Google Sheets

### What You Can Edit
- Any attribute values (names, descriptions, categories, etc.)
- Add new rows for new features (if geometry is provided)
- Modify existing values

### What to Preserve
- **Do not delete** the ID column or change ID values
- **Do not modify** the geometry column unless you know WKT format
- **Keep column headers** exactly as they are

### Tips for Editing
- Use filters and sorting to find specific features
- Use find/replace for bulk text changes
- Add comments for team coordination
- Save frequently (Google Sheets auto-saves)

### Adding New Features
To add a new feature via spreadsheet:
1. Add a new row at the bottom
2. Leave the ID column empty (a new ID will be assigned)
3. Fill in all required attribute columns
4. Provide geometry in WKT format (e.g., `POINT(-122.5 38.0)`)

## Step 3: Import from Google Sheets

1. Return to the **Admin Console**
2. Find the same layer in the Layer Operations panel
3. Click **"(import sheet)"** next to the layer name
4. Wait for the import to complete
5. Confirm the success message

### What Happens During Import
- Changed rows are updated in the atlas
- New rows are added as new features
- Deleted rows are **not** automatically removed (for safety)
- A delta file is created recording the changes

## Important Notes

### Access Control
- Export/import requires Admin access
- The Google Sheet is created in your connected Google account
- Share the sheet carefully - it may contain sensitive data

### Data Safety
- Always export before making major edits (as a backup)
- Import creates change records (deltas) that can be reviewed
- Test with a small batch of changes first

### Geometry Handling
- Geometry is exported as WKT (Well-Known Text)
- Format examples:
  - Point: `POINT(-122.5 38.0)`
  - Line: `LINESTRING(-122.5 38.0, -122.4 38.1)`
  - Polygon: `POLYGON((-122.5 38.0, -122.4 38.0, -122.4 38.1, -122.5 38.1, -122.5 38.0))`
- Malformed geometry will cause import errors for that row

### Troubleshooting

**Export fails:**
- Check your network connection
- Ensure you have Google account access configured
- Try refreshing the page and exporting again

**Import fails:**
- Check for malformed data in the spreadsheet
- Verify column headers haven't been changed
- Look for invalid geometry in new rows

**Changes not appearing:**
- Refresh the webmap or edit map
- Clear browser cache
- Check that import completed successfully

## Alternative: Direct Upload

For data from other sources (not Google Sheets), use the **(upload)** link:
1. Prepare a GeoJSON file with your features
2. Click **(upload)** next to the layer
3. Select your GeoJSON file
4. Features will be added to the layer

This is useful for:
- GPS data exports
- Data from other GIS software
- Automated data pipelines
