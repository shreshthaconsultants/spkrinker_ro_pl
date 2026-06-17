"""NFPA-13 design constants and derived values (all distances in mm, areas in mm^2)."""

import math

HAZARDS: dict[str, dict[str, float]] = {
    "Light":    {"max_area": 20_900_000.0, "max_spacing": 4600.0},
    "Ordinary": {"max_area": 12_100_000.0, "max_spacing": 4600.0},
    "Extra":    {"max_area": 9_300_000.0,  "max_spacing": 3700.0},
}

MIN_HEAD_SPACING = 1800.0   # minimum distance between any two heads, mm
MIN_WALL_DIST = 100.0       # minimum clearance from a head to any wall, mm
COVERAGE_FACTOR = 0.5 ** 0.5  # ~0.707; coverage circle radius = COVERAGE_FACTOR * S
                              # (exact sqrt(2)/2 so an S x S grid cell's centre
                              # sits exactly on, not just outside, the circle)

#: Spacing used when /route is called without a hazard class and the head
#: geometry is too sparse to infer one (single head).
DEFAULT_SPACING = 3479.0    # ~= Ordinary hazard spacing


def spacing_for(hazard: str) -> float:
    """Design spacing S = min(max_spacing, sqrt(max_area)) for the hazard class."""
    h = HAZARDS[hazard]
    return min(h["max_spacing"], math.sqrt(h["max_area"]))


def coverage_radius(hazard: str) -> float:
    """Drawn coverage circle radius for one head."""
    return COVERAGE_FACTOR * spacing_for(hazard)
