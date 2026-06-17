"""
autofix.py — Step 6: the self-fix loop (closes the human loop).

    while errors and iterations < N:
        for each error → apply its fix
        re-verify

Fix table (errors only — warnings like NFPA min-spacing are left alone
per the project decision that the custom alpha/gama rules win):

  coverage             → insert a head at the gap centroid (nudged inside
                         if the raw centroid is too close to a wall/obstacle)
  obstacle             → delete the offending head (it's inside/hugging an
                         obstacle; the gap it leaves is fixed next round by
                         a coverage insert in a legal spot)
  obstruction_clearance→ delete the head (3× rule) — same follow-up
  obstruction_wide     → add a head at the suggested far-side position
  wall_min             → pull the head inward to wall_min along the inward
                         normal; delete if that can't be made legal
  density              → handled implicitly: too few heads shows up as
                         coverage gaps, which insert heads

Convergence: stops when 0 errors or after `max_iters`. A room that can't
reach 0 errors is returned with its residual report so the pipeline flags
it for a human — the ONLY thing a human ever looks at.
"""

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from geometry import (
    point_in_poly, poly_centroid, min_dist_to_segs, SpatialHash, TOLERANCE,
)
from placement import blocked_by_obstacle

from . import verifier as V
from . import obstructions as OB
from . import nfpa_rules as R


@dataclass
class FixResult:
    heads:        List[tuple]
    report:       "V.VerifyReport"
    iterations:   int
    converged:    bool
    inserted:     int = 0
    deleted:      int = 0
    moved:        int = 0

    def to_dict(self) -> dict:
        return {
            "iterations": self.iterations,
            "converged":  self.converged,
            "inserted":   self.inserted,
            "deleted":    self.deleted,
            "moved":      self.moved,
            "final":      self.report.to_dict(),
        }


def _legal(pt, floor_poly, floor_segs, rule, obs_polys, obs_segs, obs_min_offset):
    """Can a head sit here? inside floor, ≥ wall_min, off obstacles."""
    x, y = pt
    if not point_in_poly(x, y, floor_poly):
        return False
    if min_dist_to_segs(x, y, floor_segs) + TOLERANCE < rule.wall_min_mm:
        return False
    if blocked_by_obstacle(x, y, obs_polys, obs_segs, obs_min_offset):
        return False
    return True


def _nudge_inside(pt, floor_poly, floor_segs, rule, obs_polys, obs_segs,
                  obs_min_offset, max_steps=12):
    """Walk a point toward the floor centroid until it becomes legal."""
    if _legal(pt, floor_poly, floor_segs, rule, obs_polys, obs_segs, obs_min_offset):
        return pt
    cx, cy = poly_centroid(floor_poly)
    x, y = pt
    vx, vy = cx - x, cy - y
    vlen = math.hypot(vx, vy)
    if vlen < 1e-6:
        return None
    vx, vy = vx / vlen, vy / vlen
    step = max(rule.wall_min_mm, 300.0)
    for s in range(1, max_steps + 1):
        cand = (x + vx * step * s, y + vy * step * s)
        if _legal(cand, floor_poly, floor_segs, rule, obs_polys, obs_segs, obs_min_offset):
            return cand
    return None


def autofix_room(
    floor_poly:     list,
    heads:          List[tuple],
    rule:           "R.HazardRule",
    room_index:     int = 0,
    obs_polys:      Optional[list] = None,
    obs_segs:       Optional[list] = None,
    obs_min_offset: float = 0.0,
    excl_polys:     Optional[list] = None,
    is_guess:       bool = False,
    max_iters:      int = 8,
    dedup_radius:   float = 900.0,
) -> FixResult:
    """
    Iteratively repair a room's layout until it has zero ERROR-severity
    violations (or max_iters is hit). Heads carry their original rotation
    where present; inserted heads inherit the room's dominant rotation.
    """
    obs_polys = obs_polys or []
    obs_segs  = obs_segs or []
    excl_polys = excl_polys or []
    floor_segs = [(floor_poly[i][0], floor_poly[i][1],
                   floor_poly[i + 1][0], floor_poly[i + 1][1])
                  for i in range(len(floor_poly) - 1)]

    # Preserve a rotation to stamp on inserted heads (most common value).
    rot = 0.0
    if heads and len(heads[0]) >= 3:
        rots = {}
        for h in heads:
            r = h[2] if len(h) >= 3 else 0.0
            rots[r] = rots.get(r, 0) + 1
        rot = max(rots, key=rots.get)

    work = [(float(h[0]), float(h[1])) for h in heads]
    inserted = deleted = moved = 0

    def reverify():
        rep = V.verify_room(
            floor_poly, work, rule, room_index=room_index,
            obs_polys=obs_polys, obs_segs=obs_segs,
            obs_min_offset=obs_min_offset, excl_polys=excl_polys,
            is_guess=is_guess,
        )
        ob_v, ob_sugg = OB.check_obstructions(work, obs_polys, rule.reach_mm)
        # fold obstruction violations into the report
        rep.violations.extend(ob_v)
        return rep, ob_sugg

    rep, ob_sugg = reverify()
    iters = 0

    def _far_enough(pt):
        for (qx, qy) in work:
            if (pt[0] - qx) ** 2 + (pt[1] - qy) ** 2 < dedup_radius * dedup_radius:
                return False
        return True

    while rep.errors and iters < max_iters:
        iters += 1
        # Index current heads for delete-by-point.
        def _find(pt):
            best, bi = float("inf"), -1
            for i, (qx, qy) in enumerate(work):
                d = (pt[0] - qx) ** 2 + (pt[1] - qy) ** 2
                if d < best:
                    best, bi = d, i
            return bi

        # Collect actions; apply inserts after deletes so indices stay valid.
        to_delete = set()
        to_insert: List[tuple] = []

        for v in rep.errors:
            if v.kind in ("obstacle", "obstruction_clearance", "wall_min"):
                if v.point is not None:
                    bi = _find(v.point)
                    if v.kind == "wall_min":
                        # try to move it inward; fall back to delete
                        nl = _nudge_inside(v.point, floor_poly, floor_segs, rule,
                                           obs_polys, obs_segs, obs_min_offset)
                        if nl is not None and _far_enough(nl):
                            if bi >= 0:
                                work[bi] = nl
                                moved += 1
                            continue
                    if bi >= 0:
                        to_delete.add(bi)
            elif v.kind in ("coverage", "obstruction_wide"):
                if v.point is not None:
                    nl = _nudge_inside(v.point, floor_poly, floor_segs, rule,
                                       obs_polys, obs_segs, obs_min_offset)
                    if nl is not None and _far_enough(nl):
                        to_insert.append(nl)
            # density: rely on coverage inserts

        if not to_delete and not to_insert:
            break  # nothing actionable — escape, report residual

        if to_delete:
            work = [p for i, p in enumerate(work) if i not in to_delete]
            deleted += len(to_delete)
        for pt in to_insert:
            if _far_enough(pt) and _legal(pt, floor_poly, floor_segs, rule,
                                          obs_polys, obs_segs, obs_min_offset):
                work.append(pt)
                inserted += 1

        rep, ob_sugg = reverify()

    out_heads = [(round(x, 3), round(y, 3), rot) for (x, y) in work]
    return FixResult(
        heads=out_heads, report=rep, iterations=iters,
        converged=rep.passed, inserted=inserted, deleted=deleted, moved=moved,
    )
