# Handoff: stewardship_atlas-20261008-1541-parity-rollout

- Repo / branch / HEAD: stewardship_atlas / main / handoff commit on top of `4f77ba6` — pushed, clean (untracked `faults.geojson`, `geology.geojson` predate the session)
- Session: 2026-10-03 → 10-08, web map UI trim → parity push (new console everywhere, help rewrite, 17 atlases) → Fall release email

## Where things stand
- **Live on the box (staging only, not published):** web map UI (Visibility panel + None basemap, Share/Go-to pop-ups, one-row header, Preset Views, show-my-location), new console on all 17 active atlases, old html console 301-redirected, console admin/internal password-gated, audience-tagged help + `?` links everywhere. Verified with curl on every atlas.
- Each atlas's published version still shows the old UI until its **next normal publish** (Scot chose staging-only).
- Fall release email final: `documents/release_notes/2026-10.txt` (`4f77ba6`; draft `cfac461`). Cut sections queued in memory `project_release_notes_queue.md`.
- `~/.claude/skills/wrapup` and `~/.claude/skills/pickup` created (local to this Mac only).

## Next step
"Claude config" session: make skills, meatbot and memory portable across machines/surfaces (repo `.claude/skills/` vs Claude.ai account skills vs dotfiles repo). See memory `project_claude_config.md`. Otherwise pick from the issues below.

## Open questions for Scot
1. **Publishing:** publish atlases now so viewers see the new UI, or wait for each atlas's normal cadence?
2. **Skill portability:** which option — per-repo `.claude/skills`, Claude.ai account skills, or a dotfiles repo?

## Decisions this session
- html console retired; old URLs redirect; old dirs stay on disk until cloud cutover.
- Home → `console/public/` everywhere, incl. webedit ("for now").
- Separate user_manual / admin_manual (not sections); user_guide.md → developers_guide.md.
- `?` links: most specific wins, new tab. Webmap's inline help popup replaced by a link.
- Active vs abandoned atlas list — see project CLAUDE.md "Decisions & Constraints (2026-10)".
- Runbooks not rebuilt; no publish; atlas names left as-is (slugs) — fhe keeps "Foreest" typo.
- Share pop-up always defaults to internal map link; panel toggle sits below map controls on every atlas.

## Issues filed / relevant
- #208 Replace with File appends · #209 internal.fireatlas.org still serves html · #210 trim developers_guide · #211 mobile console `?` links · #212 configs reference undefined layers · #213 kennedy format tests fail · #214 drop `--reload`
- Closed #100, #156; commented #144 (item 2 done). Still open and relevant: #195 (console edits uncommitted on box).

## Gotchas
- `pkill -f "uvicorn…"` inside `ssh … bash -c` kills its own shell → use `[u]vicorn` (cost ~2 min API outage).
- `versioned_outlets` is a publish copy filter — keep `[]`.
- Headless Chrome on the Mac: use `--use-angle=swiftshader --enable-unsafe-swiftshader`, a background-only stub style (remote tiles stall `load`), and kill Chrome after the screenshot (it hangs).
- zsh: `$C:` in paths triggers modifiers, and unquoted `$3` doesn't word-split (use `--label "a,b,c"`).
- Box-only nginx files (`kennedy.`, `internal.`, `westportdev.fireatlas.org`) aren't in git.

## Verify state on pickup
```bash
git log --oneline -3   # handoff commit, 4f77ba6, cfac461
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' https://fireatlas.org/scvfd/staging/outlets/html/admin/   # 301 → console/admin/
curl -s https://fireatlas.org/kennedy/staging/outlets/webmap/index.html | grep -o 'SHOW_USER_LOCATION = true'
gh issue list --search "208..214 in:number" --state open
```
