# Add iNaturalist inlet to Stewardship Atlas

## Summary

Add a new inlet that ingests iNaturalist observations for one or more geographic regions, optionally filtered by user(s) and time range, and materializes them into the dataswale as a point layer (one feature per observation).

This follows the standard Inlet → Eddy → Outlet pattern. Phase 1 is a full all-at-once materialize; see **Refresh model** for the delta follow-up.

## Source

- iNaturalist API v1: `GET /observations`
- Cursor pagination via `id_above` + `per_page=200` (avoid `page` offset — it caps at 10k results)
- Relevant query params: `nelat`/`nelng`/`swlat`/`swlng` (bbox), `user_id`/`user_login`, `d1`/`d2` (date range), `quality_grade`, `updated_since`

## Config (inlet/eddy config)

```yaml
inlet: inaturalist
params:
  regions:                # list — one fetch per region, merged into the layer
    - bbox: [swlng, swlat, nelng, nelat]
    - bbox: [swlng, swlat, nelng, nelat]
  users: []               # list of iNat logins; empty/omitted → all users
  time_range: null        # [start, end] ISO dates; null → all time
  quality_grade:          # list; default → everything
    - research
    - needs_id
    - casual
```

### Param notes
- **regions** — list of bboxes. Each region is a separate fetch; results merged into one layer. **No polygon clipping in Phase 1** — bbox is the region, full stop.
- **users** — omit/empty for all users; otherwise filter to the listed logins.
- **time_range** — omit for all time; otherwise pass through to `d1`/`d2`.
- **quality_grade** — config-driven asset parameter, defaults to all three grades. iNat's API takes a single `quality_grade` value per call, so a list → one call per grade, merged (or omit the param entirely when the default set is requested, which is the cheapest path).

## Refresh model

**Phase 1: full materialize, all at once.**

> **Note — delta paradigm candidate.** This inlet is a clean place to pilot update-refreshes. iNat observations carry `updated_at`, and the API supports `updated_since`, so an incremental delta refresh maps directly onto the upstream. Recommend: ship the full-refresh first, then add update-refresh here as the reference delta implementation.

## Output (outlet → dataswale)

- **One feature per observation.**
- **Geometry:** point from the observation's `geojson` / `location`.
- **Properties:**
  | property | source |
  |---|---|
  | `observation_id` | `id` |
  | `username` | `user.login` |
  | `observed_on` | `observed_on` |
  | `quality_grade` | `quality_grade` |
  | `taxon_name` | `taxon.name` |
  | `taxon_id` | `taxon.id` |
  | `iconic_taxon` | `taxon.iconic_taxon_name` |
  | `image_s3_url` | default photo, mirrored to S3 (see below) |
  | `inat_url` | `uri` |
  | `updated_at` | `updated_at` |

## Image handling

- Mirror the **default photo only** to our S3 bucket at ingest; store the resulting S3 link in `image_s3_url`.
- Rationale: iNat photo URLs aren't stable and hotlinking is discouraged.
- Capture `photo.license_code` so we can flag/skip All-Rights-Reserved photos later (don't block Phase 1 on this, but record it).

## Out of scope (Phase 1)

- Polygon clipping (bbox only)
- Mirroring non-default photos
- `taxon` filtering — leave a config hook for it; likely wanted soon
- Update/delta refresh (noted above as the immediate follow-up)

## Acceptance criteria

- [ ] Inlet fetches observations for each configured bbox region and merges them
- [ ] `users`, `time_range`, and `quality_grade` filters work; omitting each yields all-users / all-time / all-grades
- [ ] Output layer has one point feature per observation with the properties above
- [ ] Default photo mirrored to S3 and linked in `image_s3_url`
- [ ] Full materialize refresh runs end-to-end into the dataswale
- [ ] Delta/update-refresh path noted in code as a TODO with `updated_since` wiring
