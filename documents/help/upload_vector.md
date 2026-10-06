# Add Features from a File
<!-- audience: admin -->

Load features into a layer from a GeoJSON file you've prepared elsewhere (in
QGIS, say).

1. In the Admin Console, find the layer.
2. Open **Dataset → Replace with File** and choose your GeoJSON file.

Despite the menu's name, the file's features are **added** to what's already in
the layer. To replace the layer's contents, [clear the layer](replace_layer.md)
first, then load the file.

## Preparing the file

- Standard GeoJSON, in latitude/longitude (WGS 84).
- Useful properties on each feature: `name` (used for labels) and, for lines,
  `vector_width`.
- Features should match the layer's kind — points, lines or areas.

To add a brand-new layer from a file, use [Add a Layer](add_layer.md). To add
just a few features by hand, use [Draw](draw_vector.md).
