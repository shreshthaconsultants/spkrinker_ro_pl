"""Per-room sub-header + branch rows for joint mode.

route_room_tree / _choose_subheader_x are COPIES of routing._route_tree /
routing._choose_main_x (keep them in sync by hand if full mode changes):
the one behavioural change is that the room's vertical header must sit
BESIDE the sprinkler columns (slightly left/right by header_offset),
never on top of the heads.
"""

import numpy as np
from shapely.geometry import LineString

from ..geometry import EPS, Pt, dist
from ..nfpa import spacing_for
from ..routing import (
    _MAIN_CLEARANCE,
    Segment,
    _cluster_rows,
    _infer_spacing,
    _main_segments,
    _seg,
)
from .paths import ortho_segments


def route_room_tree(points: list[Pt], hazard, entry: Pt, room_poly,
                    header_offset: float, obstacles=()) -> list[Segment]:
    """One sub-header tree inside one room, fed from the entry point at
    the corridor door.  Copy of routing._route_tree with the offset
    sub-header and orthogonal-only entry runs."""
    s = spacing_for(hazard) if hazard else _infer_spacing(points)
    rows = _cluster_rows(points, tol=s / 2)

    xs = [p[0] for p in points]
    desired_x = min(max(entry[0], min(xs)), max(xs))
    sub_x = _choose_subheader_x(desired_x, entry[0], rows, xs,
                                room_poly, list(obstacles), header_offset)

    # Each row tees into the sub-header at the y of its ENTRY head (the
    # head nearest the sub-header), so the tee sits on the branch pipe.
    tees = []
    for heads in rows:
        entry_head = min(heads, key=lambda p: abs(p[0] - sub_x))
        tees.append((entry_head[1], heads))

    tee_ys = [tee_y for tee_y, _ in tees]
    lo_y, hi_y = min(tee_ys), max(tee_ys)
    tap_y = min(max(entry[1], lo_y), hi_y)
    tap = [sub_x, tap_y]

    segments: list[Segment] = []
    if dist(entry, tap) > EPS:
        segments += ortho_segments(entry, tap, room_poly, "riser")

    subheader = _main_segments(sub_x, tap_y, tee_ys)
    for seg in subheader:
        seg.kind = "subheader"
    segments += subheader

    for tee_y, heads in tees:
        segments += _branch_segments_ortho(sub_x, tee_y, heads)
    return segments


def _branch_segments_ortho(main_x: float, tee_y: float, heads: list[Pt]) -> list[Segment]:
    """Branch line for one row, rectilinear-only.

    Unlike full mode's _branch_segments (which chains 'inline' heads at
    their ACTUAL position - a head staggered by up to _ROW_LINE_TOL then
    produces a DIAGONAL), every head here is routed via the perpendicular-
    foot pattern: the line runs through (head_x, tee_y) and a vertical
    drop connects to the head.  For a head exactly on the line the drop is
    zero-length and skipped, so aligned rows come out identical to full
    mode.
    """
    tee = [main_x, tee_y]
    segments: list[Segment] = []
    for is_right in (True, False):
        def on_side(p):
            return (p[0] >= main_x - EPS) if is_right else (p[0] < main_x - EPS)

        stations = [([p[0], tee_y], list(p)) for p in heads if on_side(p)]
        stations.sort(key=lambda item: abs(item[0][0] - main_x))

        prev = tee
        for foot, head in stations:
            if dist(prev, foot) > EPS:
                segments.append(_seg(prev, foot, "branch"))
                prev = foot
            if dist(prev, head) > EPS:
                segments.append(_seg(prev, head, "branch"))  # perpendicular drop
    return segments


def _choose_subheader_x(desired_x: float, entry_x: float, rows, xs: list[Pt],
                        poly, obstacles, offset: float) -> float:
    """Sub-header x: BESIDE a sprinkler column (column +/- offset), never
    on one.

    Offset candidates that would poke through the room wall are rejected
    by the outside-length score (heads sit ~100 mm off walls but the
    offset is ~300 mm, so an outer-side candidate can land OUTSIDE the
    room).  The columns themselves are kept as last-resort candidates so
    a narrow room degrades to the full-mode behaviour (header on the
    column) instead of ever leaving the room.

    Sort keys are rounded to 0.001 mm so fp noise from the tilt
    round-trip cannot flip a tie (rotation equivariance); structural ties
    (column +/- offset are equidistant from a clamped desired_x) break
    toward the corridor door (entry_x), then toward smaller x.
    """
    anchor_ys = [row[0][1] for row in rows]
    lo, hi = min(anchor_ys), max(anchor_ys)
    if hi - lo <= EPS:
        return desired_x  # single row: no vertical run to place

    def outside_len(x: float) -> float:
        out = LineString([(x, lo), (x, hi)]).difference(poly).length
        return 0.0 if out <= EPS else out

    if obstacles:
        obs = np.asarray(obstacles, dtype=float)
        band = obs[(obs[:, 1] >= lo - _MAIN_CLEARANCE) & (obs[:, 1] <= hi + _MAIN_CLEARANCE)]
        band_xs = band[:, 0]
    else:
        band_xs = np.empty(0)

    def blocked(x: float) -> int:
        if band_xs.size == 0:
            return 0
        return int(np.count_nonzero(np.abs(band_xs - x) <= _MAIN_CLEARANCE))

    def sort_key(x: float):
        return (round(abs(x - desired_x), 3), round(abs(x - entry_x), 3), round(x, 3))

    columns = sorted(set(xs))

    def clear_of_columns(x: float) -> bool:
        return min(abs(x - c) for c in columns) >= offset - EPS

    beside = [x for c in columns for x in (c - offset, c + offset) if clear_of_columns(x)]
    if clear_of_columns(desired_x):
        beside.insert(0, desired_x)  # the door's own x, already off the columns
    beside.sort(key=sort_key)
    on_column = sorted(columns, key=sort_key)

    best_x, best_score = None, None
    for tier in (beside, on_column):
        for x in tier:
            score = (blocked(x), outside_len(x))
            if score == (0, 0.0):
                return x
            if best_score is None or score < best_score:
                best_x, best_score = x, score
    return best_x if best_x is not None else desired_x
