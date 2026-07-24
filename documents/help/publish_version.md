# Publish a New Version

Publishing creates a permanent snapshot of your atlas data that can be referenced and shared. This is how you release changes from the staging environment to production.

## Understanding Versions

Your atlas has two types of data states:

- **Staging**: The working copy where all edits happen. Always changing.
- **Published Versions**: Permanent snapshots (e.g., `2024-01-15_10-30-00`). Never change once created.

## When to Publish

Publish a new version when:
- You've completed a set of edits and want to preserve them
- You need a stable reference point for sharing
- You want to make changes available to public/internal users
- Before making major changes (as a backup point)

## How to Publish

### From the Admin Console

1. Go to the **Admin Console** (`/staging/outlets/html/admin`)
2. Scroll to the **VERSIONS** section
3. Click **"Publish Atlas"**
4. Wait for the publishing process to complete
   - Status updates will appear showing progress
   - This may take a few minutes depending on data size

### What Happens During Publishing

Publishing is a pure snapshot: it copies staging as-is and computes nothing.
Outputs are **not** rebuilt at publish time — build any that need it first (see
[Build an Output](build_outputs.md)).

1. A new version is created with a timestamp name (e.g., `2024-01-15_14-30-00`)
2. All staging data is copied to the new version folder, exactly as it stands
3. The version is added to the configuration's version list
4. The full edit history (deltas) is retained in the snapshot; nothing is deleted
5. The new version becomes accessible at its own URL path, and becomes the
   version public/internal viewers see

## After Publishing

- The new version appears in the VERSIONS list on the Admin Console
- Each version has its own URL: `/{version}/outlets/html/admin`
- You can switch between versions to compare data
- The staging area is ready for new edits

## Version URLs

Published versions are accessible at:
- Admin console: `/{version}/outlets/html/admin`
- Webmap: `/{version}/outlets/webmap/`
- Downloads: `/{version}/outlets/{outlet_name}/`

## Important Notes

- ⚠️ **Published versions cannot be modified** - they are permanent snapshots
- Publishing does not delete or change the staging data
- You can continue editing in staging immediately after publishing
- Store the version name if you need to reference it later

## Rollback and Reset

- **Rollback** (button in the Versions section) repoints the live version to the
  previous published version — useful if a publish shouldn't have gone out.
- **Reset Staging** discards staging changes and restores it from the current
  published version. Existing staging is backed up first.

## Troubleshooting

**Publishing seems stuck:**
- Check the status messages for progress
- Large datasets may take several minutes
- Refresh the page and check the versions list

**Version not appearing:**
- Refresh the Admin Console page
- Check that publishing completed without errors
- Verify the version appears in the versions list

**Need to undo a publish:**
- You cannot delete published versions through the interface
- Continue editing in staging and publish a corrected version
- Contact your system administrator for manual cleanup if needed
