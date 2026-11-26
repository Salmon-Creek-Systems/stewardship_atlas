"""
Smoothing a line for the 3D view's Animate Camera.

The camera aims at a point moving along the line, MapLibre keeps that point on
the terrain, and the camera sits a fixed height (the page's slider) above it.
Two things make that jerky:

  * corners: a hand-drawn line has long straight segments and sharp kinks, so
    the direction of travel changes all at once at each vertex;
  * terrain: the camera copies every bump under the target, so crossing a
    ridge lifts it and drops it again as fast as the target moves.

`smooth_line` rounds the corners and resamples the line evenly. `lift_profile`
turns the ground profile along the result into a per-vertex *lift*, metres to
add to the slider height, so the camera glides over ridges and stays slider
height above the ground on flat stretches. Lift is relative on purpose: the
slider keeps working, and only the difference between two samples of the same
terrain is stored, so small mismatches with MapLibre's own ground cancel out.

Standard library only, so it is testable on a bare checkout. Fetching the
terrain lives in eddies.camera_route_smooth.
"""
import math

EARTH_RADIUS_M = 6371008.8


# --- local metric frame -----------------------------------------------------

def to_local(coords):
    """Project [lng, lat] pairs onto a flat metre grid centred on the line.

    Equirectangular about the line's mean latitude: accurate to well under 1%
    over the tens of kilometres a camera route spans, which is plenty for
    angles and spacing. Returns (points, to_lnglat) where to_lnglat inverts it.
    """
    lng0 = sum(c[0] for c in coords) / len(coords)
    lat0 = sum(c[1] for c in coords) / len(coords)
    k = math.pi / 180 * EARTH_RADIUS_M
    kx = k * math.cos(math.radians(lat0))

    def to_lnglat(p):
        return [lng0 + p[0] / kx, lat0 + p[1] / k]

    return [((c[0] - lng0) * kx, (c[1] - lat0) * k) for c in coords], to_lnglat


def _dist(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def turn_deg(a, b, c):
    """How far the direction of travel turns at b, in degrees (0 = straight on)."""
    h1 = math.atan2(b[1] - a[1], b[0] - a[0])
    h2 = math.atan2(c[1] - b[1], c[0] - b[0])
    d = math.degrees(h2 - h1)
    return abs((d + 180) % 360 - 180)


# --- horizontal ---------------------------------------------------------------

def simplify(pts, tolerance_m):
    """Douglas-Peucker: drop vertices within tolerance_m of the simplified line.

    Run before corner detection so digitizing jitter doesn't read as turns.
    Endpoints always stay.
    """
    if tolerance_m <= 0 or len(pts) < 3:
        return list(pts)
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        a, b = pts[i], pts[j]
        seg = _dist(a, b)
        far, far_d = None, tolerance_m
        for k in range(i + 1, j):
            p = pts[k]
            if seg == 0:
                d = _dist(a, p)
            else:
                d = abs((b[0] - a[0]) * (a[1] - p[1]) - (a[0] - p[0]) * (b[1] - a[1])) / seg
            if d > far_d:
                far, far_d = k, d
        if far is not None:
            keep[far] = True
            stack.append((i, far))
            stack.append((far, j))
    return [p for p, k in zip(pts, keep) if k]


def round_corners(pts, min_turn_deg=30.0, radius_m=150.0):
    """Replace each vertex that turns more than min_turn_deg with a curve.

    The curve is a quadratic Bezier from a point `r` before the vertex to a
    point `r` after it, with the vertex as its control point, so it meets both
    segments tangentially. `r` is radius_m, capped at half of each neighbouring
    segment, so two curves never claim the same stretch of line. Sharper turns
    get more points (one per ~10 degrees, at least 3).
    """
    if len(pts) < 3:
        return list(pts)
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        a, b, c = pts[i - 1], pts[i], pts[i + 1]
        turn = turn_deg(a, b, c)
        len_in, len_out = _dist(a, b), _dist(b, c)
        if turn <= min_turn_deg or len_in == 0 or len_out == 0:
            out.append(b)
            continue
        r = min(radius_m, len_in / 2, len_out / 2)
        p0 = (b[0] + (a[0] - b[0]) * r / len_in, b[1] + (a[1] - b[1]) * r / len_in)
        p2 = (b[0] + (c[0] - b[0]) * r / len_out, b[1] + (c[1] - b[1]) * r / len_out)
        n = max(3, math.ceil(turn / 10)) + 1
        for k in range(n + 1):
            t = k / n
            u = 1 - t
            out.append((u * u * p0[0] + 2 * u * t * b[0] + t * t * p2[0],
                        u * u * p0[1] + 2 * u * t * b[1] + t * t * p2[1]))
    out.append(pts[-1])
    return out


def cumulative(pts):
    cum = [0.0]
    for i in range(1, len(pts)):
        cum.append(cum[-1] + _dist(pts[i - 1], pts[i]))
    return cum


def densify(pts, spacing_m):
    """Resample at even spacing along the line (the last gap may be shorter)."""
    cum = cumulative(pts)
    total = cum[-1]
    if total == 0:
        return [pts[0]]
    n = max(1, math.ceil(total / spacing_m))
    out, j = [], 1
    for k in range(n + 1):
        d = min(total, k * total / n)
        while j < len(cum) - 1 and cum[j] < d:
            j += 1
        seg = cum[j] - cum[j - 1]
        t = (d - cum[j - 1]) / seg if seg > 0 else 0.0
        a, b = pts[j - 1], pts[j]
        out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


def smooth_line(coords, simplify_m=5.0, min_turn_deg=30.0, radius_m=150.0, spacing_m=15.0):
    """[lng, lat] coords -> smoothed, evenly spaced [lng, lat] coords."""
    pts, to_lnglat = to_local([c[:2] for c in coords])
    pts = simplify(pts, simplify_m)
    pts = round_corners(pts, min_turn_deg, radius_m)
    pts = densify(pts, spacing_m)
    return [to_lnglat(p) for p in pts]


# --- vertical -----------------------------------------------------------------

def grade_envelope(dist, values, max_grade):
    """Smallest profile >= values whose slope never exceeds max_grade.

    Two passes: forward limits how fast it can fall (after a ridge it descends
    at most max_grade), backward limits how fast it can rise (it starts
    climbing early enough to reach a ridge top at max_grade). Raising a point
    in the backward pass never breaks the forward constraint, so two passes
    are exact: the result is the lower envelope of max_grade cones on every
    sample.
    """
    env = list(values)
    for i in range(1, len(env)):
        env[i] = max(env[i], env[i - 1] - max_grade * (dist[i] - dist[i - 1]))
    for i in range(len(env) - 2, -1, -1):
        env[i] = max(env[i], env[i + 1] - max_grade * (dist[i + 1] - dist[i]))
    return env


def gaussian_blur(dist, values, sigma_m):
    """Gaussian smoothing along the line, by distance, truncated at 3 sigma."""
    if sigma_m <= 0 or len(values) < 3:
        return list(values)
    out, lo, hi = [], 0, 0
    n = len(values)
    for i in range(n):
        while dist[i] - dist[lo] > 3 * sigma_m:
            lo += 1
        while hi < n - 1 and dist[hi + 1] - dist[i] <= 3 * sigma_m:
            hi += 1
        wsum = vsum = 0.0
        for k in range(lo, hi + 1):
            w = math.exp(-0.5 * ((dist[k] - dist[i]) / sigma_m) ** 2)
            wsum += w
            vsum += w * values[k]
        out.append(vsum / wsum)
    return out


def lift_profile(dist, ground, max_grade=0.15, blur_m=60.0):
    """Metres to add to the slider height at each sample (always >= 0).

    1. grade_envelope: a virtual ground that is never below the real one and
       never steeper than max_grade.
    2. Blur it, which rounds the cone corners at the foot of each climb.
    3. The blur shaves ridge tops below the real ground, so take the envelope
       again of max(blurred, ground). That restores ground clearance at the
       cost of a sharp apex on ridge tops; the 3D view averages the lift over
       a short window as it plays, which rounds that off.
    """
    if len(ground) < 2:
        return [0.0] * len(ground)
    env = grade_envelope(dist, ground, max_grade)
    env = gaussian_blur(dist, env, blur_m)
    env = grade_envelope(dist, [max(e, g) for e, g in zip(env, ground)], max_grade)
    return [e - g for e, g in zip(env, ground)]


# --- Terrarium tiles (what the 3D view renders) ---------------------------------

def terrarium_elevation(r, g, b):
    """Metres from one Terrarium-encoded pixel."""
    return r * 256 + g + b / 256 - 32768


def tile_pixel(lng, lat, zoom, tile_size=256):
    """(tile_x, tile_y, px, py) for a lng/lat in Web Mercator XYZ, px/py fractional."""
    n = 2 ** zoom
    x = (lng + 180) / 360 * n
    lat_r = math.radians(lat)
    y = (1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 * n
    tx, ty = int(x), int(y)
    return tx, ty, (x - tx) * tile_size, (y - ty) * tile_size
