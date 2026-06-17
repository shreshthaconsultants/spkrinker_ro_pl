"""NFPA-13 validation rules.

The literal spec rule "wall distance <= S/2 for all heads" would fail every
interior head of a large room, so it is split into its two real meanings:
  * clearance      - every head at least MIN_WALL_DIST from the boundary
                     (and inside it),
  * wall_coverage  - every boundary edge has some head within S/2.
"""

from dataclasses import dataclass

from shapely.geometry import LineString, Point

from .geometry import EPS, Pt, clean_polygon, dist
from .nfpa import (
    HAZARDS,
    MIN_HEAD_SPACING,
    MIN_WALL_DIST,
    coverage_radius,
    spacing_for,
)

# Engineering slack (mm) so heads placed exactly on a limit (e.g. precisely
# S/2 from a wall) don't fail from sub-millimetre asymmetry.  1 mm is
# physically meaningless for sprinkler coverage but far above float noise.
TOL = 1.0


@dataclass
class RuleResult:
    rule: str
    passed: bool
    detail: str


@dataclass
class ValidationReport:
    passed: bool
    rules: list[RuleResult]
    failing_heads: list[Pt]


def validate(boundary: list[Pt], points: list[Pt], hazard: str) -> ValidationReport:
    s = spacing_for(hazard)
    poly = clean_polygon(boundary)
    rules: list[RuleResult] = []
    failing: list[Pt] = []

    rules.append(_check_area(poly, hazard))
    rules.append(_check_max_spacing(points, hazard))

    r, bad = _check_min_spacing(points)
    rules.append(r)
    failing += bad

    r, bad = _check_clearance(points, poly)
    rules.append(r)
    failing += bad

    rules.append(_check_wall_coverage(points, poly, s / 2))
    rules.append(_check_full_coverage(points, poly, coverage_radius(hazard), s / 4))

    return ValidationReport(
        passed=all(r.passed for r in rules),
        rules=rules,
        failing_heads=_dedupe(failing),
    )


def _check_area(poly, hazard: str) -> RuleResult:
    max_area = HAZARDS[hazard]["max_area"]
    n_required = poly.area / max_area
    return RuleResult(
        rule="max_area",
        passed=True,
        detail=(
            f"room area {poly.area / 1e6:.1f} m2; one head covers at most "
            f"{max_area / 1e6:.1f} m2 -> at least {max(1, -(-n_required // 1)):.0f} head(s) needed"
        ),
    )


def _check_max_spacing(points: list[Pt], hazard: str) -> RuleResult:
    max_s = HAZARDS[hazard]["max_spacing"]
    if len(points) < 2:
        return RuleResult("max_spacing", True, "single head; spacing not applicable")
    worst = max(min(dist(p, q) for q in points if q is not p) for p in points)
    passed = worst <= max_s + TOL
    return RuleResult(
        "max_spacing",
        passed,
        f"worst nearest-neighbour distance {worst:.0f} mm "
        f"{'<=' if passed else '>'} max {max_s:.0f} mm",
    )


def _check_min_spacing(points: list[Pt]) -> tuple[RuleResult, list[Pt]]:
    bad: list[Pt] = []
    worst = None
    for i, p in enumerate(points):
        for q in points[i + 1:]:
            d = dist(p, q)
            if d < MIN_HEAD_SPACING - TOL:
                bad += [p, q]
                worst = d if worst is None else min(worst, d)
    passed = not bad
    detail = (
        f"all pairs >= {MIN_HEAD_SPACING:.0f} mm"
        if passed
        else f"{len(_dedupe(bad))} head(s) closer than {MIN_HEAD_SPACING:.0f} mm "
             f"(closest pair {worst:.0f} mm)"
    )
    return RuleResult("min_head_spacing", passed, detail), bad


def _check_clearance(points: list[Pt], poly) -> tuple[RuleResult, list[Pt]]:
    """Every head inside the boundary and >= MIN_WALL_DIST from it."""
    bad: list[Pt] = []
    for p in points:
        pt = Point(p)
        if not poly.covers(pt) or poly.exterior.distance(pt) < MIN_WALL_DIST - TOL:
            bad.append(p)
        else:
            # interior rings (columns/shafts) count as walls too
            if any(ring.distance(pt) < MIN_WALL_DIST - TOL for ring in poly.interiors):
                bad.append(p)
    passed = not bad
    detail = (
        f"all heads inside boundary and >= {MIN_WALL_DIST:.0f} mm from walls"
        if passed
        else f"{len(_dedupe(bad))} head(s) outside the boundary or closer than "
             f"{MIN_WALL_DIST:.0f} mm to a wall"
    )
    return RuleResult("min_wall_dist", passed, detail), bad


def _check_wall_coverage(points: list[Pt], poly, max_wall: float) -> RuleResult:
    """Every boundary edge must have some head within S/2 (perpendicular reach)."""
    if not points:
        return RuleResult("wall_coverage", False, "no heads")
    head_pts = [Point(p) for p in points]
    uncovered = 0
    total = 0
    rings = [poly.exterior, *poly.interiors]
    for ring in rings:
        coords = list(ring.coords)
        for a, b in zip(coords, coords[1:]):
            edge = LineString([a, b])
            if edge.length <= EPS:
                continue
            total += 1
            if min(edge.distance(h) for h in head_pts) > max_wall + TOL:
                uncovered += 1
    passed = uncovered == 0
    detail = (
        f"all {total} wall edge(s) have a head within {max_wall:.0f} mm"
        if passed
        else f"{uncovered} of {total} wall edge(s) have no head within {max_wall:.0f} mm"
    )
    return RuleResult("wall_coverage", passed, detail)


def _check_full_coverage(points: list[Pt], poly, radius: float, step: float) -> RuleResult:
    """Sample the interior on a step grid; every sample within radius of a head."""
    if not points:
        return RuleResult("full_coverage", False, "no heads")
    minx, miny, maxx, maxy = poly.bounds
    gaps = 0
    total = 0
    y = miny + step / 2
    while y < maxy:
        x = minx + step / 2
        while x < maxx:
            sample = Point(x, y)
            if poly.covers(sample):
                total += 1
                if all(dist([x, y], p) > radius + TOL for p in points):
                    gaps += 1
            x += step
        y += step
    passed = gaps == 0
    detail = (
        f"all {total} interior samples within {radius:.0f} mm of a head"
        if passed
        else f"{gaps} of {total} interior samples not covered (radius {radius:.0f} mm)"
    )
    return RuleResult("full_coverage", passed, detail)


def _dedupe(points: list[Pt]) -> list[Pt]:
    seen: set[tuple[float, float]] = set()
    out: list[Pt] = []
    for p in points:
        key = (round(p[0], 6), round(p[1], 6))
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out
