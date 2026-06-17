"""Probe harness for the joint corridor-band change."""
import math
from app.joint.routing import route_joint, _shared_corridor_band
from app.routing import _BOUNDARY_SLACK
from shapely.geometry import Polygon, Point, LineString


def conserve(name, points, rooms, corridor, shafts, **kw):
    plan = route_joint(points, rooms, corridor, shafts, **kw)
    routed = sum(g.head_count for g in plan.groups)
    total = routed + plan.skipped_heads
    ok = (total == len(points))
    print(f"[{name}] heads={len(points)} routed={routed} "
          f"skipped={plan.skipped_heads} total={total} "
          f"{'OK' if ok else '*** MISMATCH ***'}")
    print("    groups:", [g.head_count for g in plan.groups])
    return plan, ok


HZ = "Light"
CORR = [[0, 0], [60000, 0], [60000, 4000], [0, 4000]]
HEADS = [[x, 2000] for x in range(4000, 56000, 4000)]
SHAFTS = [[5000, -600], [55000, -600]]
conserve("A straight 2-shaft", HEADS, [], CORR, SHAFTS, hazard=HZ)

HEADS2 = [[x, y] for x in range(4000, 56000, 4000) for y in (1000, 3000)]
conserve("B straight 2-shaft 2-row", HEADS2, [], CORR, SHAFTS, hazard=HZ)

HEADS3 = [[x, 2000] for x in range(4000, 60000, 4000)]
conserve("C odd count", HEADS3, [], CORR, SHAFTS, hazard=HZ)

SHAFTS_CO = [[30000, -600], [30300, -600]]
conserve("D co-located shafts", HEADS, [], CORR, SHAFTS_CO, hazard=HZ)

SHAFTS3 = [[5000, -600], [30000, -600], [55000, -600]]
conserve("E three shafts", HEADS, [], CORR, SHAFTS3, hazard=HZ)

print("\n--- direct _shared_corridor_band ---")
poly = Polygon(CORR).buffer(_BOUNDARY_SLACK)
for nm, hd, sh in [
    ("sb-A", HEADS, SHAFTS),
    ("sb-B", HEADS2, SHAFTS),
    ("sb-C", HEADS3, SHAFTS),
]:
    res = _shared_corridor_band([list(h) for h in hd], [list(s) for s in sh],
                                poly, 300.0, HZ, [list(h) for h in hd])
    if res is None:
        print(f"  [{nm}] -> None")
        continue
    tot = sum(c for _, _, c in res)
    print(f"  [{nm}] shafts={[k for k,_,_ in res]} counts={[c for _,_,c in res]} "
          f"sum={tot} heads={len(hd)} {'OK' if tot==len(hd) else '*** LOST/DUP ***'}")
