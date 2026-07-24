# Refresh a Layer

Edits — whether drawn on the map, imported from a spreadsheet, or emailed in as
photos — are stored as **pending changes** and applied to the layer when you
**refresh** it. Refreshing is also what pushes those changes out to the webmap
and other outputs.

Each layer in the Admin Console has a **Refresh** menu with up to two options.

## Update (apply edits)

Applies pending edits on top of the current layer, and updates the products that
show it (webmap, and so on). This is the everyday choice — fast, and safe for any
layer.

Most editing surfaces already apply your change immediately, so you usually only
need **Update** to gather several accumulated changes at once, or to refresh a
layer whose edits were made in bulk.

## Rebuild from source

Only offered for layers that come from an external source (for example, roads
pulled from OpenStreetMap). Rebuild re-fetches that source to get a fresh base,
then re-applies your pending edits on top.

⚠️ **Edits applied in past refreshes are not replayed by a rebuild.** A rebuild
reflects the fresh source plus whatever edits are currently pending. Use it to
pick up upstream changes or to reconcile a layer — not as a routine refresh.

## After refreshing

The webmap and other layer-driven views update automatically. Printable products
(runbook, gazetteer) are heavier and are built separately — see
[Build an Output](build_outputs.md). When your data is the way you want it,
[publish a new version](publish_version.md) to make it permanent.
