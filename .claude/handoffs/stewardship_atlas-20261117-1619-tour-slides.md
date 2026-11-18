# Handoff: stewardship_atlas-20261117-1619-tour-slides

- Repo / branch / HEAD: stewardship_atlas / `feature/map-polygon-inlet` (stacked on `feature/slides-outlet`) / see `git log -1`, pushed. Untracked, not ours: `faults.geojson`, `geology.geojson` at repo root.
- The box `/root/swales_dev/app` is on `feature/map-polygon-inlet` too, not main. Switch it back after merging.
- Session: 2026-11-13..17. Legend fix, slides outlet → tour decks (remark + Google Slides), THP map inlet, 3D view scoping and sky, scvfd `federated` and `miranda_to_kennedy` layers.

## Where things stand
- **On main (68d23f4):** legend pinned to `@2.0.7`. The webmaps broke when 3.0.0 dropped UMD. #230 covers the rollout to other atlases.
- **On the branch, all verified on the box:**
  - `slides` outlet: PNGs, a remark `index.html` and `gslides: true` Google deck, with views from `"tour": "<name>"`. Tests: `test_slides.py` and `test_gslides.py` (32).
  - `s3_map_polygons` inlet (`map_trace.py`, 13 tests), which made scvfd `thp_boundaries`.
  - 3D view `in_layers`/`visible_layers` (scvfd, SFE), the blue sky gradient, and the back link hidden with `?chrome=0`.
  - scvfd `federated`: kennedy and mineralkinsey boundaries, teal at 50%.
  - `miranda_to_kennedy`: Scot's console layer, now committed.
- **Decks are live, rendered by hand with `scripts/tour_slides.py`:**
  - scvfd: https://fireatlas.org/scvfd/staging/outlets/slides/ and Google deck `1YE3cHYumXUqyc5XmfkN5pDSj6EihpRoTYd7iaBWTuIE`
  - SFE: https://fireatlas.org/south_fork_eel/staging/outlets/slides/ and Google deck `1t1_42K2hotdz6naVM7tZxYCs6ZyCrYjjTByjERwCHws` (shared with Scot by hand; SFE has no `admin_emails`)
- **scvfd staging is ahead of CURRENT and unpublished:** thp, federated, 3dview, miranda, slides.

## Next step
Ask Scot whether to merge `feature/slides-outlet` and then `feature/map-polygon-inlet` into main. If yes:
1. Merge.
2. On the box, check out main and pull.
3. Hard restart the API (`[u]vicorn` trick; `publish-status?swale=kennedy` must return 200).
4. Re-run `config_only` for scvfd and south_fork_eel.
5. Offer to publish scvfd.

## Open questions for Scot
1. **Merge:** both branches into main now? The summary said the cloud branch waits for Phase 3; that's separate.
2. **Publish scvfd:** staging holds THP, federated, the 3D view, miranda and slides.
3. **nginx stopgap for #144:** the box serves `staging/layers/{private_notes,photos,lpss}` without a login (confirmed 200). Never answered.
4. **`circuit` in scvfd 3D:** the public-only list dropped it. Restore it?
5. **`offline-demo` branch:** keep, merge or delete?
6. **Slides config and console (#231):** said "not yet".

## Decisions this session
- Slides: PNGs first, then a remark deck and a Google Slides deck. Views come from the tour file, not the config.
  - Notes = the paragraph the link is in, else the previous one (Scot's rule).
  - The same Google deck is rebuilt each run (link stable, edits overwritten), shared with `admin_emails` and anyone with the link.
- 3D wait raised to 240 s rather than skipping route views. Scot asked to skip animated views; the SFE view had no route (it's just slow), and he agreed.
- THP layer: translucent red fill rather than thick outlines. `federated` is a static copy, not federation (v2 stays design-only).
- Look: console tokens (IBM Plex, blue links); fireatlas.org dark panel for the title slide.
- Scot declined writing the parsing/fitting pieces himself. Keep offering.

## Issues filed / relevant
- #231 Slides outlet: run from `/refresh`; add tour slides assets to scvfd and SFE
- #232 3D views with no `in_layers` load every layer, admin-only ones included
- #230 legend/pmtiles pin rollout · #144 static layer data served without auth · #190/#128 inlet re-run duplicates

## Gotchas
- `/refresh` is async, so `sync_playwright` fails inside it. Render with `scripts/tour_slides.py`.
- Software WebGL: 3D slides take 1–3 min and the time varies between runs. A full deck takes ~10 min. Don't run two renders at once.
- `--deck-only` reuses old PNGs and `slides.json`, so a tour edit needs a full render.
- Pull before a long run; code changes reach the box only when it pulls.
- remark.js isn't on cdnjs, jsdelivr or unpkg; it's pinned to `remarkjs.com/downloads/remark-0.15.0.min.js`.
- PDF inlets need system `osgeo.gdal`; the box's scipy 1.8 doesn't match numpy 1.26.
- The box repo needs `sudo git …` (safe.directory).

## Verify state on pickup
```bash
git fetch && git log -1 --oneline origin/feature/map-polygon-inlet
ssh -i ~/.ssh/SCSProd.pem ubuntu@54.241.152.98 'cd /root/swales_dev/app && sudo git branch --show-current && sudo git log -1 --oneline'
curl -s -o /dev/null -w '%{http_code}\n' https://fireatlas.org:9000/publish-status?swale=kennedy
curl -s https://fireatlas.org/scvfd/staging/outlets/slides/slides.json | python3 -c 'import sys,json;print([s["status"] for s in json.load(sys.stdin)["slides"]])'
cd python && python3 -m pytest tests/test_slides.py tests/test_gslides.py -q
```
