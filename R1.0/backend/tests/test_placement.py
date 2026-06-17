import math

import pytest
from shapely.geometry import Point, Polygon

from app.geometry import dist
from app.nfpa import MIN_HEAD_SPACING, spacing_for
from app.placement import place

ROOM_10x8 = [[0, 0], [10000, 0], [10000, 8000], [0, 8000]]


def test_spacing_values():
    assert spacing_for("Light") == pytest.approx(min(4600, math.sqrt(20_900_000)))
    assert spacing_for("Ordinary") == pytest.approx(math.sqrt(12_100_000))
    assert spacing_for("Extra") == pytest.approx(math.sqrt(9_300_000))


def test_room_10x8_light_basic():
    pts = place(ROOM_10x8, "Light")
    assert len(pts) > 0
    poly = Polygon(ROOM_10x8)
    for p in pts:
        assert poly.covers(Point(p)), f"head {p} outside boundary"
    for i, p in enumerate(pts):
        for q in pts[i + 1:]:
            assert dist(p, q) >= MIN_HEAD_SPACING - 1e-6, f"{p} and {q} too close"


def test_room_10x8_grid_spacing():
    """Interior heads sit on a regular grid with step S."""
    s = spacing_for("Light")
    pts = place(ROOM_10x8, "Light")
    # nearest-neighbour distance of any head never exceeds S (rows/cols step S)
    for p in pts:
        nn = min(dist(p, q) for q in pts if q is not p)
        assert nn <= s + 1e-6


def test_tiny_room_single_head_at_centroid():
    tiny = [[0, 0], [2000, 0], [2000, 2000], [0, 2000]]
    pts = place(tiny, "Light")  # inset by S/2 (~2286) is empty
    assert len(pts) == 1
    assert pts[0] == pytest.approx([1000, 1000])


def test_l_shaped_room_all_heads_inside():
    l_shape = [[0, 0], [12000, 0], [12000, 6000], [6000, 6000], [6000, 12000], [0, 12000]]
    pts = place(l_shape, "Ordinary")
    poly = Polygon(l_shape)
    assert len(pts) >= 4
    for p in pts:
        assert poly.covers(Point(p))


# --- regression tests from the adversarial review -------------------------

def _assert_layout_valid(boundary, hazard):
    from app.validation import validate

    pts = place(boundary, hazard)
    poly = Polygon(boundary)
    for p in pts:
        assert poly.covers(Point(p)), f"head {p} outside boundary"
    report = validate(boundary, pts, hazard)
    assert report.passed, [r for r in report.rules if not r.passed]
    return pts


def test_narrow_corridor_gets_spine_of_heads():
    """A 30 m x 4.5 m corridor (narrower than S) used to get ONE head."""
    corridor = [[0, 0], [30000, 0], [30000, 4500], [0, 4500]]
    pts = _assert_layout_valid(corridor, "Light")
    assert len(pts) >= 6
    assert all(abs(p[1] - 2250) < 1.0 for p in pts)  # centred spine


def test_corridor_just_wider_than_s_two_rows():
    """Width 4572 (S + 0.35mm) used to leave every head on one wall."""
    corridor = [[0, 0], [30000, 0], [30000, 4572], [0, 4572]]
    pts = _assert_layout_valid(corridor, "Light")
    rows = {round(p[1]) for p in pts}
    assert len(rows) == 2  # two rows pushed toward the walls, 1800 apart


def test_c_shaped_room_heads_inside_and_valid():
    """Centroid of a C-shape lies outside it; the head must not."""
    c_shape = [[0, 0], [10000, 0], [10000, 2000], [2000, 2000],
               [2000, 8000], [10000, 8000], [10000, 10000], [0, 10000]]
    _assert_layout_valid(c_shape, "Light")


def test_u_shaped_room_wings_are_covered():
    """The 4 m wings used to be eroded by the S/2 inset and got no heads."""
    u_shape = [[0, 0], [12000, 0], [12000, 12000], [8000, 12000],
               [8000, 5000], [4000, 5000], [4000, 12000], [0, 12000]]
    pts = _assert_layout_valid(u_shape, "Light")
    assert any(p[1] > 8000 and p[0] < 4000 for p in pts), "left wing has no head"
    assert any(p[1] > 8000 and p[0] > 8000 for p in pts), "right wing has no head"


def test_triangle_room_fully_covered():
    triangle = [[0, 0], [12000, 0], [6000, 10000]]
    for hazard in ("Light", "Ordinary", "Extra"):
        _assert_layout_valid(triangle, hazard)


def test_l_shaped_room_passes_validation():
    """The original L test only checked containment, masking coverage gaps."""
    l_shape = [[0, 0], [12000, 0], [12000, 6000], [6000, 6000], [6000, 12000], [0, 12000]]
    _assert_layout_valid(l_shape, "Ordinary")


def test_degenerate_boundary_raises():
    with pytest.raises(ValueError):
        place([[0, 0], [1000, 0]], "Light")           # 2 vertices
    with pytest.raises(ValueError):
        place([[0, 0], [1000, 0], [2000, 0]], "Light")  # collinear


def test_self_intersecting_boundary_raises():
    bowtie = [[0, 0], [10000, 10000], [10000, 0], [0, 10000]]
    with pytest.raises(ValueError):
        place(bowtie, "Light")


def test_unknown_hazard_raises():
    with pytest.raises(KeyError):
        place(ROOM_10x8, "Severe")
