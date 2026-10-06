# View and Edit Configuration
<!-- audience: admin -->

The Technical Console provides access to your atlas configuration, allowing administrators to view and modify settings directly.

## Accessing the Configuration Editor

1. In the Admin Console, open the configuration editor from the **Maps** list
   (it's listed as `config_edit` on atlases that have it)
2. Or go straight to `/staging/outlets/config_edit/` under your atlas's address

## Using the Configuration Editor

The editor provides a visual interface for editing your atlas configuration JSON:

### Navigation Modes
- **Tree View**: Easy navigation through nested configuration. Click to expand/collapse sections.
- **Code View**: Direct JSON editing with syntax highlighting
- **Text View**: Plain text editing
- **Preview**: Read-only formatted view

### Common Tasks

**Edit Layer Properties:**
1. Expand `dataswale` → `layers`
2. Find the layer you want to modify
3. Edit properties like `color`, `add_labels`, `editable_columns`, etc.

**Modify Asset/Outlet Settings:**
1. Expand `assets`
2. Select the outlet (e.g., `runbook`, `gazetteer`)
3. Adjust properties like `in_layers` or `feature_scale`. (Runbooks always
   print Letter landscape; a `page_size` setting is ignored.)

**Update Bounding Box:**
1. Expand `dataswale` → `bbox`
2. Modify `north`, `south`, `east`, `west` coordinates

### Saving Changes

1. Click **"💾 Save Configuration"** in the toolbar
2. Wait for the success message
3. Changes are saved to the staging configuration immediately

### Important Notes

- ⚠️ Changes are saved to **staging only** - they won't affect published versions
- ⚠️ The editor changes the *built* configuration. The next full configuration
  rebuild replaces it from the atlas's source settings, so treat edits here as
  temporary unless SCS also makes them permanent
- Use **"✓ Validate"** to check for errors before saving
- Use **"🔄 Reload from Server"** to discard unsaved changes
- Invalid JSON will not be saved - fix errors first
- After saving, you may need to **Publish** to see changes in production

### Configuration Structure

Key sections in the configuration:
- `name`: Atlas name
- `data_root`: Base path for data storage
- `dataswale.bbox`: Geographic bounding box
- `dataswale.layers`: Layer definitions and styling
- `dataswale.versions`: List of published versions
- `assets`: Output configurations (runbook, gazetteer, webmap, etc.)
