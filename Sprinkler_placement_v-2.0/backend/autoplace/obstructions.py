"""
obstructions.py — Step 7: NFPA obstruction rules (2D subset).

Two enforceable-in-plan rules:

  1. Three-times rule (columns / free-standing obstructions): a sprinkler
     positioned too close to an obstruction has its spray shadowed. NFPA
     wants the head ≥ 3× the obstruction's max dimension away, capped at
     610 mm (above that the obstruction is wide enough to need its own
     coverage, rule 2). We ENFORCE the offset at placement time via
     obs_min_offset; here we VERIFY it and flag heads that violate it.

  2. Wide-obstruction rule: an obstruction ≥ 1200 mm across (a duct,
     platform, big column cap) blocks spray to the floor beneath/behind
     it. NFPA requires additional sprinklers below or on the far side.
     In plan we can't place inside the obstacle, so we check that heads
     exist on opposite sides within reach; if a side is bare we emit a
     suggested head position (offset just outside the obstruction on the
     bare side) for the self-fix loop to add.

Beam / deflector obstruction rules need ceiling elevation data (deflector
height vs bottom-of-beam) and are STUBBED — flagged for engineer review
when beam layers are supplied. This module is honest about being a 2D
subset: it does not model spray cones in 3D.

All thresholds are config-style constants at the top so they are easy to
tune per AHJ.
"""

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from geometry import bbox, point_in_poly, poly_centroid, min_dist_to_segs, poly_to_segs

from .verifier import Violation


# ── Tunable thresholds (mm) ───────────────────────────────────────
THREE_TIMES_FACTOR   = 3.0      # head must clear 3× obstruction dimension
THREE_TIMES_CAP      = 610.0    # …capped at 610 mm (NFPA 24 in)
WIDE_OBSTRUCTION_MM  = 1200.0   # at/over this an obstruction needs heads
                                # covering each side (4 ft rule of thumb)
FAR_SIDE_OFFSET_MM   = 700.0    # where to suggest a head outside a bare side


def obstruction_dims(obs_poly: list) -> Tuple[float, float]:
    """(max_dim, min_dim) of an obstruction's bbox."""
    minx, maxx, miny, maxy = bbox(obs_poly)
    w, h = maxx - minx, maxy - miny
    return (max(w, h), min(w, h))


def required_clearance(obs_poly: list) -> float:
    """Three-times clearance a head must keep from this obstruction,
    capped at THREE_TIMES_CAP. Based on the SMALLER dimension (a thin
    column needs less clearance than its long face would imply)."""
    _, small = obstruction_dims(obs_poly)
    return min(THREE_TIMES_FACTOR * small, THREE_TIMES_CAP)


# ── Verification ──────────────────────────────────────────────────

def check_obstructions(
    heads:      List[tuple],
    obs_polys:  List[list],
    reach_mm:   float,
    beam_segs:  Optional[list] = None,
) -> Tuple[List[Violation], List[tuple]]:
    """
    Verify obstruction rules for one room. Returns (violations, suggestions)
    where `suggestions` is a list of (x, y) head positions the self-fix
    loop may add to cover a bare side of a wide obstruction.

    reach_mm: the NFPA 0.75-reach (from the hazard rule) — a side counts
    as "covered" if a head sits within reach of the obstruction edge on
    that side.
    """
    violations: List[Violation] = []
    suggestions: List[tuple] = []
    xy = [(float(h[0]), float(h[1])) for h in heads]

    for ob in obs_polys:
        ob_segs = poly_to_segs(ob)
        max_dim, _ = obstruction_dims(ob)
        clear = required_clearance(ob)

        # Rule 1 — three-times clearance (verify; placement enforces it).
        for (x, y) in xy:
            if point_in_poly(x, y, ob):
                continue  # already an 'obstacle' error in verifier
            d = min_dist_to_segs(x, y, ob_segs)
            if d + 1e-6 < clear:
                violations.append(Violation(
                    "obstruction_clearance", "error",
                    f"head {d:.0f}mm from obstruction (needs 3× rule "
                    f"clearance {clear:.0f}mm)",
                    point=(x, y), value=d,
                ))

        # Rule 2 — wide obstruction needs coverage on each side.
        if max_dim >= WIDE_OBSTRUCTION_MM:
            cx, cy = poly_centroid(ob)
            minx, maxx, miny, maxy = bbox(ob)
            # Four cardinal "sides": the point just outside each edge.
            sides = {
                "left":  (minx - FAR_SIDE_OFFSET_MM, cy),
                "right": (maxx + FAR_SIDE_OFFSET_MM, cy),
                "bottom": (cx, miny - FAR_SIDE_OFFSET_MM),
                "top":    (cx, maxy + FAR_SIDE_OFFSET_MM),
            }
            for name, (sx, sy) in sides.items():
                covered = any(math.hypot(sx - hx, sy - hy) <= reach_mm
                              for (hx, hy) in xy)
                if not covered:
                    violations.append(Violation(
                        "obstruction_wide", "error",
                        f"wide obstruction ({max_dim:.0f}mm) has no head "
                        f"covering its {name} side near ({sx:.0f},{sy:.0f})",
                        point=(sx, sy),
                    ))
                    suggestions.append((sx, sy))

    # Beam rule — needs ceiling elevations; flag only.
    if beam_segs:
        violations.append(Violation(
            "beam", "warning",
            "beam layer supplied — 3D beam/deflector rule not modelled in "
            "2D; engineer must verify deflector heights",
        ))

    return violations, suggestions
