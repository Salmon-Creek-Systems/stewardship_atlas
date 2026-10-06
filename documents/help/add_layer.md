# Add a Layer
<!-- audience: admin -->

Add a new layer to your atlas from the Admin Console — either from a file you
upload, or empty, ready for drawing into.

1. Click **+** beside **Layers** at the top of the Admin Console.
2. Fill in the form:
   - **Layer name** — lowercase letters, numbers and underscores, starting with
     a letter (e.g. `turnouts`). This can't be changed later.
   - **Type** — points, lines or areas.
   - **Source** — **Upload file** (a GeoJSON file) or **Empty**.
   - **Colour**, **width** (line width, or dot size for points) and, for areas,
     **outline colour** and **opacity**.
   - **Show labels** and which property to label from.
   - **Colour by property** — shade features by a number, such as size or count.
   - **Visible by default** — whether it's showing when the map first opens.
3. Click **Add**.

The layer appears in the console, the web map and the edit pages. Use
[Draw](draw_vector.md) to add to an empty layer.

## Notes

- Uploads must be GeoJSON. Raster (image) layers can't be added this way yet —
  ask SCS.
- Uploaded files are stored privately with your atlas.
- Change the styling later with [Edit Style](edit_layer_style.md).
- Layer changes made in the console are saved on the server. Let SCS know when
  you've added or changed layers so they're kept with your atlas's permanent
  settings.
