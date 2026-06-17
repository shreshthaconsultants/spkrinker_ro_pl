"""
area_stats.py — Sprinkler coverage area statistics (optimized)

Uses SpatialHash for O(1) per-sample coverage checks instead of
brute-force O(n_sprinklers) scans.

Computes:
  - Area of each floor polygon (Shoelace)
  - Coverage area per sprinkler = π × r²
  - Total theoretical coverage = count × π × r²
  - Total floor area (sum of all zones)
  - Avg sprinkler coverage area = theoretical_coverage / count
  - Overlap-aware effective coverage (via sampling + SpatialHash)
  - Residual uncovered area = floor_area - effective_covered
  - Coverage efficiency % = effective_covered / floor_area × 100
"""

import math
from geometry import bbox, point_in_poly, poly_area, SpatialHash


# ── Per-sprinkler circle area ─────────────────────────────────────

def sprinkler_circle_area(radius_mm: float) -> float:
    """Area of single sprinkler coverage circle in mm²."""
    return math.pi * radius_mm ** 2


def sprinkler_circle_area_m2(radius_mm: float) -> float:
    """Area of single sprinkler coverage circle in m²."""
    return sprinkler_circle_area(radius_mm) / 1_000_000


# ── Effective coverage via sampling ─────────────────────────────


"""
| Type       | Meaning                |
| ---------- | ---------------------- |
| excl_polys | ignore area completely |
| obs_polys  | physical blockage      |
"""
def effective_covered_area_m2(
    floor_polys: list,
    all_points: list,
    coverage_radius: float,
    excl_polys: list,
    obs_polys: list,
    sample_step: float = None,
) -> dict:
    """
    Sample the union of floor polygons on a grid.
    Uses SpatialHash so each sample checks coverage in O(1) instead of
    scanning all sprinklers.

    Returns dict with:
      floor_area_m2, covered_m2, uncovered_m2, coverage_pct, sample_count
    """
    if sample_step is None:
        # radius/10 gives accurate coverage — radius/4 was too coarse
        # and caused wrong coverage % (more sprinklers = less coverage bug)
        sample_step = max(50.0, coverage_radius / 10.0)

    all_pts = [p for poly in floor_polys for p in poly]
    if not all_pts:
        return {"floor_area_m2": 0, "covered_m2": 0, "uncovered_m2": 0,
                "coverage_pct": 0, "sample_count": 0}

    xs = [p[0] for p in all_pts]
    ys = [p[1] for p in all_pts]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)

    sh = SpatialHash(coverage_radius)
    sh.bulk_load(all_points)

    total   = 0
    covered = 0
    half    = sample_step / 2.0

    x = minx + half
    while x <= maxx:
        y = miny + half
        while y <= maxy:
            in_floor = any(point_in_poly(x, y, fp) for fp in floor_polys)
            if in_floor:
                in_excl = any(point_in_poly(x, y, ex) for ex in excl_polys)
                in_obs  = any(point_in_poly(x, y, ob) for ob in obs_polys)
                if not in_excl and not in_obs:
                    total += 1
                    if sh.any_within(x, y, coverage_radius):
                        covered += 1
            y += sample_step
        x += sample_step

    cell_area      = sample_step ** 2
    floor_area_mm2 = total   * cell_area
    covered_mm2    = covered * cell_area
    uncovered_mm2  = floor_area_mm2 - covered_mm2

    return {
        "floor_area_m2":   round(floor_area_mm2   / 1e6, 2),
        "covered_m2":      round(covered_mm2       / 1e6, 2),
        "uncovered_m2":    round(uncovered_mm2      / 1e6, 2),
        "coverage_pct":    round(100.0 * covered / total, 1) if total > 0 else 0.0,
        "sample_count":    total,
    }


# ── Exact floor area via Shoelace ─────────────────────────────────

def total_floor_area_m2(floor_polys: list, excl_polys: list = None) -> float:
    """
    Sum of all floor polygon areas minus exclusion areas (mm² → m²).
    Note: overlapping exclusion zones may slightly oversubtract.
    """
    excl_polys = excl_polys or []
    total = sum(poly_area(fp) for fp in floor_polys)
    total -= sum(poly_area(ep) for ep in excl_polys)
    return round(max(0, total) / 1e6, 2)


# ── Full scenario stats ───────────────────────────────────────────

def compute_scenario_stats(
    scenario_id: int,
    scenario_name: str,
    all_points: list,
    extra_points: list,
    floor_polys: list,
    excl_polys: list,
    obs_polys: list,
    coverage_radius: float,
    zone_reports: list,
    fast_mode: bool = True,
) -> dict:
    """
    Compute complete coverage statistics for one scenario.
    Returns a flat dict ready for JSON serialisation.
    """
    grid_count  = len(all_points)
    extra_count = len(extra_points)
    total_count = grid_count + extra_count
    placed_all  = list(all_points) + list(extra_points)

    single_area_m2  = sprinkler_circle_area_m2(coverage_radius)
    theoretical_m2  = round(single_area_m2 * total_count, 2)
    floor_area_m2   = total_floor_area_m2(floor_polys, excl_polys)

    if fast_mode:
        covered_m2    = min(theoretical_m2, floor_area_m2)
        uncovered_m2  = max(0.0, floor_area_m2 - theoretical_m2)
        coverage_pct  = round(100.0 * covered_m2 / floor_area_m2, 1) if floor_area_m2 > 0 else 0.0
    else:
        eff = effective_covered_area_m2(
            floor_polys, placed_all, coverage_radius,
            excl_polys, obs_polys,
        )
        covered_m2   = eff["covered_m2"]
        uncovered_m2 = eff["uncovered_m2"]
        coverage_pct = eff["coverage_pct"]

    avg_per_sprinkler_m2 = round(single_area_m2, 4)
    floor_per_sprinkler = round(floor_area_m2 / total_count, 2) if total_count > 0 else 0.0

    return {
        "scenario_id":           scenario_id,
        "scenario_name":         scenario_name,
        "total_sprinklers":      total_count,
        "grid_sprinklers":       grid_count,
        "extra_sprinklers":      extra_count,
        "coverage_radius_mm":    coverage_radius,
        "single_circle_area_m2": round(single_area_m2, 4),
        "avg_sprinkler_area_m2": avg_per_sprinkler_m2,
        "theoretical_coverage_m2": theoretical_m2,
        "floor_area_m2":         floor_area_m2,
        "effective_covered_m2":  covered_m2,
        "uncovered_m2":          uncovered_m2,
        "coverage_pct":          coverage_pct,
        "floor_per_sprinkler_m2": floor_per_sprinkler,
        "zone_count":            len(zone_reports),
        "zones":                 zone_reports,
    }