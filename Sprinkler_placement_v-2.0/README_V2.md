# Sprinkler Auto-Placement v2.0 — Universal Model

DXF / room polygons in → **compliant, minimal, verified** sprinkler layout
out, with **zero human touches** in the happy path. Built on the v1
grid + alpha/gama/pull engine; adds an NFPA rules engine, a verifier, a
self-fix loop, head minimisation, a GA fallback, a headless CLI, and a
feedback flywheel.

> Implements the 10-step roadmap in `../Sprinkler_placement_v-1.0/UNIVERSAL_MODEL_ROADMAP.md`.
> Steps 2/3 (room auto-detection from raw walls) are intentionally NOT in
> this version — rooms still come from the polyline layer / request.

## The pipeline (`autoplace/pipeline.py`)

```
classify → place → verify → self-fix → minimise → GA(if stuck) → report
```

| Module | Step | Job |
|---|---|---|
| `autoplace/nfpa_rules.py`   | 5 | hazard class → max area / spacing / wall bands (single source of truth) |
| `autoplace/classify.py`     | 4 | room TEXT labels → hazard class (most-hazardous wins; unknown → flagged) |
| `placement.py` (Phase 1-4)  | 1 | obstacles ENFORCED (v1 ignored them); grid + alpha/gama/pull |
| `autoplace/verifier.py`     | 2 | coverage (NFPA 0.75-reach, **not** the display circle), spacing, walls, obstacles, density |
| `autoplace/obstructions.py` | 7 | three-times rule, heads-under-wide-obstructions; beam rule stubbed (needs 3D) |
| `autoplace/autofix.py`      | 6 | place → verify → fix → repeat to 0 errors |
| `autoplace/ga_fallback.py`  | 9 | greedy min-heads ("best" pass) + GA for stubborn rooms |
| `autoplace/feedback_log.py` | 10 | log runs + human edits → intervention-rate metric |
| `autoplace/cli.py`          | 8 | headless DXF → DXF + JSON report |
| `routes/auto.py`            | 10 | `POST /api/zwcad/auto` one-shot endpoint for the plugin |

## Key design decisions

- **Coverage = NFPA 0.75-reach rule, never the r=1500 circle.** At pitch =
  2r a square grid leaves the cell diagonal (2121 mm) outside the circle,
  so a circle test flags every compliant grid as full of holes. The reach
  rule (every floor point within 0.75 × max_spacing of a head) is what NFPA
  actually requires and what the verifier + self-fix use.
- **Custom alpha/gama rules win over NFPA min-spacing (1800 mm).** The
  verifier emits min-spacing as a WARNING only; self-fix never moves a head
  to satisfy it. (Project decision — keeps the spacing you tuned by hand.)
- **Obstacles are hard.** Every placement gate (grid, pull, wall row,
  Phase-4 add) and every fix rejects heads in/near obstacles.
- **"Best" = fewest heads at 100 % coverage.** `minimize_heads` greedily
  drops every redundant head; on Light hazard a 3000 grid collapses ~25→13.

## Run it

```bash
cd backend
# headless batch
python -m autoplace.cli --input building.dxf --out result.dxf --report report.json \
       --obs-layer OBS --obs-offset 600 [--tilted] [--conservative]

# API (v2 on its own port so v1 keeps :8000)
python -m uvicorn main:app --port 8001
#   POST /api/zwcad/auto   {room_polys, obs_polys, labels:[[x,y,text]], tilted, ...}
#   ->  ((0 ((x y rot)...) () (passed total flagged)) ("VALIDATION: PASS" ...))
```

CLI exit code: `0` all rooms pass, `2` some room flagged for review.

## NFPA-13 rule sheet
Encoded in `autoplace/nfpa_rules.py`; full reference in the roadmap's Part 2.
Light 20.9 m²/4600 mm · Ordinary 12.1 m²/4600 mm · Extra/Storage 9.3 m²/3700 mm ·
min head-to-head 1800 (advisory) · max wall = ½ spacing · min wall 102 mm ·
3× rule for columns (cap 610 mm). **Confirm against your AHJ's edition before stamping.**

## Plugin — AUTOSPRINKLER2 command

The v2 plugin (`plugin/`, assembly SprinklerPlugin) adds:

- **`AUTOSPRINKLER2`** (alias **`SPK2`**) — prompts room layer / obstacle
  layer / Straight-Tilted, collects rooms + obstacles + **TEXT/MTEXT
  labels**, POSTs `/api/zwcad/auto`, auto-erases old heads, places the
  returned layout, and echoes the VALIDATION report to the ZWCAD terminal.
- `ApiClient.PostZwcadAuto` + `LispParser.ParseAuto` (now parses quoted
  strings) + `AutoRequest` / `AutoResult`.
- `ApiClient.BaseUrl` points at **:8001** (the v2 backend). Don't NETLOAD
  the v1 and v2 DLLs in the same ZWCAD session — same assembly name.

Build: open ZWCAD-free, `MSBuild plugin/SprinklerPlugin.csproj /p:Configuration=Debug /p:Platform=x64`,
then NETLOAD `plugin/bin/Debug/SprinklerPlugin.dll`.

## Status
All 10 roadmap steps (minus 2/3) implemented and tested end-to-end:
backend pipeline, CLI, `/api/zwcad/auto` route (HTTP-verified), and the
`AUTOSPRINKLER2` plugin command (built). v1.0 folder untouched, still on
:8000; v2 backend on :8001.
