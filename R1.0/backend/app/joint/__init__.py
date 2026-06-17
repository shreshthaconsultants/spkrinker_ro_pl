"""Joint-architecture routing: corridor main header + per-room sub-headers.

Separate package from the full-mode pipeline (app.routing).  Rooms are
closed polylines on a room layer and the corridor on a corridor layer; a
main header runs from each shaft THROUGH the corridor and taps into every
room, and each room is fed by a sub-header that sits BESIDE the sprinkler
columns (slightly left/right), never on top of the heads.
"""
