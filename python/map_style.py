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


# Named 9-class ColorBrewer ramps, low → high. Named to match cog_color's
# "Brewer{Name}9" so a raster and a vector coloured with the same palette look
# the same. The console's Add Layer form draws its palette picker from this
# table, so this is the one list to extend.
PALETTES = {
    'YlGn':     ['#ffffe5', '#f7fcb9', '#d9f0a3', '#addd8e', '#78c679', '#41ab5d', '#238443', '#006837', '#004529'],
    'YlOrRd':   ['#ffffcc', '#ffeda0', '#fed976', '#feb24c', '#fd8d3c', '#fc4e2a', '#e31a1c', '#bd0026', '#800026'],
    'OrRd':     ['#fff7ec', '#fee8c8', '#fdd49e', '#fdbb84', '#fc8d59', '#ef6548', '#d7301f', '#b30000', '#7f0000'],
    'BuGn':     ['#f7fcfd', '#e5f5f9', '#ccece6', '#99d8c9', '#66c2a4', '#41ae76', '#238b45', '#006d2c', '#00441b'],
    'PuBu':     ['#fff7fb', '#ece7f2', '#d0d1e6', '#a6bddb', '#74a9cf', '#3690c0', '#0570b0', '#045a8d', '#023858'],
    'Purples':  ['#fcfbfd', '#efedf5', '#dadaeb', '#bcbddc', '#9e9ac8', '#807dba', '#6a51a3', '#54278f', '#3f007d'],
    'Blues':    ['#f7fbff', '#deebf7', '#c6dbef', '#9ecae1', '#6baed6', '#4292c6', '#2171b5', '#08519c', '#08306b'],
    'Greens':   ['#f7fcf5', '#e5f5e0', '#c7e9c0', '#a1d99b', '#74c476', '#41ab5d', '#238b45', '#006d2c', '#00441b'],
    'Reds':     ['#fff5f0', '#fee0d2', '#fcbba1', '#fc9272', '#fb6a4a', '#ef3b2c', '#cb181d', '#a50f15', '#67000d'],
    'RdYlGn':   ['#d73027', '#f46d43', '#fdae61', '#fee08b', '#ffffbf', '#d9ef8b', '#a6d96a', '#66bd63', '#1a9850'],
    'Spectral': ['#d53e4f', '#f46d43', '#fdae61', '#fee08b', '#ffffbf', '#e6f598', '#abdda4', '#66c2a5', '#3288bd'],
}


def palette_stops(palette, lo, hi):
    """[[value, '#rrggbb'], ...] spreading the palette's colours evenly from lo
    to hi. The form both colour outputs below are built from."""
    if palette not in PALETTES:
        raise ValueError(f"Unknown palette {palette!r}; expected one of {sorted(PALETTES)}")
    lo, hi = float(lo), float(hi)
    if not lo < hi:
        raise ValueError(f"Colour map range needs min < max, got {lo}..{hi}")
    colors = PALETTES[palette]
    step = (hi - lo) / (len(colors) - 1)
    return [[round(lo + i * step, 6), c] for i, c in enumerate(colors)]


def palette_paint_expression(prop, palette, lo, hi):
    """MapLibre colour expression: linear ramp of `prop` over the palette.
    Features missing the property get the palette's low colour rather than
    MapLibre's black."""
    expr = ['interpolate', ['linear'], ['to-number', ['get', prop], lo]]
    for value, color in palette_stops(palette, lo, hi):
        expr += [value, color]
    return expr


def palette_qgis_color_stops(prop, palette, lo, hi):
    """The `qgis_color_stops` block (read by utils.build_qgis_color_expression)
    that makes PDF output use the same ramp as the webmap."""
    return {'expression': f'coalesce("{prop}", {lo})',
            'stops': palette_stops(palette, lo, hi)}
