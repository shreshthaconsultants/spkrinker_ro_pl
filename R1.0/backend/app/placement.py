"""Sprinkler placement: adaptive rectangular grid + edge pass + prune + gap repair.

The naive spec pipeline (grid the S/2 inset, centroid fallback when it is
empty) breaks on real rooms: corridors narrower than S got a single head,
concave rooms lost whole wings to the inset erosion, and the centroid of a
C-shaped room lies outside it.  This implementation keeps the rectangular
grid idea but makes it robust:

  * grid coordinates are distributed per axis with margins that adapt to the
    room size (never more than S/2, never crowding rows closer than the
    1800 mm minimum),
  * heads must only honour the 100 mm wall clearance (not the S/2 inset),
  * an edge pass along the S/2 inset rings fills wall gaps in large rooms,
  * a coverage-repair loop then adds heads for any region the grid cannot
    see (corridor ends, concave wings, triangle corners) by growing the
    covered area outward until the 0.707*S coverage check is satisfied.
"""

import math

from shapely.geometry import Point, Polygon

from .geometry import EPS, Pt, clean_polygon, dist, polygon_parts
from .nfpa import MIN_HEAD_SPACING, MIN_WALL_DIST, coverage_radius, spacing_for

_REPAIR_CAP = 1000  # hard stop for the repair loop (way above any real room)


def place(boundary: list[Pt], hazard: str) -> list[Pt]:
    """Compute sprinkler head positions for a room boundary."""
    s = spacing_for(hazard)
    poly = clean_polygon(boundary)

    safe_parts = polygon_parts(poly.buffer(-MIN_WALL_DIST))
    if not safe_parts:
        # Sliver thinner than 200 mm: representative_point() is guaranteed
        # to be inside the polygon (the centroid of a concave room is not).
        rp = poly.representative_point()
        return [[rp.x, rp.y]]

    points = _grid_points(poly, safe_parts, s)
    points += _edge_points(polygon_parts(poly.buffer(-s / 2)), s)
    points = _prune(points, MIN_HEAD_SPACING)
    points = _coverage_repair(poly, safe_parts, points, coverage_radius(hazard), s)
    return points


def _grid_points(poly: Polygon, safe_parts: list[Polygon], s: float) -> list[Pt]:
    """Rectangular grid over the room bounding box, filtered to legal spots."""
    minx, miny, maxx, maxy = poly.bounds
    pts: list[Pt] = []
    for x in _axis_coords(minx, maxx, s):
        for y in _axis_coords(miny, maxy, s):
            p = Point(x, y)
            if any(part.covers(p) for part in safe_parts):
                pts.append([x, y])
    return pts


def _axis_coords(lo: float, hi: float, s: float) -> list[float]:
    """Row/column coordinates along one axis.

    * width <= S: a single centred row (wall distance w/2 <= S/2).
    * width slightly above S (margins of S/2 would leave the two rows closer
      than the 1800 mm minimum): two rows pushed toward the walls just far
      enough to keep the minimum gap, margins still well under S/2.
    * otherwise: S/2 margins, equal intervals <= S.
    """
    w = hi - lo
    if w <= s + EPS:
        return [lo + w / 2]
    span = w - s
    if span < MIN_HEAD_SPACING:
        margin = (w - MIN_HEAD_SPACING) / 2
        return [lo + margin, hi - margin]
    k = max(1, math.ceil(span / s))
    return [lo + s / 2 + span * i / k for i in range(k + 1)]


def _edge_points(inset_parts: list[Polygon], s: float) -> list[Pt]:
    """Walk each S/2-inset ring dropping a point every S to fill wall gaps."""
    pts: list[Pt] = []
    for part in inset_parts:
        if part.buffer(-MIN_WALL_DIST).is_empty:
            # Paper-thin sliver (room barely wider than S): its ring is just
            # the corridor's medial line, already handled by the grid rows.
            continue
        ring = part.exterior
        length = ring.length
        if length <= EPS:
            continue
        d = 0.0
        while d < length - EPS:
            p = ring.interpolate(d)
            pts.append([p.x, p.y])
            d += s
    return pts


def _prune(points: list[Pt], min_gap: float) -> list[Pt]:
    """Greedy prune: keep a point only if >= min_gap from every kept point.

    Grid points come first in the input, so the edge pass only fills genuine
    gaps instead of displacing the regular grid.
    """
    kept: list[Pt] = []
    for p in points:
        if all(dist(p, q) >= min_gap - EPS for q in kept):
            kept.append(p)
    return kept


def _coverage_repair(
    poly: Polygon,
    safe_parts: list[Polygon],
    points: list[Pt],
    radius: float,
    s: float,
) -> list[Pt]:
    """Add heads until every interior sample is within the coverage radius.

    Growth is frontier-first: each new head goes to the uncovered sample
    CLOSEST to the already-covered area, so consecutive heads stay within
    ~0.99*S of a neighbour (satisfying both max-spacing and, because any
    uncovered sample is > 0.707*S away, min-spacing).
    """
    samples = _interior_samples(poly, s / 5)
    if not samples:
        return points

    points = list(points)
    if not points:
        rp = poly.representative_point()
        points.append(_clamp_to_safe([rp.x, rp.y], safe_parts))

    for _ in range(_REPAIR_CAP):
        uncovered = [
            g for g in samples
            if all(dist(g, p) > radius for p in points)
        ]
        if not uncovered:
            break
        target = min(uncovered, key=lambda g: min(dist(g, p) for p in points))
        points.append(_clamp_to_safe(target, safe_parts))
    return points


def _interior_samples(poly: Polygon, step: float) -> list[Pt]:
    minx, miny, maxx, maxy = poly.bounds
    samples: list[Pt] = []
    y = miny + step / 2
    while y < maxy:
        x = minx + step / 2
        while x < maxx:
            if poly.covers(Point(x, y)):
                samples.append([x, y])
            x += step
        y += step
    return samples


def _clamp_to_safe(p: Pt, safe_parts: list[Polygon]) -> Pt:
    """Nudge a point to honour the 100 mm wall clearance if it does not."""
    pt = Point(p)
    if any(part.covers(pt) for part in safe_parts):
        return p
    best = min(safe_parts, key=lambda part: part.distance(pt))
    moved = best.exterior.interpolate(best.exterior.project(pt))
    return [moved.x, moved.y]
