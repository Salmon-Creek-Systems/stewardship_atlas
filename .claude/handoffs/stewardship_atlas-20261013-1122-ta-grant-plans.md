# Handoff: stewardship_atlas-20261013-1122-ta-grant-plans

- Repo / branch / HEAD: stewardship_atlas `main` @ wrapup commit (pushed). Uncommitted and not ours: `documents/release_notes/2026-10.txt`, `faults.geojson`, `geology.geojson`.
- Also touched: **internal** repo `main` @ `765e9b7` (pushed).
- Session: 2026-10-08 → 10-13. Issue audit and cleanup, then planning docs for the two new TA grants.

## Where things stand
- **Issue audit done.** Six read-only agents checked all 152 open issues.
  - Closed 18 (17 done/obsolete plus #68 after a box check). Each close has a comment citing the commit.
  - 27 issues got the new `partial` label and a "Shipped / Remaining" comment.
  - #149–151 (redtreevole) triaged.
  - Box checks posted on #66, #117 and #209. #209 also found a writable WebDAV `/upload/` endpoint on internal.fireatlas.org and got the `security` label.
- **#188 (P0) is still live.** `atlas.py:367-372` unconditionally overwrites `regions.geojson` on a full `build_atlas.py <geojson>`. The comment is posted; the code fix hasn't started.
- **TA 2026 grants.** Plans are in `internal/Documents/projects/ta_2026_{scvfd,laytonville}/project_plan.md`. Working agency scope docs (`*_Sep2026_working.docx`) have Tasks, Deliverables and Timeline filled in, plus the no-travel/insurance sentence. The originals are untouched. All committed and pushed.
- CLAUDE.md: the email "Authorized Senders" and "Subject Line Format" sections are corrected to match the code.

## Next step
Scot reviews and edits the draft issue lists in both `project_plan.md` files. Then create, in Salmon-Creek-Systems:
1. Two org Projects: "TA 2026: SCVFD Fire Atlas" and "TA 2026: North Mendocino Fire Atlas".
2. Labels `ta-scvfd` and `ta-nmendo` in both repos.
3. Midway (2026-12-05) and Release (2027-01-31) milestones in `internal`.
4. The project issues in `internal`, with shared issues on both Projects. Add the linked existing atlas/internal issues to the Projects.

## Open questions for Scot
1. **Issue lists:** are S1–S12 and N1–N8 final enough to file?
2. **Non-specialist tools deliverable:** fold it into the atlas deliverable, as was done for Tasks? It's still listed separately.
3. **#188 fix:** seed `regions.geojson` only when it's missing or empty, plus a test. OK to start? Also check whether scvfd, kennedy and napachar regions were ever restored.
4. **#209 priority:** raise it from P3, given the WebDAV upload endpoint?

## Decisions this session
- Grant deliverables and issues cover only the sheet rows marked In. Platform upkeep (15 h) is a reserve, never an issue.
- Shared issues on both Projects: field reporting, web editing, spreadsheets, archived artifacts. Per project: custom features and the data management plan.
- North Mendocino is a new regional atlas that federates per-partner atlases. Midway means that atlas exists and initial partner data is federated, before custom products.
- North Mendocino Access & water dropped below the line after Scot re-ranked the sheet (10-09).
- Project issues live in `internal`; technical cards stay in stewardship_atlas and link up to them.
- Dropped deliberately; don't re-raise: the sqldb empty-db finding (`outlets.py:2069`).

## Issues filed / relevant
- No new issues filed.
- Closed: #10 #19 #21 #23 #28 #29 #32 #40 #58 #62 #68 #73 #87 #90 #118 #121 #122 #155.
- Flagged: #188 (P0, unfixed), #209 (WebDAV), #159 (phase-3 branch holds the fixes for #40, #114, #163 and #181).

## Gotchas
- The internal repo tracks `Documents/` with a capital D; on the Mac it shows as `documents/`. Stage with the capital-D path.
- The docx skill's validate, render and merge scripts don't run on this Mac (Python 3.9, no LibreOffice or pandoc). Edit the XML directly and check it with stdlib.
- The docx fill script was in the session scratchpad and is gone. Its method is recorded in memory (`project_ta_2026_grants.md`).
- In rm commands, a bare `rm -rf $VAR/...` is blocked by the safety check. Use `"${VAR:?}"`.

## Verify state on pickup
```bash
git -C ~/projects/work/scs/stewardship_atlas log --oneline -1
git -C ~/projects/work/scs/internal log --oneline -1          # expect 765e9b7 or later
gh issue list -R Salmon-Creek-Systems/stewardship_atlas --label partial --state open --json number --jq length   # 27
gh project list --owner Salmon-Creek-Systems                  # TA 2026 projects not yet present
```
