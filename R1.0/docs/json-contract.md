# JSON Contract — Sprinkler Design Backend

Shared schema between the FastAPI backend (`backend/app/models.py`) and the
ZWCAD plugin (`plugin/src/Dtos.cs`). All coordinates are `[x, y]` arrays in
**millimetres**; the drawing is assumed to use 1 unit = 1 mm.

Base URL: `http://127.0.0.1:9000`

Hazard classes: `"Light"` | `"Ordinary"` | `"Extra"` (NFPA-13).

---

## POST /place

Compute sprinkler head positions for a closed room boundary.

### Request
```json
{
  "boundary": [[0, 0], [10000, 0], [10000, 8000], [0, 8000]],
  "hazard": "Light"
}
```
- `boundary` — closed polygon vertices, ≥ 3 points (do not repeat the first
  point at the end; both forms are accepted).
- `hazard` — hazard class, selects spacing `S = min(max_spacing, sqrt(max_area))`.

### Response `200`
```json
{
  "points": [[2285.8, 2285.8], [2285.8, 5714.2], [5000.0, 2285.8],
             [5000.0, 5714.2], [7714.2, 2285.8], [7714.2, 5714.2]],
  "spacing": 4571.65,
  "coverage_radius": 3232.65,
  "count": 6
}
```
- `coverage_radius` — radius (mm) for the coverage circle drawn per head
  (`0.707 * S`).

---

## POST /validate

Check head positions against NFPA-13 rules.

### Request
```json
{
  "boundary": [[0, 0], [10000, 0], [10000, 8000], [0, 8000]],
  "points": [[2285.8, 2285.8], [5000.0, 4000.0]],
  "hazard": "Light"
}
```

### Response `200`
```json
{
  "passed": false,
  "rules": [
    { "rule": "max_area",         "passed": true,  "detail": "room area 80.0 m2; ..." },
    { "rule": "max_spacing",      "passed": true,  "detail": "worst nearest-neighbour distance 3175 mm <= max 4600 mm" },
    { "rule": "min_head_spacing", "passed": true,  "detail": "all pairs >= 1800 mm" },
    { "rule": "min_wall_dist",    "passed": true,  "detail": "all heads inside boundary and >= 100 mm from walls" },
    { "rule": "wall_coverage",    "passed": false, "detail": "2 of 4 wall edge(s) have no head within 2286 mm" },
    { "rule": "full_coverage",    "passed": false, "detail": "5 of 63 interior samples not covered (radius 3233 mm)" }
  ],
  "failing_heads": []
}
```
- `failing_heads` — heads that individually violate `min_head_spacing` or
  `min_wall_dist`; the plugin marks these red on layer `SPK-FAIL`.
- Layout-level failures (`wall_coverage`, `full_coverage`) have no single
  offending head and are reported in `rules` only.

Rule names: `max_area`, `max_spacing`, `min_head_spacing`, `min_wall_dist`
(≥ 100 mm clearance, head inside boundary), `wall_coverage` (every wall edge
has a head within `S/2`), `full_coverage` (interior sampled on an `S/4` grid,
every sample within `0.707*S` of a head).

---

## POST /route

Compute the pipe network (structured tree: branch rows + cross main + riser
connection — not an MST). Supports **multiple shafts**: each head is assigned
to its nearest shaft, and each shaft routes an independent tree over its
share of the heads.

### Request
```json
{
  "points": [[2285.8, 2285.8], [5000.0, 2285.8], [2285.8, 5714.2], [5000.0, 5714.2]],
  "hazard": "Light",
  "risers": [[-1500, 4000]]
}
```
- `hazard` — *optional*. When omitted, the row-clustering tolerance is
  inferred from the heads (median nearest-neighbour distance / 2). The
  plugin's SPKROUTE omits it.
- `risers` — *optional* list of shaft/start points. Heads are divided among
  them by nearest distance. When omitted, one tree roots at the min-Y end of
  the cross main and no `riser` segment is emitted.
- `riser` — *legacy* single shaft, equivalent to `risers` with one entry
  (`risers` wins when both are present).
- `boundary` — *optional* room polygon. Heads outside it are ignored (counted
  in `skipped_heads`), the cross main is moved to a head column that stays
  inside the room, and shaft connections prefer an L-shaped run over a
  straight diagonal when the diagonal would leave the room.

### Response `200`
```json
{
  "segments": [
    { "start": [-1500, 4000],   "end": [2285.8, 4000],   "kind": "riser",  "length": 3785.8 },
    { "start": [2285.8, 4000],  "end": [2285.8, 5714.2], "kind": "main",   "length": 1714.2 },
    { "start": [2285.8, 4000],  "end": [2285.8, 2285.8], "kind": "main",   "length": 1714.2 },
    { "start": [2285.8, 2285.8],"end": [5000.0, 2285.8], "kind": "branch", "length": 2714.2 },
    { "start": [2285.8, 5714.2],"end": [5000.0, 5714.2], "kind": "branch", "length": 2714.2 }
  ],
  "risers": [[-1500, 4000]],
  "groups": [
    { "riser": [-1500, 4000], "head_count": 4, "length": 12642.4 }
  ],
  "total_length": 12642.4
}
```
- **Flow direction**: every segment is oriented `start → end` in the flow
  direction (upstream → downstream, away from its shaft). The plugin draws
  the centred flow arrow pointing from `start` to `end` — no inference needed.
  A riser tapping the middle of the main produces main segments running in
  *both* directions away from the tap.
- `kind` — `riser` | `main` | `branch`. The plugin draws `riser`/`main` at the
  main pipe width and `branch` at the branch width.
- `shaft` (per segment) — index into `risers`; the plugin colours each
  shaft's network differently (shaft 0 red, 1 yellow, 2 green, ...).
- `risers` (response) — the effective tree roots, one per shaft (echo the
  request shafts, or the chosen main end when none were given).
- `groups` — per-shaft summary: the shaft point, how many heads were assigned
  to it (nearest-shaft division), and that tree's pipe length. A shaft with
  no nearby heads appears with `head_count: 0`.
- `skipped_heads` — heads dropped for lying outside `boundary` (0 without one).

---

## POST /route-joint

Joint architecture: multiple **rooms** around a **corridor**. A main
**header** runs from each shaft through the corridor and taps into every
room; each room is fed by a **sub-header** that sits beside its sprinkler
columns (`header_offset` mm away — never on top of the heads), with branch
rows to the heads. Joint mode draws **no diagonals**: every connector is a
straight axis-aligned run or an L through one corner.

### Request
```json
{
  "points": [[1000, 2000], [2000, 2000], [1000, 6000], [2000, 6000]],
  "rooms": [[[0, 0], [3000, 0], [3000, 8000], [0, 8000]]],
  "corridor": [[3000, 0], [5000, 0], [5000, 8000], [3000, 8000]],
  "risers": [[3400, -600]],
  "branch_width": 32, "main_width": 65,
  "header_offset": 300,
  "rotation": 0
}
```
- `rooms` — one closed polygon per room (≥ 1). Heads are assigned to rooms
  by point-in-polygon; heads inside the corridor hang off the header; heads
  in neither are counted in `skipped_heads`.
- `corridor` — the corridor polygon. The header graph stays ≥ 100 mm off
  its walls (relaxed automatically for very thin corridors).
- `risers` — shaft start points (≥ 1). Each room goes to the shaft with the
  **cheapest header path through the corridor** (graph distance, not
  straight-line — straight-line misjudges U-shaped corridors).
- `header_offset` — how far the room sub-header sits beside the sprinkler
  column (default 300 mm). In rooms too narrow for any offset position the
  sub-header falls back onto the column rather than leaving the room.
- `hazard` *(optional)* and `rotation` behave exactly as in `/route`.

### Response `200`

Same shape as `/route` plus a per-room report:

```json
{
  "segments": [ { "start": [3400, -600], "end": [3400, 99], "kind": "riser", "length": 699, "shaft": 0 } ],
  "outlines": [ { "shaft": 0, "points": [[3367.5, -600], [3432.5, -600], "..."] } ],
  "risers": [[3400, -600]],
  "groups": [ { "riser": [3400, -600], "head_count": 4, "length": 14210.0 } ],
  "rooms":  [ { "index": 0, "head_count": 4, "status": "tapped", "shaft": 0 } ],
  "total_length": 14210.0,
  "skipped_heads": 0,
  "skipped_rooms": 0
}
```
- `kind` — `riser` (shaft connector + room entry stub) | `header` (corridor
  main) | `subheader` (room header) | `branch`. Everything except `branch`
  is drawn at the main width.
- `rooms[].status` — `tapped` (shared wall with the corridor found) |
  `fallback` (no shared wall; connected through the nearest point) |
  `empty` (no heads inside; not piped) | `outline` (the ring covers the
  whole corridor — a building outline, ignored as a room) | `skipped`
  (unusable polygon or more than ~800 mm from the corridor). Heads inside
  an untappable room are not lost: they fall through to another covering
  room or to the corridor; only heads in neither count in `skipped_heads`.
- With multiple shafts each shaft builds its **own header tree** (they meet
  mid-corridor rather than forming one continuous loop), coloured per shaft
  like `/route`.

---

## Errors (all endpoints)

```json
{ "error": "geometry_error", "message": "invalid boundary: Self-intersection[5000 5000]" }
```
- `400 geometry_error` — degenerate/self-intersecting boundary, no points, ...
- `422 validation_error` — request shape does not match the schema.

The plugin surfaces `message` on the ZWCAD command line and aborts without
drawing anything.
