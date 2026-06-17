"""
geometry.py — Pure geometry helpers (no FastAPI, no ezdxf)
All coordinates in mm (native DXF units).

v4.1 — Added SpatialHash for O(1)-average proximity queries,
       precompute_sample_grid / coverage_from_samples for GA,
       single-pass bbox.
v4.2 — NumPy-vectorised batch point-in-polygon used by the gap-finder
       and sample-grid scanners (10–100× faster than per-point ray cast
       on dense sample grids).
"""

import math
import numpy as np

TOLERANCE = 30.0  # floating-point fuzz (mm)


# ── Spatial hash for O(1)-average proximity queries ───────────────

class SpatialHash:
    """
    Fixed-cell spatial hash grid.

    Points are bucketed into cells of *cell_size*.  Radius queries only
    inspect the handful of neighbouring cells that could contain a match,
    giving O(1) average-case instead of O(n) brute-force.
    """
    __slots__ = ('cell_size', 'inv', 'grid')

    def __init__(self, cell_size: float):
        self.cell_size = float(cell_size)
        self.inv  = 1.0 / self.cell_size
        self.grid: dict = {}

    # -- insertion --------------------------------------------------

    def insert(self, x: float, y: float):
        inv = self.inv
        k = (int(math.floor(x * inv)), int(math.floor(y * inv)))
        b = self.grid.get(k)
        if b is None:
            self.grid[k] = [(x, y)]
        else:
            b.append((x, y))

    def bulk_load(self, points):
        """Insert many points at once (avoids repeated dict look-ups)."""
        inv  = self.inv
        grid = self.grid
        _floor = math.floor
        for x, y in points:
            k = (int(_floor(x * inv)), int(_floor(y * inv)))
            b = grid.get(k)
            if b is None:
                grid[k] = [(x, y)]
            else:
                b.append((x, y))

    # -- queries ----------------------------------------------------

    def any_within(self, x: float, y: float, radius: float) -> bool:
        """True if any stored point is within *radius* of (x, y)."""
        inv = self.inv
        cx  = int(math.floor(x * inv))
        cy  = int(math.floor(y * inv))
        rc  = int(math.ceil(radius * inv))
        rsq = radius * radius
        grid = self.grid
        for dx in range(-rc, rc + 1):
            kx = cx + dx
            for dy in range(-rc, rc + 1):
                b = grid.get((kx, cy + dy))
                if b is not None:
                    for px, py in b:
                        ddx = x - px
                        ddy = y - py
                        if ddx * ddx + ddy * ddy <= rsq:
                            return True
        return False

    def min_dist(self, x: float, y: float, search_radius: float) -> float:
        """Min Euclidean distance to any stored point within search_radius."""
        inv = self.inv
        cx  = int(math.floor(x * inv))
        cy  = int(math.floor(y * inv))
        rc  = int(math.ceil(search_radius * inv))
        best_sq = float('inf')
        grid = self.grid
        for dx in range(-rc, rc + 1):
            kx = cx + dx
            for dy in range(-rc, rc + 1):
                b = grid.get((kx, cy + dy))
                if b is not None:
                    for px, py in b:
                        ddx = x - px
                        ddy = y - py
                        dsq = ddx * ddx + ddy * ddy
                        if dsq < best_sq:
                            best_sq = dsq
        return math.sqrt(best_sq) if best_sq < float('inf') else float('inf')


# ── Rotation / orientation helpers ────────────────────────────────

ANGLE_TOLERANCE_RAD = 0.0175  # ~1° — below this we treat the room as axis-aligned


def rotate_pt(x: float, y: float, angle: float,
              ox: float = 0.0, oy: float = 0.0) -> tuple:
    """Rotate (x, y) by *angle* radians around (ox, oy). CCW positive."""
    if angle == 0.0:
        return (x, y)
    c = math.cos(angle)
    s = math.sin(angle)
    dx = x - ox
    dy = y - oy
    return (ox + dx * c - dy * s, oy + dx * s + dy * c)


def rotate_poly(poly: list, angle: float, ox: float, oy: float) -> list:
    """Rotate every vertex of *poly* by *angle* radians around (ox, oy)."""
    if angle == 0.0:
        return [(float(x), float(y)) for x, y in poly]
    c = math.cos(angle)
    s = math.sin(angle)
    out = []
    for x, y in poly:
        dx = x - ox
        dy = y - oy
        out.append((ox + dx * c - dy * s, oy + dx * s + dy * c))
    return out


def poly_centroid(poly: list) -> tuple:
    """Area-weighted centroid (shoelace). Falls back to vertex mean for degenerate polys."""
    n = len(poly) - 1
    if n < 3:
        if not poly:
            return (0.0, 0.0)
        sx = sum(p[0] for p in poly)
        sy = sum(p[1] for p in poly)
        return (sx / len(poly), sy / len(poly))
    a2 = 0.0
    cx = 0.0
    cy = 0.0
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[i + 1]
        cross = x0 * y1 - x1 * y0
        a2 += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if a2 == 0.0:
        sx = sum(p[0] for p in poly[:-1])
        sy = sum(p[1] for p in poly[:-1])
        return (sx / n, sy / n)
    return (cx / (3.0 * a2), cy / (3.0 * a2))


def find_longest_edge_angle(poly: list) -> float:
    """
    Detect the orientation of *poly* by taking the angle of its single
    longest edge, folded into [-π/4, π/4] so the returned value is the
    smallest rotation that aligns the placement grid with that edge.

    Simpler than `find_principal_angle` (no weighted accumulation) and
    matches the user-facing rule: "find the longest wall and align with
    it." For axis-aligned polygons this returns ~0.
    """
    n = len(poly) - 1
    if n < 1:
        return 0.0
    best_len_sq = -1.0
    best_dx = 1.0
    best_dy = 0.0
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[i + 1]
        dx = x1 - x0
        dy = y1 - y0
        L_sq = dx * dx + dy * dy
        if L_sq > best_len_sq:
            best_len_sq = L_sq
            best_dx = dx
            best_dy = dy
    if best_len_sq <= 0.0:
        return 0.0
    angle = math.atan2(best_dy, best_dx)
    # Fold into [-π/4, π/4] (grid is symmetric every π/2).
    quarter = math.pi / 2.0
    while angle >  math.pi / 4.0: angle -= quarter
    while angle < -math.pi / 4.0: angle += quarter
    return angle


def find_principal_angle(poly: list) -> float:
    """
    Detect the dominant orientation of *poly* in radians, in [-π/4, π/4].

    Strategy: weight each edge by its length and accumulate it as a 2θ vector
    in the (cos2θ, sin2θ) plane. Edges parallel to one another reinforce;
    perpendicular edges reinforce the same axis (since 2θ wraps every 90°).
    The result is the orientation of the longest-aligned wall direction —
    exactly what we want to align the placement grid with.

    For axis-aligned rooms this returns ~0; for a 30°-tilted building, ~30°
    (or equivalently -60°, but we fold into [-π/4, π/4] so the returned
    angle is the smallest rotation that aligns the grid).
    """
    n = len(poly) - 1
    if n < 2:
        return 0.0
    sum_cos = 0.0
    sum_sin = 0.0
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[i + 1]
        dx = x1 - x0
        dy = y1 - y0
        L = math.hypot(dx, dy)
        if L < 1e-9:
            continue
        # 2θ accumulator: cos(2θ) = (dx²−dy²)/L², sin(2θ) = 2·dx·dy/L²
        sum_cos += (dx * dx - dy * dy) / L
        sum_sin += (2.0 * dx * dy) / L
    if sum_cos == 0.0 and sum_sin == 0.0:
        return 0.0
    angle2 = math.atan2(sum_sin, sum_cos)
    angle = angle2 / 2.0
    # Fold into [-π/4, π/4] so we always pick the smallest aligning rotation.
    quarter = math.pi / 2.0
    while angle >  math.pi / 4.0: angle -= quarter
    while angle < -math.pi / 4.0: angle += quarter
    return angle


# ── Polygon extraction ────────────────────────────────────────────

def poly_vertices(entity) -> list:
    """Extract (x,y) pairs from LWPOLYLINE or POLYLINE."""
    pts = []
    if entity.dxftype() == "LWPOLYLINE":
        pts = [(float(p[0]), float(p[1])) for p in entity.get_points()]
    elif entity.dxftype() == "POLYLINE":
        for v in entity.vertices:
            pts.append((float(v.dxf.location.x), float(v.dxf.location.y)))
    if len(pts) < 3:
        return []
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts


def poly_area(pts: list) -> float:
    """Shoelace formula — returns area in units² (mm²)."""
    n = len(pts) - 1
    area = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[i + 1]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def bbox(pts: list) -> tuple:
    """Return (minx, maxx, miny, maxy) — single-pass, no temp lists."""
    minx = maxx = pts[0][0]
    miny = maxy = pts[0][1]
    for i in range(1, len(pts)):
        x, y = pts[i]
        if   x < minx: minx = x
        elif x > maxx: maxx = x
        if   y < miny: miny = y
        elif y > maxy: maxy = y
    return minx, maxx, miny, maxy


# ── Point-in-polygon ──────────────────────────────────────────────

def point_in_poly(px: float, py: float, poly: list) -> bool:
    """Ray-casting polygon test."""
    inside = False
    n = len(poly) - 1
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > py) != (yj > py)) and (
            px < (xj - xi) * (py - yi) / (yj - yi) + xi
        ):
            inside = not inside
        j = i
    return inside


def points_in_poly_batch(pts: np.ndarray, poly: list) -> np.ndarray:
    """
    Vectorised ray-casting for many points against one polygon.

    pts: (N, 2) float array of [x, y] points.
    poly: list of (x, y) tuples; assumed closed (last == first).

    Returns: (N,) boolean array — True if the point is inside.

    Roughly 20–100× faster than calling point_in_poly N times on dense
    sample grids (the per-iteration overhead of the Python interpreter
    dominates the original loop).
    """
    n = len(poly) - 1
    if n < 3 or pts.shape[0] == 0:
        return np.zeros(pts.shape[0], dtype=bool)

    px = pts[:, 0]
    py = pts[:, 1]
    inside = np.zeros(pts.shape[0], dtype=bool)

    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        dy = yj - yi
        if dy == 0.0:
            # Horizontal edge — original CPU test short-circuits via
            # `(yi > py) != (yj > py)` being False; skip to avoid div-by-zero.
            j = i
            continue
        cond = ((yi > py) != (yj > py)) & (
            px < (xj - xi) * (py - yi) / dy + xi
        )
        inside ^= cond
        j = i
    return inside


# ── Distance helpers ──────────────────────────────────────────────

def pt_seg_dist(px, py, x1, y1, x2, y2) -> float:
    """Distance from point to line segment."""
    dx, dy = x2 - x1, y2 - y1
    lsq = dx * dx + dy * dy
    if lsq == 0:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / lsq))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def min_dist_to_segs(px, py, segs: list) -> float:
    """Minimum distance from point to a list of (x1,y1,x2,y2) segments."""
    d = float("inf")
    for s in segs:
        d = min(d, pt_seg_dist(px, py, s[0], s[1], s[2], s[3]))
    return d


def min_dist_to_polys(px, py, polys: list) -> float:
    """Minimum distance from point to edges of any polygon in list."""
    d = float("inf")
    for poly in polys:
        for i in range(len(poly) - 1):
            d = min(d, pt_seg_dist(px, py, poly[i][0], poly[i][1],
                                   poly[i + 1][0], poly[i + 1][1]))
    return d


def poly_to_segs(poly: list) -> list:
    """Convert closed polygon to list of (x1,y1,x2,y2) segments."""
    segs = []
    for i in range(len(poly) - 1):
        segs.append((poly[i][0], poly[i][1], poly[i + 1][0], poly[i + 1][1]))
    return segs


# ── Sample-grid pre-computation (for GA / repeated coverage) ──────

def _bbox_sample_array(
    floor_poly: list,
    sample_step: float,
) -> np.ndarray:
    """Build an (N, 2) float array of grid sample points covering the bbox."""
    minx, maxx, miny, maxy = bbox(floor_poly)
    half = sample_step / 2.0
    xs = np.arange(minx + half, maxx + 1e-9, sample_step, dtype=np.float64)
    ys = np.arange(miny + half, maxy + 1e-9, sample_step, dtype=np.float64)
    if xs.size == 0 or ys.size == 0:
        return np.empty((0, 2), dtype=np.float64)
    XX, YY = np.meshgrid(xs, ys, indexing='ij')
    return np.column_stack((XX.ravel(), YY.ravel()))


def _filter_inside_floor_outside_blockers(
    pts: np.ndarray,
    floor_poly: list,
    excl_polys: list,
    obs_polys:  list,
) -> np.ndarray:
    """Vectorised mask: inside floor AND outside every excl/obs polygon."""
    if pts.shape[0] == 0:
        return pts
    mask = points_in_poly_batch(pts, floor_poly)
    for ex in excl_polys:
        if mask.any():
            mask &= ~points_in_poly_batch(pts, ex)
    for ob in obs_polys:
        if mask.any():
            mask &= ~points_in_poly_batch(pts, ob)
    return pts[mask]


def precompute_sample_grid(
    floor_poly: list,
    excl_polys: list,
    obs_polys:  list,
    sample_step: float,
) -> list:
    """
    Pre-compute valid (x, y) sample points that lie inside the floor
    polygon and outside every exclusion / obstacle polygon.

    Expensive (point_in_poly per cell), but meant to be called once and
    reused across many coverage evaluations (e.g. the GA). Vectorised
    via NumPy.
    """
    pts = _bbox_sample_array(floor_poly, sample_step)
    valid = _filter_inside_floor_outside_blockers(pts, floor_poly, excl_polys, obs_polys)
    return [(float(x), float(y)) for x, y in valid]


def coverage_from_samples(
    sample_points: list,
    placed: list,
    coverage_radius: float,
) -> float:
    """
    Fast coverage fraction [0..1] using pre-computed sample points.
    Builds a SpatialHash of *placed* sprinklers, then counts how many
    pre-validated samples fall within coverage_radius of any sprinkler.
    """
    if not sample_points:
        return 0.0
    sh = SpatialHash(coverage_radius)
    sh.bulk_load(placed)
    covered = 0
    for x, y in sample_points:
        if sh.any_within(x, y, coverage_radius):
            covered += 1
    return covered / len(sample_points)


# ── Gap detection ─────────────────────────────────────────────────

def find_uncovered_gaps(
    floor_poly: list,
    placed: list,
    coverage_radius: float,
    excl_polys: list,
    obs_polys: list,
    sample_step: float = None,
) -> list:
    """
    Scan the floor polygon on a grid.  Return (x, y) points that are
    inside floor, not excluded, and NOT covered by any placed sprinkler.

    Sample-grid filtering is vectorised via NumPy; the per-sample
    coverage check uses SpatialHash for O(1) average-case look-up.
    """
    if sample_step is None:
        sample_step = max(200.0, coverage_radius / 3.0)

    pts = _bbox_sample_array(floor_poly, sample_step)
    candidates = _filter_inside_floor_outside_blockers(
        pts, floor_poly, excl_polys, obs_polys,
    )
    if candidates.shape[0] == 0:
        return []

    sh = SpatialHash(coverage_radius)
    sh.bulk_load(placed)

    gaps = []
    for x, y in candidates:
        fx, fy = float(x), float(y)
        if not sh.any_within(fx, fy, coverage_radius):
            gaps.append((fx, fy))
    return gaps


def coverage_fraction(
    floor_poly: list,
    placed: list,
    coverage_radius: float,
    excl_polys: list,
    obs_polys: list,
    sample_step: float = None,
) -> float:
    """
    Return fraction 0..1 of valid floor area that is covered.
    Sample-grid filtering is vectorised via NumPy; coverage check uses
    SpatialHash for O(1) average-case look-up.
    """
    if sample_step is None:
        sample_step = max(200.0, coverage_radius / 3.0)

    pts = _bbox_sample_array(floor_poly, sample_step)
    candidates = _filter_inside_floor_outside_blockers(
        pts, floor_poly, excl_polys, obs_polys,
    )
    total = candidates.shape[0]
    if total == 0:
        return 0.0

    sh = SpatialHash(coverage_radius)
    sh.bulk_load(placed)

    covered_count = 0
    for x, y in candidates:
        if sh.any_within(float(x), float(y), coverage_radius):
            covered_count += 1

    return covered_count / total
