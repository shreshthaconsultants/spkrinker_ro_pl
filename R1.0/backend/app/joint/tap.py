"""Room-to-corridor connection (tap) detection.

The door between a room and the corridor is found by intersecting the
room's exterior ring with the corridor's exterior ring buffered by a
tolerance: shared walls register even when the two polylines were drawn
slightly apart (real drawings have wall thickness and snapping slop).
"""

import math
from dataclasses import dataclass

from shapely.geometry import LineString, Point
from shapely.ops import linemerge, nearest_points

from ..geometry import EPS, Pt
from ..nfpa import MIN_WALL_DIST

#: how far (mm) the room and corridor outlines may sit apart and still
#: count as sharing a wall (wall thickness + drawing slop)
DOOR_TOL = 400.0


@dataclass
class Tap:
    tap: Pt        # just inside the corridor, where the header connects
    entry: Pt      # just inside the room, where the sub-header starts
    mid: Pt        # the door: midpoint of the shared wall
    status: str    # "tapped" (shared wall) | "fallback" (nearest points)


def detect_tap(room, corridor, door_tol: float = DOOR_TOL):
    """Tap/entry pair for one room, or None when the room cannot reach the
    corridor (gap larger than 2 * door_tol)."""
    shared = room.exterior.intersection(corridor.exterior.buffer(door_tol))
    wall = _longest_line(shared)
    if wall is not None and wall.length > EPS:
        mid = wall.interpolate(0.5, normalized=True)
        status = "tapped"
    else:
        near_room, near_corridor = nearest_points(room.exterior, corridor.exterior)
        if near_room.distance(near_corridor) > 2 * door_tol:
            return None
        mid = near_room
        status = "fallback"

    entry = _push_inside(mid, room)
    anchor = nearest_points(mid, corridor.exterior)[1]
    tap = _push_inside(anchor, corridor)
    return Tap(tap=[tap.x, tap.y], entry=[entry.x, entry.y],
               mid=[mid.x, mid.y], status=status)


def _longest_line(geom):
    """Longest merged LineString component of an intersection result.

    linemerge first: the ring's start vertex can split one shared wall into
    two pieces, which would otherwise halve the detected door."""
    lines: list[LineString] = []
    stack = [geom]
    while stack:
        g = stack.pop()
        if isinstance(g, LineString):
            if g.length > EPS:
                lines.append(g)
        elif hasattr(g, "geoms"):
            stack.extend(g.geoms)
    if not lines:
        return None
    if len(lines) > 1:
        merged = linemerge(lines)
        lines = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    return max(lines, key=lambda line: line.length)


def _push_inside(pt: Point, poly, depth: float = MIN_WALL_DIST) -> Point:
    """pt (on the polygon's exterior ring) moved ~depth into the polygon
    along the local wall normal; falls back to stepping toward the
    representative point at corners and odd geometry."""
    ring = poly.exterior
    s = ring.project(pt)
    a = ring.interpolate(max(s - 1.0, 0.0))
    b = ring.interpolate(min(s + 1.0, ring.length))
    dx, dy = b.x - a.x, b.y - a.y
    norm = math.hypot(dx, dy)
    if norm > EPS:
        nx, ny = -dy / norm, dx / norm
        for sign in (1.0, -1.0):
            cand = Point(pt.x + sign * nx * depth, pt.y + sign * ny * depth)
            if poly.covers(cand):
                return cand
    rep = poly.representative_point()
    for frac in (0.02, 0.05, 0.1, 0.2, 0.4, 0.7):
        cand = Point(pt.x + (rep.x - pt.x) * frac, pt.y + (rep.y - pt.y) * frac)
        if poly.covers(cand):
            return cand
    return rep
