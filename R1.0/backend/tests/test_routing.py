import pytest

from app.geometry import dist
from app.routing import build_outlines, route

# 3 rows x 3 cols grid, spacing 3000
GRID = [[x, y] for y in (0, 3000, 6000) for x in (0, 3000, 6000)]


def test_every_segment_length_matches_endpoints():
    plan = route(GRID, hazard="Light", risers=[[-2000, 3000]])
    for seg in plan.segments:
        assert seg.length == pytest.approx(dist(seg.start, seg.end))
    assert plan.total_length == pytest.approx(sum(s.length for s in plan.segments))


def test_canary_mid_main_tap_flows_both_directions():
    """Riser tapping the middle of the cross main must yield main segments
    pointing both directions away from the tap."""
    plan = route(GRID, hazard="Light", risers=[[-2000, 3000]])
    mains = [s for s in plan.segments if s.kind == "main"]
    up = [s for s in mains if s.end[1] > s.start[1]]
    down = [s for s in mains if s.end[1] < s.start[1]]
    assert up and down, f"expected both directions, got {mains}"
    # every main segment starts nearer the tap (y=3000) than it ends
    for s in mains:
        assert abs(s.start[1] - 3000) < abs(s.end[1] - 3000)


def test_riser_segment_connects_riser_to_main():
    riser = [-2000, 3000]
    plan = route(GRID, hazard="Light", risers=[riser])
    riser_segs = [s for s in plan.segments if s.kind == "riser"]
    assert len(riser_segs) == 1
    assert riser_segs[0].start == riser            # flow leaves the riser
    assert riser_segs[0].end == [0, 3000]          # tap on the main (min-X column)
    assert plan.risers == [riser]
    assert plan.groups[0].head_count == len(GRID)


def test_no_riser_roots_at_min_y_no_riser_segment():
    plan = route(GRID, hazard="Light")
    assert all(s.kind != "riser" for s in plan.segments)
    assert plan.risers == [[0, 0]]                 # min-X main, min-Y tap
    # all main flow goes upward from the root
    for s in plan.segments:
        if s.kind == "main":
            assert s.end[1] > s.start[1]


def test_branches_flow_outward_from_main():
    plan = route(GRID, hazard="Light", risers=[[-2000, 0]])
    for s in plan.segments:
        if s.kind == "branch":
            assert abs(s.end[0] - 0) >= abs(s.start[0] - 0)  # away from main at x=0


def test_riser_in_room_center_splits_branches_both_sides():
    """Main through the middle: branch flow must point away from it on both sides."""
    riser = [3000, 3000]  # center column of the grid
    plan = route(GRID, hazard="Light", risers=[riser])
    branches = [s for s in plan.segments if s.kind == "branch"]
    rightward = [s for s in branches if s.end[0] > s.start[0]]
    leftward = [s for s in branches if s.end[0] < s.start[0]]
    assert rightward and leftward
    for s in branches:
        assert abs(s.end[0] - 3000) >= abs(s.start[0] - 3000) - 1e-9


def test_hazard_omitted_infers_spacing():
    plan = route(GRID, risers=[[-2000, 0]])        # no hazard
    # same row structure as with an explicit hazard: 3 branch rows reachable
    branch_ys = {round(s.end[1]) for s in plan.segments if s.kind == "branch"}
    assert branch_ys == {0, 3000, 6000}


def test_single_head_with_riser():
    """A single head is fed directly: one riser segment straight to the head."""
    plan = route([[5000, 5000]], risers=[[0, 0]])
    assert len(plan.segments) == 1
    assert plan.segments[0].kind == "riser"
    assert plan.segments[0].start == [0, 0]
    assert plan.segments[0].end == [5000, 5000]


def test_empty_points_raises():
    with pytest.raises(ValueError):
        route([], hazard="Light")


def test_chained_row_drift_does_not_collapse_the_main():
    """Rows at y=0/2000/4000 with pairwise gaps below tol (Light tol=2286)
    used to merge into ONE row via single-linkage drift, deleting the cross
    main and emitting overlapping duplicate pipes."""
    heads = [[x, y] for y in (0, 2000, 4000) for x in (0, 3000)]
    plan = route(heads, hazard="Light", risers=[[-2000, 0]])
    mains = [s for s in plan.segments if s.kind == "main"]
    assert mains, "cross main vanished"
    # no two segments may be the same pipe drawn in opposite directions
    seen = {(tuple(s.start), tuple(s.end)) for s in plan.segments}
    assert all((b, a) not in seen for a, b in seen)


def test_tee_sits_on_entry_head_row():
    """Jittered row: the tee must use the entry head's y, not a phantom mean."""
    heads = [[0, 100], [3000, 0], [6000, 150]]
    plan = route(heads, hazard="Light", risers=[[-2000, 100]])
    branches = [s for s in plan.segments if s.kind == "branch"]
    # first branch leaves the tee at the entry head's y (100), horizontally
    first = branches[0]
    assert first.start == [0, 100]
    assert first.end == [3000, 0]


def test_collinear_heads_single_row():
    row = [[x, 0] for x in (0, 3000, 6000, 9000)]
    plan = route(row, hazard="Light", risers=[[-2000, 0]])
    assert all(s.kind != "main" for s in plan.segments)  # one row -> no main run
    riser_segs = [s for s in plan.segments if s.kind == "riser"]
    assert len(riser_segs) == 1 and riser_segs[0].end == [0, 0]
    # the head at [0,0] sits exactly at the tap, so only 3 pipes are needed
    branches = [s for s in plan.segments if s.kind == "branch"]
    assert len(branches) == 3
    assert branches[-1].end == [9000, 0]


# --- multiple shafts -------------------------------------------------------

def test_two_shafts_divide_heads_by_distance():
    """Heads split into two clusters; each routes to its nearest shaft."""
    heads = [[x, y] for y in (0, 3000) for x in (0, 3000, 12000, 15000)]
    shafts = [[-2000, 1500], [17000, 1500]]
    plan = route(heads, hazard="Light", risers=shafts)
    assert plan.risers == shafts
    assert [g.head_count for g in plan.groups] == [4, 4]
    assert plan.total_length == pytest.approx(sum(s.length for s in plan.segments))
    # no pipe may span the empty middle between the clusters (x 3000..12000)
    for s in plan.segments:
        lo, hi = sorted((s.start[0], s.end[0]))
        assert not (lo < 6000 < hi), f"segment crosses between shaft groups: {s}"
    # group lengths add up to the total
    assert sum(g.length for g in plan.groups) == pytest.approx(plan.total_length)


def test_shaft_with_no_heads_reports_zero():
    heads = [[0, 0], [3000, 0]]
    shafts = [[0, -2000], [50000, 0]]                # all heads nearer shaft 1
    plan = route(heads, hazard="Light", risers=shafts)
    assert plan.groups[0].head_count == 2
    assert plan.groups[1].head_count == 0
    assert plan.groups[1].length == 0.0
    assert plan.risers == shafts


def test_three_shafts_each_feed_own_cluster():
    clusters = {
        0: [[0, 0], [2000, 0]],
        1: [[20000, 0], [22000, 0]],
        2: [[0, 20000], [2000, 20000]],
    }
    heads = [p for pts in clusters.values() for p in pts]
    shafts = [[-1000, 0], [23000, 0], [-1000, 20000]]
    plan = route(heads, risers=shafts)              # hazard omitted too
    assert [g.head_count for g in plan.groups] == [2, 2, 2]
    # every shaft's tree contains a riser segment leaving that shaft
    riser_starts = [s.start for s in plan.segments if s.kind == "riser"]
    for shaft in shafts:
        assert shaft in riser_starts


def test_segments_tagged_with_shaft_index():
    heads = [[0, 0], [3000, 0], [20000, 0], [23000, 0]]
    shafts = [[-1000, 0], [24000, 0]]
    plan = route(heads, hazard="Light", risers=shafts)
    shaft_of = {tuple(s.end): s.shaft for s in plan.segments}
    assert all(s.shaft in (0, 1) for s in plan.segments)
    assert shaft_of[(3000, 0)] == 0
    assert shaft_of[(20000, 0)] == 1


def test_staggered_row_uses_perpendicular_drops_not_zigzag():
    """Offset heads must hang from a PERPENDICULAR drop off the branch line
    (a tee directly above the head + a vertical stub), never a diagonal."""
    heads = [[0, 0], [3000, 800], [6000, 0], [9000, 800]]
    plan = route(heads, hazard="Light", risers=[[-2000, 0]])
    branches = [s for s in plan.segments if s.kind == "branch"]
    # every branch segment is purely horizontal or purely vertical
    for s in branches:
        assert s.start[0] == s.end[0] or s.start[1] == s.end[1], f"diagonal: {s}"
    # the line passes through the drop feet: [0,0]->[3000,0]->[6000,0]->[9000,0]
    line = sorted([s for s in branches if s.start[1] == 0 and s.end[1] == 0],
                  key=lambda s: s.start[0])
    assert [(s.start[0], s.end[0]) for s in line] == [(0, 3000), (3000, 6000), (6000, 9000)]
    # vertical drops, 800mm, flowing from the line down to each offset head
    drops = [s for s in branches if s.end[1] == 800]
    assert len(drops) == 2
    for d in drops:
        assert d.start == [d.end[0], 0]
        assert d.length == pytest.approx(800)
    # every head is reached
    ends = {tuple(s.end) for s in plan.segments}
    for h in heads[1:]:  # heads[0] sits at the tee itself
        assert tuple(h) in ends


def test_main_never_runs_through_other_shafts_heads():
    """Slanted territory split: shaft B's desired main column passes through
    rows owned by shaft A.  The main must slide to a column fully inside
    B's own territory instead of running through A's sprinklers."""
    heads = [[x, y] for y in (0, 3000, 6000, 9000) for x in (0, 3000, 6000, 9000, 12000)]
    shafts = [[0, 0], [6000, 12000]]
    plan = route(heads, hazard="Light", risers=shafts)

    # ownership cut per row (x = 15000 - 2y, ties -> shaft 0):
    owner = {}
    for x, y in heads:
        owner[(x, y)] = 0 if x <= 15000 - 2 * y else 1

    for s in plan.segments:
        if s.kind != "main":
            continue
        x = s.start[0]
        y_lo, y_hi = sorted((s.start[1], s.end[1]))
        for (hx, hy), shaft in owner.items():
            if shaft == s.shaft:
                continue
            near_column = abs(hx - x) <= 400
            inside_span = y_lo - 400 <= hy <= y_hi + 400
            assert not (near_column and inside_span), (
                f"shaft {s.shaft} main at x={x} runs through shaft {shaft} head ({hx},{hy})")


def test_staggered_row_split_keeps_feet_in_own_territory():
    """A staggered row split between two shafts: each shaft's branch line and
    drop feet must stay within its own contiguous span (row-projected
    assignment), never extending through the other shaft's heads."""
    heads = ([[x, 0] for x in range(0, 12001, 3000)]
             + [[x, 900] for x in (1500, 4500, 7500, 10500)])
    shafts = [[0, -3000], [12000, -3000]]
    plan = route(heads, hazard="Light", risers=shafts)
    # shaft 0 owns projected x <= 6000, shaft 1 the rest
    for s in plan.segments:
        xs = (s.start[0], s.end[0])
        if s.shaft == 0:
            assert max(xs) <= 6000 + 1e-6, f"shaft0 segment crosses east: {s}"
        else:
            assert min(xs) >= 7500 - 1e-6, f"shaft1 segment crosses west: {s}"


# --- merged double-line outlines (clean elbows / tees / crosses) -----------

def test_outline_tee_is_one_clean_polygon():
    """Riser + main + branch meeting must union into a single outline with
    no construction lines crossing the pipe interior (no holes)."""
    heads = [[0, 0], [3000, 0], [6000, 0]]
    plan = route(heads, hazard="Light", risers=[[0, -3000]])
    outlines = build_outlines(plan.segments, 32, 65)
    assert len(outlines) == 1                      # one merged ring, zero holes
    shaft, ring = outlines[0]
    assert shaft == 0 and len(ring) >= 6
    # every segment midpoint lies inside the merged pipe body
    from shapely.geometry import Point, Polygon
    body = Polygon(ring)
    for s in plan.segments:
        mid = Point((s.start[0] + s.end[0]) / 2, (s.start[1] + s.end[1]) / 2)
        assert body.covers(mid), f"{s.kind} centreline escapes the outline"


def test_outline_four_way_cross_junction():
    """Riser below + main above + branches both sides = a 4-way cross at the
    tap; the union must still be a single clean polygon."""
    heads = [[-3000, 0], [0, 0], [3000, 0], [0, 3000]]
    plan = route(heads, hazard="Light", risers=[[0, -3000]])
    outlines = build_outlines(plan.segments, 32, 65)
    assert len(outlines) == 1
    _, ring = outlines[0]
    assert len(ring) >= 8                          # cross silhouette


def test_outlines_one_polygon_per_shaft():
    heads = [[0, 0], [3000, 0], [20000, 0], [23000, 0]]
    shafts = [[-2000, 0], [25000, 0]]
    plan = route(heads, hazard="Light", risers=shafts)
    outlines = build_outlines(plan.segments, 32, 65)
    assert sorted({shaft for shaft, _ in outlines}) == [0, 1]


# --- room boundary constraint ----------------------------------------------

L_ROOM = [[0, 0], [12000, 0], [12000, 4000], [4000, 4000], [4000, 12000], [0, 12000]]


def test_boundary_filters_outside_heads():
    heads = [[2000, 2000], [8000, 2000], [20000, 20000]]   # last one far outside
    plan = route(heads, hazard="Light", risers=[[1000, 1000]],
                 boundary=[[0, 0], [12000, 0], [12000, 4000], [0, 4000]])
    assert plan.skipped_heads == 1
    assert plan.groups[0].head_count == 2
    ends = [tuple(s.end) for s in plan.segments]
    assert (20000, 20000) not in ends


def test_boundary_keeps_main_inside_l_room():
    """Without the boundary the main would run at x=10000 straight through
    the notch; with it the main must move to a column inside the L."""
    heads = ([[2000, y] for y in (2000, 5000, 8000, 11000)]
             + [[x, 2000] for x in (5000, 8000, 11000)])
    plan = route(heads, hazard="Light", risers=[[11000, 1000]], boundary=L_ROOM)
    from shapely.geometry import LineString, Polygon
    room = Polygon(L_ROOM).buffer(2.0)
    for s in plan.segments:
        line = LineString([s.start, s.end])
        assert room.covers(line), f"{s.kind} segment leaves the room: {s}"
    mains = [s for s in plan.segments if s.kind == "main"]
    assert mains and all(s.start[0] == 2000 for s in mains)


def test_all_heads_outside_boundary_raises():
    with pytest.raises(ValueError):
        route([[50000, 50000]], boundary=[[0, 0], [1000, 0], [1000, 1000], [0, 1000]])
