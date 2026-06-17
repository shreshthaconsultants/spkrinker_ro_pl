# Universal Sprinkler Model — 10 Steps + NFPA-13 Rule Sheet

> Goal: DWG in → compliant sprinkler layout out → ZERO human touches.
> Metric that defines "done": **intervention rate = 0** (nobody moves a head after the run).
> Written 2026-06-05 against codebase v1.0 (alpha/gama/newrow + pulls + top-left anchor).

---

## PART 1 — The 10 Steps

### Step 1 — Make obstacles real (TRUST FOUNDATION)
`obs_polys` travels from the plugin to the backend and is **silently ignored** in the live
path (`generate_zone_sprinklers` Phase 1 never tests it; `is_point_valid` is dead code).
- Reject any head inside an obstacle polygon or closer than `obs_min_offset`.
- Re-place rejected heads via the existing pull/Phase-4 machinery (treat obstacle edges
  like walls with their own clearance).
- Files: `backend/placement.py` (Phase 1 loop, `_try_add`, pull guards).

### Step 2 — Validation report = the product
After every placement run a verifier, print + return it:
- coverage proof: sample grid (you have `precompute_sample_grid`) → % covered, list of gap centers
- min/max head spacing violations, head-to-wall violations, heads in/near obstacles
- per-room table: area, heads, density (m²/head) vs allowed maximum
Ship result ONLY when 0 violations; otherwise auto-fix loop (Step 6).
Wire it as a 5th LISP element → plugin prints `VALIDATION: PASS (coverage 100.0%)`.

### Step 3 — Room auto-detection (kills the polyline prompt)
Humans today draw `layerx` polylines. Universal = detect rooms from raw architecture:
- collect wall LINE/LWPOLYLINE segments from wall layers
- snap endpoints within tolerance, bridge door openings (gap ≤ ~1200 mm)
- build a planar graph → closed faces = room polygons (shapely `polygonize` / networkx)
- discard faces smaller than `MIN_ROOM_DIM` (already in config)
Fallback: if a `layerx`-style layer exists, keep using it (compat path).

### Step 4 — Hazard auto-classification (kills the scenario prompt)
Read MTEXT/TEXT inside each room polygon → map label → hazard class:
- "OFFICE", "LOBBY", "CORRIDOR" → Light
- "RETAIL", "PARKING", "WORKSHOP", "KITCHEN" → Ordinary I/II
- "STORE", "WAREHOUSE", "PLANT" → Extra/Storage (flag for engineer review at first)
Hazard class → spacing band + coverage from the NFPA table (Part 2), per room — the
SCENARIOS table becomes a derived output, not a user pick. Unknown label → safest class
in the building + note in the validation report.

### Step 5 — Replace presets with a code-rules engine
One module `nfpa_rules.py`: input (hazard, ceiling type, room geometry) → output
(max_coverage_m2, max_spacing, min_spacing, wall_min, wall_max, small-room allowances).
alpha/gama/newrow + anchor stay as the *strategy*; the engine supplies their numbers and
the verifier (Step 2) checks against the same engine — single source of truth.

### Step 6 — Self-fix loop (closes the human loop)
`while violations and iterations < N:` map each violation type to a fix:
- coverage gap → insert head at gap centroid (validated)
- spacing too tight → delete/merge the worse head
- wall too close → re-run the pull for that head
Converges or escalates: a room that can't reach 0 violations gets flagged in the report
(the ONLY thing a human ever looks at).

### Step 7 — Obstruction rules (beams/columns — the NFPA "three-times rule")
Columns: keep heads ≥ 3× obstruction width away (cap 600 mm) or add a head on the far
side. Beams (needs ceiling info): if deflector above bottom-of-beam, apply the beam-rule
distance table; wide obstructions (> 1.2 m) get heads underneath. Start with columns
(2D, you already get obs_polys), add beams when you ingest ceiling data.

### Step 8 — Batch/headless mode
CLI: `python -m autoplace building.dxf --out result.dxf --report report.json`
- auto-detect layers (entity statistics: layer with most closed polylines = rooms, most
  LINEs = walls), run Steps 3-7, write DXF (you have `blocks.py`/`lsp_writer.py` pieces)
- the ZWCAD plugin becomes a one-click wrapper around the same pipeline.

### Step 9 — GA fallback for the weird 5%
Rect/bent rooms: grid rules win. Domes, saw-tooth, column forests: seed the GA
(`genetic_placement.py`, already in repo, web-only today) with the grid result,
HARD-constrain (reject invalid chromosomes instead of penalising), objective =
fewest heads at 100% coverage. Trigger only when the rule engine can't reach 0
violations — keeps runtime sane.

### Step 10 — Feedback flywheel
Log every drawing + placement + (if a human edited afterwards) the diff.
- every human fix becomes a regression test polygon in a test suite
- tune config knobs (or train a small model) against that suite
- dashboard: intervention rate per week → drive it to 0. When it's 0 for a month
  across all your real projects, it's universal.

---

## PART 2 — NFPA-13 Placement Rules (Standard Spray, Ceiling-Mounted)

> Converted to mm for this codebase. Based on NFPA-13 (2019/2022 ed. protection-area &
> spacing chapters). **Verify against the edition your AHJ enforces before stamping
> anything** — this sheet is for the rules engine, not a legal substitute.

### A. Protection area & spacing per hazard class

| Hazard class | Max area/head | Max spacing (S or L) | Typical grid for you |
|---|---|---|---|
| Light (office, lobby, corridor, school) | 20.9 m² (225 ft²) | 4600 mm (15 ft) | 4500×4500 |
| Ordinary I (parking, laundry, restaurant) | 12.1 m² (130 ft²) | 4600 mm (15 ft) | 3900×3000 / 3500×3400 |
| Ordinary II (retail, workshop, mill) | 12.1 m² (130 ft²) | 4600 mm (15 ft) | same |
| Extra I/II (paint, plastics processing) | 9.3 m² (100 ft²)* | 3700 mm (12 ft) | 3000×3000 |
| High-piled storage | 9.3 m² (100 ft²) | 3700 mm (12 ft) | 3000×3000 |

\* 12.1 m² allowed for Extra hazard if hydraulically designed with density < 10.2 mm/min.
Rule: S × L ≤ max area; spacing measured along the ceiling slope.
**Note:** compliance is the S×L rectangle, not your r=1500 display circle — the circle is
visualization only.

### B. Universal limits (all hazard classes)

| Rule | Value | Your config today |
|---|---|---|
| Min head-to-head spacing | **1800 mm** (6 ft) | `MIN_HEAD_SEPARATION=500` ⚠ too small as a hard floor — gama pairs at ~850-1600 violate 1800 unless baffled |
| Max wall distance | **½ × allowed spacing** (e.g. 2300 for 4600) | wall bands 1000–1700 ✓ conservative |
| Min wall distance | **102 mm** (4 in) | pulls land ≥ 700 ✓ |
| Deflector below ceiling (unobstructed constr.) | 25–300 mm (1–12 in) | N/A (2D) — needs ceiling data |
| Deflector below ceiling (obstructed constr.) | 25–150 mm below members | N/A (2D) |
| Clearance above storage | ≥ 450 mm (18 in) | N/A (2D) |
| Sprinklers under open gratings | shielded | N/A |

### C. Walls / rooms / corridors
- Small-room rule (Light hazard, room ≤ 74 m² / 800 ft²): heads may sit up to 4600 mm
  from one wall if within max-area; relaxes corner cases.
- Corridors (Light): single row allowed, max 4600 spacing, ≤ 2300 from end walls.
- Irregular rooms: distance to ANY point of the floor from nearest head ≤ 0.75 × allowed
  spacing (the "0.75 rule" for extended/irregular coverage checks).
- Curved/angled walls: measure perpendicular — your pulls already approximate this.

### D. Obstructions (the part nobody implements and everybody fails inspection on)
- **Three-times rule**: head at least 3 × max obstruction dimension away from columns,
  privacy curtains, free-standing obstructions (cap: 600 mm / 24 in).
- **Beam rule**: if head is within ~7.6 m of a beam, deflector must be high/far enough
  per the beam-rule table (distance vs deflector-above-bottom-of-beam); else move it.
- Obstructions ≥ 1200 mm wide (ducts, platforms): add sprinklers BELOW them.
- Soffits/bulkheads ≥ 200 mm: treat as walls for spacing.
- Open grid ceilings, clouds: special rules — flag for engineer.

### E. Ceiling geometry
- Slope ≤ 2:12 (~9.5°): treat as flat; > 2:12: run branch lines parallel to ridge,
  space along slope, head within 900 mm of peak (vertically).
- Skylights > 0.9 m²: treat as ceiling pockets; pockets ≤ 32 ft² with depth rules can
  be skipped (edition-dependent).

### F. What your engine maps to (gap list)

| NFPA item | Status in code |
|---|---|
| Max spacing per hazard | scenario presets — replace with engine (Step 5) |
| Min spacing 1800 | ⚠ add as hard verifier rule; gama landings may need rework or baffle note |
| Wall max ½-spacing | anchor 1500 + alpha/gama ✓ conservative |
| Wall min 102 | ✓ (700 pulls, 1000 gama) |
| Obstructions/3× rule | ✗ obs_polys ignored — Step 1 + Step 7 |
| Coverage proof | sampler exists, not run in live path — Step 2 |
| Small-room / corridor allowances | corridors partially (relaxed clearance) — engine
  should formalize |
| Slope/ceiling rules | ✗ 2D only — needs ceiling data ingestion |

---

## Suggested order (same as steps): 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10.
Steps 1+2 ≈ one day, immediately raise trust. Steps 3+4 kill all prompts. 5+6 make it
self-correcting. 7 makes it pass inspection. 8 makes it scale. 9+10 make it universal.
