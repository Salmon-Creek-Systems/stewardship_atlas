# Adding a Layer to an Atlas

Add a GeoJSON dataset (points, lines, or polygons) as a new layer in an existing
atlas — either from a file, or as an empty layer to draw into. Atlas
administrators do this from the console (below); developers can also use the
script or the GET endpoint with a file already in S3.

## From the console (the usual way)

On the **Administration console**, the Layers bar has a **+** button on the
right (the Technical console has a `+ Add Layer` button). It opens a form:

| Field | What it does |
|---|---|
| **Layer name** | The layer's id: lowercase letters, digits, underscores, starting with a letter. Suggested from the file name. |
| **Type** | point, linestring or polygon. Set automatically from an uploaded file when the file has one geometry type. |
| **Width** | Pixels: *line width* for lines, *point radius* for points. Not shown for polygons (a fill outline is always 1px). |
| **Source** | **Upload file**: a GeoJSON FeatureCollection, up to 50 MB. Every feature must match the chosen type. **Empty**: no data; draw features in the edit map. |
| **Label** | Upload: the property to label features with, or *(no labels)*. The choice is copied into `name` at import, which is what the map and PDF labels read. Empty: a checkbox; labels read the `name` field you type when drawing. |
| **Colour** | **Single colour**: R/G/B sliders. **Colour map**: colour features by a numeric property across a named palette (the same palettes rasters use), from *Min* to *Max*. Min/Max are pre-filled from the file. PDFs use the same ramp. |

**Add** uploads the file, registers the layer, rebuilds the config and
materializes the map, edit map, SQL database and consoles. Reload the console
to see the new layer.

`scripts/add_layer.py` (and the equivalent `/add_layer` endpoint) automate what
was previously a hand-edit of the atlas config: it registers the layer and its
inlet, wires it into the map/edit/SQL outlets, rebuilds the config, and
materializes it.

## From a shell: 1. Put the file in S3

Upload the GeoJSON to the **`scs-internal`** bucket, which the server can read
(the `scs-atlas-data` bucket is write-only for the webapp role, so a fetch from
it returns 403):

```
aws s3 cp derelicts.geojson s3://scs-internal/kennedy/imports/derelicts.geojson --profile atlas
```

The default key is `{atlas}/imports/{layer}.geojson`. Use `--s3-key` for a
different location.

## From a shell: 2. Run the tool

On the server, from the app checkout:

```
python scripts/add_layer.py kennedy derelicts
python scripts/add_layer.py kennedy fireline --geometry linestring
python scripts/add_layer.py kennedy parcels  --geometry polygon \
    --s3-key incoming/parcels_2026.geojson --consumers webmap,sqldb
```

Set a custom color with `--color` (hex); it defaults to dark green:

```
python scripts/add_layer.py kennedy hazards --color "#FFAA33"
```

Equivalent HTTP call (`s3_url` accepts a full `s3://bucket/key` or a bare key;
`color` is a hex string):

```
GET /add_layer/{atlas}/{layer}?geometry=point&s3_url=...&color=%23FFAA33
```

The console form uses `POST /add_layer/{atlas}/{layer}` with a JSON body
(`geometry`, `source`, `color`, `width`, `label_property`, `colormap`, `data`).
Uploads are stored in the private bucket, `s3://scs-atlas-private-prod/{atlas}/imports/{layer}.geojson`.
Not in `scs-internal`, which is public-read.

## What it does

- Registers a layer definition, styled **dark green with thick lines / big dots**
  by default so it's visible immediately.
- Adds an `s3_geojson_inlet` asset carrying `out_layer` (required — the delta is
  routed to `deltas/{out_layer}/`).
- Wires the layer into `webmap`, `webedit`, and `sqldb` by default. Override with
  `--consumers`; note atlases with several maps (e.g. `private_webmap`,
  `experimental_webmap`) only get `webmap` unless you name the others.
- Rebuilds `atlas_config.json` and materializes the inlet, layer, and outputs.

## Notes

- **Bounding box clip:** features outside the atlas extent are dropped by the
  inlet. If nothing appears, check the data actually falls within the atlas.
- **Re-runnable:** `add_layer` is idempotent for its own imports. If a run fails
  partway (e.g. the S3 fetch), just fix the cause and run it again — it repairs
  the inlet (bucket/key/`out_layer`) and re-materializes rather than erroring.
  Your layer styling is preserved across re-runs; only the inlet is refreshed.
- **Config drift:** like `copy_layer`, this edits the server-side source
  `{atlas}.geojson`. Commit any pending server-side config changes first so the
  rebuild doesn't reconcile unexpectedly.
- **Feature popups:** the layer shows in the console but click-popups are off
  until you add `show_attributes` + `editable_columns` to the layer definition
  (you need to know the data's fields first). See
  [Layer Interaction Options](user_guide.md#layer-interaction-options).

## Under the hood

`add_layer` automates the manual config described under **Vector GeoJSON layers
from S3** in the [User Guide](user_guide.md#adding-private-s3-data-layers). Reach
for the manual approach when you need field-level control the tool doesn't expose
(custom styling, non-default inlet options).
