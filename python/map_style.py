"""Helpers for the MapLibre style layers the webmap outlet emits.

Standard library only: `outlets` imports duckdb, geopandas, pandas, nbformat,
PIL and gspread at module scope and cannot be imported on a bare checkout, so
anything here that deserves a test lives outside it.
"""

# Every `maxzoom` in every config in this repo is 22, and every one of them
# means "no upper limit". MapLibre hides a layer at zoom levels *greater than
# or equal to* its maxzoom, and 22 is also the default ceiling of the map
# itself — so those layers vanish at exactly the point you finish zooming in.
# 24 is the style spec's maximum and what the label layer already defaults to,
# so it is this codebase's spelling of "no limit".
MAPLIBRE_CEILING_ZOOM = 22
NO_ZOOM_LIMIT = 24


def visibility(vis):
    """A layer's `vis` block with a maxzoom that means what its author meant.

    Returns a copy — the caller merges it into a style layer, and the config it
    came from is shared with the label layer and any badge layers.
    """
    vis = dict(vis or {})
    if vis.get('maxzoom') is not None and vis['maxzoom'] >= MAPLIBRE_CEILING_ZOOM:
        vis['maxzoom'] = NO_ZOOM_LIMIT
    return vis


# ---------------------------------------------------------------------------
# COG addressing. A raster layer normally reads its COG out of its own atlas,
# but `cog_url` lets it name one published by another atlas — so a wider atlas
# can show a neighbour's canopy or fuel rasters without copying gigabytes or
# re-deriving them. Referencing rather than copying is what `classify_layer`
# in federation.py already assumes about rasters.
#
# A federation inlet is the eventual writer of this field, resolving the href
# through the source atlas's STAC catalog instead of having it typed in by
# hand (#159's raster federation). The field is the seam either way.
# ---------------------------------------------------------------------------

def cog_href(layer, local_href):
    """Where a COG layer's data lives — its own atlas, or another's."""
    return layer.get('cog_url') or local_href


def is_external_cog(layer):
    """True when this layer's COG belongs to a different atlas."""
    return bool(layer.get('cog_url'))


def cog_color_wants_stats(cog_color):
    """Whether a cog_color string defers its range to a stats.json sidecar."""
    return bool(cog_color) and 'auto' in str(cog_color).split(',')


def resolve_cog_color(cog_color, stats):
    """Substitute 'auto' min/max in a cog_color with values from a stats dict.

    `stats` is the parsed stats.json sidecar, or None when there isn't one, in
    which case the string comes back untouched for the caller to complain about.
    Format is "{palette},{min},{max},{mode}".
    """
    if not cog_color_wants_stats(cog_color) or not stats:
        return cog_color
    parts = str(cog_color).split(',')
    if len(parts) < 3:
        return cog_color
    if parts[1] == 'auto' and stats.get('min') is not None:
        parts[1] = str(round(stats['min'], 4))
    if parts[2] == 'auto' and stats.get('max') is not None:
        parts[2] = str(round(stats['max'], 4))
    return ','.join(parts)


def has_local_basemap(in_layers, layers_dict, layer_name='basemap'):
    """Whether an outlet should offer the atlas's own raster basemap.

    The dropdown option selects a style layer named `{layer_name}-layer`, which
    exists only when a raster layer of that exact name is among the outlet's
    `in_layers`. The test this replaces — "is the first in_layers entry a
    raster" — held only by the convention of listing the basemap first, and
    broke the moment some other raster went first: a COG borrowed from another
    atlas has to be ordered first so its `before_layer_id` resolves to a vector
    layer and it draws underneath. That offered a basemap the atlas does not
    have, and selecting it makes MapLibre fire an error and skip the change.
    """
    return (layer_name in (in_layers or ())
            and (layers_dict.get(layer_name) or {}).get('geometry_type') == 'raster')
