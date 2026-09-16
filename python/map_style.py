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
