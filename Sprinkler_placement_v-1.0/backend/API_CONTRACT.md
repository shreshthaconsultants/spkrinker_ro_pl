# Sprinkler Auto-Placement API — Contract

**Base URL:** `http://localhost:8000`
**Content type for JSON endpoints:** `application/json`
**Content type for file-upload endpoints:** `multipart/form-data`
**Auth:** none. **CORS:** not configured (call from same origin or use a proxy).
**Units:** all coordinates and offsets are in **millimetres**.

All field names are case-sensitive. Errors are returned as `{ "detail": "<message>" }`
(plus an extra `"hint"` field on uncaught 500s — see [Error envelope](#error-envelope)).

---

### GET /
Description: liveness banner.
Response (200, `text/plain`):
```
Sprinkler backend is running. Use API endpoints or ZWCAD LISP plugin.
```

---

### GET /api/health
Description: structured liveness probe + scenario IDs.
Response (200):
```json
{
  "status":  "ok",
  "version": "1.0.0",
  "ezdxf":   "1.4.3",
  "scenarios": [
    { "id": 1, "name": "Standard NFPA-13" },
    { "id": 2, "name": "Dense Coverage" },
    ...
    { "id": 10, "name": "Institutional" }
  ]
}
```

---

### GET /api/scenarios
Description: full scenario definitions (spacing rules per scenario).
Response (200): array of objects:
```json
[
  {
    "id":              1,
    "name":            "Standard NFPA-13",
    "description":     "Nominal spacing 2900mm, wall band 1000–1500mm",
    "space_min":       2400,
    "space_max":       3200,
    "wall_min":        1000,
    "wall_max":        1500,
    "coverage_radius": 1500
  },
  ... (10 entries total)
]
```

---

### POST /api/layers
Description: list every layer in an uploaded DXF.
Request body (`multipart/form-data`):
- `file`: a `.dxf` file (required)

Response (200):
```json
{
  "layers":       ["LAYER-A", "LAYER-B", ...],
  "count":        12,
  "layer_detail": {
    "LAYER-A": { "count": 4, "types": ["LWPOLYLINE", "LINE"] },
    ...
  }
}
```
Errors:
- `400` — file is not a `.dxf` or could not be parsed.

---

### POST /api/geometry
Description: extract closed polylines and wall segments from a DXF for canvas drawing.
Request body (`multipart/form-data`):
- `file`: `.dxf` (required)
- `floor_layers`: comma-separated string (default `""`)
- `wall_layers`:  comma-separated string (default `""`)
- `excl_layers`:  comma-separated string (default `""`)
- `obs_layers`:   comma-separated string (default `""`)

Response (200):
```json
{
  "floor_polys": [[[x, y], ...], ...],
  "excl_polys":  [[[x, y], ...], ...],
  "obs_polys":   [[[x, y], ...], ...],
  "wall_segs":   [[x1, y1, x2, y2], ...],
  "floor_bbox":  [minx, maxx, miny, maxy] | null
}
```
Errors:
- `400` — DXF parse failure.

---

### POST /api/preview
Description: run grid placement on an uploaded DXF and return points + zone reports + coverage stats.
Request body (`multipart/form-data`):
- `file`: `.dxf` (required)
- `floor_layers`: string, default `"ZONE-01,ZONE-02"`
- `wall_layers`:  string, default `"S-A-WALL,wall"`
- `excl_layers`:  string, default `"S-S-COLS,S-A-STAIRS"`
- `obs_layers`:   string, default `"obs"`
- `obs_min_offset`: float, default `150.0`
- `radius`:          int, default `1500`
- `wall_min`:        int, default `1000`
- `wall_max`:        int, default `1500`
- `space_min`:       int, default `2400`
- `space_max`:       int, default `3200`
- `coverage_radius`: int, default `1500`
- `enable_gap_fill`: bool, default `true`

Response (200):
```json
{
  "points":       [[x, y], ...],
  "extra_points": [[x, y], ...],
  "zones": [
    {
      "zone": 1, "count": 12, "extra_count": 3, "rejected": 0, "warnings": [],
      "width_m": 12.0, "height_m": 9.0,
      "spacing_x": { ... }, "spacing_y": { ... },
      "grid_cols": 4, "grid_rows": 3,
      "x_lines": [...], "y_lines": [...],
      "x_offset_mm": 1500.0, "y_offset_mm": 1500.0
    }, ...
  ],
  "total":       15,
  "floor_bbox":  [minx, maxx, miny, maxy],
  "stats":       { ... },
  "floor_polys": [...],
  "excl_polys":  [...],
  "obs_polys":   [...],
  "wall_segs":   [...]
}
```
Errors:
- `400` — DXF parse failure or invalid spacing parameters.
- `404` — no floor polygons found on requested layers.

---

### POST /api/scenarios/generate
Description: run all 10 scenarios on the same DXF and return per-scenario stats and points.
Request body (`multipart/form-data`):
- `file`: `.dxf` (required)
- `floor_layers`: string, default `"ZONE-01,ZONE-02"`
- `wall_layers`:  string, default `"S-A-WALL,wall"`
- `excl_layers`:  string, default `"S-S-COLS,S-A-STAIRS"`
- `obs_layers`:   string, default `"obs"`
- `obs_min_offset`: float, default `150.0`
- `radius`:        int, default `1500`
- `chunk_size`:    int, default `2000`
- `use_block`:     bool, default `false`
- `block_name`:    string, default `"SPRINKLER"`

Response (200):
```json
{
  "scenarios": [
    {
      "scenario_id": 1, "scenario_name": "Standard NFPA-13",
      "total_sprinklers": 12, "grid_sprinklers": 10, "extra_sprinklers": 2,
      "coverage_radius_mm": 1500.0, "single_circle_area_m2": 7.07,
      "avg_sprinkler_area_m2": 6.5, "theoretical_coverage_m2": 78.0,
      "floor_area_m2": 80.0, "effective_covered_m2": 78.5, "uncovered_m2": 1.5,
      "coverage_pct": 98.13, "floor_per_sprinkler_m2": 6.67,
      "zone_count": 1, "zones": [...], "scenario": { ... },
      "spacing_rules": { "wall_band": "1000–1500", "spacing": "2400–3200" },
      "points": [[x, y], ...], "extra_points": [[x, y], ...],
      "floor_bbox": [minx, maxx, miny, maxy]
    },
    ... (10 entries; one may carry `"error": "<msg>"` instead of stats)
  ]
}
```

---

### POST /api/scenarios/{scenario_id}/download
Description: build and download a ZIP for a single scenario (LSP files + stats JSON + README).
Path parameter:
- `scenario_id`: integer 1..10

Request body (`multipart/form-data`): same fields as `POST /api/scenarios/generate`.

Response (200, `application/zip`):
- Body: zipped LSP files + `grid_sprinklers.txt` + `gap_fill_sprinklers.txt` + `scenario_stats.json` + `README.txt`
- Header `X-Sprinkler-Commands: SPKL_SC{id}[,SPKL_SC{id}2,...]` — AutoCAD command names defined in each LSP file
- Header `Content-Disposition: attachment; filename=sprinklers_scenario_{id}.zip`

Errors:
- `404` — `Scenario {scenario_id} not found`.

---

### GET /api/ga/presets
Description: list available genetic-algorithm presets.
Response (200):
```json
{
  "available": true,
  "presets": {
    "fast":      { "pop_size": 20, "generations": 30, ... },
    "balanced":  { "pop_size": 40, "generations": 60, ... },
    "thorough":  { "pop_size": 80, "generations": 120, ... }
  }
}
```
Errors:
- `503` — `Genetic algorithm module not available.`

---

### POST /api/ga/optimise
Description: run grid placement, then a GA pass per zone; return both for comparison.
Request body (`multipart/form-data`):
- All fields from `POST /api/preview`, **plus**:
- `ga_preset`: string `"fast"|"balanced"|"thorough"`, default `"balanced"`
- `ga_seed`:   int, default `-1` (use system random)

Response (200):
```json
{
  "grid": {
    "points": [...], "extra_points": [...],
    "stats": {...}, "floor_bbox": [...],
    "floor_polys": [...], "excl_polys": [...], "obs_polys": [...], "wall_segs": [...],
    "zones": [...]
  },
  "ga": {
    "points": [...], "extra_points": [...],
    "stats": {...}, "zone_reports": [...],
    "fitness_logs":  [[float, ...], ...],
    "coverage_logs": [[float, ...], ...],
    "preset": "balanced"
  },
  "comparison": {
    "grid_count": 12, "ga_count": 11, "count_delta": -1,
    "grid_coverage_pct": 92.5, "ga_coverage_pct": 96.0, "coverage_delta": 3.5
  }
}
```
Errors:
- `503` — `genetic_placement.py not found. Place it next to main.py.`
- `400` — unknown GA preset name.
- `500` — `GA optimisation failed: <message>`.

---

### POST /api/ga/download
Description: same as `/api/ga/optimise` but returns a ZIP of LSP files for the GA result.
Request body (`multipart/form-data`): all fields from `POST /api/ga/optimise`, **plus**:
- `chunk_size`: int, default `2000`
- `use_block`:  bool, default `false`
- `block_name`: string, default `"SPRINKLER"`

Response (200, `application/zip`): ZIP with header `X-Sprinkler-Commands: SPKL_GA[,SPKL_GA2,...]`.

Errors: same as `/api/ga/optimise`.

---

### GET /api/ga/progress
Description: Server-Sent Events stream of GA optimisation progress.
Connect to this BEFORE (or in parallel with) calling `/api/ga/optimise` or `/api/ga/download` to receive live updates.

Response (200, `text/event-stream`):
```
event: ga_progress
data: {"type":"ga_progress","zone":1,"total_zones":2,"generation":5,"fitness":0.87,"coverage_pct":92.4}

: heartbeat
```
- `event: ga_progress` events arrive whenever the GA reports a generation
- `: heartbeat` lines are sent every ~0.5s when no progress is queued
- Headers: `Cache-Control: no-cache`, `Connection: keep-alive`, `X-Accel-Buffering: no`

Frontend usage:
```javascript
const es = new EventSource('/api/ga/progress');
es.addEventListener('ga_progress', (e) => {
    const data = JSON.parse(e.data);
    // { type, zone, total_zones, generation, fitness, coverage_pct }
});
```

---

### POST /api/blocks/create-from-points
Description: create a DXF with block inserts (or fallback circles) at given points.
Request body (JSON):
```json
{
  "room_points":    [[x1, y1], [x2, y2], ...],
  "block_lib_path": "blockssprinkler.dxf",
  "block_name":     "PP-CEILING PENDANT",
  "insert_layer":   "SPRINKLERS"
}
```
Defaults applied if omitted: `block_lib_path="blockssprinkler.dxf"`, `block_name="PP-CEILING PENDANT"`, `insert_layer="SPRINKLERS"`.

Response (200, `application/vnd.opendesign`):
- Body: DXF bytes
- Header `Content-Disposition: attachment; filename=sprinklers_blocks.dxf`

Errors:
- `400` — `room_points required`.
- `500` — `Block generation failed: <message>`.

---

### POST /api/generate
Description: legacy combined "run placement and download ZIP" endpoint.
Request body (`multipart/form-data`):
- `file`: `.dxf` (required)
- `floor_layers`: string, default `"ZONE-01,ZONE-02"`
- `wall_layers`:  string, default `"S-A-WALL,S-A-WALL-,wall"`
- `excl_layers`:  string, default `"S-S-COLS,S-A-STAIRS,S-A-ELE-3,S-A-HACH,cut"`
- `obs_layers`:   string, default `"obs"`
- `obs_min_offset`: float, default `150.0`
- `radius`:        int, default `1500`
- `chunk_size`:    int, default `2000`
- `wall_min`:      int, default `1000`
- `wall_max`:      int, default `1500`
- `space_min`:     int, default `2400`
- `space_max`:     int, default `3200`
- `coverage_radius`: int, default `1500`
- `enable_gap_fill`: bool, default `true`
- `use_block`:     bool, default `false`
- `block_name`:    string, default `"SPRINKLER"`

Response (200, `application/zip`): same shape as `/api/scenarios/{id}/download`, header `X-Sprinkler-Commands: SPRINKLERS[,SPRINKLERS2,...]`.

Errors: `400`, `404` as for `/api/preview`.

---

### POST /api/zwcad/scenarios
Description: synchronous placement against in-memory room polylines (no DXF upload). Designed for the ZWCAD plugin — input and output are CAD-friendly.
Request body (JSON):
```json
{
  "room_polys":      [[[x, y], [x, y], ...], ...],
  "scenario_ids":    [1, 4, 10],
  "obs_min_offset":  150.0,
  "enable_gap_fill": true
}
```
- `room_polys`: list of closed polylines (each is a list of `[x, y]` pairs in mm). The endpoint auto-closes them if not already closed.
- `scenario_ids`: only `1`, `4`, `10` are honored. Other ids are silently dropped. If the resulting list is empty, `[1, 4, 10]` is used.
- `obs_min_offset`: float, default `150.0`.
- `enable_gap_fill`: bool, default `true`.

Response (200, `text/plain`): a single LISP S-expression listing each scenario's deduped points:
```
((1 ((1250.0 2000.0) (3750.0 2000.0)))
 (4 ((1250.0 3000.0) (3750.0 3000.0) (1250.0 1000.0) (3750.0 1000.0)))
 (10 ((1250.0 2000.0) (3750.0 2000.0))))
```
Empty point lists are encoded as `()`. Coordinates are rounded to 3 decimal places.

Errors:
- `400` — `No valid room polylines supplied.`

---

## Error envelope

Any uncaught exception is converted into a 500 by the global handler:
```json
{
  "detail": "<ExceptionType>: <message>",
  "hint":   "Check server console for full traceback"
}
```
Endpoints raise `HTTPException(status, detail)` with a single `detail` string for expected errors.

## Pydantic models

Defined in `backend/models.py`:

### `CreateBlocksRequest`
| field           | type                    | default                |
|-----------------|-------------------------|------------------------|
| `room_points`   | `list[list[float]]`     | required               |
| `block_lib_path`| `str`                   | `"blockssprinkler.dxf"`|
| `block_name`    | `str`                   | `"PP-CEILING PENDANT"` |
| `insert_layer`  | `str`                   | `"SPRINKLERS"`         |

### `ZWCADScenarioRequest`
| field             | type                          | default      |
|-------------------|-------------------------------|--------------|
| `room_polys`      | `list[list[list[float]]]`     | `[]`         |
| `scenario_ids`    | `list[int]`                   | `[1, 4, 10]` |
| `obs_min_offset`  | `float`                       | `150.0`      |
| `enable_gap_fill` | `bool`                        | `true`       |
