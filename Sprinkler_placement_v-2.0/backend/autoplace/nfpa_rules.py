"""
nfpa_rules.py — Step 5: NFPA-13 rules engine.

Single source of truth for the numbers that drive BOTH placement and
verification. The v1 SCENARIOS table (placement.py) hard-coded spacing per
preset; here the numbers are DERIVED from a hazard class + ceiling context,
so the verifier (verifier.py) and the placer check against exactly the same
limits.

Units: millimetres and m². Values converted from NFPA-13 (2019/2022
protection-area & spacing chapters) for standard upright/pendent spray
heads on flat ceilings. ALWAYS confirm against the edition your AHJ
enforces before stamping a real design — this table is for the engine,
not a legal substitute.

Key NFPA facts encoded here:
  * Max protection area per head and max S×L spacing per hazard class.
  * Min head-to-head spacing 1800 mm (the "6 ft rule") — advisory in v2:
    the user's custom alpha/gama formulas win, the verifier only WARNS.
  * Max head-to-wall = ½ × allowed spacing; min head-to-wall = 102 mm.
  * Coverage compliance is the S×L rectangle / the 0.75-spacing reach
    rule — NOT the r=1500 display circle (which leaves diagonal pockets
    at pitch = 2r and would make every square grid look non-compliant).
"""

from dataclasses import dataclass, field
from typing import Dict


# ── Hazard classes ────────────────────────────────────────────────

LIGHT       = "light"
ORDINARY_1  = "ordinary_1"
ORDINARY_2  = "ordinary_2"
EXTRA_1     = "extra_1"
EXTRA_2     = "extra_2"
STORAGE     = "storage"

HAZARD_ORDER = [LIGHT, ORDINARY_1, ORDINARY_2, EXTRA_1, EXTRA_2, STORAGE]


# ── Universal limits (all hazard classes) ─────────────────────────

# Min head-to-head spacing (NFPA "6 ft rule"). Advisory in v2 — the
# verifier emits a WARNING, never auto-moves a head, because the user's
# alpha/gama formulas legitimately land bays slightly under this.
MIN_HEAD_SPACING_MM = 1800.0

# Min head-to-wall (NFPA 4 in). Hard rule.
MIN_WALL_MM = 102.0

# Below this floor we treat a coverage sample as "reached" by a head if
# the head is within REACH_FACTOR × max_spacing of it (the 0.75 rule).
REACH_FACTOR = 0.75


@dataclass(frozen=True)
class HazardRule:
    """Resolved NFPA limits for one hazard class (flat ceiling)."""
    hazard:          str
    label:           str
    max_area_m2:     float   # max protection area per head
    max_spacing_mm:  float   # max S or L (centre-to-centre)
    min_spacing_mm:  float   # NFPA min head-to-head (advisory)
    wall_min_mm:     float   # min head-to-wall (hard)
    coverage_radius_mm: float  # display/coverage circle radius for stats
    pitch_mm:        float   # FIXED head-to-head pitch used for placement
                             # (exact, leftover goes to the walls — no
                             # equal-bay drift). Must be ≤ the area- and
                             # spacing-limited maximum for the class.

    @property
    def max_wall_mm(self) -> float:
        """NFPA max head-to-wall = ½ × allowed spacing."""
        return 0.5 * self.max_spacing_mm

    @property
    def reach_mm(self) -> float:
        """0.75-rule reach: every floor point must be within this of a head."""
        return REACH_FACTOR * self.max_spacing_mm

    def to_dict(self) -> dict:
        return {
            "hazard":             self.hazard,
            "label":              self.label,
            "max_area_m2":        self.max_area_m2,
            "max_spacing_mm":     self.max_spacing_mm,
            "min_spacing_mm":     self.min_spacing_mm,
            "wall_min_mm":        self.wall_min_mm,
            "max_wall_mm":        self.max_wall_mm,
            "reach_mm":           round(self.reach_mm, 1),
            "coverage_radius_mm": self.coverage_radius_mm,
            "pitch_mm":           self.pitch_mm,
        }


# ── The NFPA-13 protection-area / spacing table ───────────────────
#
# max_area_m2 / max_spacing_mm straight from the standard; coverage_radius
# is the visualization circle (kept ≈ historical 1500 for Light/Ordinary
# so the drawn symbol matches v1, smaller for tighter classes).

# pitch_mm is the FIXED placement pitch per class — a round value at or
# below the area/spacing maximum so head-to-head spacing is exact (no
# equal-bay drift). Ordinary = 3000 (the user's "fixed 3000" rule);
# Light is wider (light hazard), Extra/Storage tighter (high hazard).
_RULES: Dict[str, HazardRule] = {
    LIGHT: HazardRule(
        LIGHT, "Light hazard (office / lobby / corridor / school)",
        max_area_m2=20.9, max_spacing_mm=4600.0, min_spacing_mm=MIN_HEAD_SPACING_MM,
        wall_min_mm=MIN_WALL_MM, coverage_radius_mm=1500.0, pitch_mm=4000.0,
    ),
    ORDINARY_1: HazardRule(
        ORDINARY_1, "Ordinary hazard I (parking / laundry / restaurant)",
        max_area_m2=12.1, max_spacing_mm=4600.0, min_spacing_mm=MIN_HEAD_SPACING_MM,
        wall_min_mm=MIN_WALL_MM, coverage_radius_mm=1500.0, pitch_mm=3000.0,
    ),
    ORDINARY_2: HazardRule(
        ORDINARY_2, "Ordinary hazard II (retail / workshop / mill)",
        max_area_m2=12.1, max_spacing_mm=4600.0, min_spacing_mm=MIN_HEAD_SPACING_MM,
        wall_min_mm=MIN_WALL_MM, coverage_radius_mm=1500.0, pitch_mm=3000.0,
    ),
    EXTRA_1: HazardRule(
        EXTRA_1, "Extra hazard I (paint / processing)",
        max_area_m2=9.3, max_spacing_mm=3700.0, min_spacing_mm=MIN_HEAD_SPACING_MM,
        wall_min_mm=MIN_WALL_MM, coverage_radius_mm=1400.0, pitch_mm=2500.0,
    ),
    EXTRA_2: HazardRule(
        EXTRA_2, "Extra hazard II (plastics / flammable processing)",
        max_area_m2=9.3, max_spacing_mm=3700.0, min_spacing_mm=MIN_HEAD_SPACING_MM,
        wall_min_mm=MIN_WALL_MM, coverage_radius_mm=1400.0, pitch_mm=2500.0,
    ),
    STORAGE: HazardRule(
        STORAGE, "High-piled storage",
        max_area_m2=9.3, max_spacing_mm=3700.0, min_spacing_mm=MIN_HEAD_SPACING_MM,
        wall_min_mm=MIN_WALL_MM, coverage_radius_mm=1400.0, pitch_mm=2500.0,
    ),
}


def rule_for(hazard: str) -> HazardRule:
    """Resolve a hazard class to its NFPA limits. Unknown → safest (Light
    is the most permissive spacing, so 'safest' for protection means the
    TIGHTEST = storage; callers that want the conservative default should
    pass STORAGE explicitly. Here unknown maps to ORDINARY_2 as a sane
    middle ground, and the caller is expected to flag it)."""
    return _RULES.get(hazard, _RULES[ORDINARY_2])


def conservative_rule() -> HazardRule:
    """Tightest spacing — used when a building has any unknown room and we
    want a single safe class for the whole job."""
    return _RULES[STORAGE]


# ── Spacing solver: hazard → exact pitch for a room dimension ─────

def fit_pitch(dimension_mm: float, rule: HazardRule) -> dict:
    """
    Choose a head pitch ≤ rule.max_spacing_mm that divides `dimension_mm`
    into equal bays, mirroring v1's fit_spacing but bounded by NFPA.

    Returns {n, pitch_mm, valid}. `valid` is False when even a single bay
    exceeds the max (room longer than one max-spacing with no interior
    head) — caller should still place the centered head.
    """
    dim = float(dimension_mm)
    if dim <= 0:
        return {"n": 1, "pitch_mm": rule.max_spacing_mm, "valid": False}
    import math
    n = max(1, int(math.ceil(dim / rule.max_spacing_mm)))
    pitch = dim / n
    return {"n": int(n), "pitch_mm": round(pitch, 3),
            "valid": bool(pitch <= rule.max_spacing_mm + 1e-6)}


def scenario_from_rule(rule: HazardRule, scenario_id: int = 100) -> dict:
    """
    Build a v1-style SCENARIOS dict from an NFPA rule so the existing
    run_scenario_for_floors / generate_zone_sprinklers engine can consume
    it unchanged.

    Uses a FIXED pitch (space_min == space_max == rule.pitch_mm). The
    placement engine has a dedicated branch for that case: it honours the
    EXACT head-to-head pitch and pushes the leftover to the walls, instead
    of fit_spacing's equal-bay division (which drifts, e.g. landing 3200
    when the user expects 3000). The alpha/gama/new-row rules still shape
    the wall edges; only the interior pitch is locked.
    """
    pitch = float(rule.pitch_mm)
    return {
        "id":              scenario_id,
        "name":            rule.label,
        "description":     f"NFPA {rule.hazard}: fixed {pitch:.0f}mm, "
                           f"≤{rule.max_area_m2}m²/head",
        "space_min":       int(pitch),
        "space_max":       int(pitch),
        "wall_min":        int(round(rule.max_wall_mm * 0.55)),
        "wall_max":        int(round(rule.max_wall_mm)),
        "coverage_radius": rule.coverage_radius_mm,
        "hazard":          rule.hazard,
        "nfpa":            rule.to_dict(),
    }


def all_rules() -> Dict[str, dict]:
    """Serializable copy of the full table (for reports / API)."""
    return {k: v.to_dict() for k, v in _RULES.items()}
