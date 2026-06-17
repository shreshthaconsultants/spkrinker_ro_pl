"""Tilted-building support: rotate -> route/place -> rotate back."""

import math

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Point, Polygon

from app.geometry import rotate
from app.main import app

client = TestClient(app)

GRID = [[x, y] for y in (0, 3000, 6000) for x in (0, 3000, 6000)]
TILT = 30.0


def test_route_is_equivariant_under_rotation():
    """Routing a 30-degree-tilted copy of the grid (with rotation=30) must
    give exactly the flat solution rotated by 30 degrees."""
    riser = [-2000, 3000]
    flat = client.post("/route", json={
        "points": GRID, "hazard": "Light", "risers": [riser],
    }).json()
    tilted = client.post("/route", json={
        "points": rotate(GRID, TILT),
        "hazard": "Light",
        "risers": [rotate([riser], TILT)[0]],
        "rotation": TILT,
    }).json()

    assert len(flat["segments"]) == len(tilted["segments"])
    assert tilted["total_length"] == pytest.approx(flat["total_length"])
    for fs, ts in zip(flat["segments"], tilted["segments"]):
        assert fs["kind"] == ts["kind"]
        assert fs["shaft"] == ts["shaft"]
        assert fs["length"] == pytest.approx(ts["length"], abs=1e-3)
        for flat_pt, tilted_pt in ((fs["start"], ts["start"]), (fs["end"], ts["end"])):
            expected = rotate([flat_pt], TILT)[0]
            assert tilted_pt[0] == pytest.approx(expected[0], abs=1e-3)
            assert tilted_pt[1] == pytest.approx(expected[1], abs=1e-3)


def test_tilted_pipes_run_along_building_axes():
    """Branches and mains of the tilted solution must lie at 30 or 120
    degrees (orthogonal in the building frame), never axis-aligned."""
    tilted = client.post("/route", json={
        "points": rotate(GRID, TILT),
        "hazard": "Light",
        "risers": [rotate([[-2000, 0]], TILT)[0]],
        "rotation": TILT,
    }).json()
    for s in tilted["segments"]:
        if s["kind"] == "riser":
            continue  # the shaft connection may be diagonal by design
        dx = s["end"][0] - s["start"][0]
        dy = s["end"][1] - s["start"][1]
        angle = math.degrees(math.atan2(dy, dx)) % 180.0
        assert min(abs(angle - TILT), abs(angle - TILT - 90)) < 1e-3, \
            f"{s['kind']} segment not aligned with the tilted building: {angle:.2f} deg"


def test_tilted_outlines_follow_the_tilt():
    tilted = client.post("/route", json={
        "points": rotate(GRID, TILT),
        "risers": [rotate([[-2000, 0]], TILT)[0]],
        "rotation": TILT,
    }).json()
    assert tilted["outlines"], "expected merged outlines"
    # every segment midpoint must sit inside some outline ring
    rings = [Polygon(o["points"]) for o in tilted["outlines"]]
    for s in tilted["segments"]:
        mid = Point((s["start"][0] + s["end"][0]) / 2, (s["start"][1] + s["end"][1]) / 2)
        assert any(r.buffer(1.0).covers(mid) for r in rings)


def test_place_in_tilted_room_heads_inside_and_valid():
    room = [[0, 0], [10000, 0], [10000, 8000], [0, 8000]]
    tilted_room = rotate(room, TILT)
    placed = client.post("/place", json={
        "boundary": tilted_room, "hazard": "Light", "rotation": TILT,
    }).json()
    assert placed["count"] >= 4
    poly = Polygon(tilted_room).buffer(1.0)
    for p in placed["points"]:
        assert poly.covers(Point(p)), f"head {p} outside the tilted room"
    # validation is rotation-invariant and must accept the tilted layout
    verdict = client.post("/validate", json={
        "boundary": tilted_room, "points": placed["points"], "hazard": "Light",
    }).json()
    assert verdict["passed"], [r for r in verdict["rules"] if not r["passed"]]


def test_rotation_zero_is_identity():
    flat = client.post("/route", json={"points": GRID, "risers": [[-2000, 0]]}).json()
    explicit = client.post("/route", json={
        "points": GRID, "risers": [[-2000, 0]], "rotation": 0,
    }).json()
    assert flat == explicit
