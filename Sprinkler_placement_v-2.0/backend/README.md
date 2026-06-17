# Sprinkler Auto-Placement Backend

**Version 1.0** — FastAPI backend for NFPA-13-style sprinkler layout. Computes
grid placement, gap-fill, coverage statistics, and (optionally) genetic-algorithm
optimisation, then ships results as JSON, LISP S-expressions, or ZIP-packaged
AutoLISP scripts.

The full per-endpoint contract (request bodies, response shapes, error codes)
is in **[`API_CONTRACT.md`](./API_CONTRACT.md)**. The C# ZWCAD plugin in
`../plugin/` is built against that contract.

## Layout

```
backend/
├── main.py                 ← FastAPI app + global exception handler + router includes
├── models.py               ← Pydantic request bodies (CreateBlocksRequest, ZWCADScenarioRequest)
├── routes/
│   ├── _shared.py          ← safe_json, run_placement (DXF→points pipeline), bbox helpers
│   ├── health.py           ← GET /, /api/health, /api/scenarios
│   ├── dxf.py              ← /api/layers, /api/geometry
│   ├── placement.py        ← /api/preview, /api/scenarios/generate, /api/scenarios/{id}/download, /api/generate
│   ├── ga.py               ← /api/ga/* including SSE
│   ├── blocks.py           ← /api/blocks/create-from-points
│   └── zwcad.py            ← /api/zwcad/scenarios
├── geometry.py             ← polygons, distances, SpatialHash, NumPy batch point-in-poly
├── dxf_loader.py           ← DXF → polygons / wall segments (4-strategy parser)
├── placement.py            ← grid placement, NFPA-13 spacing, gap-fill, SCENARIOS,
│                              run_scenario_for_floors (picklable worker for parallel scenarios)
├── area_stats.py           ← coverage / floor-area statistics
├── lsp_writer.py           ← AutoLISP file generation + ZIP packaging
├── genetic_placement.py    ← GA optimiser (optional)
├── sprinkler_coverage_to_blocks.py ← standalone CLI (not exposed via API)
├── blockssprinkler.dxf     ← block library used by /api/blocks/create-from-points
├── zwcad_plugin.lsp        ← legacy AutoLISP plugin (calls /api/zwcad/scenarios directly)
├── API_CONTRACT.md         ← full endpoint spec (authoritative)
├── PROGRESS_STREAMING_GUIDE.md
└── requirements.txt
```

The non-route Python modules (`geometry.py`, `placement.py`, ...) stay at
`backend/` root because they're already cohesive — moving them under `core/`
would just be churn.

## Setup

```bash
cd backend
python -m venv venv
venv\Scripts\activate          # Windows
# or: source venv/bin/activate # macOS/Linux
pip install -r requirements.txt
```

## Run

```bash
uvicorn main:app --reload --port 8000
```

You should see `Uvicorn running on http://127.0.0.1:8000`. Open
`http://localhost:8000/docs` for Swagger UI.

## Quick smoke test

```bash
# Liveness
curl http://localhost:8000/api/health
# → {"status":"ok","version":"1.0.0","ezdxf":"1.4.3","scenarios":[...]}

# ZWCAD endpoint (no DXF needed)
curl -X POST http://localhost:8000/api/zwcad/scenarios \
  -H "Content-Type: application/json" \
  -d '{"room_polys":[[[0,0],[5000,0],[5000,4000],[0,4000]]],"scenario_ids":[1,4,10]}'
# → ((1 ((1250.0 2000.0) (3750.0 2000.0))) (4 (...)) (10 (...)))

# DXF preview (requires a sample.dxf with ZONE-01 and S-A-WALL layers)
curl -X POST http://localhost:8000/api/preview \
  -F "file=@sample.dxf"
```

## Endpoints (overview)

See `API_CONTRACT.md` for the authoritative spec. Quick map:

| Method | Path                                       | Purpose |
|-------:|--------------------------------------------|---------|
| GET    | `/`                                        | Liveness banner |
| GET    | `/api/health`                              | Status + scenario IDs |
| GET    | `/api/scenarios`                           | Full scenario definitions |
| POST   | `/api/layers`                              | List layers in uploaded DXF |
| POST   | `/api/geometry`                            | Floor / wall / excl / obs polylines from DXF |
| POST   | `/api/preview`                             | Run placement, return points + zone reports + stats |
| POST   | `/api/scenarios/generate`                  | Run all 10 scenarios on one DXF |
| POST   | `/api/scenarios/{id}/download`             | ZIP (LSP + stats) for one scenario |
| GET    | `/api/ga/presets`                          | Available GA presets |
| POST   | `/api/ga/optimise`                         | Grid + GA, return both |
| POST   | `/api/ga/download`                         | Grid + GA, return ZIP |
| GET    | `/api/ga/progress`                         | SSE: live GA progress |
| POST   | `/api/blocks/create-from-points`           | DXF with block inserts at given points |
| POST   | `/api/generate`                            | Legacy combined run+ZIP |
| POST   | `/api/zwcad/scenarios`                     | JSON in, LISP S-expression out (used by ZWCAD plugin) |

## Using with the ZWCAD plugin (C#)

The C# plugin in `../plugin/` calls `/api/health` (for `SPKHEALTH`) and
`/api/zwcad/scenarios` (for `/AUTO-SPRINKLER`). See `../plugin/README.md` for
build and load instructions.

### Behaviour notes for `/api/zwcad/scenarios`

- **Parallel scenarios.** The route dispatches each requested scenario to a
  shared `ProcessPoolExecutor` (max 3 workers, lazy-initialised on first
  request, reused for the lifetime of the server). Total wall time ≈ slowest
  scenario, not the sum of all three. The worker function
  `placement.run_scenario_for_floors` is at module top level so it's
  picklable and child processes only import `placement` + `geometry` (not
  FastAPI), keeping spawn cost low on Windows.
- **`enable_gap_fill` toggle.** The plugin exposes a Yes/No prompt for this.
  Setting it to `False` skips the iterative gap-detection phase, which is
  the most expensive part of large-room placement.
- **`tilted` toggle (Straight vs Tilted architecture).** When `False`
  (default) the route uses the original axis-aligned `generate_zone_sprinklers`
  and emits zero rotation per point. When `True`, each room's longest-edge
  angle is detected via `geometry.find_longest_edge_angle`, the placement
  runs in the rotated local frame, and points are returned in world coords
  carrying their per-room rotation in radians so the plugin can rotate the
  inserted blocks to match.
- **Per-point rotation in the LISP response.** Each point is now a
  3-tuple `(x y rotation_radians)` instead of `(x y)`. Two-element points
  are still accepted by the C# parser for backward compatibility.
- **NumPy-vectorised sample grid.** `find_uncovered_gaps`,
  `coverage_fraction`, and `precompute_sample_grid` use a vectorised
  `points_in_poly_batch` that tests an `(N, 2)` array of grid points
  against a polygon in one pass — typically 20–100× faster than the
  per-point ray cast on dense sample grids.
- **Centered grid with parity preservation.** `placement.make_grid_lines`
  always centers the grid in the bbox. When the residual to the wall is
  600–800 mm the spacing is stretched to push the outer rows into the
  wall band; when residual > 800 mm a row is added — but **only** if the
  current row count is even (adding the row makes it odd, gaining a row
  through the room's middle). With an odd row count the centered layout
  is preserved so a sprinkler always sits in the middle of the room
  rather than being biased to the right or left.
- **Narrow rooms / corridors get a centered row.** A room is skipped
  only if **both** its bbox dimensions are below 2000 mm (closet / void
  threshold). Above that, even rooms narrower than `2 × wall_min` (e.g.
  a 1500 mm-wide corridor with `wall_min = 1000`) receive sprinklers:
  the wall-distance check is locally relaxed to
  `max(150 mm, short_side / 2 − 50 mm)` so the centered single-row
  placement passes validation. The relaxation is also forwarded to the
  gap-fill phase so extras can be placed in the same room.

## Using with the LISP plugin

The bundled `zwcad_plugin.lsp` is a pure-LISP variant that calls
`/api/zwcad/scenarios` via MSXML/WinHttp:

1. Start the backend (`uvicorn main:app --reload --port 8000`)
2. In ZWCAD, `APPLOAD` the file `zwcad_plugin.lsp`
3. Run command `SPK`
4. Select the outer rectangle polyline (white boundary)
5. Enter the room polyline layer name (e.g. green room polylines)
6. Choose scenario option:
   - `1` → scenario ID `1`
   - `2` → scenario ID `4`
   - `3` → scenario ID `10`

Note: this LISP file currently points at port `8091` — edit
`*spk-backend-url*` at the top of the file if you run the backend on `8000`.

## Disclaimer

This tool assists **layout exploration**. Always verify designs against
applicable codes, insurance, and project specifications.

---

© 2026 Shreshtha Consultants. All rights reserved.
