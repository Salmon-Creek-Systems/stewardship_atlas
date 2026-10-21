# Create a New Atlas
<!-- audience: admin -->

Anyone can start a new atlas at **https://fireatlas.org/create** — a
neighbourhood, a parcel, a preserve, a field trip.

1. Enter an **Atlas Name** (e.g. *Salmon Creek VFD*). An **Atlas ID** is filled
   in from it; this becomes part of the atlas's web address, so keep it short.
2. Pick a **Starter** — the kind of data the atlas starts with. For example,
   *Field Trip* comes with roads and creeks tuned for zooming in and out, recent
   iNaturalist sightings, and photos by email.
3. Mark the area, in one of three ways:
   - click **Draw Area** and drag a rectangle on the map,
   - type the corner coordinates, or
   - **upload a GeoJSON** boundary — a parcel, preserve or watershed outline.
4. Click **Create Atlas** and wait while it's built (progress is shown). You'll
   be taken to the atlas's console when it's ready.

## What you get

- A web map, edit pages and a console
- An email address — *atlas-id*@fireatlas.org — for
  [sending in geotagged photos](email_photo_submission.md)
- The starter kit's layers, filled in for your area

Printable runbooks aren't built at creation; ask for them once the atlas is set
up.

## Uploading more than a boundary

An uploaded file can also carry features for the starter's layers: give each
feature a `layer` property naming the layer it belongs in (e.g. `regions`).
Features without one are only used for the atlas's outline.
