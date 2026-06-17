"""
pipeline.py — Step 10: orchestration + the building report.

One call turns rooms (+ labels + obstacles) into a compliant, minimal,
verified layout with ZERO human touches in the happy path:

    classify → place → verify → self-fix → minimise → GA(if stuck) → report

Per room:
  1. classify hazard (classify.py) → NFPA rule (nfpa_rules.py)
  2. derive a v1 scenario from the rule and place with the existing
     orientation-aware grid engine (placement.generate_zone_sprinklers_oriented
     — alpha/gama/pull/obstacle logic all intact)
  3. verify (verifier.py); if errors, self-fix (autofix.py)
  4. minimise heads (ga_fallback.minimize_heads) — the "best" pass
  5. if self-fix couldn't converge, GA refine (ga_fallback.ga_refine)

Cross-room spacing is shared (a running all_placed list) so heads near a
shared wall don't collide. The building report is the deliverable a human
scans: per-room pass/fail, coverage, head count, and every residual
warning (guessed hazards, NFPA min-spacing notes).

`autoplace_building()` returns a structured result; `feedback_log` records
the run for the Step-10 flywheel (intervention-rate tracking + regression
seeding).
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict

from placement import (
    generate_zone_sprinklers_oriented, build_zone_wall_segs, poly_to_segs,
)

from . import classify as C
from . import nfpa_rules as R
from . import verifier as V
from . import autofix as F
from . import ga_fallback as G


@dataclass
class RoomResult:
    room_index: int
    hazard:     str
    heads:      List[tuple]            # (x, y, rot)
    report:     "V.VerifyReport"
    fixed:      bool                   # self-fix or GA was applied
    converged:  bool
    pruned_from: int = 0               # head count before minimise
    ga_used:    bool = False

    def to_dict(self) -> dict:
        return {
            "room_index": self.room_index,
            "hazard":     self.hazard,
            "head_count": len(self.heads),
            "pruned_from": self.pruned_from,
            "converged":  self.converged,
            "ga_used":    self.ga_used,
            "report":     self.report.to_dict(),
        }


@dataclass
class BuildingResult:
    rooms:       List[RoomResult]
    total_heads: int
    all_passed:  bool
    flagged:     List[int]             # room indices needing human review

    @property
    def coverage_pct(self) -> float:
        """Area-weighted coverage across all rooms (the headline number)."""
        tot_area = sum(r.report.floor_area_m2 for r in self.rooms)
        if tot_area <= 0:
            return 0.0
        return sum(r.report.coverage_pct * r.report.floor_area_m2
                   for r in self.rooms) / tot_area

    def to_dict(self) -> dict:
        return {
            "total_heads": self.total_heads,
            "all_passed":  self.all_passed,
            "coverage_pct": round(self.coverage_pct, 1),
            "room_count":  len(self.rooms),
            "flagged_rooms": self.flagged,
            "rooms":       [r.to_dict() for r in self.rooms],
        }

    def summary_lines(self) -> List[str]:
        """Human-readable report lines (printed by the CLI / plugin)."""
        out = [
            f"VALIDATION: {'PASS' if self.all_passed else 'REVIEW NEEDED'}",
            f"  rooms: {len(self.rooms)}   heads: {self.total_heads}",
        ]
        if self.flagged:
            out.append(f"  rooms needing review: {self.flagged}")
        for r in self.rooms:
            tag = "ok " if r.report.passed else "ERR"
            extra = []
            if r.report.errors:
                extra.append(f"{len(r.report.errors)} errors")
            if r.report.warnings:
                extra.append(f"{len(r.report.warnings)} warn")
            if r.pruned_from and r.pruned_from != len(r.heads):
                extra.append(f"pruned {r.pruned_from}->{len(r.heads)}")
            if r.ga_used:
                extra.append("GA")
            out.append(
                f"  [{tag}] room {r.room_index} {r.hazard:11s} "
                f"{len(r.heads):3d} heads  cov {r.report.coverage_pct:5.1f}%  "
                + (", ".join(extra) if extra else "")
            )
        return out


def autoplace_building(
    room_polys:     List[list],
    labels:         Optional[List[tuple]] = None,
    obs_polys:      Optional[list] = None,
    obs_min_offset: float = 150.0,
    excl_polys:     Optional[list] = None,
    default_hazard: str = R.ORDINARY_2,
    force_conservative_if_any_unknown: bool = False,
    tilted:         bool = False,
    minimise:       bool = False,
    enable_ga:      bool = True,
) -> BuildingResult:
    """
    Full universal-model pass over a building. See module docstring.

    labels: (x, y, text) TEXT/MTEXT used for hazard classification. If
    None/empty every room is a guess at `default_hazard`.

    minimise (default False): the placement grid is already uniform and
    compliant; minimisation reduces head count by dropping whole grid
    lines, which WIDENS spacing toward the NFPA maximum and makes the
    drawn coverage circles stop overlapping. Off by default so the shipped
    layout keeps the familiar dense, fully-overlapping grid (no
    raggedness). Turn on only when the goal is the fewest compliant heads
    and the sparser look is acceptable.
    """
    labels = labels or []
    obs_polys = obs_polys or []
    excl_polys = excl_polys or []
    obs_segs = []
    for ob in obs_polys:
        obs_segs.extend(poly_to_segs(ob))

    classes = C.classify_rooms(
        room_polys, labels, default_hazard=default_hazard,
        force_conservative_if_any_unknown=force_conservative_if_any_unknown,
    )

    rooms: List[RoomResult] = []
    all_placed: List[tuple] = []        # (x,y) shared spacing across rooms

    for rc in classes:
        poly = room_polys[rc.room_index]
        rule = rc.rule
        sc = R.scenario_from_rule(rule)

        # ── 1. place with the orientation-aware grid engine ──
        zr = generate_zone_sprinklers_oriented(
            floor_poly      = poly,
            obs_polys       = obs_polys,
            obs_min_offset  = float(obs_min_offset),
            all_placed      = all_placed,
            wall_min        = sc["wall_min"],
            wall_max        = sc["wall_max"],
            space_min       = sc["space_min"],
            space_max       = sc["space_max"],
            coverage_radius = sc["coverage_radius"],
            enable_gap_fill = False,
            tilted          = tilted,
        )
        rot = float(zr.get("angle", 0.0))
        heads = [(p[0], p[1], rot) for p in zr["points"]]

        # ── 2. verify ──
        rep = V.verify_room(
            poly, heads, rule, room_index=rc.room_index,
            obs_polys=obs_polys, obs_segs=obs_segs,
            obs_min_offset=obs_min_offset, excl_polys=excl_polys,
            is_guess=rc.is_guess,
        )
        was_fixed = False
        converged = rep.passed
        ga_used = False

        # ── 3. self-fix if needed ──
        if not rep.passed:
            fr = F.autofix_room(
                poly, heads, rule, room_index=rc.room_index,
                obs_polys=obs_polys, obs_segs=obs_segs,
                obs_min_offset=obs_min_offset, excl_polys=excl_polys,
                is_guess=rc.is_guess,
            )
            heads, rep, converged = fr.heads, fr.report, fr.converged
            was_fixed = True

            # ── 5. GA for rooms self-fix couldn't solve ──
            if not converged and enable_ga:
                zone_wall_segs = build_zone_wall_segs(poly, [], [])
                gout = G.ga_refine(
                    poly, heads, rule, zone_wall_segs,
                    obs_polys=obs_polys, obs_segs=obs_segs,
                    obs_min_offset=obs_min_offset, excl_polys=excl_polys,
                    is_guess=rc.is_guess,
                )
                if gout.get("ga_ran") and gout["after"].passed:
                    heads = [(h[0], h[1], rot) for h in gout["heads"]]
                    rep = gout["after"]
                    converged = True
                    ga_used = True

        # ── 4. minimise heads (the "best" pass) ──
        pruned_from = len(heads)
        if minimise and rep.passed:
            pruned = G.minimize_heads(
                poly, heads, rule, obs_polys=obs_polys, obs_segs=obs_segs,
                obs_min_offset=obs_min_offset, excl_polys=excl_polys,
                is_guess=rc.is_guess,
            )
            if len(pruned) < len(heads):
                heads = [(h[0], h[1], h[2] if len(h) >= 3 else rot) for h in pruned]
                rep = V.verify_room(
                    poly, heads, rule, room_index=rc.room_index,
                    obs_polys=obs_polys, obs_segs=obs_segs,
                    obs_min_offset=obs_min_offset, excl_polys=excl_polys,
                    is_guess=rc.is_guess,
                )

        for (x, y, _r) in heads:
            all_placed.append((x, y))

        rooms.append(RoomResult(
            room_index=rc.room_index, hazard=rule.hazard, heads=heads,
            report=rep, fixed=was_fixed, converged=converged,
            pruned_from=pruned_from, ga_used=ga_used,
        ))

    total = sum(len(r.heads) for r in rooms)
    flagged = [r.room_index for r in rooms
               if (not r.report.passed) or r.report.warnings]
    all_passed = all(r.report.passed for r in rooms)
    return BuildingResult(rooms=rooms, total_heads=total,
                          all_passed=all_passed, flagged=flagged)
