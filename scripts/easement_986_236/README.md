# Easement Bk 986 OR p 236 vs. the THP 1-26-00170-HUM haul route

A record of one analysis (2026-11-20), done for a letter to the editor about whether the
proposed Salmon Creek log haul uses a 60-ft easement whose 1968 grant bars hauling logs.
**Answer: yes.** The easement is today's Thomas Rd through T3S R2E HM Sections 13 and 14,
and the haul route drives it for about 6,630 ft (1.26 mi).

This is deliberately not generalized. To do another easement, copy this directory, then
edit `courses.csv` and the constants at the top of `easement_986_236.py`.

## Sources

| What | Where |
|---|---|
| Grant of Easement, 1968 | Humboldt Bk 986 OR p 236: 60 ft wide, centerline described by 8 calls through the NE¼ SE¼ of Sec 14; Exhibit A lists the served parcels |
| Parcel Map No. 22 | Bk 1 of Parcel Maps pp 34–37; sheet 4 has the station-course-distance table (courses 1–26) |
| THP 1-26-00170-HUM | Sec. IV p. 120, "Traffic Assessment Area": the haul route, as narrative only |
| Prelim title report | Fidelity, 7926 Thomas Rd: the Murphy Sec 11 parcel holds this easement as appurtenant (Parcel Three) |
| Section corners | BLM CadNSDI PLSS (sections and quarter-quarters) |
| Roads | Humboldt County (the scvfd atlas `roads` layer), OpenStreetMap, Overture, Census TIGER/Line, USGS National Transportation Dataset |
| Parcels | Humboldt assessor GIS, `/root/data/humboldt_parcels.geojson` on the box (APN only, no owners) |
| Imagery | USDA NAIP, 2022-05-31, 0.6 m (USGS ImageServer `exportImage`) |

## Method

1. **Transcribe the courses** into `courses.csv`: courses 1–26 from sheet 4 of the parcel
   map (handwritten, so read carefully) and the grant's 8 calls. Course 26 reads "West".
2. **Anchor.** The grant ties course 26's end to the line between Sections 13 and 14,
   33 ft south of their common ¼ corner. That corner comes from BLM's survey-grid data
   (the shared vertex of the quarter-quarters around it).
3. **Rotate to BLM bearings.** The parcel map records Sec 13's north line as N 89°24'58" W;
   BLM's version of that line differs by −1.26°, so every course is turned by that much.
4. **Walk.** Courses 1–26 walk *backward* from the anchor to Station 1; the grant's 8 calls
   walk forward to the west end.
5. **Fit to the county road.** Public corner positions are off by tens of feet out here, and
   the walked line runs ~40 ft (median) from Thomas Rd. The parcel map notes that the road along
   stations 1–8 is county maintained, so courses 1–8 are fitted to Thomas Rd with a rigid
   rotation + shift (iterated closest point). The whole line then moves: −1.1° and ~60 ft.
   Afterwards the *entire* line, not just the fitted part, runs a median 8 ft from the road.
   **Call this correction out whenever the result is quoted.**
6. **Easement strip:** the fitted centerline buffered 30 ft each side (9.5 acres).
7. **Haul route:** the narrative's roads (Serendipity → Lower Samuels Ranch → Thomas →
   Salmon Creek Rd → US 101 Exit 653), as the shortest path over those named roads in the
   county road layer. The THP maps only the first leg.
8. **Overlap:** where the route is within 100 ft of the centerline (6,630 ft), and the part
   strictly inside the 60-ft strip (5,881 ft).
9. **Road-data check:** every 10 ft along the county road, a cross-section perpendicular to it,
   recording where each other dataset crosses. Only road pieces within 45° of the road's
   direction count, so side roads don't register. The datasets never differ by more than
   29 ft (median 9 ft). OSM, Overture and TIGER share lineage and coincide, so this is really
   two independent versions of the road, and the conclusion holds whichever is right.

## Caveats

- **Approximate, not a survey.** The title report notes recorded surveys that put section
  corners at variance with the government plats.
- **The fit uses the county road**, so the county line's agreement with the deed is partly
  by construction. The other four datasets weren't used in the fit and agree just as well.
- **Station 23** is the worst spot: the deed line makes a sharp corner there, and every road
  dataset misses it by 65–100 ft on the same side. Either the road was straightened after 1968
  or courses 22–23 are misread. Check sheet 4 before quoting station-level detail.
- **Grant Exhibit A** lists land in T2S R2E Secs 32/33, where the THP is, among the parcels
  the easement serves. That suggests the THP land is bound by the hauling restriction, but
  it's a question for someone who reads title, and the THP boundary wasn't checked against
  those exact quarter-quarters.
- **Don't measure "nearest road" from the deed line.** A first pass did, snapped to a side
  road at Station 23 and reported a false 102-ft disagreement. Cross-sections with the
  direction filter (step 9) avoid that.

## Running it

```
python3 -m venv /tmp/ezvenv && /tmp/ezvenv/bin/pip install shapely duckdb
/tmp/ezvenv/bin/python scripts/easement_986_236/easement_986_236.py OUT_DIR [--refresh]
```

It needs network access, Google Chrome (for headless screenshots), and ssh to the box as
`ubuntu@` (to clip parcels). Downloads are cached in `OUT_DIR/cache`. Public Overpass servers
are flaky; the script tries two. Overture keeps only ~2 releases, so bump `OVERTURE_RELEASE`
if the query fails.

Outputs in `OUT_DIR`:

- `easement_986_236.geojson`, `thp_haul_route.geojson`: the atlas layers
- `haul_route_vs_easement.{html,png}`: route, strip, stations, parcels, overlap start/end
- `road_datasets_cross_sections.{html,png}`: overview plus close-ups A–F of the six biggest
  disagreements

It finishes by printing the S3 upload and refresh commands for the atlas. It doesn't run
them. The scvfd layers are configured in `configuration/scvfd.geojson` (commit `5ce5fa7`):
admin-only, shown only in `private_webmap`.

Tests: `python/tests/test_easement_traverse.py` covers bearing parsing, the traverse, the fit,
the cross-sections, and pins the transcribed courses' net displacement.
