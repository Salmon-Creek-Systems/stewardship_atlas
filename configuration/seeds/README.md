# Layer seed data

One-off starter contents for **editable control layers** that have no inlet. These
are not auto-loaded by `build_atlas.py` — an editable layer's data lives at
`{data_root}/{atlas}/staging/layers/{layer}/{layer}.geojson` and is normally
populated by drawing in webedit. A seed just gives you something to materialize
before any drawing happens.

## scvfd_mileage_zones.geojson

A single `Thomas` LRS zone whose polygon is the SCVFD atlas boundary, with the
legacy single-anchor coordinates. Materializing against this reproduces today's
single-LRS behavior as one zone; subdivide into real per-road zones in webedit
afterward.

`anchor` is a JSON string in the exact format the webmap **Share → JSON** button
emits, so new zones can be created by drawing a polygon and pasting a shared point
into the `anchor` field.

Install on the server (once), then rebuild + materialize:

```bash
mkdir -p /root/swales_dev/scvfd/staging/layers/mileage_zones
cp configuration/seeds/scvfd_mileage_zones.geojson \
   /root/swales_dev/scvfd/staging/layers/mileage_zones/mileage_zones.geojson

python scripts/build_atlas.py config_only configuration/scvfd.geojson
# then materialize: road_lrs -> road_lrs_markers -> webmap -> html
```
