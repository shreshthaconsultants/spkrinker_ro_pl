"""Probe: _feed_segments None collapsing shared -> fallback; parallel mains."""
from app.joint.routing import (route_joint, _shared_corridor_band,
                                _feed_segments, _outside_len)
from app.routing import _BOUNDARY_SLACK
from shapely.geometry import Polygon, Point, LineString
import itertools


def describe(name, points, rooms, corridor, shafts, **kw):
    plan = route_joint(points, rooms, corridor, shafts, **kw)
    routed = sum(g.head_count for g in plan.groups)
    total = routed + plan.skipped_heads
    headers = [s for s in plan.segments if s.kind == "header"]
    # detect parallel/overlapping long header runs from DIFFERENT shafts
    longs = [s for s in headers if s.length > 3000]
    # group long headers by orientation and overlapping span
    print(f"[{name}] heads={len(points)} routed={routed} skipped={plan.skipped_heads}"
          f" total={total} {'OK' if total==len(points) else '*** MISMATCH ***'}"
          f" groups={[g.head_count for g in plan.groups]}")
    # report overlap between long headers of different shafts
    overlaps = []
    for a, b in itertools.combinations(longs, 2):
        if a.shaft == b.shaft:
            continue
        la = LineString([a.start, a.end]).buffer(400)
        lb = LineString([b.start, b.end])
        ov = lb.intersection(la).length
        if ov > 1000:
            overlaps.append((a.shaft, b.shaft, round(ov)))
    if overlaps:
        print(f"    *** PARALLEL/OVERLAP long headers (shaftA,shaftB,overlap_mm): {overlaps}")
    return plan


HZ = "Light"
CORR = [[0, 0], [60000, 0], [60000, 4000], [0, 4000]]
HEADS = [[x, 2000] for x in range(4000, 56000, 4000)]

# Shafts placed at the band tap area but offset in x so the feed corner may
# go outside. Below-corridor shafts whose column is well inside:
describe("F shafts below mid-x", HEADS, [], CORR,
         [[15000, -3000], [45000, -3000]], hazard=HZ)

# Shafts at far ends, below, with big x offset from corridor
describe("G shafts far-below ends", HEADS, [], CORR,
         [[2000, -2000], [58000, -2000]], hazard=HZ)

# A shaft horizontally outside the corridor x-range (to the left of x=0)
describe("H shaft left-of-corridor", HEADS, [], CORR,
         [[-3000, 2000], [58000, 2000]], hazard=HZ)

# Now directly check _shared returns for these
poly = Polygon(CORR).buffer(_BOUNDARY_SLACK)
for nm, sh in [("F", [[15000, -3000], [45000, -3000]]),
               ("G", [[2000, -2000], [58000, -2000]]),
               ("H", [[-3000, 2000], [58000, 2000]])]:
    res = _shared_corridor_band([list(h) for h in HEADS], [list(s) for s in sh],
                                poly, 300.0, HZ, [list(h) for h in HEADS])
    print(f"  shared[{nm}] -> {'None' if res is None else [(k,c) for k,_,c in res]}")
