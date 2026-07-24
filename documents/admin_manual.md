# Atlas Administrator Manual

A guide for **maintaining a single atlas**: editing its data, refreshing layers,
building outputs, and publishing versions. It assumes you have admin access to
your atlas's console.

This manual is deliberately scoped to running one atlas. Platform-level tasks —
creating a new atlas, adding a new layer, debugging the email pipeline,
infrastructure — are covered separately in the maintainer documentation, not
here. See [Where to Get More Help](#where-to-get-more-help) at the end.

For the read-only viewer's side (navigating the map, downloading data), see the
[User Manual](user_manual.md).

---

## Staging vs. Published

Your atlas has two kinds of state:

- **Staging** — the working copy. Every edit happens here, and it is always
  changing.
- **Published versions** — permanent, timestamped snapshots. Once created they
  never change. Viewers see the current published version.

The workflow is: **edit in staging → refresh → build outputs → publish**. The
Admin Console is where you do all of this; the **Versions** section lists staging
(on top) and every published version.

---

## Editing Data

There are several ways to get data into a layer; use whichever fits the task.

- **Draw and edit features on the map** — add points, lines, or areas and fill in
  their attributes. See **[Draw Vector Features](help/draw_vector.md)**,
  **[Editing Layer Data](help/editing_layer_data.md)**, and
  **[Add a Private Note](help/add_private_note.md)**.
- **Upload or replace layer data from a file** — bring in a GeoJSON file. See
  **[Upload Vector Data](help/upload_vector.md)** and
  **[Replace a Layer](help/replace_layer.md)**.
- **Edit in a spreadsheet** — export a layer to Google Sheets, edit tabular
  attributes, and import it back. See
  **[Spreadsheet Workflow](help/spreadsheet_workflow.md)**.
- **Submit photos by email** — field crews can email geotagged photos straight
  into a layer. See **[Email Photo Submission](help/email_photo_submission.md)**
  for how to submit and how to enable it for your crew.

---

## Making Edits Appear: Refresh

Edits are stored as pending changes and applied to a layer when you **refresh**
it, which also updates the webmap and other layer-driven views. Most editing
surfaces apply your change right away; use **Update** to gather accumulated
changes, and **Rebuild** only for source-backed layers you want to re-pull.

See **[Refresh a Layer](help/refresh_layer.md)**.

---

## Building Outputs

Publishing does **not** rebuild outputs. Lightweight ones (the webmap) stay
current as you refresh; heavier ones (PDF runbook, gazetteer) are built on
demand with the **Build** button next to each entry in Maps and Downloads. Build
anything whose data has changed before publishing.

See **[Build an Output](help/build_outputs.md)**.

---

## Publishing a Version

When staging looks right, publish to freeze a permanent, shareable snapshot.
Publishing is an exact snapshot of staging — build any stale outputs first.

The Versions section also offers **Rollback** (repoint the live version to the
previous one) and **Reset Staging** (discard staging changes, restoring from the
current published version).

See **[Publish a New Version](help/publish_version.md)**.

---

## Advanced: Viewing Configuration

The Technical Console includes a configuration viewer/editor for inspecting your
atlas's settings. Changes made there apply to the running configuration but are
a live convenience, not the source of record — treat it as an inspection tool
unless you know what you're changing. See
**[View and Edit Configuration](help/edit_config.md)**.

---

## Where to Get More Help

Each section above links to a short how-to in `help/`. For anything beyond
running your own atlas — adding a new layer, creating a new atlas, email-pipeline
problems, or platform/infrastructure questions — contact your platform
maintainer (SCS). Those tasks live in the maintainer documentation, not this
manual.
