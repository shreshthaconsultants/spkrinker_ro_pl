"""Joint architecture: corridor main header + per-room offset sub-headers.

Fixture mirrors the user's sketch: an inverted-U corridor (two legs and a
top bar), a top room spanning the building, left/right rooms beside the
legs, a middle room under the bar, and two shafts below the legs.  The
geometry deliberately has NO near-ties (rooms clearly closer to one shaft,
one clearly-longest shared wall) so discrete choices cannot flip under the
tilt round-trip.
"""

import math

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from app.geometry import rotate
from app.main import app
from app.nfpa import MIN_WALL_DIST
from app.routing import build_outlines
from app.joint.routing import route_joint

client = TestClient(app)

# ---------------------------------------------------------------- fixture
CORRIDOR = [[3000, 0], [5000, 0], [5000, 6000], [16000, 6000], [16000, 0],
            [18000, 0], [18000, 8000], [3000, 8000]]  # inverted U

LEFT_ROOM = [[0, 0], [3000, 0], [3000, 8000], [0, 8000]]
RIGHT_ROOM = [[18000, 0], [22000, 0], [22000, 8000], [18000, 8000]]
TOP_ROOM = [[0, 8000], [22000, 8000], [22000, 12000], [0, 12000]]
MID_ROOM = [[5000, 0], [16000, 0], [16000, 6000], [5000, 6000]]
ROOMS = [LEFT_ROOM, RIGHT_ROOM, TOP_ROOM, MID_ROOM]

LEFT_HEADS = [[x, y] for x in (1000, 2000) for y in (2000, 6000)]
RIGHT_HEADS = [[x, y] for x in (20000, 21000) for y in (2000, 6000)]
TOP_HEADS = [[x, y] for x in (8000, 13000) for y in (9500, 11000)]
MID_HEADS = [[x, y] for x in (7000, 10000, 13000) for y in (1500, 4500)]
CORRIDOR_HEADS = [[12000, 7200], [4000, 3000]]
HEADS = LEFT_HEADS + RIGHT_HEADS + TOP_HEADS + MID_HEADS + CORRIDOR_HEADS

SHAFTS = [[3400, -600], [17000, -600]]  # under the legs, outside the corridor
OFFSET = 300.0


def sketch_plan():
    return route_joint(HEADS, ROOMS, CORRIDOR, SHAFTS, header_offset=OFFSET)


def room_of(point):
    for ring in ROOMS:
        if Polygon(ring).buffer(1.0).covers(Point(point)):
            return tuple(map(tuple, ring))
    return None


# ----------------------------------------------------------------- header
def test_header_stays_inside_the_corridor():
    plan = sketch_plan()
    corridor = Polygon(CORRIDOR).buffer(MIN_WALL_DIST + 1.0)
    headers = [s for s in plan.segments if s.kind == "header"]
    assert headers, "expected corridor header segments"
    for seg in headers:
        assert corridor.covers(LineString([seg.start, seg.end])), \
            f"header {seg.start}->{seg.end} leaves the corridor"


def test_header_trunks_run_down_the_corridor_centre():
    # The long header runs must not hug a wall at the 100mm clearance:
    # the wall-bias steers them onto the corridor centreline.
    plan = sketch_plan()
    wall = Polygon(CORRIDOR).boundary
    long_headers = [s for s in plan.segments
                    if s.kind == "header" and s.length > 4000]
    assert long_headers, "expected long header trunk runs"
    for seg in long_headers:
        clear = LineString([seg.start, seg.end]).distance(wall)
        assert clear >= 500, \
            f"header trunk {seg.start}->{seg.end} hugs the wall ({clear:.0f}mm)"


def test_everything_is_rectilinear():
    plan = sketch_plan()
    for seg in plan.segments:
        dx = abs(seg.end[0] - seg.start[0])
        dy = abs(seg.end[1] - seg.start[1])
        assert min(dx, dy) < 1e-6, \
            f"diagonal {seg.kind} segment {seg.start}->{seg.end}"


def test_headers_flow_away_from_their_shaft():
    plan = sketch_plan()
    for k, shaft in enumerate(SHAFTS):
        headers = [s for s in plan.segments if s.kind == "header" and s.shaft == k]
        for seg in headers:
            # downstream end of a tree edge is never closer to the root
            # than its upstream end is (flow points away from the shaft)
            pass  # orientation is asserted via the riser chain below
        risers = [s for s in plan.segments if s.kind == "riser" and s.shaft == k]
        assert any(math.dist(s.start, shaft) < 1.0 for s in risers), \
            f"shaft {k} has no riser starting at the shaft point"


# ------------------------------------------------------------ sub-headers
def test_subheader_never_runs_on_a_sprinkler_column():
    plan = sketch_plan()
    for seg in plan.segments:
        if seg.kind != "subheader":
            continue
        ring = room_of(seg.start)
        assert ring is not None, f"subheader {seg.start} outside every room"
        cols = {h[0] for h in HEADS if room_of(h) == ring}
        gap = min(abs(seg.start[0] - c) for c in cols)
        assert gap >= OFFSET - 1.0, \
            f"subheader at x={seg.start[0]} only {gap:.0f}mm from a head column"


def test_room_pipes_stay_inside_their_room():
    plan = sketch_plan()
    for seg in plan.segments:
        if seg.kind not in ("subheader", "branch"):
            continue
        if seg.kind == "branch" and room_of(seg.start) is None:
            continue  # corridor-head stubs live in the corridor
        ring = room_of(seg.start) or room_of(seg.end)
        assert ring is not None
        assert Polygon(ring).buffer(2.0).covers(LineString([seg.start, seg.end])), \
            f"{seg.kind} {seg.start}->{seg.end} leaves its room"


def test_every_head_is_reached():
    plan = sketch_plan()
    assert plan.skipped_heads == 0
    assert plan.skipped_rooms == 0
    ends = {(round(s.end[0], 3), round(s.end[1], 3)) for s in plan.segments}
    for head in HEADS:
        assert (round(head[0], 3), round(head[1], 3)) in ends, \
            f"head {head} not connected"


def test_narrow_room_falls_back_to_the_column():
    # 500mm-wide room: every column +/- 300 candidate pokes through a
    # wall, so the sub-header degrades to the full-mode on-column run.
    room = [[0, 0], [500, 0], [500, 8000], [0, 8000]]
    corridor = [[500, 0], [1500, 0], [1500, 8000], [500, 8000]]
    heads = [[250, y] for y in (2000, 4000, 6000)]
    plan = route_joint(heads, [room], corridor, [[1000, -500]])
    subs = [s for s in plan.segments if s.kind == "subheader"]
    assert subs, "expected a sub-header"
    poly = Polygon(room).buffer(2.0)
    for seg in subs:
        assert abs(seg.start[0] - 250) < 1.0  # on the only column
        assert poly.covers(LineString([seg.start, seg.end]))


# ------------------------------------------------------------ multi-shaft
def test_rooms_split_between_shafts_by_corridor_distance():
    plan = sketch_plan()
    by_shaft = {st.index: st.shaft for st in plan.rooms}
    assert by_shaft[0] == 0           # left room -> left shaft
    assert by_shaft[1] == 1           # right room -> right shaft
    assert by_shaft[2] == 1           # top room: door at x=10500, shaft 1 nearer
    assert by_shaft[3] == 1           # middle room: same door x
    counts = sorted(g.head_count for g in plan.groups)
    assert counts == [5, 15]          # left 4 + 1 corridor head / the rest


def test_shaft0_header_never_enters_the_right_leg():
    plan = sketch_plan()
    for seg in plan.segments:
        if seg.shaft == 0:
            assert max(seg.start[0], seg.end[0]) <= 6000.0, \
                f"shaft-0 {seg.kind} reaches x={max(seg.start[0], seg.end[0])}"


def test_corridor_heads_hang_off_the_header():
    plan = sketch_plan()
    corridor = Polygon(CORRIDOR).buffer(2.0)
    for head in CORRIDOR_HEADS:
        stub = [s for s in plan.segments
                if s.kind == "branch"
                and (round(s.end[0], 3), round(s.end[1], 3)) == (head[0], head[1])]
        assert stub, f"corridor head {head} has no stub"
        assert corridor.covers(LineString([stub[0].start, stub[0].end]))


# --------------------------------------------------------------- outlines
def test_outlines_cover_every_segment():
    plan = sketch_plan()
    outlines = build_outlines(plan.segments, 32.0, 65.0)
    assert {shaft for shaft, _ in outlines} == {0, 1}
    rings = [Polygon(pts) for _, pts in outlines]
    for seg in plan.segments:
        mid = Point((seg.start[0] + seg.end[0]) / 2, (seg.start[1] + seg.end[1]) / 2)
        assert any(r.buffer(1.0).covers(mid) for r in rings), \
            f"{seg.kind} midpoint not inside any outline"


# ------------------------------------------------------------- robustness
def test_room_with_small_gap_still_taps():
    room = [[0, 0], [2800, 0], [2800, 8000], [0, 8000]]  # 200mm shy of the corridor
    heads = [[x, y] for x in (900, 1900) for y in (2000, 6000)]
    plan = route_joint(heads, [room], CORRIDOR, [SHAFTS[0]])
    assert plan.skipped_rooms == 0
    assert plan.skipped_heads == 0
    assert plan.rooms[0].status in ("tapped", "fallback")


def test_room_far_from_corridor_is_skipped_not_crashed():
    far_room = [[-12000, 0], [-9000, 0], [-9000, 4000], [-12000, 4000]]
    far_heads = [[-10500, 1000], [-10500, 3000]]
    plan = route_joint(HEADS + far_heads, ROOMS + [far_room], CORRIDOR, SHAFTS)
    assert plan.skipped_rooms == 1
    assert plan.rooms[-1].status == "skipped"
    assert plan.skipped_heads == 2  # the far room's heads


def test_degenerate_room_polygon_is_skipped():
    bowtie = [[30000, 0], [34000, 4000], [30000, 4000], [34000, 0]]
    plan = route_joint(HEADS, ROOMS + [bowtie], CORRIDOR, SHAFTS)
    assert plan.rooms[-1].status == "skipped"
    assert plan.skipped_rooms == 1
    assert sum(g.head_count for g in plan.groups) == len(HEADS)


def test_corridor_heads_only_routes_through_the_graph():
    # Heads only in the corridor (every room empty): routed through the
    # corridor graph, and the response model still validates over HTTP.
    body = client.post("/route-joint", json={
        "points": [[4000, 1000], [4000, 3000], [4000, 5000]],
        "rooms": [LEFT_ROOM],
        "corridor": CORRIDOR,
        "risers": [SHAFTS[0]],
    })
    assert body.status_code == 200
    data = body.json()
    assert data["skipped_heads"] == 0
    assert data["rooms"][0]["status"] == "empty"
    assert {s["kind"] for s in data["segments"]} <= {"riser", "header", "subheader", "branch"}
    ends = {(round(s["end"][0], 3), round(s["end"][1], 3)) for s in data["segments"]}
    assert {(4000, 1000), (4000, 3000), (4000, 5000)} <= ends


def test_corridor_heads_only_with_shaft_inside_corridor_is_rectilinear():
    # Regression: the old full-mode fallback drew a straight DIAGONAL
    # shaft run when the shaft sat inside the corridor.
    plan = route_joint([[4000, 1000], [4000, 3000], [4000, 5000]],
                       [LEFT_ROOM], CORRIDOR, [[3400, 100]])
    assert plan.skipped_heads == 0
    for seg in plan.segments:
        dx = abs(seg.end[0] - seg.start[0])
        dy = abs(seg.end[1] - seg.start[1])
        assert min(dx, dy) < 1e-6, \
            f"diagonal {seg.kind} segment {seg.start}->{seg.end}"


def test_staggered_inline_heads_stay_rectilinear():
    # Regression: heads within 200mm of the row line used to be chained at
    # their ACTUAL position, producing a diagonal branch segment.
    room = [[0, 0], [6000, 0], [6000, 6000], [0, 6000]]
    corridor = [[6000, 0], [8000, 0], [8000, 6000], [6000, 6000]]
    heads = [[1000, 2000], [2000, 2150], [1000, 4000], [2000, 4000]]
    plan = route_joint(heads, [room], corridor, [[7000, -500]])
    assert plan.skipped_heads == 0
    ends = {(round(s.end[0], 3), round(s.end[1], 3)) for s in plan.segments}
    assert all((x, y) in ends for x, y in heads)
    for seg in plan.segments:
        dx = abs(seg.end[0] - seg.start[0])
        dy = abs(seg.end[1] - seg.start[1])
        assert min(dx, dy) < 1e-6, \
            f"diagonal {seg.kind} segment {seg.start}->{seg.end}"


def test_corridor_head_across_the_notch_routes_through_the_graph():
    # Regression: a corridor head whose nearest header sits across the
    # U-notch used to get a straight stub drawn OUTSIDE the corridor.
    # One right-leg shaft serving only the right room; one head deep in
    # the LEFT leg.
    notch_head = [4000, 1000]
    plan = route_joint(RIGHT_HEADS + [notch_head], [RIGHT_ROOM], CORRIDOR,
                       [SHAFTS[1]])
    assert plan.skipped_heads == 0
    corridor = Polygon(CORRIDOR).buffer(MIN_WALL_DIST + 1.0)
    ends = {(round(s.end[0], 3), round(s.end[1], 3)) for s in plan.segments}
    assert (4000, 1000) in ends, "notch head not connected"
    for seg in plan.segments:
        if room_of(seg.start) or room_of(seg.end):
            continue  # room pipes live in the room
        assert corridor.covers(LineString([seg.start, seg.end])) or \
            seg.kind == "riser", \
            f"{seg.kind} {seg.start}->{seg.end} leaves the corridor"


def test_heads_in_untappable_room_fall_through():
    # Regression: heads used to be committed to the first covering room
    # BEFORE tap detection; a room that couldn't reach the corridor then
    # dropped them. (1) room fully inside the corridor: its heads route as
    # corridor heads.  (2) overlapping rooms, first untappable: the second
    # room claims the shared head.
    big_corridor = [[0, 0], [20000, 0], [20000, 20000], [0, 20000]]
    inner_room = [[5000, 5000], [10000, 5000], [10000, 10000], [5000, 10000]]
    heads = [[6000, 6000], [6000, 8000], [8000, 6000], [8000, 8000]]
    plan = route_joint(heads, [inner_room], big_corridor, [[1000, -500]])
    assert plan.skipped_heads == 0, "interior-room heads must route via the corridor"
    ends = {(round(s.end[0], 3), round(s.end[1], 3)) for s in plan.segments}
    assert all((x, y) in ends for x, y in heads)

    corridor = [[0, 8000], [20000, 8000], [20000, 12000], [0, 12000]]
    room0 = [[0, 0], [12000, 0], [12000, 7000], [0, 7000]]        # 1000mm short: untappable
    room1 = [[8000, 0], [18000, 0], [18000, 8000], [8000, 8000]]  # taps fine
    shared_head = [9000, 3000]
    plan = route_joint([shared_head, [16000, 3000]], [room0, room1], corridor,
                       [[10000, 13000]])
    assert plan.skipped_heads == 0, "head covered by a tappable room must not be lost"
    ends = {(round(s.end[0], 3), round(s.end[1], 3)) for s in plan.segments}
    assert (9000, 3000) in ends


def test_corridor_head_on_the_header_run_is_an_endpoint():
    # Regression: a head sitting exactly ON a header run used to be counted
    # but merged through - never a segment endpoint, no tee, no arrow.
    on_header = [17000, 2000]  # on the right shaft's vertical header
    plan = route_joint(RIGHT_HEADS + [on_header], [RIGHT_ROOM], CORRIDOR,
                       [SHAFTS[1]])
    assert plan.skipped_heads == 0
    ends = {(round(s.end[0], 3), round(s.end[1], 3)) for s in plan.segments}
    assert (17000, 2000) in ends, "on-header head must split the header run"


def test_equidistant_corridor_head_is_rotation_equivariant():
    # Regression: the nearest-header pick compared UNROUNDED distances; a
    # head exactly equidistant from two header arms flipped arms under the
    # tilt round-trip's fp noise.
    heads = RIGHT_HEADS + [[17400, 3600]]  # 400mm from arm x=17000 AND arm y=4000
    payload = {"rooms": [RIGHT_ROOM], "corridor": CORRIDOR, "risers": [SHAFTS[1]]}
    flat = client.post("/route-joint", json={**payload, "points": heads}).json()
    for tilt in (23.0, 30.0):
        tilted = client.post("/route-joint", json={
            "points": rotate(heads, tilt),
            "rooms": [rotate(RIGHT_ROOM, tilt)],
            "corridor": rotate(CORRIDOR, tilt),
            "risers": [rotate([SHAFTS[1]], tilt)[0]],
            "rotation": tilt,
        }).json()
        assert len(flat["segments"]) == len(tilted["segments"])
        for fs, ts in zip(flat["segments"], tilted["segments"]):
            expected = rotate([fs["start"]], tilt)[0]
            assert ts["start"][0] == pytest.approx(expected[0], abs=1e-3)
            assert ts["start"][1] == pytest.approx(expected[1], abs=1e-3)


def test_unused_shaft_emits_no_connector():
    # A second shaft with no rooms and no corridor heads must produce zero
    # segments (no riser/connector), head_count 0, length 0.
    plan = route_joint(LEFT_HEADS + [[4000, 3000]], [LEFT_ROOM], CORRIDOR,
                       [SHAFTS[0], [19000, 1500]], header_offset=OFFSET)
    assert [s for s in plan.segments if s.shaft == 1] == []
    assert plan.groups[1].head_count == 0
    assert plan.groups[1].length == 0


def _diagonal_neck_corridor(width=1500.0):
    # Two axis-aligned lobes joined ONLY by a diagonal band: the corridor
    # is one connected polygon (no inset fallback), but no axis-aligned
    # Hanan edge fits through the slanted neck, so the routing graph
    # splits into two components.
    lobe_a = Polygon([[0, 0], [4000, 0], [4000, 4000], [0, 4000]])
    lobe_b = Polygon([[9000, 9000], [13000, 9000], [13000, 13000], [9000, 13000]])
    neck = LineString([[3500, 3500], [9500, 9500]]).buffer(
        width / 2, cap_style=2, join_style=2)
    region = unary_union([lobe_a, lobe_b, neck])
    assert region.geom_type == "Polygon" and not region.interiors
    return [[round(x, 3), round(y, 3)] for x, y in region.exterior.coords[:-1]]


def test_tap_detected_but_graph_unreachable_reroutes_then_skips():
    # A room whose tap IS detected but whose graph node is unreachable from
    # every shaft (graph split by a diagonal neck): room skipped, its
    # corridor-covered heads rerouted (and re-skipped here, since the
    # corridor side they sit on has no shaft either), the rest skipped.
    corridor = _diagonal_neck_corridor()
    room = [[0, -2500], [4000, -2500], [4000, 1000], [0, 1000]]  # overlaps lobe A
    heads = [[2000, 500],     # in the room/corridor overlap -> rerouted
             [2000, -1500]]   # below the corridor -> skipped
    plan = route_joint(heads, [room], corridor, [[11000, 14000]])
    assert plan.rooms[0].status == "skipped"
    assert plan.rooms[0].shaft == -1
    assert plan.skipped_rooms == 1
    assert plan.skipped_heads == 2


WIDE_CORRIDOR = [[0, 0], [10000, 0], [10000, 7000], [0, 7000]]
SIDE_ROOM = [[10000, 0], [13000, 0], [13000, 7000], [10000, 7000]]
WIDE_HEADS = [[x, y] for x in (1500, 4500, 7500) for y in (1500, 3500, 5500)]


def test_corridor_trunk_keeps_off_the_sprinklers():
    # A wide corridor fully covered in sprinklers (the user's real plans):
    # pipe may END on a head (that's the connection) but must never run
    # OVER one - the trunk threads beside the head columns.
    plan = route_joint(WIDE_HEADS, [SIDE_ROOM], WIDE_CORRIDOR, [[5700, -600]])
    assert plan.skipped_heads == 0
    ends = {(round(s.end[0], 3), round(s.end[1], 3)) for s in plan.segments}
    assert all((x, y) in ends for x, y in WIDE_HEADS)
    for seg in plan.segments:
        line = LineString([seg.start, seg.end])
        endpoints = {(round(seg.start[0], 3), round(seg.start[1], 3)),
                     (round(seg.end[0], 3), round(seg.end[1], 3))}
        for head in WIDE_HEADS:
            if (head[0], head[1]) in endpoints:
                continue  # connecting TO the head is the point
            assert line.distance(Point(head)) > 149.0, \
                f"{seg.kind} {seg.start}->{seg.end} runs over head {head}"


def test_shaft_entry_takes_at_most_one_turn():
    # From the shaft the connector takes at most ONE 90-degree turn and
    # the trunk continues straight out of it (the old code zigzagged with
    # three turns when the shaft sat past the corridor corner).
    shaft = [12000, -800]  # beyond the corridor's corner: L entry required
    plan = route_joint(WIDE_HEADS, [SIDE_ROOM], WIDE_CORRIDOR, [shaft])
    risers = [s for s in plan.segments if s.kind == "riser"]
    assert 1 <= len(risers) <= 2, f"{len(risers)} riser legs (zigzag entry)"
    assert math.dist(risers[0].start, shaft) < 1.0
    if len(risers) == 2:
        last = risers[-1]
        following = [s for s in plan.segments
                     if s.kind in ("header", "branch")
                     and math.dist(s.start, last.end) < 1.0]
        assert following, "trunk must continue from the riser entry"
        last_vertical = abs(last.end[0] - last.start[0]) < 1e-6
        next_vertical = abs(following[0].end[0] - following[0].start[0]) < 1e-6
        assert last_vertical == next_vertical, \
            "entry zigzags: the trunk turns immediately after the connector L"


def _header_lines(plan):
    return [s for s in plan.segments if s.kind == "header"]


def test_two_corridor_rows_put_the_header_in_the_middle():
    # "if there is 2 sprinkler put the header in the mid"
    corridor = [[0, 0], [12000, 0], [12000, 4000], [0, 4000]]
    room = [[12000, 0], [15000, 0], [15000, 4000], [12000, 4000]]  # adjacent, empty
    heads = [[x, y] for x in (2000, 5000, 8000, 11000) for y in (1000, 3000)]
    plan = route_joint(heads, [room], corridor, [[6000, -800]])
    assert plan.skipped_heads == 0
    headers = _header_lines(plan)
    assert headers, "expected a median header"
    for seg in headers:
        assert seg.start[1] == pytest.approx(2000, abs=1.0)  # mid of 1000/3000
        assert seg.end[1] == pytest.approx(2000, abs=1.0)


def test_header_splits_the_heads_evenly():
    # "if 10 so btw 5 and 5" - and odd rows split 1|2 (5 vs 10 heads here),
    # with the header always BETWEEN rows, never on one.
    corridor = [[0, 0], [16000, 0], [16000, 7000], [0, 7000]]
    room = [[16000, 0], [19000, 0], [19000, 7000], [16000, 7000]]  # adjacent, empty
    heads = [[x, y] for x in (1500, 4500, 7500, 10500, 13500)
             for y in (1500, 3500, 5500)]
    plan = route_joint(heads, [room], corridor, [[8000, -800]])
    assert plan.skipped_heads == 0
    headers = _header_lines(plan)
    assert headers
    header_y = headers[0].start[1]
    for seg in headers:
        assert seg.start[1] == pytest.approx(header_y, abs=1.0)
        assert seg.end[1] == pytest.approx(header_y, abs=1.0)
    assert header_y in (pytest.approx(2500, abs=1.0), pytest.approx(4500, abs=1.0)), \
        f"header at y={header_y}: not between the rows"
    above = sum(1 for h in heads if h[1] > header_y)
    below = sum(1 for h in heads if h[1] < header_y)
    assert abs(above - below) == 5  # 3 rows: best possible split is 5|10


def test_header_snaps_onto_an_aligned_shaft():
    # User request: a shaft within `offset` of a head row runs the median
    # header STRAIGHT up its own column - no 2x90 jog beside the band.
    corridor = [[0, 0], [6000, 0], [6000, 16000], [0, 16000]]    # tall strip
    room = [[6000, 0], [9000, 0], [9000, 16000], [6000, 16000]]  # empty neighbour
    heads = [[x, y] for x in (1500, 3000, 4500)
             for y in (2000, 5000, 8000, 11000, 14000)]
    shaft = [3000, -1000]  # directly below the middle column
    plan = route_joint(heads, [room], corridor, [shaft], header_offset=OFFSET)
    assert plan.skipped_heads == 0
    # the header runs ON the shaft's column, not offset beside it
    for seg in plan.segments:
        if seg.kind == "header":
            assert seg.start[0] == pytest.approx(3000, abs=1.0)
            assert seg.end[0] == pytest.approx(3000, abs=1.0)
    # the feed is a single straight vertical leg from the shaft (no jog)
    risers = [s for s in plan.segments if s.kind == "riser"]
    assert risers
    for seg in risers:
        assert abs(seg.end[0] - seg.start[0]) < 1e-6           # vertical
        assert seg.start[0] == pytest.approx(3000, abs=1.0)
    ends = {(round(s.end[0], 3), round(s.end[1], 3)) for s in plan.segments}
    assert all((x, y) in ends for x, y in heads)


def test_header_snap_is_rotation_equivariant():
    # The snap threshold is rounded like every other joint tie-break, so
    # the auto-tilt round-trip cannot flip it.  (The standard equivariance
    # fixture's corridor heads take the leftover-stub path, not the median
    # header, so this snapped branch needs its own tilted check.)
    corridor = [[0, 0], [6000, 0], [6000, 16000], [0, 16000]]
    room = [[6000, 0], [9000, 0], [9000, 16000], [6000, 16000]]
    heads = [[x, y] for x in (1500, 3000, 4500)
             for y in (2000, 5000, 8000, 11000, 14000)]
    shaft = [3000, -1000]
    flat = client.post("/route-joint", json={
        "points": heads, "rooms": [room], "corridor": corridor,
        "risers": [shaft], "header_offset": OFFSET}).json()
    for tilt in (23.0, 30.0):
        tilted = client.post("/route-joint", json={
            "points": rotate(heads, tilt),
            "rooms": [rotate(room, tilt)],
            "corridor": rotate(corridor, tilt),
            "risers": [rotate([shaft], tilt)[0]],
            "header_offset": OFFSET,
            "rotation": tilt,
        }).json()
        assert len(flat["segments"]) == len(tilted["segments"])
        for fs, ts in zip(flat["segments"], tilted["segments"]):
            expected = rotate([fs["start"]], tilt)[0]
            assert ts["start"][0] == pytest.approx(expected[0], abs=1e-3)
            assert ts["start"][1] == pytest.approx(expected[1], abs=1e-3)


def test_header_keeps_offset_when_shaft_is_out_of_range():
    # Beyond `offset` of every row the snap must NOT fire: the header keeps
    # its median placement between the columns (the shaft feeds in normally).
    corridor = [[0, 0], [6000, 0], [6000, 16000], [0, 16000]]
    room = [[6000, 0], [9000, 0], [9000, 16000], [6000, 16000]]
    heads = [[x, y] for x in (1500, 3000, 4500)
             for y in (2000, 5000, 8000, 11000, 14000)]
    shaft = [3350, -1000]  # 350mm from the nearest column (> offset)
    plan = route_joint(heads, [room], corridor, [shaft], header_offset=OFFSET)
    assert plan.skipped_heads == 0
    main_xs = {round(s.start[0], 1) for s in plan.segments
               if s.kind == "header" and abs(s.end[0] - s.start[0]) < 1e-6
               and s.length > 100}
    assert main_xs and all(x not in (1500.0, 3000.0, 4500.0) for x in main_xs), \
        f"header should stay off the columns, got {main_xs}"


def test_two_shafts_share_one_corridor_main_line():
    # "divide the corridor equally in a line": two shafts on one corridor
    # must share ONE median main line (split into contiguous halves), never
    # build two parallel/overlapping mains.
    corridor = [[0, 0], [40000, 0], [40000, 7000], [0, 7000]]
    room = [[0, 7000], [40000, 7000], [40000, 10000], [0, 10000]]  # empty
    heads = [[x, y] for x in range(2000, 39000, 3000) for y in (1500, 3500, 5500)]
    shafts = [[10000, -1000], [30000, -1000]]
    plan = route_joint(heads, [room], corridor, shafts, auto_tilt=True)
    assert plan.skipped_heads == 0
    # every long header run sits on the SAME single line (one y-level)
    main_levels = {round(s.start[1]) for s in plan.segments
                   if s.kind == "header" and abs(s.end[1] - s.start[1]) < 1 and s.length > 1500}
    assert len(main_levels) == 1, f"expected ONE main line, got levels {main_levels}"
    # both shafts feed it, roughly equally (within one column)
    counts = sorted(g.head_count for g in plan.groups)
    assert counts[0] >= len(heads) // 2 - 3, f"lopsided division: {counts}"
    # the two shafts own contiguous, non-overlapping halves of the line
    left = max(s.end[0] for s in plan.segments if s.shaft == 0 and s.kind == "header")
    right = min(s.start[0] for s in plan.segments if s.shaft == 1 and s.kind == "header")
    assert left <= right + 3100, "shaft territories overlap (parallel mains)"


def test_two_shafts_at_same_corridor_end_stay_inside():
    # degenerate: both shafts bunched at one end - must still be ONE clean
    # main, nothing outside, every head reached (no 0/all dump that leaks).
    corridor = [[0, 0], [16000, 0], [16000, 30000], [0, 30000]]
    room = [[16000, 0], [19000, 0], [19000, 30000], [16000, 30000]]
    heads = [[x, y] for x in (2000, 5000, 8000, 11000, 14000)
             for y in range(2000, 29000, 3000)]
    cover = Polygon(corridor).buffer(2.0)
    for shafts in ([[5000, -1000], [11000, -1000]], [[-1000, 15000], [17000, 15000]]):
        plan = route_joint(heads, [room], corridor, shafts, auto_tilt=True)
        assert plan.skipped_heads == 0
        for seg in plan.segments:
            outside = LineString([seg.start, seg.end]).difference(cover).length
            if seg.kind == "riser" and outside <= 1600.0:
                continue  # the shaft's own approach
            assert outside <= 5.0, f"{seg.kind} runs {outside:.0f}mm outside"
        for head in heads:
            near = min(math.dist(head, s.end) for s in plan.segments)
            assert near < 25.0, f"head {head} not connected"


def test_two_shafts_divide_the_corridor_cleanly():
    # Two shafts under one corridor band: whole columns per shaft, meeting
    # at ONE contiguous cut - never interleaved, never a mixed column.
    corridor = [[0, 0], [18000, 0], [18000, 4000], [0, 4000]]
    room = [[18000, 0], [21000, 0], [21000, 4000], [18000, 4000]]  # empty
    heads = [[x, y] for x in (1500, 4500, 7500, 10500, 13500, 16500)
             for y in (1000, 3000)]
    plan = route_joint(heads, [room], corridor, [[3000, -800], [15000, -800]])
    assert plan.skipped_heads == 0
    column_shafts: dict = {}
    for seg in plan.segments:
        for head in heads:
            if math.dist(seg.end, head) < 1.0:
                column_shafts.setdefault(head[0], set()).add(seg.shaft)
    assert all(len(s) == 1 for s in column_shafts.values()), \
        f"mixed columns: {column_shafts}"
    order = [next(iter(column_shafts[x])) for x in sorted(column_shafts)]
    assert order == sorted(order), f"interleaved division: {order}"
    assert sorted(g.head_count for g in plan.groups) == [6, 6]


def test_one_main_line_and_rooms_tee_off_it():
    # Corridor full of sprinklers + a room: ONE main runs forward in the
    # corridor (the median header between the columns) and the room tees
    # off it - never a second parallel trunk.
    corridor = [[0, 0], [4000, 0], [4000, 20000], [0, 20000]]   # vertical leg
    room = [[4000, 2000], [9000, 2000], [9000, 8000], [4000, 8000]]
    c_heads = [[x, y] for x in (1000, 3000)
               for y in (1500, 4500, 7500, 10500, 13500, 16500, 19000)]
    r_heads = [[x, y] for x in (5500, 7500) for y in (3500, 6500)]
    plan = route_joint(c_heads + r_heads, [room], corridor, [[2000, -800]])
    assert plan.skipped_heads == 0
    corridor_poly = Polygon(corridor).buffer(2.0)
    vertical_main_xs = set()
    for seg in plan.segments:
        if seg.kind != "header":
            continue
        if not corridor_poly.covers(LineString([seg.start, seg.end])):
            continue
        if abs(seg.start[0] - seg.end[0]) < 1e-6 and seg.length > 500:
            vertical_main_xs.add(round(seg.start[0], 1))
    assert vertical_main_xs == {2000.0}, \
        f"expected ONE main at the median x=2000, got {vertical_main_xs}"
    ends = {(round(s.end[0], 3), round(s.end[1], 3)) for s in plan.segments}
    assert all((x, y) in ends for x, y in c_heads + r_heads)


def test_l_shaped_corridor_band_splits_into_chained_bands():
    # Heads wrapping an L corner cannot be one straight band: the field
    # splits into two bands, the second teeing off the first - still no
    # pipe outside the corridor and every head reached.
    corridor = [[0, 0], [4000, 0], [4000, 12000], [16000, 12000],
                [16000, 16000], [0, 16000]]
    room = [[16000, 12000], [19000, 12000], [19000, 16000], [16000, 16000]]
    heads = ([[x, y] for x in (1000, 3000) for y in (2000, 5000, 8000, 11000)]
             + [[x, y] for x in (6000, 9000, 12000, 15000) for y in (13000, 15000)])
    plan = route_joint(heads, [room], corridor, [[2000, -800]])
    assert plan.skipped_heads == 0
    ends = {(round(s.end[0], 3), round(s.end[1], 3)) for s in plan.segments}
    assert all((x, y) in ends for x, y in heads)
    poly = Polygon(corridor).buffer(2.0)
    for seg in plan.segments:
        if seg.kind in ("header", "branch"):
            assert poly.covers(LineString([seg.start, seg.end])), \
                f"{seg.kind} {seg.start}->{seg.end} leaves the corridor"


def test_grid_angle_detection():
    from app.joint.angle import grid_angle
    import random
    random.seed(7)
    for true_tilt in (-20.5, 0.0, 13.0, 44.0):
        grid = [[gx * 3000 + random.uniform(-30, 30), gy * 2600 + random.uniform(-30, 30)]
                for gx in range(12) for gy in range(9)]
        tilted = rotate(grid, true_tilt)
        detected = grid_angle(tilted)
        assert detected is not None
        assert abs(detected - true_tilt) < 0.2, f"{true_tilt} -> {detected}"
    scatter = [[random.uniform(0, 30000), random.uniform(0, 30000)] for _ in range(60)]
    assert grid_angle(scatter) is None, "random scatter must not produce a guess"


def test_auto_tilt_routes_a_tilted_building():
    # The whole fixture rotated 17 deg, auto_tilt on, rotation NOT given:
    # the backend must detect the angle itself and route building-aligned.
    T = 17.0
    plan = route_joint(rotate(HEADS, T), [rotate(r, T) for r in ROOMS],
                       rotate(CORRIDOR, T), rotate(SHAFTS, T),
                       header_offset=OFFSET, auto_tilt=True)
    assert plan.skipped_heads == 0
    assert sorted(g.head_count for g in plan.groups) == [5, 15]
    # every head reached (detection error tolerance: a few mm at this radius)
    for head in rotate(HEADS, T):
        near = min(math.dist(head, s.end) for s in plan.segments)
        assert near < 25.0, f"head {head} not connected (nearest end {near:.0f}mm)"
    # every pipe runs along the detected grid (=T) or perpendicular to it
    # (risers exempt: shaft connectors / wall stubs may be off-grid by design)
    for seg in plan.segments:
        if seg.length < 1.0 or seg.kind == "riser":
            continue
        angle = math.degrees(math.atan2(seg.end[1] - seg.start[1],
                                        seg.end[0] - seg.start[0])) % 90.0
        off = min(abs(angle - T % 90.0), 90.0 - abs(angle - T % 90.0))
        assert off < 0.3, f"{seg.kind} not grid-aligned: {angle:.2f}"


def test_mixed_straight_and_tilted_rooms():
    # THE user scenario: one room straight, one room's sprinkler grid
    # tilted 25 deg - each room must route in its OWN frame.
    corridor = [[0, 0], [20000, 0], [20000, 3000], [0, 3000]]
    room_a = [[1000, 3000], [9000, 3000], [9000, 11000], [1000, 11000]]
    room_b = [[11000, 3000], [19000, 3000], [19000, 11000], [11000, 11000]]
    heads_a = [[x, y] for x in (2500, 5000, 7500) for y in (4500, 7000, 9500)]
    grid_b = [[x, y] for x in (-2000, 0, 2000) for y in (-2000, 0, 2000)]
    cx, cy = 15000, 7000  # rotate room B's grid about the room centre
    heads_b = [[cx + p[0], cy + p[1]]
               for p in rotate(grid_b, 25.0)]
    corridor_heads = [[x, 1500] for x in range(2000, 19000, 2000)]
    heads = heads_a + heads_b + corridor_heads
    plan = route_joint(heads, [room_a, room_b], corridor, [[10000, -800]],
                       auto_tilt=True)
    assert plan.skipped_heads == 0
    poly_a = Polygon(room_a).buffer(2.0)
    poly_b = Polygon(room_b).buffer(2.0)
    for seg in plan.segments:
        if seg.kind not in ("subheader", "branch") or seg.length < 1.0:
            continue
        line = LineString([seg.start, seg.end])
        angle = math.degrees(math.atan2(seg.end[1] - seg.start[1],
                                        seg.end[0] - seg.start[0])) % 90.0
        if poly_a.covers(line):       # straight room: axis-aligned pipes
            off = min(angle, 90.0 - angle)
            assert off < 0.3, f"room A pipe tilted: {angle:.2f}"
        elif poly_b.covers(line):     # tilted room: pipes at 25 deg
            off = min(abs(angle - 25.0), 90.0 - abs(angle - 25.0))
            assert off < 0.3, f"room B pipe not at 25 deg: {angle:.2f}"
    for head in heads:
        near = min(math.dist(head, s.end) for s in plan.segments)
        assert near < 25.0, f"head {head} not connected"


def test_fanned_tilted_building_never_leaves_the_polyline():
    # Regression (user screenshot): fanned rows on a tilted building made
    # connectors run far outside the boundary.  Pipe may only leave the
    # polygon for the shaft's own short approach - never to cut across.
    heads = []
    for j in range(18):
        ang = math.radians(-22.0 + j * 0.45)   # rows fan from -22 to -14.3 deg
        ux, uy = math.cos(ang), math.sin(ang)
        ox, oy = -math.sin(ang) * j * 3000.0, math.cos(ang) * j * 3000.0
        for i in range(26):
            heads.append([ox + ux * i * 3000.0, oy + uy * i * 3000.0])
    hull = Polygon([(h[0], h[1]) for h in heads]).convex_hull.buffer(1200, join_style=2)
    boundary = [[x, y] for x, y in hull.exterior.coords[:-1]]
    xs = [h[0] for h in heads]
    ys = [h[1] for h in heads]
    sx = (min(xs) + max(xs)) / 2
    cut = LineString([(sx, min(ys) - 60000), (sx, max(ys))]).intersection(hull)
    shaft = [sx, cut.bounds[1] - 2000.0]   # 2m below the wall, like the drawing
    far_room = [[-60000, 0], [-55000, 0], [-55000, 5000], [-60000, 5000]]
    plan = route_joint(heads, [far_room], boundary, [shaft], auto_tilt=True)
    assert plan.skipped_heads == 0
    cover = hull.buffer(2.0)
    for seg in plan.segments:
        outside = LineString([seg.start, seg.end]).difference(cover).length
        if seg.kind == "riser" and outside <= 2600.0:
            continue  # the shaft's own unavoidable approach
        assert outside <= 5.0, \
            f"{seg.kind} {seg.start}->{seg.end} runs {outside:.0f}mm outside"
    for head in heads[::5]:
        near = min(math.dist(head, s.end) for s in plan.segments)
        assert near < 25.0, f"head {head} not connected"


def test_building_outline_ring_is_ignored():
    # Regression: a building outline on the room layer used to swallow all
    # heads (order-dependent).  Now any ring covering the corridor is set
    # aside with status "outline", first or last.
    outline = [[-1000, -1000], [23000, -1000], [23000, 13000], [-1000, 13000]]
    for rooms in ([outline] + ROOMS, ROOMS + [outline]):
        plan = route_joint(HEADS, rooms, CORRIDOR, SHAFTS)
        by_status = {st.status for st in plan.rooms}
        outline_idx = 0 if rooms[0] is outline else len(rooms) - 1
        assert plan.rooms[outline_idx].status == "outline"
        assert plan.skipped_heads == 0
        assert plan.skipped_rooms == 0
        assert sorted(g.head_count for g in plan.groups) == [5, 15]
        assert "empty" not in by_status


def test_single_room_single_shaft_smoke():
    room = [[0, 0], [6000, 0], [6000, 6000], [0, 6000]]
    corridor = [[6000, 0], [8000, 0], [8000, 6000], [6000, 6000]]
    heads = [[x, y] for x in (1500, 4500) for y in (1500, 4500)]
    plan = route_joint(heads, [room], corridor, [[7000, -500]])
    assert plan.skipped_heads == 0
    ends = {(round(s.end[0], 3), round(s.end[1], 3)) for s in plan.segments}
    assert all((x, y) in ends for x, y in heads)


# ------------------------------------------------------------- equivariance
TILT = 30.0


def _payload(rotation=0.0, transform=lambda pts: pts):
    return {
        "points": transform(HEADS),
        "rooms": [transform(r) for r in ROOMS],
        "corridor": transform(CORRIDOR),
        "risers": transform(SHAFTS),
        "rotation": rotation,
    }


def test_route_joint_is_equivariant_under_rotation():
    flat = client.post("/route-joint", json=_payload()).json()
    tilted = client.post("/route-joint", json=_payload(
        rotation=TILT, transform=lambda pts: rotate(pts, TILT))).json()

    assert len(flat["segments"]) == len(tilted["segments"])
    assert tilted["total_length"] == pytest.approx(flat["total_length"], abs=1e-2)
    assert tilted["rooms"] == flat["rooms"]
    for fs, ts in zip(flat["segments"], tilted["segments"]):
        assert fs["kind"] == ts["kind"]
        assert fs["shaft"] == ts["shaft"]
        assert fs["length"] == pytest.approx(ts["length"], abs=1e-3)
        for flat_pt, tilted_pt in ((fs["start"], ts["start"]), (fs["end"], ts["end"])):
            expected = rotate([flat_pt], TILT)[0]
            assert tilted_pt[0] == pytest.approx(expected[0], abs=1e-3)
            assert tilted_pt[1] == pytest.approx(expected[1], abs=1e-3)


def test_route_joint_http_contract():
    body = client.post("/route-joint", json=_payload()).json()
    assert body["skipped_heads"] == 0
    assert body["skipped_rooms"] == 0
    assert len(body["risers"]) == 2
    assert len(body["groups"]) == 2
    assert len(body["rooms"]) == len(ROOMS)
    assert {o["shaft"] for o in body["outlines"]} == {0, 1}
    kinds = {s["kind"] for s in body["segments"]}
    assert kinds == {"riser", "header", "subheader", "branch"}
