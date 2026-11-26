# Handoff: stewardship_atlas-20261121-0913-easement-haul

- Repo / branch / HEAD: stewardship_atlas / main / the wrapup commit after `58136f1` (pushed; nothing uncommitted)
- Session: 2026-11-20, does the Salmon Creek THP haul route use the 60-ft easement (Bk 986 OR p 236)? For a letter to the editor.

## Where things stand
- **Answer: yes.** The easement is today's Thomas Rd through T3S R2E Secs 13/14. The haul route runs along it for ~6,630 ft (1.26 mi); 5,881 ft is strictly inside the 60-ft strip.
- **Atlas:** scvfd layers `easement_986_236` and `thp_haul_route` are live (commit `5ce5fa7`): admin-only, s3_geojson imports from `scs-atlas-private-prod/scvfd/imports/`, in `private_webmap` only. Rendering in the live private_webmap is **not visually confirmed** (headless Chrome never finished loading it).
- **Script:** `scripts/easement_986_236/` (`58136f1`) reproduces everything from scratch: courses.csv, the script, and a README with the method and caveats. Tests are in `python/tests/test_easement_traverse.py` (9 pass in the bare local env).
- **Images** for the letter are in `~/Downloads/reimagesforthplettertotheeditor/`. The two current ones are `haul_route_vs_easement_986-236_naip.png` and `road_datasets_cross_sections_naip.png`. The rest are earlier versions.
- **Scot's summary text** for the letter was corrected and rewritten in the session; the method in the README matches it.
- **New tools:** `scripts/pdf_tools/*.swift` read PDFs with macOS PDFKit, because there's no poppler locally.

## Next step
Nothing is required. If Scot comes back to this:
- open `https://fireatlas.org/scvfd/staging/outlets/private_webmap/` and eyeball both layers on Thomas Rd below Miranda;
- check rows 22–23 of the station table on Parcel Map No. 22 sheet 4 (`001pm034.pdf` p4) against `courses.csv`.

## Open questions for Scot
1. **Station 23:** Every road dataset misses the deed's corner by 65–100 ft. Is that a road realignment, or my misreading of the handwriting?
2. **Exhibit A:** The grant lists T2S R2E Secs 32/33 parcels as served by the easement. Does that need a title professional before the letter leans on it?

## Decisions this session
- Admin-only layers that are still world-readable on the box are acceptable here, because every input is public record (#144 remains the real fix).
- Commit to `main`; no branch.
- The script is deliberately ungeneralized. Copy and edit it for the next easement; generalize only then.
- The script prints the atlas S3/refresh steps rather than running them.
- The haul route is reconstructed from the THP narrative over county roads, since the THP maps only the first leg.
- Imagery is NAIP (USGS exportImage), not Esri, at Scot's request.
- A candidate issue ("scvfd `parcels` layer holds address points, not polygons") was dropped at Scot's request. Don't refile it.

## Issues filed / relevant
- None filed. Related: #144 (admin layers world-readable on the box), #190 (re-running an s3 inlet appends duplicates).

## Gotchas
- **Re-running the s3 imports duplicates features (#190).** To replace geometry, upload to the same S3 key, then `/refresh_layer … mode=rebuild`.
- **Don't measure "nearest road" from the deed line.** A first pass snapped to a side road and reported a false 102-ft disagreement. Use perpendicular cross-sections with the direction filter.
- **Network quirks on this Mac.** macOS Python 3.9 urllib fails TLS with overpass-api.de, so use curl. Overpass mirrors time out often. Overture keeps only ~2 releases.
- **NAIP access:** the USGS NAIP `/tile/` endpoint 404s and the WMS errors on 3857; `exportImage` with a 3857 bbox works.
- **The scvfd `parcels` layer is address points;** real parcel polygons are on the box at `/root/data/humboldt_parcels.geojson`.

## Verify state on pickup
```
git log --oneline -3                    # wrapup commit, 58136f1, 5ce5fa7
cd python && python3 -m pytest -q tests/test_easement_traverse.py
for L in easement_986_236 thp_haul_route; do curl -s https://fireatlas.org/scvfd/staging/layers/$L/$L.geojson | python3 -c "import json,sys; print(len(json.load(sys.stdin)['features']))"; done   # 1 and 1
```
