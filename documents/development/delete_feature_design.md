# Delete Feature — Design Document

## Overview

Add the ability to delete features from a layer via the web edit interface. Users draw a polygon to select features, and a "Delete" button appears in the top-right edit panel to remove all intersecting features from the layer. Deletions are tracked via a new delta type for versioning and auditability.

---

## User Workflow

1. User navigates to the web edit interface for an Atlas layer that has `editable_fields` configured
2. User draws a polygon on the map to select feature(s) — same as the existing annotation workflow
3. Top-right panel displays the editable fields for the selected features (existing behavior)
4. **NEW**: A "Delete Feature" button appears in the same panel alongside the existing edit controls
5. User clicks "Delete Feature"
6. Confirmation dialog shows: "Delete X feature(s)? This cannot be undone." with Cancel / Confirm buttons
7. If confirmed, all features intersecting the drawn polygon are removed from the layer
8. A `delete` delta file is written and applied

---

## Design Principles

- **Mirror annotation as closely as possible.** The delete flow should be nearly identical to the annotate flow — same spatial query, same delta pipeline, same API structure.
- **No new layer config needed.** Delete button visibility is determined entirely by the existing presence of `editable_fields` on the layer.
- **Geometry-based matching.** No feature IDs needed. Features are identified by spatial intersection, exactly as annotation currently works.

---

## UI Changes

**Location**: Web edit interface, top-right panel (same panel as editable fields and annotation controls)

**Visibility**: Only show the delete button if the layer has `editable_fields` configured

**Confirmation Dialog**:
- Message: "Delete X feature(s)?"
- Buttons: "Cancel" and "Delete"

---

## API

**Method**: POST (same as annotation)

**URL pattern**: Same endpoint as annotation, with a new action parameter

**Query parameter**: `action=delete`

**Request body**: GeoJSON FeatureCollection containing the drawn polygon geometry (same format as annotation request)

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "geometry": {
        "type": "Polygon",
        "coordinates": [[[lon, lat], [lon, lat], "..."]]
      },
      "properties": {}
    }
  ]
}
```

**Response**: Same structure as annotation response — count of features affected, status.

---

## Backend Processing

### Spatial Query

- Identical to the annotation spatial intersection query
- Find all features in the target layer whose geometry intersects with the drawn polygon
- Any intersection counts (no requirement for full containment)

### Delta File

**Filename**: `{layer_name}__{timestamp}__delete.geojson`

**Location**: `{atlas_path}/staging/deltas/{layer_name}/` (same as all other deltas)

**Content**: GeoJSON FeatureCollection containing the geometries of the features to be deleted. Properties can be empty — geometry match is sufficient for deletion.

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "geometry": { "..." },
      "properties": {}
    }
  ]
}
```

### Delta Application

- Reuse the existing delta application pipeline in `deltas_geojson.py`
- Add handling for `__delete__` delta files: when encountered, remove features from the layer whose geometry intersects with the delta geometries
- All other delta types (create, annotate) remain unchanged

---

## Delta File Naming Convention (Updated)

| Action | Filename suffix |
|--------|----------------|
| create | `__create.geojson` |
| annotate | `__annotate.geojson` |
| **delete** | **`__delete.geojson`** ← new |

---

## Edge Cases

| Scenario | Behavior |
|----------|----------|
| No features intersect drawn polygon | Show message: "No features found to delete" — no delta written |
| Multiple features intersect | All intersecting features deleted in one delta file |
| User cancels confirmation dialog | No delta written, no change |
| Undo / rollback needed | Use existing versioning system — rollback to previous published version |
| Layer has no `editable_fields` | Delete button is not shown |

---

## Files to Create / Modify

| File | Change |
|------|--------|
| `templates/` (web edit UI) | Add delete button to top-right panel (conditional on `editable_fields`); add confirmation dialog |
| `python/webapp.py` | Add `action=delete` handling to edit endpoint |
| `python/deltas_geojson.py` | Add delete delta application logic |

---

## Implementation Checklist

- [ ] Add delete button to web edit UI (conditional on `editable_fields`)
- [ ] Add confirmation dialog with feature count
- [ ] Add `action=delete` handling to edit API endpoint
- [ ] Implement spatial query to find intersecting features (reuse annotation query)
- [ ] Write delete delta file with matched feature geometries
- [ ] Add delete delta handling in `deltas_geojson.apply_deltas()`
- [ ] Test: single feature deletion
- [ ] Test: multiple features deleted in one action
- [ ] Test: confirmation dialog cancel does nothing
- [ ] Test: delete button hidden when no `editable_fields` on layer
- [ ] Test: delta file created correctly and versioning intact
