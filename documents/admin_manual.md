# Atlas Administrator Manual

A guide for **looking after an atlas**: editing its data, managing its layers,
keeping its maps current, and publishing versions. It assumes you can open your
atlas's **Admin Console**.

Each section is an overview that points to a short how-to page for the details.
For the viewer's side — the web map, sharing, downloads — see the
[User Manual](user_manual.md).

---

## Staging vs. Published

Your atlas has two kinds of state:

- **Staging** — the working copy. Every edit happens here.
- **Published versions** — permanent, dated snapshots. Once made they never
  change, and viewers see the current one.

The rhythm is: **edit in staging → check the maps → build any printed maps →
publish**. The Admin Console's **Versions** section lists staging and every
published version.

---

## Editing Data

Each layer in the Admin Console has an **Alter** menu with three ways to change
it on the map. Each opens an edit map for that layer:

- **Draw** adds new points, lines or areas — see
  [Draw New Features](help/draw_vector.md). Private notes for administrators
  are drawn the same way — see [Add Private Notes](help/add_private_note.md).
- **Edit** changes, deletes or moves existing features: draw an area around
  them, check the highlighted selection, then change their properties — see
  [Edit Existing Features](help/editing_layer_data.md) and
  [Move Features to Another Layer](help/move_features.md).
- **Reshape** fixes the shape of one existing feature without touching its
  properties — see [Reshape a Feature](help/reshape_feature.md).

Other ways in:

- **Spreadsheets** — export a layer to Google Sheets, edit many attributes at
  once, and import it back. See
  [Export, Edit, and Import Spreadsheets](help/spreadsheet_workflow.md).
- **Photos by email** — field crews can email geotagged photos straight into a
  layer. See [Add a Location by Emailing a Photo](help/email_photo_submission.md).
- **Files** — load features from a GeoJSON file. See
  [Add Features from a File](help/upload_vector.md).

---

## Working with Layers

Layers can be added, restyled, copied and removed from the console:

- **+** beside **Layers** adds a layer, from a file or empty — see
  [Add a Layer](help/add_layer.md).
- **Alter → Edit Style…** changes colours, labels, icons and whether a layer
  shows by default — see [Change a Layer's Style](help/edit_layer_style.md).
- **Dataset → Copy Layer…** makes an independent copy — see
  [Copy a Layer](help/copy_layer.md).
- **Dataset → Clear Layer** empties a layer but keeps it — see
  [Clear a Layer](help/replace_layer.md).
- **Dataset → Delete Layer…** removes a layer and everything that refers to it,
  and refuses if something depends on it — see
  [Delete a Layer](help/delete_layer.md).
- **Dataset → Directory** lists a layer's files for download — see
  [Export a Layer](help/export_layer.md).

⚠️ Layer changes made in the console are saved on the server but not yet
recorded in the atlas's permanent configuration. Let SCS know after adding,
restyling or deleting layers so the change is kept.

---

## Making Edits Appear: Refresh

Edits are applied to a layer when it's **refreshed**, which also updates the web
map. Most editing surfaces apply your change straight away. The **Refresh** menu
offers **Update** to gather several pending changes at once, and **Rebuild** to
re-pull layers that come from an outside source. See
[Refresh a Layer](help/refresh_layer.md).

---

## Building Outputs

Publishing doesn't rebuild outputs. The web map keeps itself current as you
edit, but heavier outputs — the PDF runbook and gazetteer — are only rebuilt
when you click **Build** beside them in **Maps** or **Downloads**. Build
anything whose data has changed before publishing. See
[Build an Output](help/build_outputs.md).

---

## Publishing a Version

When staging looks right, **Publish** (in **Versions**) freezes it as a
permanent, shareable version. **Rollback** points viewers back at the previous
version; **Reset Staging** throws away staging changes and starts again from the
current version. See [Publish a New Version](help/publish_version.md).

---

## Querying the Data

The **SQL query** page answers questions about your data — counts, lengths,
lists — from a plain-English question or SQL. See
[Query the Atlas with SQL](help/sql_query.md).

---

## New Atlases

Anyone can start a new atlas at fireatlas.org/create, from a drawn box or an
uploaded boundary, with a choice of starter kits. See
[Create a New Atlas](help/create_atlas.md).

---

## Advanced: Viewing Configuration

The configuration editor shows your atlas's full built settings. Edits there
are temporary — the next configuration rebuild replaces them — so treat it as an
inspection tool. See [View and Edit Configuration](help/edit_config.md).

---

## Where to Get More Help

Every how-to is listed in the [Help Index](help/index.html). For anything
beyond running your own atlas — new kinds of data, raster layers, the email
pipeline, or platform questions — contact SCS.
