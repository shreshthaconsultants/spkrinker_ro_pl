"""
placement.py — Sprinkler grid placement engine v1.0 (optimized)

STRATEGY (v4):
  Phase 1 is the only phase. We lay an axis-aligned grid across the
  floor polygon's bbox and at every grid intersection do one of three
  things:

    * point inside floor → place a head as-is
    * point outside floor but within BOUNDARY_NUDGE_THRESHOLD (600 mm)
      of the nearest polyline edge → project onto that edge and step
      BOUNDARY_NUDGE_INSET (300 mm) inward along the inward normal so
      the rescued head sits 300 mm inside the polyline
    * point outside floor by more than 600 mm → discard silently

  Matches the user rule: "if the point is 600 outside, put it 300 inside
  the polyline layerx."

  The `enable_gap_fill` parameter is accepted for API compatibility but
  is a no-op — the bbox-grid + boundary nudge handles irregular shapes
  inline.
"""

import math
from geometry import (
    bbox, point_in_poly, poly_to_segs,
    min_dist_to_segs, min_dist_to_polys, pt_seg_dist,
    SpatialHash, TOLERANCE,
    find_principal_angle, find_longest_edge_angle,
    rotate_pt, rotate_poly, poly_centroid,
    ANGLE_TOLERANCE_RAD,
)


# ── Grid-aligned slide: bring outside heads back inside the polyline ──

# Maximum distance a grid intersection that lies OUTSIDE the floor polyline
# may be slid along its own grid line (X-line or Y-line) to land back inside.
# Either axis is tried; the shorter slide wins. If neither axis-aligned slide
# within this budget reaches the polyline interior, the point is dropped
# (recorded as a culled marker for the plugin's debug view).
GRID_SLIDE_MAX = 600.0   # mm


def _line_polygon_crossings_y(gx: float, floor_poly: list) -> list:
    """Y-coordinates where the vertical line X=gx crosses *floor_poly*.

    Uses the standard half-open ray-casting convention so vertices that
    lie exactly on the line aren't double-counted.
    """
    crossings = []
    n = len(floor_poly) - 1
    for i in range(n):
        x0, y0 = floor_poly[i]
        x1, y1 = floor_poly[i + 1]
        dx = x1 - x0
        if dx == 0.0:
            continue
        if (x0 <= gx < x1) or (x1 <= gx < x0):
            t = (gx - x0) / dx
            crossings.append(y0 + t * (y1 - y0))
    return crossings


def _line_polygon_crossings_x(gy: float, floor_poly: list) -> list:
    """X-coordinates where the horizontal line Y=gy crosses *floor_poly*."""
    crossings = []
    n = len(floor_poly) - 1
    for i in range(n):
        x0, y0 = floor_poly[i]
        x1, y1 = floor_poly[i + 1]
        dy = y1 - y0
        if dy == 0.0:
            continue
        if (y0 <= gy < y1) or (y1 <= gy < y0):
            t = (gy - y0) / dy
            crossings.append(x0 + t * (x1 - x0))
    return crossings


def _slide_to_inside_along_grid(
    gx: float,
    gy: float,
    floor_poly: list,
    max_slide: float = GRID_SLIDE_MAX,
    inset: float = 1.0,
) -> tuple:
    """
    Given a grid intersection (gx, gy) that lies OUTSIDE *floor_poly*,
    find the closest point that is INSIDE the polygon AND lies on either
    the vertical grid line X=gx or the horizontal grid line Y=gy.

    Returns (new_x, new_y) when a slide of ≤ *max_slide* mm in either axis
    reaches the polygon interior; otherwise returns None. The shorter of
    the two axis-aligned slides is chosen so the head stays as close to
    its original grid intersection as possible.

    `inset` keeps the slid point a small distance off the boundary crossing
    so it lands strictly inside the polyline rather than on its edge.
    """
    best_slide = float("inf")
    best_pt = None

    # ── Vertical X-grid line — slide along Y, X stays = gx ──
    y_cross = _line_polygon_crossings_y(gx, floor_poly)
    if len(y_cross) >= 2:
        y_cross.sort()
        for k in range(0, len(y_cross) - 1, 2):
            y_lo_raw = y_cross[k]
            y_hi_raw = y_cross[k + 1]
            y_lo = y_lo_raw + inset
            y_hi = y_hi_raw - inset
            if y_lo > y_hi:
                # Inside slot narrower than 2*inset — clip to midpoint.
                y_pick = 0.5 * (y_lo_raw + y_hi_raw)
            elif gy < y_lo:
                y_pick = y_lo
            elif gy > y_hi:
                y_pick = y_hi
            else:
                # gy already lies in this inside interval (point appeared
                # outside only due to fuzz) — no slide needed.
                y_pick = gy
            slide = abs(y_pick - gy)
            if slide <= max_slide and slide < best_slide:
                best_slide = slide
                best_pt = (gx, y_pick)

    # ── Horizontal Y-grid line — slide along X, Y stays = gy ──
    x_cross = _line_polygon_crossings_x(gy, floor_poly)
    if len(x_cross) >= 2:
        x_cross.sort()
        for k in range(0, len(x_cross) - 1, 2):
            x_lo_raw = x_cross[k]
            x_hi_raw = x_cross[k + 1]
            x_lo = x_lo_raw + inset
            x_hi = x_hi_raw - inset
            if x_lo > x_hi:
                x_pick = 0.5 * (x_lo_raw + x_hi_raw)
            elif gx < x_lo:
                x_pick = x_lo
            elif gx > x_hi:
                x_pick = x_hi
            else:
                x_pick = gx
            slide = abs(x_pick - gx)
            if slide <= max_slide and slide < best_slide:
                best_slide = slide
                best_pt = (x_pick, gy)

    return best_pt

# ── Global NFPA-13 constants (mm) ────────────────────────────────
# All tunable hyperparameters live in config.py — edit there, then
# restart the backend to apply.
from config import (
    WALL_MIN, WALL_MAX, SPACE_MIN, SPACE_MAX, SPACE_NOM,
    out_cov, in_cov, on_cov,
    DEEPEN_STEP, DEEPEN_LIMIT,
    WALL_CLEARANCE_MIN, MIN_HEAD_SEPARATION, PULL_SEPARATION,
    CORRIDOR_RELAX_MARGIN, CORRIDOR_MIN_CLEARANCE,
    MIN_ROOM_DIM, MIN_ROOM_SHORT_SIDE,
    RESIDUAL_STRETCH_THRESHOLD, RESIDUAL_NEW_LINE_THRESHOLD,
    WALL_STRETCH_EXTRA, WALL_STRETCH_HEADS, WALL_STRETCH_FAR_LAND,
    WALL_STRETCH_FAR_MAX, WALL_NEW_ROW_OFFSET, WALL_RULE_MAX_SKEW_DEG,
    GRID_ANCHOR_TOP_LEFT, GRID_ANCHOR_OFFSET,
)


# ── Wall segment builders ─────────────────────────────────────────

def floor_poly_to_segs(floor_poly: list) -> list:
    """Floor polygon edges → wall reference segments."""
    segs = []
    for i in range(len(floor_poly) - 1):
        x1, y1 = floor_poly[i]
        x2, y2 = floor_poly[i + 1]
        if math.hypot(x2 - x1, y2 - y1) > 1e-6:
            segs.append((x1, y1, x2, y2))
    return segs


def build_zone_wall_segs(floor_poly, wall_segs, wall_polys) -> list:
    """Unified wall segs = floor edges + explicit walls + wall polygon edges."""
    segs = floor_poly_to_segs(floor_poly)
    segs.extend(wall_segs)
    for poly in wall_polys:
        segs.extend(poly_to_segs(poly))
    return segs


# ── Spacing calculator (analytical, O(1)) ─────────────────────────

def fit_spacing(dimension: float, space_min: int, space_max: int) -> dict:
    """
    Find n such that dimension / n ∈ [space_min, space_max].
    Direct O(1) formula instead of iterating up to 10 000.
    """
    dim = float(dimension)
    if dim <= 0:
        return {"n": 1, "spacing": float(SPACE_NOM),
                "offset": float(WALL_MAX), "valid": False}

    n_lo = max(1, math.ceil(dim / space_max))
    n_hi = max(1, math.floor(dim / space_min))

    if n_lo <= n_hi:
        n   = n_lo
        sp  = dim / n
        off = max(WALL_MIN, min(WALL_MAX, sp / 2))
        return {"n": int(n), "spacing": round(sp, 3),
                "offset": round(off, 3), "valid": True}

    if n_hi >= 1:
        sp  = dim / n_hi
        off = max(WALL_MIN, min(WALL_MAX, sp / 2))
        return {"n": int(n_hi), "spacing": round(sp, 3),
                "offset": round(off, 3),
                "valid": bool(space_min <= sp <= space_max)}

    return {"n": 1, "spacing": round(dim, 3),
            "offset": float(WALL_MAX), "valid": False}


# ── Candidate validity check ──────────────────────────────────────

def is_point_valid(
    px, py,
    zone_wall_segs,
    excl_polys,
    obs_polys,
    obs_min_offset,
    placed,
    wall_min=WALL_MIN,
    space_min=SPACE_MIN,
    spacing_hash=None,
    wall_min_override=None,
    space_min_override=None,
) -> tuple:
    """
    Returns (bool, reason_str).  Fail-fast ordering.
    When *spacing_hash* is supplied the O(n) placed-list scan is replaced
    by an O(1) SpatialHash look-up.
    Optional overrides are used only by gap-fill tiers (narrow bays / crowding).
    """
    for ex in excl_polys:
        if point_in_poly(px, py, ex):
            return False, "inside exclusion zone"

    for ob in obs_polys:
        if point_in_poly(px, py, ob):
            return False, "inside obstacle polygon"

    if obs_polys and obs_min_offset > 0:
        d_obs = min_dist_to_polys(px, py, obs_polys)
        if d_obs < obs_min_offset - TOLERANCE:
            return False, f"too close to obstacle ({d_obs:.0f}mm)"

    wm = float(wall_min_override) if wall_min_override is not None else float(wall_min)
    if zone_wall_segs:
        d_wall = min_dist_to_segs(px, py, zone_wall_segs)
        if d_wall < wm - TOLERANCE:
            return False, f"too close to boundary ({d_wall:.0f}mm)"

    sm = float(space_min_override) if space_min_override is not None else float(space_min)
    tol_dist = max(100.0, sm) - TOLERANCE
    if spacing_hash is not None:
        if spacing_hash.any_within(px, py, tol_dist):
            return False, "too close to sprinkler"
    else:
        for qx, qy in placed:
            if math.hypot(px - qx, py - qy) < tol_dist:
                return False, f"too close to sprinkler"

    return True, "ok"


# ── Grid line generator ───────────────────────────────────────────

# Residual = how far the outermost centered grid line sits past the max
# wall band. We use it to decide whether the centered grid leaves an
# uncovered band against the wall:
#   residual ≤ STRETCH_THRESHOLD  → centered grid is acceptable, leave it
#   STRETCH < residual ≤ NEW_LINE → moderate gap; stretch the existing
#                                    rows toward the walls (uniform grid,
#                                    same row count, slightly wider spacing)
#   residual > NEW_LINE_THRESHOLD → big gap; add one more grid line on
#                                    that axis (a new "layer" of heads
#                                    along the wall, uniform grid)
# (Values live in config.py: RESIDUAL_STRETCH_THRESHOLD / RESIDUAL_NEW_LINE_THRESHOLD.)


def _build_lines(minv: float, maxv: float, wall_min: float,
                 start: float, step: float, count: int) -> list:
    """Helper: emit `count` grid positions starting at `start` with `step`."""
    lines = []
    for i in range(count):
        v = round(start + i * step, 3)
        if v <= maxv - wall_min + TOLERANCE:
            lines.append(v)
    return lines


def make_grid_lines(
    minv, maxv, wall_min, sx,
    wall_max=None, space_min=None, space_max=None,
) -> list:
    """
    Generate axis-aligned grid line positions for one zone axis.

    Default behaviour matches the original centered placement. When the
    optional `wall_max`, `space_min`, `space_max` are supplied, the
    function additionally examines the residual (how far the outermost
    centered line sits past the max wall band) and either stretches the
    existing grid or adds one more line so the wall-side coverage isn't
    left to the gap-fill phase. All output stays on a uniform grid.
    """
    span = maxv - minv
    available = span - 2 * wall_min
    if available <= 0:
        return [round(minv + span / 2, 3)]

    n = max(1, int(available / sx) + 1)
    grid_span = (n - 1) * sx
    offset = max(float(wall_min), (span - grid_span) / 2)

    # If the caller didn't pass enough info to evaluate the residual,
    # behave exactly like the original centered placement.
    if wall_max is None or space_min is None or space_max is None:
        return _build_lines(minv, maxv, wall_min,
                            minv + offset, sx, n)

    residual = offset - float(wall_max)

    def try_stretch() -> list:
        """Keep n lines, push outer lines to the wall_max band, recompute spacing."""
        if n < 2:
            return None
        new_grid_span = span - 2.0 * float(wall_max)
        new_sx = new_grid_span / (n - 1)
        if not (float(space_min) <= new_sx <= float(space_max)):
            return None
        return _build_lines(minv, maxv, wall_min,
                            minv + float(wall_max), new_sx, n)

    def try_add_line() -> list:
        """
        Add one more grid line on this axis, packed at wall_min on both sides.

        Only fires when the result keeps a row in the *middle* of the room.
        With an odd `n`, one row already passes through the bbox midpoint —
        adding a single line would flip n to even and remove that center
        row, leaving heads biased to the left and right of the middle.
        We skip add-line in that case (the caller falls back to stretch
        or the centered original layout). With an even `n` (no center row),
        adding one line flips it to odd and the new layout *gains* a
        middle row — that's the case we actually want.
        """
        if n % 2 == 1:
            return None
        n_new = n + 1
        if n_new < 2:
            return None
        new_grid_span = span - 2.0 * float(wall_min)
        new_sx = new_grid_span / (n_new - 1)
        if not (float(space_min) <= new_sx <= float(space_max)):
            return None
        return _build_lines(minv, maxv, wall_min,
                            minv + float(wall_min), new_sx, n_new)

    if residual > RESIDUAL_NEW_LINE_THRESHOLD:
        # Big gap → prefer adding a new row of heads along the wall.
        for fn in (try_add_line, try_stretch):
            res = fn()
            if res is not None:
                return res
    elif residual > RESIDUAL_STRETCH_THRESHOLD:
        # Moderate gap → prefer stretching existing rows toward the walls.
        for fn in (try_stretch, try_add_line):
            res = fn()
            if res is not None:
                return res

    # No residual handling needed (or neither option fits the spacing band)
    # → fall back to the original centered placement.
    return _build_lines(minv, maxv, wall_min,
                        minv + offset, sx, n)


# ── Wall ownership helper ─────────────────────────────────────────

def _nearest_edge_axis_aligned(px, py, segs, skew_tan):
    """True if the polygon edge nearest to (px, py) is grid-aligned
    (within WALL_RULE_MAX_SKEW_DEG of horizontal or vertical).

    Ownership rule: grid-aligned walls are handled by the Phase-4
    alpha/gama/new-row rules; Phase-2 boundary pulls serve only slanted
    walls. Without this split both mechanisms fill the same wall strip
    (pull at 700 + add at 1000) and heads pair up along the boundary.
    """
    best = None
    bd = float("inf")
    for s in segs:
        d = pt_seg_dist(px, py, s[0], s[1], s[2], s[3])
        if d < bd:
            bd = d
            best = s
    if best is None:
        return False
    dx = abs(best[2] - best[0])
    dy = abs(best[3] - best[1])
    return dx <= skew_tan * dy or dy <= skew_tan * dx


# ── Anchored grid lines (top-left rule) ───────────────────────────

def _anchored_lines(minv, maxv, anchor_off, step, end_clearance, from_high):
    """
    Grid positions anchored `anchor_off` inside ONE wall, marching at
    `step` toward the opposite wall, allowed up to `end_clearance` from
    it. `from_high=True` anchors at the high end (the TOP wall for the
    Y axis); False anchors at the low end (the LEFT wall for X).

    User rule (2026-06-05): the grid is NOT re-centered in the room
    rectangle — it starts 1500 from the top and left walls and the
    leftover space falls at the right/bottom, where the end-of-row
    NEAR/FAR stretch rules absorb it.

    Falls back to a single centered line when the anchor offset doesn't
    fit (narrow rooms / relaxed corridors keep their centered row).
    """
    lines = []
    if from_high:
        v = maxv - anchor_off
        stop = minv + end_clearance
        while v >= stop - TOLERANCE:
            lines.append(round(v, 3))
            v -= step
        lines.reverse()          # keep ascending order like make_grid_lines
    else:
        v = minv + anchor_off
        stop = maxv - end_clearance
        while v <= stop + TOLERANCE:
            lines.append(round(v, 3))
            v += step
    if not lines:
        return [round(minv + (maxv - minv) / 2.0, 3)]
    return lines


# ── Boundary nudge: keep grid heads that sit just outside the polygon ──

# User-facing rule: "if the sprinkler center is out of the main polyline
# around out_cov, pull the point on the grid inside in_cov." Two knobs:
#   out_cov = how far outside the polyline a head CENTER may be and still
#             be pulled back in
#   in_cov  = how far inside the polyline the pulled head lands
# Anything outside by more than out_cov is discarded silently.
# (Values live in config.py: out_cov / in_cov.)
BOUNDARY_NUDGE_THRESHOLD = out_cov   # backwards-compat aliases
BOUNDARY_NUDGE_INSET     = in_cov
BOUNDARY_NUDGE_MARGIN    = in_cov

# Minimum distance from any head to the room boundary polyline (user rule:
# "from wall make 800 away, no closer than 800"). Grid rows start this far
# inside the bbox, rescued (nudged) heads land this far inside the nearest
# edge, and any head that still ends up closer than this to a wall is
# dropped. Narrow corridors relax it locally so a centered row survives.
# (Value lives in config.py: WALL_CLEARANCE_MIN.)


def _nudge_inside(
    px: float,
    py: float,
    floor_poly: list,
    floor_segs: list,
    centroid: tuple,
    threshold: float = BOUNDARY_NUDGE_THRESHOLD,
    inset: float = BOUNDARY_NUDGE_INSET,
    margin: float = None,
) -> tuple:
    """
    For a grid intersection (px, py) lying OUTSIDE *floor_poly*, decide
    whether to rescue it (project inside) or drop it.

      * Distance to nearest edge > threshold → return None (discard).
      * Otherwise → project onto the closest edge, then step `inset` mm
        further inward along that edge's inward normal so the head sits
        `inset` mm inside the boundary.

    The "inward" direction is picked by comparing the two perpendicular
    normals to the polygon centroid, which is robust to CCW/CW winding.

    `margin` is kept for backwards compatibility with the old single-knob
    API — when supplied, it acts as both threshold and inset.
    """
    if margin is not None:
        threshold = float(margin)
        inset     = float(margin)

    if not floor_segs:
        return None

    best_dsq   = float("inf")
    best_proj  = None       # (qx, qy) on the closest edge
    best_edge  = None       # (x1, y1, x2, y2) of the closest edge

    for s in floor_segs:
        x1, y1, x2, y2 = s
        dx = x2 - x1
        dy = y2 - y1
        lsq = dx * dx + dy * dy
        if lsq == 0.0:
            qx, qy = x1, y1
        else:
            t = ((px - x1) * dx + (py - y1) * dy) / lsq
            if t < 0.0:
                t = 0.0
            elif t > 1.0:
                t = 1.0
            qx = x1 + t * dx
            qy = y1 + t * dy
        ddx = px - qx
        ddy = py - qy
        dsq = ddx * ddx + ddy * ddy
        if dsq < best_dsq:
            best_dsq  = dsq
            best_proj = (qx, qy)
            best_edge = s

    if best_proj is None:
        return None

    if best_dsq > threshold * threshold:
        return None

    # Build the two unit normals of the closest edge and pick the one
    # whose tip lies closer to the centroid → that's the inward normal.
    x1, y1, x2, y2 = best_edge
    ex = x2 - x1
    ey = y2 - y1
    elen = math.hypot(ex, ey)
    if elen == 0.0:
        # Degenerate edge — fall back to "toward centroid" direction.
        cx, cy = centroid
        vx = cx - best_proj[0]
        vy = cy - best_proj[1]
        vlen = math.hypot(vx, vy)
        if vlen == 0.0:
            return None
        nx, ny = vx / vlen, vy / vlen
    else:
        # Two candidate normals: (-ey, ex)/elen and (ey, -ex)/elen.
        n1x, n1y = -ey / elen, ex / elen
        n2x, n2y =  ey / elen, -ex / elen
        cx, cy = centroid
        d1sq = (best_proj[0] + n1x - cx) ** 2 + (best_proj[1] + n1y - cy) ** 2
        d2sq = (best_proj[0] + n2x - cx) ** 2 + (best_proj[1] + n2y - cy) ** 2
        if d1sq <= d2sq:
            nx, ny = n1x, n1y
        else:
            nx, ny = n2x, n2y

    qx, qy = best_proj
    return (qx + inset * nx, qy + inset * ny)


# ── Phase 4: end-of-row wall stretch ──────────────────────────────

def _stretch_runs_to_walls(points, x_lines, y_lines, floor_poly, coverage_radius):
    """
    User rules (prompt.txt, 2026-06-05): act on the gap from a wall to
    the LAST head center of the grid row/column running into it:

      gap ≤ coverage_radius                → covered, leave alone
      ALPHA — radius < gap ≤ radius+350 (1850): stretch the last 3 bays,
          each by x = (gap − 1500)/3; the end head lands exactly
          coverage_radius from the wall.
      GAMA — 1850 < gap ≤ 2600: ADD a new sprinkler 1000
          (WALL_STRETCH_FAR_LAND) inside the wall on that line, then
          squeeze the last 3 EXISTING bays, each by y = (gap − 1000)/3
          (the existing last 3 heads cascade AWAY from the wall; the new
          head is not one of the three).
      NEW ROW — gap > 2600: place sprinklers normally — add one new head
          WALL_NEW_ROW_OFFSET (1500) from the wall on that line. Across
          all grid lines this forms a normal new row/column.

    Sweeps repeat (≤3) so added rows get their own ends handled — e.g.
    the bottom-right corner. Per-line decisions are memoized by the
    line's signature ("do not calculate it many times"): a rectangle
    computes its correction once and every identical row reuses it.

    Rows/columns are grouped by the heads' shared exact coordinate in
    the LOCAL (pre-rotation) frame, so added rows participate exactly
    like lattice ones. New heads must land inside the polyline and keep
    MIN_HEAD_SEPARATION from every other head. A corner head may receive
    both an X and a Y shift; if that diagonal move would escape a
    slanted corner it falls back to the larger single-axis shift.
    Returns (points, alpha_used, gama_used) — the counts go to the
    plugin so the ZWCAD terminal can report which formulas fired.
    """
    cov      = float(coverage_radius)
    alpha_hi = cov + float(WALL_STRETCH_EXTRA)      # alpha cap (1850 @ r=1500)
    gama_hi  = float(WALL_STRETCH_FAR_MAX)          # gama cap (2600)
    gama_off = float(WALL_STRETCH_FAR_LAND)         # gama new-head offset (1000)
    new_off  = float(WALL_NEW_ROW_OFFSET)           # new-row offset (1500)
    k_max    = int(WALL_STRETCH_HEADS)
    if k_max < 1 or not points:
        return list(points), 0, 0

    pts = list(points)
    sep_sq = float(MIN_HEAD_SEPARATION) ** 2
    alpha_used = 0
    gama_used  = 0
    shifts: dict = {}            # point index -> [dx, dy]

    wall_segs = floor_poly_to_segs(floor_poly)
    min_wall  = float(in_cov)    # adds keep at least this from every wall
    skew_tan  = math.tan(math.radians(float(WALL_RULE_MAX_SKEW_DEG)))

    # Only true grid lines may form runs: the lattice rows/columns plus
    # rows/columns created by our own adds. Phase-2 pulls sharing a
    # coordinate must NOT seed pseudo-rows (that cascaded extra heads
    # along walls). Keyed by ax: 0 → row keys (shared y), 1 → column
    # keys (shared x).
    allowed = {
        0: {round(v, 3) for v in (y_lines or [])},
        1: {round(v, 3) for v in (x_lines or [])},
    }

    def _groups(ax):
        """Allowed shared line-coordinate → indices sorted along the line.
        ax is the coordinate that varies along the line (0=row, 1=column)."""
        keys = allowed[ax]
        g: dict = {}
        for i, p in enumerate(pts):
            k = p[1 - ax]
            if k not in keys:
                continue
            g.setdefault(k, []).append(i)
        for v in g.values():
            v.sort(key=lambda i: pts[i][ax])
        return g

    def _intervals(ax, fixed):
        cr = (_line_polygon_crossings_y(fixed, floor_poly) if ax == 1
              else _line_polygon_crossings_x(fixed, floor_poly))
        if len(cr) < 2:
            return []
        cr.sort()
        return [(cr[c], cr[c + 1]) for c in range(0, len(cr) - 1, 2)]

    def _decide(gap):
        """Pick the rule for one run end from its wall gap."""
        if gap <= cov:
            return None          # covered
        if gap <= alpha_hi:
            return 'alpha'       # stretch last 3 bays, land at radius
        if gap <= gama_hi:
            return 'gama'        # new head at 1000 + squeeze last 3
        return 'newrow'          # place sprinklers normally (new row @1500)

    def _end_wall_perpendicular(ax, fixed, coord):
        """True if the polygon edge at this run-end crossing is
        perpendicular to the run (within WALL_RULE_MAX_SKEW_DEG).
        Slanted/diagonal walls return False — those are owned by the
        Phase-2 boundary pulls, not the alpha/gama/new-row rules."""
        px, py = (coord, fixed) if ax == 0 else (fixed, coord)
        for x1, y1, x2, y2 in wall_segs:
            if pt_seg_dist(px, py, x1, y1, x2, y2) <= TOLERANCE:
                dx = abs(x2 - x1)
                dy = abs(y2 - y1)
                if ax == 0:                      # run along X → wall ~vertical
                    return dx <= skew_tan * dy
                return dy <= skew_tan * dx       # run along Y → wall ~horizontal
        return False

    seen = {(p[0], p[1]) for p in pts}
    done_ends: set = set()       # each (line, interval, side) handled once
    decision_memo: dict = {}     # line signature -> (hi_rule, lo_rule);
                                 # identical rows (rectangles) decide once

    # ── Sweeps: decide each run end once; adds may create new rows that
    #    get their own ends handled on the next sweep (corners etc.). ──
    for _sweep in range(3):
        pending: list = []

        def _try_add(ax, fixed, land):
            cand = ((round(land, 3), fixed) if ax == 0
                    else (fixed, round(land, 3)))
            if cand in seen:
                return False
            if not point_in_poly(cand[0], cand[1], floor_poly):
                return False
            # Wall clearance: an add must keep in_cov from EVERY wall —
            # near corners/diagonals the along-line offset can otherwise
            # land a head almost on an adjacent edge.
            if min_dist_to_segs(cand[0], cand[1], wall_segs) < min_wall - TOLERANCE:
                return False
            for q in pts:
                if (cand[0] - q[0]) ** 2 + (cand[1] - q[1]) ** 2 < sep_sq:
                    return False
            for q in pending:
                if (cand[0] - q[0]) ** 2 + (cand[1] - q[1]) ** 2 < sep_sq:
                    return False
            seen.add(cand)
            pending.append(cand)
            # The add seeds (or extends) a perpendicular row/column at
            # its varying coordinate — register it so the next sweep
            # processes that new line's own ends (e.g. corners).
            allowed[1 - ax].add(round(land, 3))
            return True

        for ax in (0, 1):
            for fixed, idxs in _groups(ax).items():
                for lo, hi in _intervals(ax, fixed):
                    run = [i for i in idxs if lo <= pts[i][ax] <= hi]
                    if not run:
                        continue
                    sig = (ax, round(lo, 1), round(hi, 1),
                           tuple(round(pts[i][ax], 1) for i in run))
                    rules = decision_memo.get(sig)
                    if rules is None:
                        rules = (_decide(hi - pts[run[-1]][ax]),
                                 _decide(pts[run[0]][ax] - lo))
                        decision_memo[sig] = rules
                    for side, rule in zip(('hi', 'lo'), rules):
                        if rule is None:
                            continue
                        key = (ax, fixed, round(lo, 1), round(hi, 1), side)
                        if key in done_ends:
                            continue
                        # Ownership: only act on walls perpendicular to
                        # the run. Slanted walls → Phase-2 pulls.
                        if not _end_wall_perpendicular(
                                ax, fixed, hi if side == 'hi' else lo):
                            done_ends.add(key)
                            continue
                        if side == 'hi':
                            sgn, wall = 1.0, hi
                            end_idx = list(reversed(run))
                        else:
                            sgn, wall = -1.0, lo
                            end_idx = list(run)
                        gap = sgn * (wall - pts[end_idx[0]][ax])
                        if rule == 'newrow':
                            # gap > 2600 → place sprinklers normally:
                            # one new head 1500 from the wall.
                            if _try_add(ax, fixed, wall - sgn * new_off):
                                done_ends.add(key)
                        elif rule == 'gama':
                            # 1850 < gap ≤ 2600 → new head 1000 from the
                            # wall + squeeze the last 3 existing bays by
                            # y = (gap−1000)/3 each (heads cascade AWAY
                            # from the wall; new head not in the three).
                            if _try_add(ax, fixed, wall - sgn * gama_off):
                                total = gap - gama_off       # = 3·y
                                k = min(k_max, len(run))
                                for j, i_pt in enumerate(end_idx[:k]):
                                    shifts.setdefault(i_pt, [0.0, 0.0])[ax] \
                                        -= sgn * total * (k - j) / k
                                gama_used += 1
                                done_ends.add(key)
                        else:
                            # alpha: 1500 < gap ≤ 1850 → stretch the last
                            # 3 bays by x = (gap−1500)/3 each; end head
                            # lands exactly coverage_radius from the wall.
                            if len(run) < 2:
                                done_ends.add(key)
                                continue
                            d = gap - cov                    # = 3·x
                            k = min(k_max, len(run) - 1)
                            for j, i_pt in enumerate(end_idx[:k]):
                                shifts.setdefault(i_pt, [0.0, 0.0])[ax] \
                                    += sgn * d * (k - j) / k
                            alpha_used += 1
                            done_ends.add(key)

        if not pending:
            break
        pts.extend(pending)

    if not shifts:
        return pts, alpha_used, gama_used
    out = []
    for i, (px, py) in enumerate(pts):
        if i not in shifts:
            out.append((px, py))
            continue
        dx, dy = shifts[i]
        # Each single-axis shift lands on its own grid line strictly
        # inside that line's inside-interval — safe. But a corner head
        # can receive BOTH shifts, and the combined diagonal move was
        # never validated against the polygon (it can escape past a
        # slanted corner). Try the full move first, then fall back to
        # the larger single-axis shift, then the smaller, then no move.
        candidates = [(dx, dy)]
        if dx != 0.0 and dy != 0.0:
            first, second = ((dx, 0.0), (0.0, dy)) if abs(dx) >= abs(dy) \
                            else ((0.0, dy), (dx, 0.0))
            candidates += [first, second]
        candidates.append((0.0, 0.0))
        for cdx, cdy in candidates:
            nx, ny = px + cdx, py + cdy
            if point_in_poly(nx, ny, floor_poly):
                out.append((round(nx, 3), round(ny, 3)))
                break
        else:
            out.append((px, py))
    return out, alpha_used, gama_used


# ── Main placement function ───────────────────────────────────────

def generate_zone_sprinklers(
    floor_poly,
    excl_polys,
    wall_segs,
    wall_polys,
    obs_polys,
    obs_min_offset,
    all_placed,
    wall_min=WALL_MIN,
    wall_max=WALL_MAX,
    space_min=SPACE_MIN,
    space_max=SPACE_MAX,
    coverage_radius=1500,
    enable_gap_fill=True,   # accepted for API compat — gap-fill removed; the
                            # bbox-grid + boundary-nudge strategy handles
                            # irregular shapes inline.
) -> dict:
    """
    Full placement for one floor zone:
      Lay an axis-aligned grid across the floor polygon's bbox, then for
      every intersection: keep it if inside the polyline (and at least
      WALL_CLEARANCE_MIN from every wall); if its center is outside but
      within out_cov of the nearest edge, pull it back along its own grid
      line so it lands in_cov inside; otherwise discard silently.
      (out_cov / in_cov are tuned in config.py.)
    """
    minx, maxx, miny, maxy = bbox(floor_poly)
    width  = maxx - minx
    height = maxy - miny

    # User rule: skip rooms where NEITHER dimension reaches 2000 mm.
    # That's the threshold below which the zone is treated as a closet /
    # tiny void that doesn't need a sprinkler. Above 2000 mm in either
    # direction we always try to place at least one centered head.
    #
    # ALSO skip sliver/degenerate polylines (double-drawn wall outlines,
    # zero-height "rooms"): they have a huge long side but almost no short
    # side, so they passed the old check and produced a centered row of
    # heads sitting visually ON the wall line. No real room is narrower
    # than MIN_ROOM_SHORT_SIDE. (Values live in config.py.)
    if (max(width, height) < MIN_ROOM_DIM
            or min(width, height) < MIN_ROOM_SHORT_SIDE):
        return {
            "points":         [],
            "extra_points":   [],
            "culled_points":  [],
            "alpha_used":     0,
            "gama_used":      0,
            "spacing_x":      fit_spacing(width,  space_min, space_max),
            "spacing_y":      fit_spacing(height, space_min, space_max),
            "x_lines":        [],
            "y_lines":        [],
            "grid_cols":      0,
            "grid_rows":      0,
            "x_offset_mm":    0,
            "y_offset_mm":    0,
            "width_m":        round(width  / 1000, 2),
            "height_m":       round(height / 1000, 2),
            "rejected":       0,
            "warnings":       [
                "room skipped — too small or sliver-thin "
                "({:.0f} × {:.0f})".format(width, height)
            ],
            "wall_segs_used": 0,
            "zone_wall_segs": [],
        }

    # Live head-to-wall clearance. For narrow corridors (one side <
    # 2*clearance) relax it so a centered single row still passes —
    # otherwise corridors narrower than 1600 mm would receive zero
    # sprinklers despite being long enough (>2000mm in the other
    # direction) to need them. A 50 mm safety margin keeps the head off
    # the wall itself.
    short_side = min(width, height)
    if short_side < 2.0 * WALL_CLEARANCE_MIN:
        clearance = max(CORRIDOR_MIN_CLEARANCE,
                        short_side / 2.0 - CORRIDOR_RELAX_MARGIN)
    else:
        clearance = WALL_CLEARANCE_MIN

    # ── Phase 1: Grid placement ───────────────────────────────────
    rx = fit_spacing(width,  space_min, space_max)
    ry = fit_spacing(height, space_min, space_max)
    sx = rx["spacing"]
    sy = ry["spacing"]

    # Fixed-spacing scenarios (space_min == space_max): honour the EXACT
    # head-to-head pitch instead of fit_spacing's equal-bay division
    # (dim / n, which lands ≥ the pitch, e.g. 3030–3140 for "Fixed 3000").
    # The grid stays centered; the leftover space goes to the walls.
    if space_min == space_max:
        sx = sy = float(space_min)
        rx = {**rx, "spacing": sx}
        ry = {**ry, "spacing": sy}
    else:
        # Range scenarios (e.g. 3050–3100): when the equal-bay division
        # cannot land inside the band for an axis (fit_spacing falls back
        # to dim/n outside it), snap that axis to the band midpoint as an
        # exact pitch — centered grid, leftover to the walls — so the
        # spacing always stays "around" the requested band.
        mid = (float(space_min) + float(space_max)) / 2.0
        if not (float(space_min) <= sx <= float(space_max)):
            sx = mid
            rx = {**rx, "spacing": sx}
        if not (float(space_min) <= sy <= float(space_max)):
            sy = mid
            ry = {**ry, "spacing": sy}

    # Use the wall clearance as the bbox-side offset: grid rows start at
    # least `clearance` inside the bbox, so no row can sit closer than the
    # user's 800 mm wall rule (relaxed only for narrow corridors above).
    grid_edge_offset = float(clearance)
    if GRID_ANCHOR_TOP_LEFT:
        # User rule: no re-centered rectangle — anchor the grid at the
        # room's TOP-LEFT (first column 1500 from the left wall, first
        # row 1500 below the top wall) and march at the pitch. The
        # right/bottom leftovers are absorbed by the Phase-4 NEAR/FAR
        # end-of-row rules.
        x_lines = _anchored_lines(
            minx, maxx, float(GRID_ANCHOR_OFFSET), sx,
            grid_edge_offset, from_high=False,
        )
        y_lines = _anchored_lines(
            miny, maxy, float(GRID_ANCHOR_OFFSET), sy,
            grid_edge_offset, from_high=True,
        )
    else:
        x_lines = make_grid_lines(
            minx, maxx, grid_edge_offset, sx,
            wall_max=wall_max, space_min=space_min, space_max=space_max,
        )
        y_lines = make_grid_lines(
            miny, maxy, grid_edge_offset, sy,
            wall_max=wall_max, space_min=space_min, space_max=space_max,
        )

    zone_wall_segs = build_zone_wall_segs(floor_poly, wall_segs, wall_polys)

    obs_segs = []
    for ob in obs_polys:
        obs_segs.extend(poly_to_segs(ob))

    spacing_hash = SpatialHash(float(space_min))
    spacing_hash.bulk_load(all_placed)

    # Cached for the boundary-nudge branch — computed once per zone, not
    # per grid intersection.
    floor_segs_cached = floor_poly_to_segs(floor_poly)
    floor_centroid    = poly_centroid(floor_poly)

    points         = []
    outside_pts: list = []     # grid intersections outside the polyline (fill input)
    culled_points: list = []   # always empty in v3 — heads outside the polyline beyond
                               # GRID_SLIDE_MAX are dropped silently. Kept in the response
                               # dict for API/wire compatibility with older consumers.
    rejected       = 0
    warnings       = []

    tol_dist     = space_min - TOLERANCE
    obs_tol      = obs_min_offset - TOLERANCE

    # With the bbox-grid + boundary-nudge strategy, the outer rows live
    # within ~300mm of the floor walls by design. Use the nudge margin as
    # the wall-distance floor for ALL heads (not just nudged ones), so the
    # outer rows actually pass validation. NFPA-13 minimum head-to-wall is
    # 102mm, so 270mm is well above code.
    nudge_wall_tol = max(150.0, BOUNDARY_NUDGE_MARGIN - TOLERANCE)

    # Polyline-aware pass: place a head at every grid intersection that lies
    # INSIDE the floor polyline (and clears the wall rule). Intersections
    # that fall OUTSIDE are remembered; the Phase-2 pull below brings the
    # ones whose center is within out_cov back in (landing in_cov inside,
    # on their own grid line). Anything farther out is discarded silently.
    for gy in reversed(y_lines):
        for gx in x_lines:
            if point_in_poly(gx, gy, floor_poly):
                # Wall-clearance rule: even an inside intersection is
                # dropped if it sits closer than `clearance` to any wall
                # (matters in L-shapes / domes where interior grid points
                # can hug a far boundary edge). A center sitting exactly
                # ON the line is routed to the Phase-2 pull (on_cov rule)
                # instead of being dropped.
                d_wall = min_dist_to_segs(gx, gy, floor_segs_cached)
                if d_wall < clearance - TOLERANCE:
                    # Too close to a wall for a regular grid head. Instead
                    # of deleting it (which left bare wedges along slanted
                    # walls and corners), hand it to the Phase-2 pull: it
                    # is kept in place — or slid deeper on its own grid
                    # line — at in_cov clearance, like the on-line /
                    # outside boundary heads.
                    outside_pts.append((gx, gy))
                    continue
                pt = (round(gx, 3), round(gy, 3))
                points.append(pt)
                spacing_hash.insert(pt[0], pt[1])
            else:
                # Outside the room polyline → remembered; the grid-aligned
                # fill pass below may slide it back in along its grid line.
                outside_pts.append((gx, gy))
                rejected += 1

    # ── Phase 2: grid-aligned boundary pull (out_cov / in_cov) ─────
    # User rule: "if the sprinkler [center point only] is out of the main
    # polyline around out_cov, pull the point on the grid inside in_cov."
    #   * Eligibility: outside grid points whose CENTER is within out_cov
    #     of the polyline. Farther out → deleted.
    #   * Pull: slide ALONG THE HEAD'S OWN grid line (column or row —
    #     shorter slide wins) so one coordinate always stays on the grid,
    #     landing in_cov inside the boundary. On diagonal walls the
    #     along-line landing can be shallower than in_cov perpendicular,
    #     so the landing is deepened in DEEPEN_STEP increments until clear.
    #   * Guards: must be inside the polygon, ≥ in_cov from every wall,
    #     and ≥ MIN_HEAD_SEPARATION from any existing head (no pile-ups).
    # Skipped for relaxed narrow corridors (their centered row is enough).
    if clearance >= WALL_CLEARANCE_MIN and outside_pts:
        ON_LINE_EPS = 1.0   # mm — a center this close to the boundary is "ON the line"
        _skew_tan = math.tan(math.radians(float(WALL_RULE_MAX_SKEW_DEG)))

        def _pull_along_axis(vertical, gx, gy, inset):
            """Slide (gx,gy) along its column (vertical) or row to land
            >= inset inside the polygon. Returns (slide, x, y) or None."""
            max_sl = max(sx, sy) + inset        # slide cap along the grid line
            crossings = (_line_polygon_crossings_y(gx, floor_poly) if vertical
                         else _line_polygon_crossings_x(gy, floor_poly))
            moving = gy if vertical else gx
            best = None
            for k in range(0, len(crossings) - 1, 2):
                lo, hi = crossings[k], crossings[k + 1]
                if hi - lo < 2.0 * inset:
                    base, ddir = (lo + hi) / 2.0, 0.0   # short slot → midpoint only
                elif moving < lo + inset:
                    base, ddir = lo + inset, +1.0       # entered from below → deepen up
                elif moving > hi - inset:
                    base, ddir = hi - inset, -1.0       # entered from above → deepen down
                else:
                    # Already within the slot — validate in place; if a
                    # slanted wall is still too close, deepen away from
                    # the nearer crossing.
                    base = moving
                    ddir = +1.0 if (moving - lo) < (hi - moving) else -1.0
                extra = 0.0
                while True:
                    land = base + ddir * extra
                    slide = abs(moving - land)
                    px, py = (gx, land) if vertical else (land, gy)
                    if (slide <= max_sl
                            and point_in_poly(px, py, floor_poly)
                            and min_dist_to_segs(px, py, floor_segs_cached)
                                >= inset - TOLERANCE):
                        if best is None or slide < best[0]:
                            best = (slide, px, py)
                        break
                    extra += DEEPEN_STEP
                    if ddir == 0.0 or extra > DEEPEN_LIMIT:
                        break
                    nxt = base + ddir * extra
                    if nxt < lo or nxt > hi:
                        break
            return best

        for gx, gy in outside_pts:
            # Ownership: pulls serve SLANTED walls only. If the nearest
            # edge is grid-aligned (in this frame), the Phase-4
            # alpha/gama/new-row rules own that wall — pulling here too
            # paired heads up along the boundary (700 pull + 1000 add).
            if _nearest_edge_axis_aligned(gx, gy, floor_segs_cached, _skew_tan):
                continue
            # Landing depth by case:
            #   center ON the polyline           → pull on_cov  (300) inside
            #   center outside by up to out_cov  → pull in_cov  (400) inside
            #   farther out                      → deleted
            d_bnd = min_dist_to_segs(gx, gy, floor_segs_cached)
            if d_bnd <= ON_LINE_EPS:
                inset_pt = float(on_cov)        # center ON the line → 300 in
            elif point_in_poly(gx, gy, floor_poly):
                inset_pt = float(in_cov)        # inside, hugging a wall → keep at 400
            elif d_bnd <= out_cov:
                inset_pt = float(in_cov)        # outside within out_cov → 400 in
            else:
                continue                        # too far outside → deleted
            cand_v = _pull_along_axis(True,  gx, gy, inset_pt)
            cand_h = _pull_along_axis(False, gx, gy, inset_pt)
            best = min((c for c in (cand_v, cand_h) if c is not None), default=None)
            if best is None:
                continue
            _, hx, hy = best
            # Spacing guard: PULL_SEPARATION (config — the fill-density
            # dial along walls: smaller = denser chains, ~1500 = a pull
            # only where no other head already covers the spot).
            if spacing_hash.any_within(hx, hy, PULL_SEPARATION):
                continue
            pt = (round(hx, 3), round(hy, 3))
            points.append(pt)
            spacing_hash.insert(pt[0], pt[1])

    # ── Phase 3: wall rows ────────────────────────────────────────
    # If the outermost grid row/column sits farther than
    # coverage_radius + in_cov (~1900) from the bbox edge, the strip
    # along that wall has no grid points at all (nothing to keep or
    # pull). Insert ONE extra row/column at in_cov (400) inside that
    # edge, using the same grid lines on the other axis, validated like
    # pulled heads. Corners get a head where both an extra row and an
    # extra column were added. Skipped for relaxed narrow corridors.
    # Skipped in top-left-anchored mode: the end gaps there belong to
    # the Phase-4 NEAR/FAR rules (and gaps beyond WALL_STRETCH_FAR_MAX
    # stay on the normal grid by user rule).
    if clearance >= WALL_CLEARANCE_MIN and not GRID_ANCHOR_TOP_LEFT:
        wall_trigger = float(coverage_radius) + float(in_cov)

        extra_rows = []
        if y_lines and (maxy - max(y_lines)) > wall_trigger:
            extra_rows.append(maxy - float(in_cov))
        if y_lines and (min(y_lines) - miny) > wall_trigger:
            extra_rows.append(miny + float(in_cov))
        extra_cols = []
        if x_lines and (maxx - max(x_lines)) > wall_trigger:
            extra_cols.append(maxx - float(in_cov))
        if x_lines and (min(x_lines) - minx) > wall_trigger:
            extra_cols.append(minx + float(in_cov))

        def _try_wall_head(px, py):
            if not point_in_poly(px, py, floor_poly):
                return
            if min_dist_to_segs(px, py, floor_segs_cached) < float(in_cov) - TOLERANCE:
                return
            # Spacing guard: PULL_SEPARATION (config) only.
            if spacing_hash.any_within(px, py, PULL_SEPARATION):
                return
            pt = (round(px, 3), round(py, 3))
            points.append(pt)
            spacing_hash.insert(pt[0], pt[1])

        for wy in extra_rows:
            for gx in x_lines:
                _try_wall_head(gx, wy)
        for wx in extra_cols:
            for gy in y_lines:
                _try_wall_head(wx, gy)
        for wx in extra_cols:
            for wy in extra_rows:
                _try_wall_head(wx, wy)

    # ── Phase 4: alpha / gama / new-row wall rules ────────────────
    # alpha: stretch last 3 bays (gap ≤ 1850, land at radius).
    # gama:  new head at 1000 + squeeze last 3 bays (1850 < gap ≤ 2600).
    # newrow: gap > 2600 → normal new row at 1500 from the wall.
    points, alpha_used, gama_used = _stretch_runs_to_walls(
        points, x_lines, y_lines, floor_poly, coverage_radius,
    )

    # Gap-fill removed in v2 — bbox-grid + boundary-nudge handles irregular
    # shapes inline above. extra_points kept in the response for API compat.
    extra_points: list = []

    return {
        "points":         points,
        "extra_points":   extra_points,
        "culled_points":  culled_points,
        "alpha_used":     alpha_used,
        "gama_used":      gama_used,
        "spacing_x":      rx,
        "spacing_y":      ry,
        "x_lines":        x_lines,
        "y_lines":        y_lines,
        "grid_cols":      len(x_lines),
        "grid_rows":      len(y_lines),
        "x_offset_mm":    round((x_lines[0] - minx) if x_lines else 0, 1),
        "y_offset_mm":    round((y_lines[0] - miny) if y_lines else 0, 1),
        "width_m":        round(width  / 1000, 2),
        "height_m":       round(height / 1000, 2),
        "rejected":       rejected,
        "warnings":       warnings,
        "wall_segs_used": len(zone_wall_segs),
        "zone_wall_segs": zone_wall_segs,
    }


# ── Rotation-aware wrapper for tilted buildings ───────────────────

def generate_zone_sprinklers_oriented(
    floor_poly,
    obs_polys,
    obs_min_offset,
    all_placed,
    wall_min, wall_max,
    space_min, space_max,
    coverage_radius,
    enable_gap_fill,
    tilted=True,
) -> dict:
    """
    Like generate_zone_sprinklers but auto-detects the room's principal
    orientation and runs placement in that rotated frame. Output points
    are returned in WORLD coordinates so callers don't need to know the
    rotation. The detected angle (radians) is included as `angle` in the
    returned dict so the caller can rotate inserted blocks to match.

    For axis-aligned rooms (|angle| < ~1°) the rotation is skipped
    entirely — zero overhead, identical behaviour to before.

    `tilted=False` (the plugin's Straight mode) skips angle detection
    entirely: placement is axis-aligned and every head gets rotation 0.
    """
    # User-facing rule: align with the longest wall — but only in Tilted
    # mode. Straight mode forces an axis-aligned grid regardless of the
    # room's actual orientation.
    angle = find_longest_edge_angle(floor_poly) if tilted else 0.0

    if abs(angle) < ANGLE_TOLERANCE_RAD:
        result = generate_zone_sprinklers(
            floor_poly      = floor_poly,
            excl_polys      = [],
            wall_segs       = [],
            wall_polys      = [],
            obs_polys       = obs_polys,
            obs_min_offset  = obs_min_offset,
            all_placed      = all_placed,
            wall_min        = wall_min,
            wall_max        = wall_max,
            space_min       = space_min,
            space_max       = space_max,
            coverage_radius = coverage_radius,
            enable_gap_fill = enable_gap_fill,
        )
        result["angle"] = 0.0
        return result

    # Rotate everything into the room's local axis-aligned frame.
    cx, cy = poly_centroid(floor_poly)
    r_floor      = rotate_poly(floor_poly, -angle, cx, cy)
    r_obs_polys  = [rotate_poly(op, -angle, cx, cy) for op in obs_polys]
    r_all_placed = [rotate_pt(x, y, -angle, cx, cy) for x, y in all_placed]

    result = generate_zone_sprinklers(
        floor_poly      = r_floor,
        excl_polys      = [],
        wall_segs       = [],
        wall_polys      = [],
        obs_polys       = r_obs_polys,
        obs_min_offset  = obs_min_offset,
        all_placed      = r_all_placed,
        wall_min        = wall_min,
        wall_max        = wall_max,
        space_min       = space_min,
        space_max       = space_max,
        coverage_radius = coverage_radius,
        enable_gap_fill = enable_gap_fill,
    )

    # Rotate placed points back to world coordinates.
    result["points"] = [
        tuple(round(v, 3) for v in rotate_pt(x, y, angle, cx, cy))
        for x, y in result["points"]
    ]
    result["extra_points"] = [
        tuple(round(v, 3) for v in rotate_pt(x, y, angle, cx, cy))
        for x, y in result["extra_points"]
    ]
    result["culled_points"] = [
        tuple(round(v, 3) for v in rotate_pt(x, y, angle, cx, cy))
        for x, y in result.get("culled_points", [])
    ]
    result["angle"] = angle
    return result


# ── Per-scenario worker (picklable, used by ProcessPoolExecutor) ──

def run_scenario_for_floors(
    scenario_def: dict,
    floor_polys:  list,
    obs_polys:    list,
    obs_min_offset: float,
    enable_gap_fill: bool,
    tilted: bool = False,
) -> tuple:
    """
    Run one scenario across many floor polylines and return:
      (deduped_triplets, culled_pairs, alpha_total, gama_total)

    `deduped_triplets`: merged + deduped (x, y, rotation_radians) heads
                        — the actual sprinklers to place (inside polyline).
    `culled_pairs`:     merged + deduped (x, y) bbox-grid intersections
                        that fell outside the polyline beyond the nudge
                        margin — returned for the plugin to draw as green
                        debug markers so the user can see what was removed.
    `alpha_total` / `gama_total`: how many row/column ends fired the
                        alpha (stretch) / gama (new head + squeeze)
                        formulas across all rooms — surfaced in the
                        ZWCAD terminal by the plugin.

    `tilted=True` (plugin's Tilted pick): each room's longest-edge angle
    is auto-detected and the grid rotated to match; heads carry that
    angle as their rotation. Axis-aligned rooms (angle < 1°) skip the
    rotation — zero overhead.
    `tilted=False` (Straight, the default): placement is axis-aligned
    and every head's rotation is 0, regardless of room orientation.

    Top-level so ProcessPoolExecutor can pickle the call. Imports only
    pure-geometry helpers — no FastAPI or ezdxf — so child processes
    spawned on Windows start quickly.
    """
    sc = scenario_def
    # We accumulate (x, y) only for spacing checks across rooms; the
    # 3-tuple (x, y, rot) is what's returned.
    all_xy:    list = []
    triplets:  list = []
    culled_xy: list = []
    alpha_total = 0
    gama_total  = 0

    for fp in floor_polys:
        # The oriented wrapper honours `tilted`: True → auto-detect each
        # room's longest-edge angle (axis-aligned rooms skip rotation);
        # False → force angle 0 (Straight mode, axis-aligned grid).
        result = generate_zone_sprinklers_oriented(
            floor_poly      = fp,
            obs_polys       = obs_polys,
            obs_min_offset  = float(obs_min_offset),
            all_placed      = all_xy,
            wall_min        = sc["wall_min"],
            wall_max        = sc["wall_max"],
            space_min       = sc["space_min"],
            space_max       = sc["space_max"],
            coverage_radius = float(sc["coverage_radius"]),
            enable_gap_fill = bool(enable_gap_fill),
            tilted          = bool(tilted),
        )
        rot = float(result.get("angle", 0.0))
        alpha_total += int(result.get("alpha_used", 0))
        gama_total  += int(result.get("gama_used", 0))

        for pt in result["points"]:
            all_xy.append(pt)
            triplets.append((pt[0], pt[1], rot))
        for pt in result["extra_points"]:
            all_xy.append(pt)
            triplets.append((pt[0], pt[1], rot))
        for pt in result.get("culled_points", []):
            culled_xy.append((pt[0], pt[1]))

    deduped: list = []
    seen: set = set()
    for x, y, rot in triplets:
        key = (x, y)
        if key not in seen:
            seen.add(key)
            deduped.append((x, y, rot))

    # Dedupe the culled markers too — adjacent rooms can share bbox grid
    # points along their boundaries. Use a separate `seen` set rather than
    # reusing the heads one (a head at (x,y) shouldn't suppress a culled
    # marker at the same (x,y); they're on different layers).
    culled_deduped: list = []
    culled_seen: set = set()
    for x, y in culled_xy:
        key = (x, y)
        if key not in culled_seen:
            culled_seen.add(key)
            culled_deduped.append((x, y))

    return deduped, culled_deduped, alpha_total, gama_total


# ── 10 scenario variants (spacing / wall band / coverage presets) ─

SCENARIOS = [
    {
        "id": 1,
        "name": "Standard NFPA-13",
        "description": "Nominal spacing 2900mm, wall band 1000–1500mm",
        "space_min": 2400, "space_max": 3200,
        "wall_min": 1000, "wall_max": 1500,
        "coverage_radius": 1500,
    },
    {
        "id": 2,
        "name": "Dense Coverage",
        "description": "Tighter spacing 2200–2800mm for high-hazard areas",
        "space_min": 2200, "space_max": 2800,
        "wall_min": 900,  "wall_max": 1400,
        "coverage_radius": 1300,
    },
    {
        "id": 3,
        "name": "Wide Spacing",
        "description": "Wider spacing 2800–3600mm for light-hazard areas",
        "space_min": 2800, "space_max": 3600,
        "wall_min": 1100, "wall_max": 1600,
        "coverage_radius": 1700,
    },
    {
        "id": 4,
        "name": "Compact Tight",
        "description": "Very tight 2000–2600mm for critical protection",
        "space_min": 2000, "space_max": 2600,
        "wall_min": 800,  "wall_max": 1300,
        "coverage_radius": 1200,
    },
    {
        "id": 5,
        "name": "Optimized Balanced",
        "description": "Balanced 2500–3100mm, auto gap-fill enabled",
        "space_min": 2500, "space_max": 3100,
        "wall_min": 1000, "wall_max": 1500,
        "coverage_radius": 1550,
    },
    {
        "id": 6,
        "name": "Office / Light hazard",
        "description": "Wider 2700–3400mm, larger throw for open office bays",
        "space_min": 2700, "space_max": 3400,
        "wall_min": 1100, "wall_max": 1600,
        "coverage_radius": 1600,
    },
    {
        "id": 7,
        "name": "Retail / Open plan",
        "description": "Even 2600–3200mm for sales floor and mall shells",
        "space_min": 2600, "space_max": 3200,
        "wall_min": 1000, "wall_max": 1500,
        "coverage_radius": 1500,
    },
    {
        "id": 8,
        "name": "Storage / Commodity",
        "description": "Tighter 2100–2700mm, shorter radius for rack aisles",
        "space_min": 2100, "space_max": 2700,
        "wall_min": 850, "wall_max": 1300,
        "coverage_radius": 1250,
    },
    {
        "id": 9,
        "name": "Parking / Large bay",
        "description": "Wide 3000–3800mm, high throw for column-free decks",
        "space_min": 3000, "space_max": 3800,
        "wall_min": 1200, "wall_max": 1700,
        "coverage_radius": 1800,
    },
    {
        "id": 10,
        "name": "Institutional",
        "description": "Moderate 2300–2900mm, narrow wall band for corridors",
        "space_min": 2300, "space_max": 2900,
        "wall_min": 700, "wall_max": 1200,
        "coverage_radius": 1400,
    },
    {
        "id": 11,
        "name": "Fixed 3000mm",
        "description": "Fixed head-to-head spacing of 3000mm, wall band 1000–1500mm",
        "space_min": 3000, "space_max": 3000,
        "wall_min": 1000, "wall_max": 1500,
        "coverage_radius": 1500,
    },
    {
        "id": 12,
        "name": "Fixed 2700mm",
        "description": "Fixed head-to-head spacing of 2700mm, wall band 950–1350mm",
        "space_min": 2700, "space_max": 2700,
        "wall_min": 950, "wall_max": 1350,
        "coverage_radius": 1400,
    },
    {
        "id": 13,
        "name": "Spacing 3050-3100mm",
        "description": "Head-to-head spacing 3050–3100mm, wall band 1000–1500mm",
        "space_min": 3050, "space_max": 3100,
        "wall_min": 1000, "wall_max": 1500,
        "coverage_radius": 1500,
    },
    {
        "id": 14,
        "name": "Fixed 3300mm",
        "description": "Fixed head-to-head spacing of 3300mm, wall band 1100–1650mm",
        "space_min": 3300, "space_max": 3300,
        "wall_min": 1100, "wall_max": 1650,
        "coverage_radius": 1650,
    },
]
