# Cloud Migration Plan — Design Document

## Overview

Migrate the Stewardship Atlas from a persistent always-on server to a cloud-native, mostly serverless architecture. The goal is to significantly reduce costs by eliminating the always-on server, serving static assets cheaply from S3, and only running compute when actively needed.

## Core Principle

Most Atlas usage is **read-only** (viewing maps, downloading data). This can be served as static files from S3 at minimal cost. The webapp only needs to run when someone is actively **editing** data. Heavy processing jobs (QGIS PDF generation, point cloud processing, raster work) run in containers that scale to zero when idle.

---

## Architecture Overview

```
READ (static):
Browser → S3 (direct file access, cheap)

WRITE (edit):
Browser → API Gateway → Lambda (webapp) → S3 (deltas, layer data)

HEAVY PROCESSING:
Lambda (webapp) → SQS queue → Fargate container (QGIS, point cloud, etc.) → S3 (results)
```

---

## Phase 1: Move Static Assets to S3

### Goal
Stop serving static files from the persistent server. Move all read-only Atlas output (HTML, GeoJSON layers, map tiles, PDFs) to S3.

### Steps

1. Create a dedicated S3 bucket for Atlas static assets (separate from the ingress bucket)
2. Modify outlet generation code to write directly to S3 instead of local disk
3. Update DNS to point Atlas domain(s) directly to S3 static website hosting
4. Test that all read-only access works (webmap, downloads, PDFs, etc.)
5. Verify cost savings before proceeding

### Notes
- Skip CloudFront for now — add later if latency or transfer costs become an issue
- Keep existing htaccess-based access control for protected layers during this phase (addressed in identity layer design doc)
- This phase alone should significantly reduce storage and bandwidth costs on the server

---

## Phase 2: Move Webapp to Lambda

### Goal
Stop running the webapp on a persistent server. Convert it to a Lambda function that only runs when edit requests arrive.

### Steps

1. Containerize the existing webapp (create Dockerfile if not already done)
2. Set up IAM roles so Lambda can read/write to S3 buckets
3. Create Lambda function running the webapp (or API Gateway + Lambda handler)
4. Modify webapp to use S3 paths instead of local filesystem paths for:
   - Layer data reads/writes
   - Delta file creation
   - Atlas config reads
5. Set up API Gateway to route edit requests to Lambda
6. Test all edit operations: create features, annotate, delete
7. Verify deltas are written to S3 correctly
8. Decommission the persistent webapp server process

### Notes
- Lambda cold start time is acceptable for edit operations (rare, not latency-sensitive)
- Lambda has access to the same S3 buckets as the old server
- Atlas config (`atlas_config.json`) lives in S3, read by Lambda on startup

---

## Phase 3: Add Async Job Queue for Heavy Processing

### Goal
Move heavy compute jobs (QGIS PDF rendering, point cloud processing, raster operations) off the Lambda and into on-demand containers that scale to zero when idle.

### Steps

1. Identify which `materialize()` calls are "heavy" (QGIS outlets, point cloud, DEM processing)
2. Create an SQS queue for job submissions (`atlas-jobs`)
3. Modify webapp Lambda to detect heavy jobs and POST to SQS instead of running locally:
   - Light jobs: run directly in Lambda (delta application, lightweight vector operations)
   - Heavy jobs: write job definition to SQS, return immediately to user
4. Create a Docker container image for QGIS-specific jobs:
   - Include QGIS system packages, Python bindings, your outlet code
   - Container reads job definition from SQS, processes it, writes results to S3
5. Set up ECS Fargate task definition for the QGIS container
6. Configure Fargate auto-scaling:
   - Min capacity: 0 (scale to zero when idle)
   - Scale up trigger: SQS queue depth > 0
   - Scale down: queue empty for N minutes
7. Test end-to-end: trigger PDF generation from webapp, verify SQS message, container processes, result appears in S3
8. Add similar containers for other heavy job types as needed

### Notes
- Fargate chosen over Lambda for heavy jobs due to Lambda's 15-minute execution limit
- Fargate tasks can run as long as needed (QGIS renders, point cloud jobs may take many minutes)
- Each job type can have its own container image, sized appropriately
- Jobs are idempotent — if a container fails, SQS retries

---

## Cost Model (Approximate)

| Component | Before | After |
|-----------|--------|-------|
| Server (always-on) | $X/month | $0 |
| S3 static serving | $0 | ~$0.023/GB |
| Lambda (edit API) | $0 | Pay per request (~$0) |
| Fargate (heavy jobs) | $0 | Pay per task execution |
| SQS | $0 | ~$0 (free tier covers low volume) |

At low volume (fire departments, field teams), total AWS costs should be well under $10/month after migration.

---

## Migration Order

Each phase is independent and can be validated before proceeding to the next:

1. **Phase 1** (Static → S3): Lowest risk, immediate savings, no code changes to webapp
2. **Phase 2** (Webapp → Lambda): Moderate complexity, requires filesystem → S3 path changes
3. **Phase 3** (Heavy jobs → Fargate): Most complex, but isolated to specific outlet types

---

## Offline Access as an Outlet Artifact

A blocker that stalled the S3 migration: our "static-first" architecture and our "works offline" product promise got fused into one thing — serving the *entire* atlas tree as static files with passive `.htpasswd` access control. That model doesn't survive the move to S3 (S3/CloudFront can't do htpasswd), and it never actually enforced access control anyway (nginx ignores per-directory `.htpasswd`; see security audit, issue #144). The two ideas should be separated:

- **Static-first** is an *implementation* property: outlets are static files; the API is only needed to edit.
- **Offline access** is a *product promise*: a customer can take data into the field with no connectivity.

**Resolution: make the offline bundle an explicit outlet artifact.** Instead of "everything is static so you can just download whatever," we build a deliberate, self-contained package (e.g. a GeoPackage + a bundled viewer, or a zipped static HTML atlas) as a named outlet. You download it, and access control of the downloaded copy is the recipient's responsibility.

Why this unblocks the migration:

- The **live serving layer no longer needs to expose raw private files statically.** Public reads go direct from S3 (cheap, cacheable, no auth). Private reads + all writes go through the authenticated API / STS-scoped temporary credentials (see Cognito Identity Layer doc), which finally enforces the layer `access` field for real.
- **Offline stops being a reason to keep everything world-readable.** It's an intentional export, not a side effect of the serving model.
- It's a **small, self-contained piece** that can be specified and even built early — essentially a `bundle`/`package` outlet that collects the authorized layers plus a viewer.

Product bonus: an explicit, access-controlled export is a *stronger* story than "it's all just static files" — bundles can be scoped to what a given recipient is allowed, timestamped, and logged. That's the same machinery **federation** needs for sharing controlled subsets with regional/agency partners, so this change feeds two commercial narratives, not just closes a security gap.

Caution: "everything through the webapp" must mean everything *sensitive or mutating* — not literally all reads. Routing public map views through the API would kill the scale-to-zero cost win and add cold-start latency to the common case. Keep public reads on the direct static/S3 path.

## Open Questions

1. **Atlas config location**: Move `atlas_config.json` to S3, or keep in Lambda environment?
2. **Staging vs production**: How does the staging/CURRENT versioning model map to S3 paths?
3. **Local development**: How do developers run the webapp locally without the full S3 setup?
4. **Multiple atlases**: One S3 bucket per atlas, or one bucket with atlas-name prefixes?
5. **Access control for protected layers**: See separate Cognito Identity Layer design doc
