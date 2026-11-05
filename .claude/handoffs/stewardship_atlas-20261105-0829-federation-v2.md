# Handoff: stewardship_atlas-20261105-0829-federation-v2

- Repo / branch / HEAD: `stewardship_atlas` / `main` / the commit that adds this file, on top of `2ff9da0` (pushed)
- Not in the wrap-up commit, not from this work: `documents/release_notes/2026-10.txt`, `faults.geojson`, `geology.geojson`
- Session: 2026-11-02 to 2026-11-05, Federation v2 design discussion and overview doc

## Where things stand
- Design overview is on main: `documents/development/federation_v2_design.md`. Scot hasn't yet commented on the written doc.
- Design only: no code and no branch. Implementation is post-cloud.
- Delta and federation issues are labeled, including the new `deltas-pipeline` area label.
- Epic filed as #224. Delta rethink filed as #223.

## Next step
None required. When Scot returns to this, have him review the design doc and work through its open issues section. Don't start implementation until he says so.

## Open questions for Scot
1. **Dashboard label:** should `ISSUES.md` and `scripts/gen_issue_dashboard.py` learn `deltas-pipeline`? Neither was changed, since that's a repo change he didn't ask for.
2. **#220 path:** it still cites `documents/federation_v2_design.md`; the doc is now in `documents/development/`. It also leaves open whether `canonicalize` should copy or move.
3. **Mixed access:** the most restrictive source wins for now. He marked this for a later revisit.

## Decisions this session
- One layer holds own and federated features, because a separate view layer adds a concept readers would have to learn.
- Consumers read published versions only. "Publish" is the existing New Version operation.
- Freshness is a notice only, with no triggered rebuilds, even on revocation. The DAG is the eventual route.
- Federated features are read-only, and v2 has no consumer-side changes. Scot rejected an `exclude` mechanism because it would parallel deltas; the delta rethink comes later (#223).
- Both sides opt in, with a `"*"` wildcard. The "With" list shows only overlapping atlases.
- No `shares/` copy: one resolver filters at read time, so publish stays a pure snapshot.
- Share widening and narrowing behave the same, and both wait for the source's next publish.
- Don't add anything just to prohibit chains.
- Admin settings must be simple dropdowns on the Edit Layer form, with a clean config format.
- Vector-only. Basic point dedup, with deeper conflation in #217 and #218.
- `federate` entries carry a full `alterations` block rather than `rename` (#220). Order: resolver, then alterations, then union and dedup.

## Issues filed / relevant
- Filed: #222 game_kit roles invisible to the Dagster graph, #223 delta rethink, #224 Federation v2 epic
- Filed earlier in the session: #217 attribute-aware point conflation, #218 line and polygon conflation
- From the 2026-11-03 session in the wrong terminal: #219 status snapshot, #220 alterations, #221 unify bbox filters
- Commented: #200, #130, #192, #128
- Prerequisite: #195 source config must persist outside the immutable image

## Gotchas
- The 2026-11-03 federation decisions were made from the lcgpt terminal by mistake, so check memory and #219 to #221 for them, not this session's transcript alone.
- `documents/federation-overview.md` describes v1 and was left unchanged on purpose.
- A `gh` body in a quoted heredoc must not escape backticks, or the backslashes get posted.

## Verify state on pickup
```bash
git log -3 --oneline
ls documents/development/federation_v2_design.md
gh issue view 224
```
