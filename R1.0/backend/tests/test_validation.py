from app.placement import place
from app.validation import validate

ROOM_20x20 = [[0, 0], [20000, 0], [20000, 20000], [0, 20000]]
ROOM_10x8 = [[0, 0], [10000, 0], [10000, 8000], [0, 8000]]


def rules_by_name(report):
    return {r.rule: r for r in report.rules}


def test_canary_20x20_light_room_passes():
    """Interior heads ~10 m from walls must NOT fail any wall rule.

    This is the canary for the broken literal reading of "wall_dist <= S/2
    for all heads".
    """
    pts = place(ROOM_20x20, "Light")
    report = validate(ROOM_20x20, pts, "Light")
    assert report.passed, [r for r in report.rules if not r.passed]
    assert report.failing_heads == []


def test_placed_layout_passes_for_all_hazards():
    for hazard in ("Light", "Ordinary", "Extra"):
        pts = place(ROOM_10x8, hazard)
        report = validate(ROOM_10x8, pts, hazard)
        assert report.passed, (hazard, [r for r in report.rules if not r.passed])


def test_close_pair_fails_min_spacing_and_lists_both():
    pts = [[5000, 4000], [6000, 4000]]  # 1000 mm apart < 1800
    report = validate(ROOM_10x8, pts, "Light")
    assert not report.passed
    assert not rules_by_name(report)["min_head_spacing"].passed
    assert [5000, 4000] in report.failing_heads
    assert [6000, 4000] in report.failing_heads


def test_head_hugging_wall_fails_clearance():
    pts = place(ROOM_10x8, "Light") + [[10, 4000]]  # 10 mm from the left wall
    report = validate(ROOM_10x8, pts, "Light")
    assert not rules_by_name(report)["min_wall_dist"].passed
    assert [10, 4000] in report.failing_heads


def test_head_outside_boundary_fails_clearance():
    pts = [[5000, 4000], [15000, 4000]]
    report = validate(ROOM_10x8, pts, "Light")
    assert not rules_by_name(report)["min_wall_dist"].passed
    assert [15000, 4000] in report.failing_heads


def test_single_corner_head_fails_coverage_in_big_room():
    pts = [[500, 500]]
    report = validate(ROOM_20x20, pts, "Light")
    by_name = rules_by_name(report)
    assert not by_name["full_coverage"].passed
    assert not by_name["wall_coverage"].passed
