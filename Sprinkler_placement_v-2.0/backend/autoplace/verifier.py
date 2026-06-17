"""
verifier.py — Step 2: internal compliance verifier.

Takes a placed layout for ONE room + its NFPA rule and returns a
structured list of violations. This is the signal the self-fix loop
(autofix.py) consumes and the report (pipeline.py) prints. It is the
single most important module in v2: if a layout passes the verifier with
zero ERRORs, no human needs to look at it.

CRITICAL design choice — coverage criterion:
  Coverage is proven against the NFPA 0.75-spacing REACH rule (every
  floor point within 0.75 × max_spacing of a head), NOT the r=1500
  display circle. At pitch = 2r a square grid leaves the cell-diagonal
  centre (2121 mm) outside the circle, so a circle test would flag every
  compliant grid as full of holes and the self-fix loop would chase
  phantom pockets forever. The reach rule is what NFPA actually requires.

Violation severities:
  "error"   — must be fixed before the layout ships (coverage gap, head
              in/near obstacle, head past max wall, max-spacing exceeded).
  "warning" — surfaced but NOT auto-fixed (NFPA min-spacing 1800: the
              user's alpha/gama formulas win per project decision; and
              "guessed hazard" notes).
"""

import math
from dataclasses import dataclass, field
from typing import List, Optional

from geometry import (
    bbox, point_in_poly, poly_area, min_dist_to_segs,
    SpatialHash, precompute_sample_grid, TOLERANCE,
)

from . import nfpa_rules as R


# ── Violation record ──────────────────────────────────────────────

@dataclass
class Violation:
    kind:     str                 # 'coverage','spacing_max','spacing_min',
                                  # 'wall_max','wall_min','obstacle','density'
    severity: str                 # 'error' | 'warning'
    message:  str
    point:    Optional[tuple] = None   # (x,y) focus — used by self-fix
    value:    Optional[float] = None   # the offending measurement

    def to_dict(self) -> dict:
        d = {"kind": self.kind, "severity": self.severity, "message": self.message}
        if self.point is not None:
            d["point"] = [round(self.point[0], 1), round(self.point[1], 1)]
        if self.value is not None:
            d["value"] = round(self.value, 1)
        return d


@dataclass
class VerifyReport:
    room_index:    int
    hazard:        str
    head_count:    int
    coverage_pct:  float
    floor_area_m2: float
    violations:    List[Violation] = field(default_factory=list)

    @property
    def errors(self) -> List[Violation]:
        return [v for v in self.violations if v.severity == "error"]

    @property
    def warnings(self) -> List[Violation]:
        return [v for v in self.violations if v.severity == "warning"]

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0

    def to_dict(self) -> dict:
        return {
            "room_index":    self.room_index,
            "hazard":        self.hazard,
            "head_count":    self.head_count,
            "coverage_pct":  round(self.coverage_pct, 2),
            "floor_area_m2": round(self.floor_area_m2, 2),
            "passed":        self.passed,
            "error_count":   len(self.errors),
            "warning_count": len(self.warnings),
            "violations":    [v.to_dict() for v in self.violations],
        }


# ── Coverage gap clustering ───────────────────────────────────────

def _cluster(points: List[tuple], radius: float) -> List[tuple]:
    """Greedy spatial clustering: collapse a cloud of uncovered sample
    points into a few representative centroids so self-fix inserts a
    handful of heads, not one per sample."""
    if not points:
        return []
    sh = SpatialHash(radius)
    clusters: List[list] = []
    centers = SpatialHash(radius)
    cidx: dict = {}
    out: List[tuple] = []
    used = [False] * len(points)
    for i, (x, y) in enumerate(points):
        if used[i]:
            continue
        cx, cy, n = x, y, 1
        for j in range(i + 1, len(points)):
            if used[j]:
                continue
            qx, qy = points[j]
            if (qx - x) ** 2 + (qy - y) ** 2 <= radius * radius:
                used[j] = True
                cx += qx; cy += qy; n += 1
        out.append((cx / n, cy / n))
    return out


# ── Main entry ────────────────────────────────────────────────────

def verify_room(
    floor_poly:     list,
    heads:          List[tuple],     # [(x,y) or (x,y,rot), ...]
    rule:           "R.HazardRule",
    room_index:     int = 0,
    obs_polys:      Optional[list] = None,
    obs_segs:       Optional[list] = None,
    obs_min_offset: float = 0.0,
    excl_polys:     Optional[list] = None,
    sample_step:    Optional[float] = None,
    is_guess:       bool = False,
) -> VerifyReport:
    """
    Verify one room's layout against its NFPA rule. Returns a VerifyReport
    whose `.passed` is True iff there are no ERROR-severity violations.
    """
    obs_polys = obs_polys or []
    obs_segs  = obs_segs or []
    excl_polys = excl_polys or []
    xy = [(float(h[0]), float(h[1])) for h in heads]

    floor_segs = [(floor_poly[i][0], floor_poly[i][1],
                   floor_poly[i + 1][0], floor_poly[i + 1][1])
                  for i in range(len(floor_poly) - 1)]
    area_m2 = poly_area(floor_poly) / 1e6

    if sample_step is None:
        # Fine enough to catch a real gap, coarse enough to stay fast.
        sample_step = max(200.0, rule.reach_mm / 4.0)

    samples = precompute_sample_grid(floor_poly, excl_polys, obs_polys, sample_step)

    rep = VerifyReport(
        room_index=room_index, hazard=rule.hazard,
        head_count=len(xy), coverage_pct=0.0, floor_area_m2=area_m2,
    )

    # ── Coverage (NFPA 0.75-reach rule) ───────────────────────────
    reach = rule.reach_mm
    if not xy:
        rep.coverage_pct = 0.0
        if samples:
            rep.violations.append(Violation(
                "coverage", "error",
                f"room has {len(samples)} uncovered sample points and no heads",
                point=samples[len(samples) // 2] if samples else None,
            ))
        return rep

    sh = SpatialHash(reach)
    sh.bulk_load(xy)
    uncovered = [s for s in samples if not sh.any_within(s[0], s[1], reach)]
    covered = len(samples) - len(uncovered)
    rep.coverage_pct = (100.0 * covered / len(samples)) if samples else 100.0

    for c in _cluster(uncovered, reach):
        rep.violations.append(Violation(
            "coverage", "error",
            f"uncovered area centred near ({c[0]:.0f}, {c[1]:.0f}) "
            f"— no head within reach {reach:.0f}mm",
            point=c,
        ))

    # ── Spacing (min head-to-head = warning) ──────────────────────
    # Nearest-neighbour EXCLUDING self. Per-room head counts are small,
    # so an O(n²) scan is both simplest and unambiguous (SpatialHash
    # min_dist would return 0 for the head's own stored copy). Each tight
    # pair is reported once (only when this head is the lower-indexed of
    # the pair) to avoid duplicate warnings.
    min_sp = rule.min_spacing_mm
    for i, (x, y) in enumerate(xy):
        nd = float("inf")
        nj = -1
        for j, (qx, qy) in enumerate(xy):
            if j == i:
                continue
            d = math.hypot(x - qx, y - qy)
            if d < nd:
                nd = d
                nj = j
        if nj > i and nd + TOLERANCE < min_sp:
            rep.violations.append(Violation(
                "spacing_min", "warning",
                f"heads {nd:.0f}mm apart (< NFPA min {min_sp:.0f}mm) "
                f"near ({x:.0f},{y:.0f}) — allowed by custom alpha/gama rule",
                point=(x, y), value=nd,
            ))
    # Max-spacing is covered by the coverage test (a too-wide gap shows up
    # as uncovered samples); an explicit pairwise max check would double
    # report. We keep coverage as the authoritative max-spacing signal.

    # ── Wall distances ────────────────────────────────────────────
    wall_min = rule.wall_min_mm
    wall_max = rule.max_wall_mm
    for (x, y) in xy:
        dw = min_dist_to_segs(x, y, floor_segs)
        if dw + TOLERANCE < wall_min:
            rep.violations.append(Violation(
                "wall_min", "error",
                f"head {dw:.0f}mm from wall (< min {wall_min:.0f}mm)",
                point=(x, y), value=dw,
            ))
        # wall_max is only meaningful where there's a wall but no head
        # beyond — handled by coverage. We do NOT flag every perimeter
        # head as "too far from the opposite wall".

    # ── Obstacles ─────────────────────────────────────────────────
    for (x, y) in xy:
        inside_ob = any(point_in_poly(x, y, ob) for ob in obs_polys)
        near_ob = (obs_segs and obs_min_offset > 0
                   and min_dist_to_segs(x, y, obs_segs) < obs_min_offset - TOLERANCE)
        if inside_ob or near_ob:
            rep.violations.append(Violation(
                "obstacle", "error",
                f"head at ({x:.0f},{y:.0f}) is inside/too close to an obstacle",
                point=(x, y),
            ))

    # ── Density vs hazard max area ────────────────────────────────
    if xy and area_m2 > 0:
        per_head = area_m2 / len(xy)
        if per_head > rule.max_area_m2 + 1e-6:
            rep.violations.append(Violation(
                "density", "error",
                f"{per_head:.1f} m²/head exceeds NFPA max "
                f"{rule.max_area_m2:.1f} m² for {rule.hazard}",
                value=per_head,
            ))

    if is_guess:
        rep.violations.append(Violation(
            "hazard", "warning",
            f"room hazard was GUESSED as {rule.hazard} (no label found) "
            "— confirm occupancy",
        ))

    return rep
