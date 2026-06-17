"""
classify.py — Step 4: hazard auto-classification.

Reads TEXT / MTEXT labels that fall inside each room polygon and maps the
label keywords to an NFPA hazard class (nfpa_rules). This replaces the
plugin's manual scenario prompt — each room gets the spacing/coverage its
occupancy actually requires.

Design:
  * Pure-geometry label→room assignment (point_in_poly) so it works with
    labels collected by dxf_loader OR sent as plain (x, y, text) tuples
    from the plugin. No ezdxf dependency here.
  * Keyword table is ordered by hazard severity; the FIRST matching
    keyword wins, and on ties the more hazardous class wins (safer).
  * Unknown / unlabelled room → ORDINARY_2 (a sane middle) with a flag so
    the verifier/report surfaces it for human confirmation. A building
    with ANY unknown can optionally be forced to the conservative class.
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from geometry import point_in_poly, poly_area, poly_centroid

from . import nfpa_rules as R


# ── Keyword → hazard table (checked most-hazardous first) ─────────
# Each entry: (hazard_class, [regex word fragments]). Word-boundary
# matched, case-insensitive. Order matters only for documentation; the
# resolver scores every class and picks the most hazardous that hits.

_KEYWORDS = [
    (R.STORAGE,    ["warehouse", "storage", "store room", "stockroom",
                    "godown", "rack", "cold storage"]),
    (R.EXTRA_2,    ["paint", "solvent", "flammable", "plastics", "chemical",
                    "spray booth", "fuel"]),
    (R.EXTRA_1,    ["workshop", "process", "plant room", "boiler",
                    "generator", "transformer", "mechanical room", "mep"]),
    (R.ORDINARY_2, ["retail", "shop", "showroom", "sales", "mall",
                    "kitchen", "restaurant", "cafeteria", "canteen",
                    "laundry", "garage", "workshop"]),
    (R.ORDINARY_1, ["parking", "car park", "store", "stair", "service",
                    "utility", "pump", "loading", "dining"]),
    (R.LIGHT,      ["office", "lobby", "reception", "corridor", "passage",
                    "hall", "room", "bedroom", "living", "class",
                    "library", "ward", "clinic", "toilet", "wc", "bath",
                    "meeting", "conference", "cabin", "lounge", "study"]),
]

# Severity rank for tie-breaking (higher = more hazardous = wins).
_SEVERITY = {h: i for i, h in enumerate(R.HAZARD_ORDER)}


@dataclass
class RoomClass:
    """Classification result for one room polygon."""
    room_index:  int
    hazard:      str
    label_text:  Optional[str]      # the winning label, or None
    matched_kw:  Optional[str]      # the keyword that matched, or None
    is_guess:    bool               # True when no label matched (defaulted)
    rule:        "R.HazardRule" = field(repr=False, default=None)

    def to_dict(self) -> dict:
        return {
            "room_index": self.room_index,
            "hazard":     self.hazard,
            "label_text": self.label_text,
            "matched_kw": self.matched_kw,
            "is_guess":   self.is_guess,
            "nfpa":       self.rule.to_dict() if self.rule else None,
        }


def _hazard_for_text(text: str) -> Optional[Tuple[str, str]]:
    """Return (hazard, matched_keyword) for a label, or None if no match.
    Most-hazardous class wins when several keyword groups match."""
    if not text:
        return None
    low = text.lower()
    best = None
    for hazard, words in _KEYWORDS:
        for w in words:
            if re.search(r"\b" + re.escape(w), low):
                if best is None or _SEVERITY[hazard] > _SEVERITY[best[0]]:
                    best = (hazard, w)
                break
    return best


def classify_rooms(
    room_polys: List[list],
    labels:     List[tuple],
    default_hazard: str = R.ORDINARY_2,
    force_conservative_if_any_unknown: bool = False,
) -> List[RoomClass]:
    """
    Assign a hazard class to every room polygon.

    room_polys : list of closed polygons [(x,y), ...].
    labels     : list of (x, y, text) — TEXT/MTEXT insertion points + string.
    default_hazard : used when a room contains no recognisable label.
    force_conservative_if_any_unknown : if True and ANY room is a guess,
        the WHOLE building is bumped to the tightest class (storage). Use
        for life-safety-critical jobs where mixed certainty isn't allowed.

    Returns one RoomClass per room (same order as room_polys). When two
    labels fall in one room, the more hazardous classification wins.
    """
    results: List[RoomClass] = []

    for ri, poly in enumerate(room_polys):
        best_haz = None
        best_text = None
        best_kw = None
        for (lx, ly, text) in labels:
            if not point_in_poly(lx, ly, poly):
                continue
            hit = _hazard_for_text(text)
            if hit is None:
                continue
            hz, kw = hit
            if best_haz is None or _SEVERITY[hz] > _SEVERITY[best_haz]:
                best_haz, best_text, best_kw = hz, text, kw

        if best_haz is None:
            results.append(RoomClass(
                room_index=ri, hazard=default_hazard, label_text=None,
                matched_kw=None, is_guess=True, rule=R.rule_for(default_hazard),
            ))
        else:
            results.append(RoomClass(
                room_index=ri, hazard=best_haz, label_text=best_text,
                matched_kw=best_kw, is_guess=False, rule=R.rule_for(best_haz),
            ))

    if force_conservative_if_any_unknown and any(r.is_guess for r in results):
        cons = R.conservative_rule()
        for r in results:
            r.hazard = cons.hazard
            r.rule = cons
    return results


def summarize(classes: List[RoomClass]) -> dict:
    """Counts per hazard + how many rooms were guessed (for the report)."""
    by_haz: dict = {}
    guesses = 0
    for c in classes:
        by_haz[c.hazard] = by_haz.get(c.hazard, 0) + 1
        if c.is_guess:
            guesses += 1
    return {"by_hazard": by_haz, "guessed_rooms": guesses,
            "total_rooms": len(classes)}
