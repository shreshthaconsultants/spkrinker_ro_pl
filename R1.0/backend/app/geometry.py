"""Shapely helpers shared by placement and validation."""

from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.validation import explain_validity

Pt = list[float]  # [x, y] in mm

EPS = 1e-6


def clean_polygon(boundary: list[Pt]) -> Polygon:
    """Build a valid shapely Polygon from raw vertices.

    Raises ValueError with a human-readable message on degenerate input.
    Invalid polygons are rejected (not silently repaired) so the user fixes
    the drawing instead of getting sprinklers for half a room.
    """
    distinct = {(round(x, 9), round(y, 9)) for x, y in boundary}
    if len(distinct) < 3:
        raise ValueError("boundary needs at least 3 distinct vertices")

    poly = Polygon(boundary)
    if not poly.is_valid:
        raise ValueError(f"invalid boundary: {explain_validity(poly)}")
    if poly.is_empty or poly.area <= EPS:
        raise ValueError("boundary encloses no area (degenerate polygon)")
    return poly


def polygon_parts(geom: BaseGeometry) -> list[Polygon]:
    """Split a buffer() result into its polygonal parts ([] if empty)."""
    if geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return [g for g in geom.geoms if not g.is_empty and g.area > EPS]
    return []


def dist(a: Pt, b: Pt) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def rotate(pts: list[Pt], degrees: float) -> list[Pt]:
    """Rotate points CCW about the origin by the given angle in degrees.

    Used to route tilted buildings: inputs are rotated by -tilt into an
    axis-aligned frame, routed there, and the outputs rotated back by +tilt.
    """
    import math

    theta = math.radians(degrees)
    c, s = math.cos(theta), math.sin(theta)
    return [[x * c - y * s, x * s + y * c] for x, y in pts]
