"""Pull polygons drawn in one color out of a georeferenced map image.

Used by the `s3_map_polygons` inlet (vector_inlets.py) for maps that arrive as
flattened GeoPDFs or GeoTIFFs, e.g. a THP map with its unit boundaries drawn as
red outlines. The steps, all in the map's own projected CRS (metres):

1. color_mask   — pixels within `tolerance` of the line color
2. close_mask   — dilate then erode, bridging JPEG speckle and small breaks
3. trace        — mask regions to polygons (rasterio.features.shapes); an
                  outline comes back as a ring, whose exterior is the filled area
4. line width   — ring area / centreline length; the filled area is then shrunk
                  by half of it, so the result follows the middle of the drawn line
5. filter       — drop areas under min_area_m2 (legend swatches, stray marks)

numpy is the only import at module level so the mask steps test anywhere;
tracing needs shapely and rasterio (both on the box).
"""
import numpy as np

DEFAULT_TOLERANCE = 60
DEFAULT_CLOSE_PX = 2
DEFAULT_MIN_AREA_M2 = 1000
DEFAULT_SIMPLIFY_M = 2.5


def color_mask(rgb, color, tolerance=DEFAULT_TOLERANCE):
    """Boolean mask of pixels within `tolerance` (Euclidean, 0–255 units) of color.

    rgb: (H, W, 3) or (3, H, W) array; the band-first layout is what GDAL reads.
    """
    a = np.asarray(rgb)
    if a.ndim != 3:
        raise ValueError(f"expected a 3-D RGB array, got shape {a.shape}")
    if a.shape[0] in (3, 4) and a.shape[-1] not in (3, 4):
        a = np.moveaxis(a, 0, -1)
    diff = a[..., :3].astype(np.int32) - np.asarray(color[:3], dtype=np.int32)
    return (diff * diff).sum(axis=-1) <= tolerance * tolerance


def _shift_any(mask, r):
    """Dilate by a (2r+1)-square: OR of the mask shifted every way up to r pixels."""
    h, w = mask.shape
    padded = np.pad(mask, r, constant_values=False)
    out = np.zeros_like(mask)
    for dy in range(2 * r + 1):
        for dx in range(2 * r + 1):
            out |= padded[dy:dy + h, dx:dx + w]
    return out


def close_mask(mask, radius=DEFAULT_CLOSE_PX):
    """Morphological closing (dilate, then erode) with a square of the given radius."""
    if radius <= 0:
        return mask.copy()
    dilated = _shift_any(mask, radius)
    return ~_shift_any(~dilated, radius)


def line_width(ring):
    """Width of a drawn outline, from its ring polygon: area / centreline length.

    The centreline is approximated by the mean of the outer and inner perimeters.
    Returns None for a shape with no hole (not a closed outline).
    """
    if not ring.interiors:
        return None
    inner = sum(i.length for i in ring.interiors)
    centreline = (ring.exterior.length + inner) / 2
    return ring.area / centreline if centreline else None


def trace_polygons(mask, transform, min_area_m2=DEFAULT_MIN_AREA_M2,
                   simplify_m=DEFAULT_SIMPLIFY_M, width_m=None):
    """Polygons enclosed by the outlines in `mask`, in the transform's CRS.

    transform: an affine.Affine pixel → map transform (metres).
    width_m: drawn line width; measured per outline when None.
    Returns (polygons, skipped): polygons sorted largest first, each a dict with
    `geometry` (shapely Polygon) and `width_m`; skipped lists reasons for regions
    that were not closed outlines or were too small, for logging.
    """
    from rasterio import features
    from shapely.geometry import Polygon, shape

    polygons, skipped = [], []
    for geom, value in features.shapes(mask.astype(np.uint8), mask=mask,
                                       transform=transform, connectivity=8):
        if not value:
            continue
        ring = shape(geom)
        width = width_m if width_m is not None else line_width(ring)
        if width is None:
            if ring.area >= min_area_m2:
                skipped.append(f"open line of {ring.area:.0f} m² (no enclosed area; a gap too wide to close?)")
            continue
        filled = Polygon(ring.exterior).buffer(-width / 2, join_style=2)
        if filled.is_empty or filled.area < min_area_m2:
            if not filled.is_empty and filled.area > 0:
                skipped.append(f"outline enclosing {filled.area:.0f} m² < min_area_m2")
            continue
        if filled.geom_type == 'MultiPolygon':  # a pinched outline; keep the main part
            filled = max(filled.geoms, key=lambda g: g.area)
        if simplify_m:
            filled = filled.simplify(simplify_m, preserve_topology=True)
        polygons.append({'geometry': filled, 'width_m': round(width, 2)})
    polygons.sort(key=lambda p: p['geometry'].area, reverse=True)
    return polygons, skipped


M2_PER_ACRE = 4046.8564224


def acres(polygon):
    return round(polygon.area / M2_PER_ACRE, 2)
