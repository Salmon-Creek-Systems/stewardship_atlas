# Handoff: stewardship_atlas-20261026-1059-tours-projects

- Repo / branch / HEAD: stewardship_atlas `main` @ 8d3f836 (+ this wrapup commit), pushed. Pre-existing uncommitted: `documents/release_notes/2026-10.txt` (M), `faults.geojson`, `geology.geojson` (untracked, not this session's).
- Second repo: `~/projects/work/scs/Salmon-Creek-Systems.github.io` `main` @ 824549c, pushed. Untracked: `projects/index.md` (links fixed this session), `projects/nomendo.md`.
- Session: 2026-10-01 → 10-26, long-running session: webmap printing, SFE email test check, TA project-menu sheet, Tours on salmoncreeksystems.com.

## Where things stand
- **Tours live:** https://salmoncreeksystems.com/tours/ (Jekyll `tours` collection, `_tours/south-fork-eel.md`, auto-index `tours/index.md`, header nav link). `documents/release_notes/sfe.md` removed; "Tours" link added to fireatlas.org landing card (`documents/html/index.html`, ce38d30).
- **TA project-menu sheet** (Google Sheet `1YiL8Ckv5bZL8wL2aTnE1rxwfBWVt7ftI7TolfudsADw`): built via CSV upload; formulas live. Later sessions evolved it (see memory `project_ta_2026_grants.md`). It never got conditional formatting: no Sheets connector.
- **SFE email inlet** verified ready (SES rule, live config, Lambda routes on To:).
- Webmap print problem filed as #216.

## Next step
Convert `projects/` on the github.io site into a Jekyll collection like Tours, **after Scot answers the questions below**. Plan: `_projects/` collection in `_config.yml`; move `projects/scvfd.md` (and nomendo if approved) into it; `projects/index.md` → Liquid loop over `site.projects` (sorted by `order` then title); nav link.

## Open questions for Scot
1. **URLs:** permalink `/projects/:name.html` (keeps live `/projects/scvfd.html`, recommended) vs `/projects/:name/` + redirect?
2. **nomendo.md:** publish as-is, hold, or retitle first (currently "...Proposal"; heading typo "Technical Assistances")?
3. **Westport:** OK that the "Westport Fire Atlas" line disappears (no wvfd page)?
4. **Nav:** add "Projects" header link next to "Tours"?
5. **Ordering:** optional `order:` front-matter field, or alphabetical?

## Decisions this session
- Tours go on salmoncreeksystems.com (reuse Jekyll + styling; don't build a fireatlas.org render step on the box being retired).
- Tour links stay on `staging/` URLs; CURRENT may be stale. Revisit during the cloud migration.
- Straight to `main` on the github.io repo is fine.
- Project-menu sheet: hours scale L=3, M=6, M–H=12, H=20; 10% reserve as an explicit priority-0 row; goal items at priorities 1–3. "Engagement" is the non-platform term.

## Issues filed / relevant
- #216 Webmap doesn't print: WebGL canvas is blank in browser print view (new)
- #196 general docs pass (covers stale `documents/html/help/`, a westport-titled generated copy)

## Gotchas
- github.io Pages builds took ~8 min this time; poll `gh run list -R Salmon-Creek-Systems/Salmon-Creek-Systems.github.io` before declaring 404s.
- The layout prints `page.title` as the h1, so tour/project bodies shouldn't repeat a `#` title.
- fireatlas.org landing page is served from the box's checkout (`app/documents/html/`): the Tours link needs a box `git pull`. Not confirmed done.
- No Google Sheets connector in Claude Code (memory `reference_google_sheets_no_connector.md`).
- Scratchpad generator scripts for the sheet were lost; they can be rebuilt from the CSV structure if needed.

## Verify state on pickup
```bash
git -C ~/projects/work/scs/stewardship_atlas log --oneline -3
git -C ~/projects/work/scs/Salmon-Creek-Systems.github.io status --short   # expect the 2 untracked projects files
curl -s -o /dev/null -w '%{http_code}\n' https://salmoncreeksystems.com/tours/south-fork-eel/   # 200
curl -s -o /dev/null -w '%{http_code}\n' https://salmoncreeksystems.com/projects/scvfd.html      # 200, keep it that way
curl -s https://fireatlas.org/ | grep -c 'salmoncreeksystems.com/tours'                          # 1 once the box has pulled
```
