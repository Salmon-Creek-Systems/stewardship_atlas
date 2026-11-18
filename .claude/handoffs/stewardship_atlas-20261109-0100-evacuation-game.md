# Handoff: stewardship_atlas-20261109-0100-evacuation-game

- Repo / branch / HEAD: stewardship_atlas / `main`. The Evacuation Game work is merged (`13e22f0`, `3c3433e`, `ab53843`); `origin/feature/game-kit-mvp` has nothing unmerged. This handoff is committed on top.
- Uncommitted and not from this session, leave alone: `documents/release_notes/2026-10.txt`, `faults.geojson`, `geology.geojson`.
- Session: 2026-11-08/09, Evacuation Game MVP (#225), then the Overture edge-road gap and why rebuild doesn't keep hand edits.

## Where things stand
- **`game_kit` MVP is merged and runs on any atlas** using only that atlas's data. See `documents/game_kit.md`.
- **SCVFD staging is built:** 19 safe locations, 292 spaces, 0 stranded. The main network has one exit, US 101, which is a real finding. https://fireatlas.org/scvfd/staging/outlets/game_kit/
- **Mather is rebuilt with the new Overture query:** 119 roads, 2 auto-generated exits, every space reaches safety. Its webmap was not re-materialized, and nothing was published.
- **`overture_roads` now keeps segments touching the atlas** (`ab53843`, release `2026-10-21.0`). Only Mather has been rebuilt with it.
- **Parked by Scot:** access enforcement, rebuild replay and config cleanup are captured as issues only. No code was written for them.

## Next step
Nothing is in flight. When returning to this, have Scot review the SCVFD board, then pick up the questions below.

## Open questions for Scot
1. **Order of work:** add a rebuild guard (warn or refuse when `work/` holds hand edits) now, and replay (#200/#223) next, before rolling the Overture fix out to atlases with hand edits?
2. **Writing the replay:** matching old IDs to new ones and reconciling replayed edits is the algorithmically interesting part. Does Scot want to write it?
3. **Access rule behavior (#228):** when inputs are stricter than declared access, tighten and log, or fail the build? Audit the 17 active atlases first either way.

## Decisions this session
- The atlas bbox is the game world: no added data, no connecting roads across the crop, no connector step. Reason: connecting clipped roads would only hide the problem.
- One main-network exit is the game doing its job. Readiness calls it out and admins edit `safe_locations`.
- Private roads count like any other for now (#226).
- Name and class fields belong in the outlet config, guessed from the atlas when unset.
- Outlet access is the most restrictive of its own and its inputs' access; nginx gating is an acceptable mechanism (#228).
- Mather is disposable, so it was fixed directly. SCVFD doesn't need the query fix.
- Don't change how per-atlas `config` blocks behave for now; cleanup is in #53.

## Issues filed / relevant
- #228 Outlet access should be the most restrictive of its inputs, and enforced (new)
- #229 Overture buildings inlet drops buildings that cross the atlas edge (new)
- #200 rebuild drops hand edits (findings comment added) and #223 delta rethink
- #53 config cleanup (item 7 added: nested `config` blocks are discarded)
- #34 Overture release pin: `overture_addresses`, `overture_buildings`, `roads_overture` and `trails_overture` still point at expired releases
- #226 private roads, #227 road labels, #222, #198 annotate duplication, #175/#176/#144 related access work

## Gotchas
- A rebuild is safe only where `work/` holds nothing but inlet pulls. Hand edits on scvfd (10 road deltas), kennedy (10) and gilhamhike (3) would drop out of the layer.
- Never `/refresh` an inlet before a rebuild, because features double. `/refresh` writes a delta and doesn't apply it, so a forgotten delta can surface on a later `mode=update`.
- Per-atlas overrides work only as top-level asset keys. A `config` block in `{atlas}.geojson` is ignored, so read the built `atlas_config.json`.
- Another session landed delta provenance on main during this one (`f134325`): new deltas are named `{source}__{ts}__{action}` and carry a `provenance` member. Older deltas still have the `assetless__` prefix, so replay has to handle both.
- The box is at `0cdabd3` from that other session, not this session's `ab53843`. Check before deploying.

## Verify state on pickup
```bash
git log -3 --oneline
gh issue view 228 --json state,title
curl -s -o /dev/null -w "%{http_code}\n" https://fireatlas.org/scvfd/staging/outlets/game_kit/board.pdf   # 200 until #228 lands
ssh -i ~/.ssh/SCSProd.pem ubuntu@54.241.152.98 'sudo git -C /root/swales_dev/app log -1 --oneline'
```
