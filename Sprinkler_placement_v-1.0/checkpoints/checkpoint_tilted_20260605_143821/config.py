"""
config.py — ALL placement hyperparameters in one place.

Units: millimetres (mm).
How to apply a change: edit the value, save, RESTART the backend
(uvicorn). The C# plugin does NOT need a rebuild for anything here.

Head-to-head pitch per scenario (Fixed 3000 etc.) is NOT here — it lives
in the SCENARIOS table at the bottom of placement.py (space_min/space_max).
"""

# ── Boundary pull ───────────────────────────────────────────────────
# out_cov: how far OUTSIDE the main polyline a sprinkler CENTER may be
#          and still get pulled back in (measured to the center point
#          only, not the circle). Farther out → the head is deleted.
# in_cov:  how far INSIDE the polyline the pulled sprinkler lands —
#          it stays on its own grid line (column or row).
#   bigger out_cov → more outside heads get pulled in (fewer gaps,
#                    more heads near walls)
#   bigger in_cov  → pulled heads sit deeper inside the room
# out_cov = one grid bay: the row/column just outside ANY wall (also
# interior step walls and gently sloping walls) is always within one bay
# of it, so every wall strip gets its on-grid pull. Heads farther outside
# than this are deleted.
out_cov = 1000.0
in_cov  = 400.0

# on_cov: when a sprinkler CENTER sits exactly ON the main polyline
#         (on the boundary line itself), it is pulled this far inside,
#         staying on its own grid line.
on_cov  = 300.0

# On slanted walls the pull can land with less clearance than INSET;
# the head is then pushed deeper along its grid line in DEEPEN_STEP
# increments, giving up after DEEPEN_LIMIT extra.
DEEPEN_STEP  = 100.0
DEEPEN_LIMIT = 1200.0

# ── Wall clearance ──────────────────────────────────────────────────
# Regular grid heads must keep at least this distance from every wall;
# closer ones are deleted. (Pulled heads use BOUNDARY_NUDGE_INSET.)
# WALL_CLEARANCE_MIN = 800.0
WALL_CLEARANCE_MIN = 700.0

# No two heads may ever be closer than this (stops pile-ups when pulled
# heads land near existing grid heads). This is also the FILL-DENSITY
# dial for the boundary pulls:
#   smaller  → more pulls placed (full, consistent wall rows, denser)
#   ~1500.0  → pulls only where no other head covers the spot (sparser,
#              but rows can stop mid-wall on gently sloping walls)
MIN_HEAD_SEPARATION = 500.0

# ── Narrow corridors ────────────────────────────────────────────────
# Rooms narrower than 2 × WALL_CLEARANCE_MIN relax the clearance so a
# single centered row still fits:
#   clearance = short_side / 2 − CORRIDOR_RELAX_MARGIN
# but never below CORRIDOR_MIN_CLEARANCE. Side effect: anything
# narrower than ~2 × CORRIDOR_MIN_CLEARANCE (stray sliver outlines)
# gets zero heads.
CORRIDOR_RELAX_MARGIN  = 50.0
CORRIDOR_MIN_CLEARANCE = 150.0

# When True, a polyline that fully CONTAINS another polyline on the room
# layer (e.g. a bounding rectangle drawn around the real rooms, or the
# building outline) is ignored — only the inner/real rooms get heads.
DROP_CONTAINER_ROOMS = True

# ── Room filters ────────────────────────────────────────────────────
# Rooms whose LONGEST side is below MIN_ROOM_DIM are skipped entirely
# (closets / tiny voids). Rooms whose SHORT side is below
# MIN_ROOM_SHORT_SIDE are skipped as sliver/degenerate outlines
# (e.g. a double-drawn wall line).
MIN_ROOM_DIM        = 2000.0
MIN_ROOM_SHORT_SIDE = 500.0

# ── Default spacing bands ───────────────────────────────────────────
# Used by the web/DXF routes as defaults. The plugin's Fixed scenarios
# override these via the SCENARIOS table.
WALL_MIN  = 1000
WALL_MAX  = 1500
SPACE_MIN = 2400
SPACE_MAX = 3200
SPACE_NOM = 2900

# ── Centered-grid residual handling (range scenarios only) ──────────
# When the centered grid's outer row sits farther past the wall band
# than these, the grid is stretched (or given one extra row). Fixed
# scenarios (space_min == space_max) are never affected — their pitch
# is exact by definition.
RESIDUAL_STRETCH_THRESHOLD  = 600.0
RESIDUAL_NEW_LINE_THRESHOLD = 800.0
