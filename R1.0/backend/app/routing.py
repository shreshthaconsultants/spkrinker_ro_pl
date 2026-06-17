"""Structured-tree pipe routing (NOT a minimum spanning tree).

Heads are clustered into rows (branch lines), a vertical cross main is placed
on the riser side (or the min-X side when no riser is given), and every branch
tees into the main.  The network is a tree rooted at the riser tap, and every
output segment is oriented start -> end in the FLOW direction (upstream ->
downstream) so the CAD plugin can draw flow arrows with no inference.

Multiple shafts (risers) are supported: each head is assigned to its NEAREST
shaft, and each shaft routes its own independent tree over its share of the
heads.  Every segment carries the index of its shaft so the plugin can colour
each shaft's network differently.

An optional room boundary polygon constrains the routing: heads outside it
are dropped (and counted in skipped_heads), the cross main is placed on a
head column that stays inside the room, and riser connections prefer an
L-shaped run over a straight diagonal when the diagonal would leave the room.
"""

import statistics
from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry import LineString
from shapely.ops import unary_union

from .geometry import EPS, Pt, clean_polygon, dist, polygon_parts
from .nfpa import DEFAULT_SPACING, spacing_for

#: tolerance (mm) for "inside the boundary" checks
_BOUNDARY_SLACK = 1.0

#: heads within this of the branch line are chained straight along it;
#: heads farther off the line hang from a PERPENDICULAR drop: the branch
#: line tees directly above/below the head and a short orthogonal stub
#: connects down to it (no zig-zag, no diagonals)
_ROW_LINE_TOL = 200.0

#: a cross main must not pass closer than this to ANOTHER shaft's sprinkler
_MAIN_CLEARANCE = 500.0


@dataclass
class Segment:
    start: Pt          # upstream (toward the riser)
    end: Pt            # downstream (toward the heads)
    kind: str          # "riser" | "main" | "branch"
    length: float
    shaft: int = 0     # index into the risers list (0 when no shafts given)


@dataclass
class RouteGroup:
    riser: Pt          # the shaft this group is fed from (effective root)
    head_count: int
    length: float      # pipe length of this group's tree


@dataclass
class RoutePlan:
    segments: list[Segment]
    risers: list[Pt]   # effective tree roots, one per shaft
    groups: list[RouteGroup]
    total_length: float
    skipped_heads: int = 0  # heads outside the boundary, ignored


def route(
    points: list[Pt],
    hazard: str | None = None,
    risers: list[Pt] | None = None,
    boundary: list[Pt] | None = None,
) -> RoutePlan:
    if not points:
        raise ValueError("no sprinkler points to route")

    poly = None
    skipped = 0
    if boundary is not None:
        poly = clean_polygon(boundary).buffer(_BOUNDARY_SLACK)
        inside = shapely.covers(poly, shapely.points(np.asarray(points, dtype=float)))
        kept = [p for p, ok in zip(points, inside) if ok]
        skipped = len(points) - len(kept)
        if not kept:
            raise ValueError("no sprinkler points inside the room boundary")
        points = kept

    if not risers:
        segments, root = _route_tree(points, hazard, None, poly, [])
        length = sum(s.length for s in segments)
        return RoutePlan(
            segments=segments,
            risers=[root],
            groups=[RouteGroup(riser=root, head_count=len(points), length=length)],
            total_length=length,
            skipped_heads=skipped,
        )

    # Divide the heads among the shafts ROW BY ROW: each head is projected
    # onto its row line and assigned to the shaft nearest that projection.
    # Plain per-head nearest-distance cuts staggered rows raggedly, letting
    # one shaft's branch line run through the other shaft's heads; the
    # row-projected assignment guarantees each row splits into clean,
    # contiguous spans.
    s_all = spacing_for(hazard) if hazard else _infer_spacing(points)
    buckets: list[list[Pt]] = [[] for _ in risers]
    for row in _cluster_rows(points, tol=s_all / 2):
        row_line = statistics.median(p[1] for p in row)
        for p in row:
            projected = [p[0], row_line]
            nearest = min(range(len(risers)), key=lambda i: dist(projected, risers[i]))
            buckets[nearest].append(p)

    segments: list[Segment] = []
    groups: list[RouteGroup] = []
    roots: list[Pt] = []
    for idx, (riser, bucket) in enumerate(zip(risers, buckets)):
        riser = list(riser)
        roots.append(riser)
        if not bucket:
            groups.append(RouteGroup(riser=riser, head_count=0, length=0.0))
            continue
        # other shafts' heads are obstacles the cross main must stay clear of
        obstacles = [p for j, other in enumerate(buckets) if j != idx for p in other]
        tree, _ = _route_tree(bucket, hazard, riser, poly, obstacles)
        for seg in tree:
            seg.shaft = idx
        segments += tree
        groups.append(RouteGroup(
            riser=riser,
            head_count=len(bucket),
            length=sum(s.length for s in tree),
        ))

    return RoutePlan(
        segments=segments,
        risers=roots,
        groups=groups,
        total_length=sum(s.length for s in segments),
        skipped_heads=skipped,
    )


def build_outlines(
    segments: list[Segment],
    branch_width: float,
    main_width: float,
) -> list[tuple[int, list[Pt]]]:
    """Merged double-line outlines, one set of rings per shaft network.

    Every segment becomes a rectangle (centreline buffered by width/2, flat
    end caps, mitred corners) and the rectangles of a shaft are unioned into
    one polygon.  The union renders every junction as a true fitting - 90
    degree elbows, tees and 4-way crosses - with no construction lines
    crossing the pipe interior.  Returns (shaft, ring) pairs; rings are
    closed (closing vertex omitted).
    """
    outlines: list[tuple[int, list[Pt]]] = []
    for shaft in sorted({s.shaft for s in segments}):
        rects = []
        for seg in segments:
            if seg.shaft != shaft or seg.length <= EPS:
                continue
            width = branch_width if seg.kind == "branch" else main_width
            line = LineString([seg.start, seg.end])
            rects.append(line.buffer(width / 2, cap_style=2, join_style=2, mitre_limit=2.0))
        if not rects:
            continue
        for poly in polygon_parts(unary_union(rects)):
            for ring in (poly.exterior, *poly.interiors):
                pts = [[round(x, 2), round(y, 2)] for x, y in list(ring.coords)[:-1]]
                if len(pts) >= 3:
                    outlines.append((shaft, pts))
    return outlines


def _route_tree(points, hazard, riser, poly, obstacles) -> tuple[list[Segment], Pt]:
    """One flow tree over one group of heads; returns (segments, effective root).

    obstacles are other shafts' heads: the cross main keeps clear of them.
    """
    s = spacing_for(hazard) if hazard else _infer_spacing(points)
    rows = _cluster_rows(points, tol=s / 2)

    # Cross main sits on the riser side of the head field, but never outside
    # it: a riser inside the room feeds a centre main (branches both sides),
    # a riser outside connects to the nearest main end by the shortest path.
    xs = [p[0] for p in points]
    if riser is not None:
        desired_x = min(max(riser[0], min(xs)), max(xs))
    else:
        desired_x = min(xs)
    main_x = _choose_main_x(desired_x, rows, xs, poly, obstacles)

    # Each row tees into the main at the y of its ENTRY head (the head
    # nearest the main), so the tee sits on the actual branch pipe instead
    # of a phantom mean-y point no head occupies.
    tees = []
    for heads in rows:
        entry = min(heads, key=lambda p: abs(p[0] - main_x))
        tees.append((entry[1], heads))

    tee_ys = [tee_y for tee_y, _ in tees]
    lo_y, hi_y = min(tee_ys), max(tee_ys)
    tap_y = min(max(riser[1], lo_y), hi_y) if riser is not None else lo_y
    tap = [main_x, tap_y]

    segments: list[Segment] = []

    if riser is not None and dist(riser, tap) > EPS:
        segments += _riser_segments(riser, tap, poly)

    segments += _main_segments(main_x, tap_y, tee_ys)

    for tee_y, heads in tees:
        segments += _branch_segments(main_x, tee_y, heads)

    effective_root = list(riser) if riser is not None else tap
    return segments, effective_root


def _choose_main_x(desired_x: float, rows, xs: list[float], poly, obstacles) -> float:
    """Pick the main's x so its vertical run stays inside the boundary AND
    clear of other shafts' sprinklers.

    Head-column x values are tried nearest-to-desired first; the first
    column whose main run lies inside the room and touches no foreign head
    wins.  Fallback: the column with (fewest blocked heads, least pipe
    outside the room).
    """
    anchor_ys = [row[0][1] for row in rows]
    lo, hi = min(anchor_ys), max(anchor_ys)
    if hi - lo <= EPS:
        return desired_x  # single row: no main run to constrain
    if poly is None and not obstacles:
        return desired_x

    def outside_len(x: float) -> float:
        if poly is None:
            return 0.0
        out = LineString([(x, lo), (x, hi)]).difference(poly).length
        return 0.0 if out <= EPS else out

    # vectorized obstacle check: pre-filter foreign heads to the main's y-band
    if obstacles:
        obs = np.asarray(obstacles, dtype=float)
        band = obs[(obs[:, 1] >= lo - _MAIN_CLEARANCE) & (obs[:, 1] <= hi + _MAIN_CLEARANCE)]
        band_xs = band[:, 0]
    else:
        band_xs = np.empty(0)

    def blocked(x: float) -> int:
        if band_xs.size == 0:
            return 0
        return int(np.count_nonzero(np.abs(band_xs - x) <= _MAIN_CLEARANCE))

    candidates = [desired_x] + sorted(set(xs), key=lambda x: abs(x - desired_x))
    best_x, best_score = desired_x, None
    for x in candidates:
        score = (blocked(x), outside_len(x))
        if score == (0, 0.0):
            return x
        if best_score is None or score < best_score:
            best_x, best_score = x, score
    return best_x


def _riser_segments(riser: Pt, tap: Pt, poly) -> list[Segment]:
    """Shaft-to-main connection: straight, or an L-run if that stays inside.

    Candidate paths are compared by how much pipe falls outside the room;
    the straight diagonal wins ties.
    """
    paths = [[riser, tap]]
    corner1 = [riser[0], tap[1]]
    corner2 = [tap[0], riser[1]]
    for corner in (corner1, corner2):
        if dist(corner, riser) > EPS and dist(corner, tap) > EPS:
            paths.append([riser, corner, tap])

    if poly is not None and len(paths) > 1:
        def outside_len(path):
            return LineString([tuple(p) for p in path]).difference(poly).length
        best = min(paths, key=outside_len)
        if outside_len(best) < outside_len(paths[0]) - EPS:
            paths[0] = best

    chosen = paths[0]
    segments = []
    for a, b in zip(chosen, chosen[1:]):
        if dist(a, b) > EPS:
            segments.append(_seg(a, b, "riser"))
    return segments


def _infer_spacing(points: list[Pt]) -> float:
    """Spacing inferred from the heads themselves (median nearest-neighbour).

    Uses an STR-tree: the naive all-pairs version is O(n^2) and took 30+
    seconds on drawings with thousands of heads.
    """
    if len(points) < 2:
        return DEFAULT_SPACING
    geoms = shapely.points(np.asarray(points, dtype=float))
    tree = shapely.STRtree(geoms)
    _, dists = tree.query_nearest(geoms, return_distance=True, exclusive=True)
    inferred = float(np.median(dists))
    return inferred if inferred > EPS else DEFAULT_SPACING


def _cluster_rows(points: list[Pt], tol: float) -> list[list[Pt]]:
    """Group heads into rows by Y; rows ordered by Y, heads in a row by X.

    A head joins the current row only while it stays within tol of the row's
    FIRST head (the anchor).  Comparing against the previous head instead
    would let a chain of sub-tol gaps drift one row across the whole field,
    collapsing every branch line (and the cross main) into a single tangle.
    """
    ordered = sorted(points, key=lambda p: p[1])
    clusters: list[list[Pt]] = [[ordered[0]]]
    anchor_y = ordered[0][1]
    for p in ordered[1:]:
        if p[1] - anchor_y > tol:
            clusters.append([p])
            anchor_y = p[1]
        else:
            clusters[-1].append(p)
    return [sorted(cluster, key=lambda p: p[0]) for cluster in clusters]


def _main_segments(main_x: float, tap_y: float, row_ys: list[float]) -> list[Segment]:
    """Cross-main pieces walking AWAY from the tap toward each end.

    A tap in the middle of the main yields segments flowing in both
    directions away from it.
    """
    tee_ys = sorted(set(row_ys))
    segments = []
    prev = tap_y
    for y in (y for y in tee_ys if y > tap_y + EPS):           # upward run
        segments.append(_seg([main_x, prev], [main_x, y], "main"))
        prev = y
    prev = tap_y
    for y in sorted((y for y in tee_ys if y < tap_y - EPS), reverse=True):  # downward run
        segments.append(_seg([main_x, prev], [main_x, y], "main"))
        prev = y
    return segments


def _branch_segments(main_x: float, tee_y: float, heads: list[Pt]) -> list[Segment]:
    """Branch line for one row: tee at (main_x, tee_y), flowing outward.

    tee_y is the entry head's y, so the tee lies on the branch pipe itself.
    Heads can sit on both sides of the main (riser in the middle of the room),
    so each side is chained independently away from the tee.

    Heads ON the branch line (within _ROW_LINE_TOL of tee_y) are chained
    straight in x-order.  Offset heads (staggered placements) hang from a
    PERPENDICULAR drop: the line runs through the foot point directly
    above/below the head and a short orthogonal stub connects to it,
    extending the line outward when the head lies beyond its end.
    """
    tee = [main_x, tee_y]
    inline = [p for p in heads if abs(p[1] - tee_y) <= _ROW_LINE_TOL]
    offset = [p for p in heads if abs(p[1] - tee_y) > _ROW_LINE_TOL]

    segments = []
    for is_right in (True, False):
        def on_side(p):
            return (p[0] >= main_x - EPS) if is_right else (p[0] < main_x - EPS)

        # Stations the line must pass through, walked outward from the tee:
        # inline heads at their own position, offset heads as a foot point on
        # the line (paired with the head their drop must reach).
        stations = [(list(p), None) for p in inline if on_side(p)]
        stations += [([p[0], tee_y], list(p)) for p in offset if on_side(p)]
        stations.sort(key=lambda item: abs(item[0][0] - main_x))

        prev = tee
        for station, drop_head in stations:
            if dist(prev, station) > EPS:
                segments.append(_seg(prev, station, "branch"))
                prev = station
            if drop_head is not None and dist(prev, drop_head) > EPS:
                segments.append(_seg(prev, drop_head, "branch"))  # perpendicular drop
    return segments


def _seg(a: Pt, b: Pt, kind: str) -> Segment:
    return Segment(start=list(a), end=list(b), kind=kind, length=dist(a, b))
