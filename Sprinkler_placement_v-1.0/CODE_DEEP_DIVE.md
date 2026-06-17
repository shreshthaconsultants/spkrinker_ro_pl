# Code Deep Dive — Sprinkler Auto-Placement v1.0

> Produced 2026-06-04 by an 8-agent deep read of every source file, with cross-language
> seam verification and an adversarial completeness pass. Code references are exact.
> Reflects the codebase *after* the scenario-1 change (id 11 → Fixed 3000mm).

---

## 1. System at a glance

Two programs joined by HTTP and a LISP S-expression:

```
ZWCAD (C# plugin, .NET 4.8 x64)                Python backend (FastAPI, uvicorn :8000)
─────────────────────────────────              ─────────────────────────────────────────
/AUTO-SPRINKLER (alias SPK)                    main.py (6 routers, no CORS,
  Commands.cs: prompts → collect                 gc+malloc_trim middleware,
  polylines → build request                      catch-all 500 handler)
  ApiClient.cs ──── POST /api/zwcad/scenarios ──→ routes/zwcad.py
                                                   ProcessPoolExecutor (≤4 workers)
                                                   one child process per scenario
                                                   placement.run_scenario_for_floors
  LispParser.cs ←── "((sid ((x y rot)…) ())…)" ──┘
  DrawingHelper.cs: EnsureBlockBuilt (in-code
  PP-CEILING PENDANT) + InsertBlockAt(rot)
  SummaryDialog (client-side per-room counts)
```

The **only two endpoints the plugin calls** are `GET /api/health` (5 s timeout) and
`POST /api/zwcad/scenarios` (10 min timeout, cancellable). Everything else in the API
surface (preview, scenarios/generate, downloads, GA, blocks) serves a web UI that is
not in this repo, or is legacy.

### Scenario set (current)

`placement.py:859-972` defines **14 scenarios** (not 10 as the docs say):
ids 1–10 are the named NFPA-style range presets; ids **11–14 are the live set** the
plugin requests (`Commands.cs:182`):

| Picker | id | Name | space_min/max | wall band | coverage_radius |
|---|---|---|---|---|---|
| 1 | 11 | **Fixed 3000mm** (changed 2026-06-04, was Fixed 2400mm) | 3000/3000 | 1000–1500 | 1500 |
| 2 | 12 | Fixed 2700mm | 2700/2700 | 950–1350 | 1400 |
| 3 | 13 | Fixed 3000mm | 3000/3000 | 1000–1500 | 1500 |
| 4 | 14 | Fixed 3300mm | 3300/3300 | 1100–1650 | 1650 |

Slots 1 and 3 are now **identical by request** (user-confirmed duplicate).

---

## 2. The live placement algorithm (placement.py — what ACTUALLY runs)

The shipped algorithm (v3) is much simpler than the file's surface area suggests.
Per room (`generate_zone_sprinklers`, placement.py:493-665):

1. **Skip tiny rooms**: both bbox dims < 2000 mm → no heads (placement.py:526-549).
2. **Analytic spacing** (`fit_spacing`, :185-213): find integer n so `dim/n` lands in
   `[space_min, space_max]`. **Fixed** scenarios (min==max) bypass the equal-bay
   division since 2026-06-04: the exact pitch is used (`sx = sy = space_min`,
   placement.py just after :567) and the leftover space goes to the walls —
   previously a 10 m room at "Fixed 3000" got 3333 mm spacing.
3. **Grid lines** (`make_grid_lines`, :296-379): centered grid using a **fixed 300 mm
   bbox-edge margin** (`grid_edge_offset = BOUNDARY_NUDGE_MARGIN`, :574) — NOT the
   scenario wall band. Wall distance falls out as `max(300, (span−grid_span)/2)` ≈
   half a bay. Residual stretch/add-line logic (:363-374) only fires for *range*
   scenarios (needs new spacing inside the band — impossible when min==max).
4. **Keep / pull / drop** (final rule, 2026-06-05): every grid intersection inside
   the room polyline and ≥800 from walls is kept; one whose center is outside but
   within **`out_cov` (400 mm)** of the polyline is pulled back **along its own grid
   line** (column or row, shorter slide wins) to land **`in_cov` (400 mm)** inside —
   one coordinate always stays on the grid lattice, deepened in 100 mm steps on
   diagonal walls, and skipped if another head is within 800; anything farther out
   is silently dropped. All knobs live in `backend/config.py`. (`_nudge_inside` is
   now dead code — the pull uses `_line_polygon_crossings_x/y`.)
5. **Tilted mode** (`generate_zone_sprinklers_oriented`, :670-748): rotates the room
   into a local frame by the **longest-edge angle** (`find_longest_edge_angle`,
   geometry.py:161-194, folded to ±45°), places, rotates back; each head carries that
   angle as its rotation. (Comments saying "principal angle" are stale —
   `find_principal_angle` is imported but never called.)
6. **Cross-room dedup**: exact `(x,y)` duplicate removal in `run_scenario_for_floors`
   (:753-854), which is the picklable ProcessPoolExecutor worker.

### ⚠️ Accepted-but-IGNORED parameters in the live loop

This is the single most important thing to know about the backend:

| Parameter / feature | Status in live path |
|---|---|
| `obs_polys` / `obs_min_offset` | **Ignored.** `obs_segs` built (:586-588) but never tested. The plugin's obstacle-layer prompt has **no effect on placement**. |
| `excl_polys` (exclusion zones) | **Ignored** in the loop. |
| Head-to-head min spacing | **Not validated.** `spacing_hash` is populated (:628,639) but `any_within` is never queried; two heads 50 mm apart at a shared wall both survive (only exact-coordinate dedup runs). |
| Wall band (`wall_min`/`wall_max`) | Scenario fields still unused, **but** since 2026-06-04 a hard `WALL_CLEARANCE_MIN = 800` mm head-to-wall rule is enforced: grid edge offset, nudge inset, and a per-head `min_dist_to_segs` filter (relaxed to `short_side/2−50` in corridors < 1600 mm so they still get a centered row). `is_point_valid` (:218-266) remains never-called. |
| `enable_gap_fill` | **No-op.** Gap-fill was removed in v2; `extra_points` is always `[]` (:643-645). The plugin's Yes/No prompt does nothing. |
| `culled_points` / outside markers | **Always empty** (:599-601). The whole outside-marker chain (third LISP list → `DrawOutsideMarker` blue circles) is live code that never receives data. |

The scenario's `space_min/max` and the 300/600 nudge constants are the only knobs
that actually shape output; wall distances are *emergent* (≈ half a bay, min 300 mm).

### Dead code in placement.py
`GRID_SLIDE` machinery (`_slide_to_inside_along_grid` + crossing helpers, :42-151,
superseded by the nudge), `is_point_valid` (:218-266), `find_principal_angle` import.

---

## 3. The two cross-language contracts (verified compatible)

### A. HTTP JSON — `ApiClient.cs` ↔ `routes/zwcad.py` + `models.py`

Request (`ZwcadScenarioRequest`, Models.cs:27-43 == models.py:16-44 — field names match
exactly, JavaScriptSerializer is name-sensitive):
```json
{ "room_polys": [[[x,y],…]], "obs_polys": [[[x,y],…]],
  "scenario_ids": [11,12,13,14], "obs_min_offset": 150.0,
  "enable_gap_fill": true, "tilted": false }
```
- Backend honours **any** id present in `SCENARIOS` (all 14); empty/unknown list →
  runs **all 14** (zwcad.py:100-101). The contract doc's "only 1/4/10 honored" is wrong.
- Sync `def` route → runs in FastAPI threadpool; per-scenario child processes;
  wall time ≈ slowest scenario.
- Cancellation: `ApiClient._currentRequest` (volatile) + `Abort()` → worker's
  `WebException` → `ProgressDialog.RunWithPolling` converts to
  `OperationCanceledException` (Commands.cs:200). Health calls are not cancellable
  (only POST registers the request).

### B. LISP S-expression — `routes/zwcad.py:53-77` (producer) ↔ `LispParser.cs` (consumer)

Wire format (one line, space-joined, numbers+parens only):
```
((11 ((1250.0 2000.0 0.0) (3750.0 2000.0 0.523599)) ()) (12 () ()) …)
```
- Heads are `(x y rot)` triples — x,y rounded to 3 dp, rot (radians) to 6 dp.
  Second per-scenario list is the (always-empty) outside markers.
- Parser is deliberately tolerant: 2-element heads → rot=0; missing third list OK;
  `InvariantCulture` + `NumberStyles.Float` so `1e-06` rotations parse fine.
- **One real fragility**: no `isfinite` guard on the producer — a `nan`/`inf`
  coordinate would emit a literal `nan` token and the parser (no per-scenario
  try/catch, strict trailing-character check) would throw, **losing all scenarios
  at once**. Low probability, big blast radius.
- Duplicate ids in the request aren't deduped → duplicate chunks; C# last-wins.

### The OTHER LISP artifact (don't confuse them)
`lsp_writer.py` generates downloadable **.lsp command files** (`SPKL_SC{id}`,
`SPKL_GA`, chunked at 2000 points/file) for human APPLOAD in CAD — never parsed by
`LispParser.cs`. Notes: coordinates are written **unrounded**; `._INSERT` rotation is
hardcoded `0` (tilt is dropped on this path); the default `block_name="SPRINKLER"`
matches no block the plugin builds (`PP-CEILING PENDANT`).

---

## 4. Plugin internals (C#)

- **Flow** (Commands.cs:76-289): layer-name prompt (default `layerx`) → **no
  interactive rectangle pick** — the "rectangle" is auto-computed as the bbox of all
  LWPOLYLINEs on the layer (:296-336) — obstacle layer (optional, **not trimmed** —
  a trailing space silently yields 0 obstacles) → Straight/Tilted → gap-fill Y/N →
  collect polylines (LWPOLYLINE only, ≥4 verts, arithmetic-mean-centroid inside
  bbox) → POST → picker loop `[1/2/3/4/Exit]` → `PlaceBlocks` (erase-then-redraw via
  `EraseTracked`) → `SummaryDialog` on exit.
- **Threading**: all DB writes inside `Transaction` on the UI thread; only the HTTP
  POST runs on a background thread. Dialogs go through `Application.ShowModalDialog`
  (raw `Form.ShowDialog` from a `[CommandMethod]` crashes ZWCAD). Progress bar is a
  heuristic two-phase curve: linear 0→60 % in 3 s, then asymptotic →99 % with
  τ=120 s, capped at 99 % until real completion (ProgressDialog.cs:121-156).
- **Block** (`DrawingHelper.EnsureBlockBuilt`, :156-230): PP-CEILING PENDANT built
  in code — 24 lines + 12 elliptical arcs + 2 circles (inner ring r≈6.56; coverage
  circle **r=1500** on magenta `SPRINKLER COVERAGE` layer). Hand-transcribed from
  `blockssprinkler.dxf`; parity is **not enforced** — editing the DXF silently drifts.
- **SummaryDialog**: per-room head counts are computed **client-side** by
  ray-casting every placed head against each room polygon — they don't come from the
  backend. Heads on shared walls may double/zero-count; Σ(rooms) ≠ total is possible.
- **Geometry duplication**: three different point-in-polygon implementations exist
  (Commands.cs:381, SummaryDialog.cs:187, backend geometry.py:281).
- **install_autoload.reg**: writes `HKCU\Software\ZWSOFT\ZWCAD\2026\en-US\Applications\…`
  but its `LOADER` path points at `…OneDrive\Desktop\files (3)\plugin\bin\Debug\…` —
  **stale location; hand-edit before using**, or NETLOAD manually.

---

## 5. The GA (web-only — the plugin never uses it)

`genetic_placement.py` refines an existing grid layout per zone
(`/api/ga/optimise`, `/api/ga/download`):

- **Chromosome** = variable-length list of `(x,y)` head positions. Population seeded
  with 1 exact copy of the grid + N−1 heavily mutated copies (rate 0.4, σ×2).
- **Fitness** = `coverage%·100 − count_penalty·n − overlap_penalty·violations −
  spacing_penalty`. Coverage = fraction of a **precomputed sample grid** within
  `coverage_radius` of any head (SpatialHash; no circle-union math anywhere).
- **Operators**: tournament select, uniform crossover, Gaussian mutate (clamps to
  bbox, not polygon — heads can drift outside and only get penalised), insert-at-gap
  (rate 0.08, gaps rescanned every 3 generations while coverage <85 %), random
  deletion (rate 0.05, only after coverage ≥95 %). Stops after 15 stagnant
  generations (hardcoded) or the preset's budget (fast 20×30, balanced 40×60,
  thorough 80×120).
- **Known defects**: preset `crossover_rate` is only the do-crossover gate — the
  per-gene swap probability is hardcoded 0.75 (:513 vs :281); crossover tail
  handling duplicates the longer parent's tail into both children; `seed` uses
  global `random.seed()` (process-wide side effect, every zone restarts from the
  same RNG state); `obs_min_offset` threads through but is **never used**; progress
  callback pairs global-best fitness with current-generation coverage (numbers can
  visibly disagree).
- **SSE progress** (`/api/ga/progress`): ONE module-global `queue.Queue`, **reset on
  every connect** — a second monitor orphans the first; a run started before any
  monitor silently drops events. The guide's "each client gets its own queue" is wrong.

---

## 6. Geometry & DXF I/O

- `geometry.py`: ray-casting point-in-poly (scalar + NumPy batch), shoelace
  area/centroid, SpatialHash (cell = query radius → 3×3 scan), sampled coverage
  (`precompute_sample_grid` / `coverage_from_samples` / `find_uncovered_gaps`).
  No polygon offsetting, no exact circle coverage. No epsilon on edge-incident points.
- `dxf_loader.py`: 4-strategy robust loader (recover → BytesIO → UTF-8 → Latin-1).
  Extracts LWPOLYLINE/POLYLINE/CIRCLE (32-gon) on named layers.
  **ARC/ELLIPSE/SPLINE and polyline bulges are silently ignored** — curved walls
  become chords or vanish. `dict.fromkeys` dedup would corrupt self-touching polys.
- `area_stats.py`: `compute_scenario_stats` defaults to **fast_mode=True**, which is
  purely theoretical (`covered = min(n·πr², floor_area)`) — coverage_pct can read
  ~100 % while real gaps exist. The accurate sampled path exists (`fast_mode=False`,
  step=radius/10, pure-Python loop) but the GA uses a different sampler (radius/3,
  vectorised) — their numbers won't match exactly.

---

## 7. Bugs & fragilities, ranked

1. **Fixed scenarios break the web routes** — `_shared.py:122` rejects
   `space_min >= space_max`, so `/api/scenarios/{id}/download` for ids 11–14 →
   HTTP 400, and `/api/scenarios/generate` returns 4 error entries of 14. The plugin
   path (`run_scenario_for_floors`) has no guard and works. Fix = change guard to
   `>` (one char) if web downloads of Fixed scenarios are wanted.
2. **Obstacle avoidance is silently off** — plugin collects & sends `obs_polys`,
   backend discards them (see §2). Users believe the obstacle prompt works.
3. **Gap-fill prompt is a no-op** (feature removed backend-side).
4. **No spacing/wall validation** — placement is grid-construction only;
   `is_point_valid` orphaned.
5. **NaN/inf would kill an entire scenarios response** (LISP seam, low probability).
6. **GA SSE queue single-flight global** + GA crossover/seed defects (§5).
7. **fast_mode stats are theoretical** — don't trust `coverage_pct` from default-mode
   endpoints.
8. **blocks route**: CWD-relative `blockssprinkler.dxf` → silent fallback to 80 mm
   circles; broad `except` masks client errors as 500.
9. **install_autoload.reg LOADER path stale** (old `files (3)` location).
10. Minor: obstacle layer name untrimmed; LWPOLYLINE-only, ≥4-verts filter drops
    triangles; arithmetic-mean centroid filter can misclassify concave rooms;
    `RunWithPolling` discards a completed result if Cancel raced it.

## 8. Dead / legacy inventory

| Item | Status |
|---|---|
| `backend/jobs.py` (entire module: JobStore, DTOs, run_design_job) | Dead — nothing imports it; the SSE stream in ga.py is the real progress story |
| `placement.is_point_valid`, GRID_SLIDE machinery, `find_principal_angle` | Dead in live pipeline |
| Gap-fill + outside/culled markers | Removed / always-empty (API shape kept for compat) |
| `POST /api/blocks/create-from-points` + `blockssprinkler.dxf` runtime use | Legacy — functional but no production caller; plugin builds the block in code |
| `sprinkler_coverage_to_blocks.py` | Standalone CLI (mutates an existing DXF; hard-fails on missing block; preserves DXF version — both unlike the route) |
| `backend/zwcad_plugin.lsp` | **Does not exist** — referenced by both READMEs, health.py banner, and zwcad.py docstrings (phantom file) |
| `scipy==1.17.1` in requirements.txt | Never imported |

## 9. Documentation drift (code is right in every case)

| Doc claim | Reality |
|---|---|
| API_CONTRACT.md: 10 scenarios, ids 1..10 | 14 scenarios, ids 1..14 |
| API_CONTRACT.md: zwcad accepts only `{room_polys, scenario_ids, obs_min_offset, enable_gap_fill}`; only ids 1/4/10 honored; response is `(x y)` pairs | Also `obs_polys` + `tilted`; all 14 ids honored (fallback = all); response is `(x y rot)` triples + outside list |
| Root/plugin README: picker `[1/2/3/Exit]` → ids 1/4/10; "three scenarios in parallel"; "max 3 workers"; "pick a rectangle polyline" | Picker `[1/2/3/4/Exit]` → ids 11–14; four scenarios; `min(4, cpus)` workers; rectangle auto-computed from layer bbox |
| PROGRESS_STREAMING_GUIDE: "each client gets its own queue" | One global queue, reset per connect |
| Comments: outside markers "green"; rotation "principal angle"; jobs.py "/status polling" | Blue (ACI 5); longest-edge angle; no /status route exists |

## 10. What "Fixed 3000mm" (scenario 1) really does

(After the 2026-06-04 exact-pitch fix — applies to all Fixed scenarios 11–14.)

- **Head↔head**: exactly 3000 mm on both axes (verified: 12.1×9.4 m, 10×9.5 m,
  8×8 m, 21.5×14.3 m test rooms all give dx=dy=3000.0). Rooms under ~3 m get a
  single centered row/column.
- **Head↔wall**: grid heads keep a hard **800 mm** minimum (`WALL_CLEARANCE_MIN`);
  boundary-pulled heads land at **500 mm** (`BOUNDARY_NUDGE_INSET`) on their own
  grid line, only when a grid point fell ≤500 outside the polyline and no head is
  within 800 of the landing. Corridors narrower than 1600 mm relax to
  `short_side/2 − 50`. The 1000–1500 band in the scenario table remains
  definitional only.
- **Coverage note**: at 3000 pitch with r=1500 circles, adjacent circles are
  tangent — the diagonal centre of each grid cell is 2121 mm from the nearest head,
  so small diagonal pockets are outside the drawn circles. That is inherent to a
  square grid at pitch = 2r, not a placement bug.
