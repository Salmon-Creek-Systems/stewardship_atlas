# Editing Layer Data

There are two approaches to modifying existing layer data: using the Edit Map interface or the spreadsheet export/import workflow.

## Method 1: Using the Edit Map (Annotate)

This is the quickest way to update individual features:

1. From the Admin Console, click the **"annotate"** link for the layer you want to edit
2. This opens the Edit Map interface for that layer
3. **Draw the updated geometry** (point, line, or polygon depending on the layer type)
4. **Fill in the property values** in the control fields - use the same identifying values as the feature you're replacing
5. Click **Save** to submit

The new feature will be processed as an update to the existing data.

**Best for:** Quick updates to individual features, geometry corrections, adding new features

## Method 2: Using Spreadsheet Export/Import

For bulk edits or when you need to modify many features at once:

1. **Export the layer data**
   - From the Admin Console, find the layer in the "Layers" section
   - Click the **"Export to Sheet"** link to export to Google Sheets

2. **Edit the data**
   - Modify property values directly in the spreadsheet cells
   - Add new rows for new features
   - Delete rows to remove features

3. **Re-import the data**
   - Use the **"Import from Sheet"** function in the Admin Console

**Best for:** Bulk text changes, fixing typos across many features, deleting multiple features

## Deleting Features

- **Single feature:** Use the spreadsheet method - export, delete the row, re-import
- **Multiple features:** Use the spreadsheet method for efficiency
- **Geometry-based deletion:** Edit the GeoJSON directly in QGIS, then upload via Edit Map

## Tips

- Always export a backup before making bulk changes
- Changes won't appear on published maps until the atlas is re-published
- For geometry changes that require precision, editing in QGIS and uploading the GeoJSON provides the best tools
