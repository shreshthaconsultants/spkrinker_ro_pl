"""
ga_fallback.py — Step 9: minimise heads + GA fallback for hard rooms.

Two passes, both measuring coverage by the NFPA 0.75-reach rule (never the
display circle), both treating constraints as HARD (a head that breaks a
rule is never kept):

  1. minimize_heads()  — deterministic greedy prune. This is the "best
     placement" pass: a square grid is near-optimal already, so the win is
     deleting heads the layout doesn't need. For each head (densest first)
     try removing it; keep it removed only if the room still verifies with
     0 errors. Directly drives head count toward the minimum.

  2. ga_refine()       — only for rooms self-fix couldn't make pass. Wraps
     the existing GeneticOptimiser (genetic_placement.py) with reach-based
     coverage and obstacle awareness, then RE-LEGALISES the GA output
     (drop illegal heads) and runs autofix to refill any gap the GA left.
     A soft row-alignment score nudges the GA toward clean branch-line
     rows (cheaper piping = "best" in real money), without ever trading
     away coverage.

Trigger policy (decided by pipeline.py): always run pass 1 (it only ever
helps); run pass 2 only when autofix.converged is False.
"""

import math
from dataclasses import dataclass
from typing import List, Optional

from geometry import point_in_poly, min_dist_to_segs, SpatialHash, TOLERANCE
from placement import blocked_by_obstacle

from . import verifier as V
from . import autofix as F
from . import nfpa_rules as R


# ── Pass 1: greedy redundant-head removal ─────────────────────────

def _cluster_lines(heads, axis, tol=300.0):
    """Group head indices into grid lines along `axis` (0=columns share x,
    1=rows share y). Returns list of index-lists, each a near-collinear
    line, ordered by the line's shared coordinate."""
    key_axis = 1 - axis                       # the coordinate that's shared
    order = sorted(range(len(heads)), key=lambda i: heads[i][key_axis])
    lines = []
    cur = []
    cur_key = None
    for i in order:
        k = heads[i][key_axis]
        if cur_key is None or abs(k - cur_key) <= tol:
            cur.append(i)
            cur_key = k if cur_key is None else (cur_key + k) / 2.0
        else:
            lines.append(cur)
            cur = [i]
            cur_key = k
    if cur:
        lines.append(cur)
    return lines


def minimize_heads(
    floor_poly:     list,
    heads:          List[tuple],
    rule:           "R.HazardRule",
    obs_polys:      Optional[list] = None,
    obs_segs:       Optional[list] = None,
    obs_min_offset: float = 0.0,
    excl_polys:     Optional[list] = None,
    is_guess:       bool = False,
) -> List[tuple]:
    """
    Reduce head count WITHOUT raggedness: remove an entire grid row or
    column only when the room still verifies (0 errors, 100 % coverage).

    The earlier version removed individual heads greedily — it hit the
    minimum count but shredded the lattice (one row dropped to 6 of 10,
    another to 9), which reads as a messy scatter in CAD and is expensive
    to pipe. Whole-line removal keeps every surviving row full-width and
    aligned. On a tight uniform grid removing any line opens a 2× gap that
    fails coverage, so it is a clean no-op; it only fires when the grid
    genuinely has a redundant full line (e.g. an over-placed wall row).

    "Clean rows beat fewest scattered heads" — a couple of extra aligned
    heads are cheaper to install than a mathematically minimal scatter.
    """
    obs_polys = obs_polys or []
    obs_segs  = obs_segs or []
    excl_polys = excl_polys or []
    work = list(heads)
    if len(work) <= 2:
        return work

    def ok(hs):
        if not hs:
            return False
        rep = V.verify_room(
            floor_poly, hs, rule, obs_polys=obs_polys, obs_segs=obs_segs,
            obs_min_offset=obs_min_offset, excl_polys=excl_polys,
            is_guess=is_guess,
        )
        return rep.passed and rep.coverage_pct >= 100.0 - 1e-6

    # Try removing whole rows, then whole columns; repeat until a full
    # pass removes nothing. Skip the two outermost lines on each axis so
    # we never strip a wall-hugging row (those carry the perimeter).
    changed = True
    while changed and len(work) > 2:
        changed = False
        for axis in (1, 0):                    # rows first, then columns
            lines = _cluster_lines(work, axis)
            if len(lines) <= 3:
                continue                        # too few lines to thin safely
            # only interior lines are removal candidates (keep perimeter)
            for line in lines[1:-1]:
                keep = set(range(len(work))) - set(line)
                trial = [work[i] for i in keep]
                if ok(trial):
                    work = trial
                    changed = True
                    break
            if changed:
                break
    return work


# ── Pass 2: GA refine for rooms self-fix couldn't solve ───────────

def _row_alignment_score(heads, tol=150.0) -> float:
    """Fraction of heads that share a Y (or X) lattice with ≥2 others —
    higher = cleaner branch-line rows = cheaper piping. Soft objective."""
    if len(heads) < 3:
        return 1.0
    def grouped(axis):
        buckets = {}
        for h in heads:
            key = round(h[axis] / tol)
            buckets.setdefault(key, 0)
            buckets[key] += 1
        return sum(c for c in buckets.values() if c >= 3)
    return max(grouped(0), grouped(1)) / len(heads)


def ga_refine(
    floor_poly:     list,
    heads:          List[tuple],
    rule:           "R.HazardRule",
    zone_wall_segs: list,
    obs_polys:      Optional[list] = None,
    obs_segs:       Optional[list] = None,
    obs_min_offset: float = 0.0,
    excl_polys:     Optional[list] = None,
    is_guess:       bool = False,
    preset:         str = "balanced",
    seed:           Optional[int] = None,
) -> dict:
    """
    Run the GA on a stubborn room, then legalise + autofix the result.
    Coverage radius handed to the GA is the NFPA reach (so the GA's own
    coverage objective matches our compliance test). Returns a dict with
    the refined heads and before/after verifier reports.

    Falls back gracefully: if genetic_placement isn't importable, returns
    the input unchanged so the pipeline still completes.
    """
    obs_polys = obs_polys or []
    obs_segs  = obs_segs or []
    excl_polys = excl_polys or []

    before = V.verify_room(
        floor_poly, heads, rule, obs_polys=obs_polys, obs_segs=obs_segs,
        obs_min_offset=obs_min_offset, excl_polys=excl_polys, is_guess=is_guess,
    )

    try:
        from genetic_placement import GeneticOptimiser
    except ImportError:
        return {"heads": heads, "before": before, "after": before,
                "ga_ran": False, "note": "genetic_placement unavailable"}

    seed_xy = [(float(h[0]), float(h[1])) for h in heads]
    opt = GeneticOptimiser(
        floor_poly      = floor_poly,
        excl_polys      = excl_polys,
        obs_polys       = obs_polys,
        zone_wall_segs  = zone_wall_segs,
        coverage_radius = rule.reach_mm,      # NFPA reach, not display circle
        wall_min        = rule.wall_min_mm,
        space_min       = rule.min_spacing_mm,
        obs_min_offset  = obs_min_offset,
        preset          = preset,
        seed            = seed,
    )
    ga = opt.run(seed_xy)
    ga_heads = ga.best_points

    # HARD-constraint re-legalisation: drop any GA head that breaks a rule.
    floor_segs = [(floor_poly[i][0], floor_poly[i][1],
                   floor_poly[i + 1][0], floor_poly[i + 1][1])
                  for i in range(len(floor_poly) - 1)]
    legal = []
    for (x, y) in ga_heads:
        if not point_in_poly(x, y, floor_poly):
            continue
        if min_dist_to_segs(x, y, floor_segs) + TOLERANCE < rule.wall_min_mm:
            continue
        if blocked_by_obstacle(x, y, obs_polys, obs_segs, obs_min_offset):
            continue
        legal.append((x, y))

    # Refill any gap the GA + legalisation opened, then prune again.
    fixed = F.autofix_room(
        floor_poly, legal, rule, obs_polys=obs_polys, obs_segs=obs_segs,
        obs_min_offset=obs_min_offset, excl_polys=excl_polys, is_guess=is_guess,
    )
    pruned = minimize_heads(
        floor_poly, fixed.heads, rule, obs_polys=obs_polys, obs_segs=obs_segs,
        obs_min_offset=obs_min_offset, excl_polys=excl_polys, is_guess=is_guess,
    )
    after = V.verify_room(
        floor_poly, pruned, rule, obs_polys=obs_polys, obs_segs=obs_segs,
        obs_min_offset=obs_min_offset, excl_polys=excl_polys, is_guess=is_guess,
    )

    # Only accept the GA result if it's compliant AND not worse on count.
    if after.passed and len(pruned) <= before.head_count:
        return {"heads": pruned, "before": before, "after": after,
                "ga_ran": True, "row_score": round(_row_alignment_score(pruned), 3)}
    return {"heads": heads, "before": before, "after": before,
            "ga_ran": True, "note": "GA result rejected (not better)",
            "row_score": round(_row_alignment_score(heads), 3)}
