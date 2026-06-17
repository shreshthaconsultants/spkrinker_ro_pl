"""Reproduce: room corridor-connector runs OVER a corridor sprinkler.

The room feed (routing.py ~337) computes foot = nearest point on a shaft
header to the room tap.  For a horizontal median main and a tap above it,
foot shares x with the tap, so _feed_segments hits the aligned early-return
and returns a straight vertical leg with NO _clear_of_heads check.  A
corridor head on that vertical line is run over.
"""
from shapely.geometry import LineString, Point, Polygon
from app.joint.routing import route_joint
from app.joint.graph import _HEAD_CLEARANCE

# Horizontal corridor; a band of corridor heads forms a median main near y~2700.
# A room sits on the TOP wall; its door is around x=9000.
# A corridor head is planted at (9000, 4200): between the median main (~y2700)
# and the room tap (just inside the top wall at y~6900), on the vertical x=9000.
corridor = [[0, 0], [18000, 0], [18000, 7000], [0, 7000]]
room = [[7500, 7000], [10500, 7000], [10500, 11000], [7500, 11000]]

# corridor band: two rows -> median main between them
band = [[x, y] for x in range(1500, 17000, 3000) for y in (1500, 3500)]
planted = [9000, 5500]          # on the x=9000 connector, above the main
room_heads = [[x, y] for x in (8200, 9800) for y in (8000, 9500)]

heads = band + [planted] + room_heads
shafts = [[9000, -800]]

plan = route_joint(heads, [room], corridor, shafts, header_offset=300.0)

print("skipped_heads:", plan.skipped_heads, "skipped_rooms:", plan.skipped_rooms)
print("room status:", [(r.index, r.status, r.shaft) for r in plan.rooms])

cover = Polygon(corridor).buffer(2.0)

# Which corridor headers/branches run OVER the planted head (non-endpoint)?
violations = []
for seg in plan.segments:
    line = LineString([seg.start, seg.end])
    ep = {(round(seg.start[0], 1), round(seg.start[1], 1)),
          (round(seg.end[0], 1), round(seg.end[1], 1))}
    if (round(planted[0], 1), round(planted[1], 1)) in ep:
        continue  # connecting TO it is fine
    d = line.distance(Point(planted))
    if d <= _HEAD_CLEARANCE and seg.kind in ("header", "branch", "subheader"):
        violations.append((seg.kind, seg.shaft, tuple(seg.start), tuple(seg.end),
                           round(d, 1)))

print("\nplanted head:", planted)
print("segments running OVER the planted head (<=150mm, non-endpoint):")
for v in violations:
    print("   ", v)
if not violations:
    print("    NONE")

# Is the planted head connected at all?
ends = {(round(s.end[0], 1), round(s.end[1], 1)) for s in plan.segments}
print("\nplanted head is a segment endpoint:",
      (round(planted[0], 1), round(planted[1], 1)) in ends)
