"""Automatic sprinkler-grid angle detection.

The user never types the building tilt: the grid angle is measured from
the sprinklers themselves.  Every head's nearest-neighbour direction is
folded modulo 90 degrees (a grid direction, not a heading) and averaged
on the 4-theta circle - the resultant length doubles as a CONFIDENCE
score, so irregular/random placements return None instead of a guess.

route_joint uses this per REGION: one global angle from the corridor
heads (the corridor graph and median headers live in that frame) and one
relative angle per room - a building where some rooms are straight and
some are tilted still routes every room like a straight one.
"""

import math

import numpy as np
import shapely

#: below this many heads the direction statistics are meaningless
_MIN_HEADS = 8

#: resultant length of the circular mean in [0, 1]; a regular grid gives
#: ~1.0, random scatter ~0 - refuse to guess below this
_MIN_CONFIDENCE = 0.6


def grid_angle(heads) -> float | None:
    """Tilt of the sprinkler grid in degrees, folded to (-45, 45].

    Returns None when there are too few heads or the placement is too
    irregular to measure (the caller then falls back to 0 / another
    region's angle).
    """
    if heads is None or len(heads) < _MIN_HEADS:
        return None
    pts = np.asarray(heads, dtype=float)
    geoms = shapely.points(pts)
    tree = shapely.STRtree(geoms)
    pairs = tree.query_nearest(geoms, exclusive=True)  # (2, M) src/dst pairs
    if pairs.shape[1] < _MIN_HEADS:
        return None
    deltas = pts[pairs[1]] - pts[pairs[0]]
    angles4 = 4.0 * np.arctan2(deltas[:, 1], deltas[:, 0])
    mean_x = float(np.cos(angles4).mean())
    mean_y = float(np.sin(angles4).mean())
    # Trimmed circular mean: a few diagonal neighbour pairs (heads at room
    # borders pairing across rooms) would drag the average off the true
    # grid angle - drop directions far from the consensus and re-average.
    kept_fraction = 1.0
    for _ in range(2):
        mean4 = math.atan2(mean_y, mean_x)
        deviation = (angles4 - mean4 + np.pi) % (2.0 * np.pi) - np.pi
        keep = np.abs(deviation) <= np.pi / 4.0
        if int(keep.sum()) < _MIN_HEADS:
            break
        kept_fraction = float(keep.sum()) / float(len(angles4))
        mean_x = float(np.cos(angles4[keep]).mean())
        mean_y = float(np.sin(angles4[keep]).mean())
    # trimming always concentrates random directions, so the resultant
    # alone would overstate confidence: scale it by the surviving fraction
    confidence = kept_fraction * math.hypot(mean_x, mean_y)
    if confidence < _MIN_CONFIDENCE:
        return None  # no dominant grid direction: don't guess
    return math.degrees(math.atan2(mean_y, mean_x) / 4.0)
