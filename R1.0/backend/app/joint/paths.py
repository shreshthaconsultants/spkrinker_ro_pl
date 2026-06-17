"""Small shared path helpers for joint mode.

Joint mode never draws diagonals: the user's plans are fully rectilinear,
so every connector is either a straight axis-aligned run or an L through
one corner (full mode's _riser_segments prefers the straight diagonal,
which is wrong here).
"""

from shapely.geometry import LineString

from ..geometry import EPS, Pt, dist
from ..routing import Segment, _seg


def ortho_segments(a: Pt, b: Pt, poly, kind: str) -> list[Segment]:
    """a -> b with axis-aligned runs only: straight when aligned, else an
    L through the corner that keeps the most pipe inside poly.

    The outside-length key is rounded so fp noise from the tilt round-trip
    cannot flip the corner choice (rotation equivariance).
    """
    if dist(a, b) <= EPS:
        return []
    if abs(a[0] - b[0]) <= EPS or abs(a[1] - b[1]) <= EPS:
        return [_seg(a, b, kind)]

    corners = [[b[0], a[1]], [a[0], b[1]]]

    def outside(corner: Pt) -> float:
        if poly is None:
            return 0.0
        return LineString([tuple(a), tuple(corner), tuple(b)]).difference(poly).length

    corner = min(corners, key=lambda c: round(outside(c), 3))
    return [_seg(a, corner, kind), _seg(corner, b, kind)]
