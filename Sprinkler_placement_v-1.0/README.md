# Sprinkler Auto-Placement

NFPA-13-style sprinkler layout, end to end:

- **`backend/`** — FastAPI app that reads DXF / room polygons and returns
  sprinkler positions (grid placement, gap-fill, optional GA optimisation).
- **`plugin/`** — C# ZWCAD plugin that calls the backend and draws the heads
  in the active drawing.
- **`backend/zwcad_plugin.lsp`** — legacy pure-AutoLISP variant that calls the
  same backend endpoint directly via MSXML/WinHttp (load with `APPLOAD`,
  command `SPK`).

```
Sprinkler_placement_v-1.0/
├── backend/                ← FastAPI app + algorithms
│   ├── main.py
│   ├── routes/             ← endpoint handlers split by concern
│   ├── geometry.py, placement.py, area_stats.py, ...
│   ├── blockssprinkler.dxf ← block library (PP-CEILING PENDANT)
│   ├── zwcad_plugin.lsp    ← legacy AutoLISP plugin (command SPK)
│   ├── API_CONTRACT.md     ← authoritative endpoint spec
│   └── README.md           ← backend setup / usage
├── plugin/                 ← C# ZWCAD plugin
│   ├── SprinklerPlugin.sln
│   ├── SprinklerPlugin.csproj
│   ├── Commands.cs, ApiClient.cs, Models.cs, ...
│   ├── install_autoload.reg ← optional: NETLOAD on every ZWCAD start
│   └── README.md           ← plugin build + load + run instructions
├── files (3).sln           ← top-level solution (legacy name)
└── README.md               ← this file
```

## Quick start

### 1. Run the backend

```bash
cd backend
python -m venv venv
venv\Scripts\activate           # Windows
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Open `http://localhost:8000/docs` to see Swagger UI.
Smoke test: `curl http://localhost:8000/api/health`.

### 2. Build the plugin

Open `plugin/SprinklerPlugin.sln` in Visual Studio 2022 and build (Ctrl+Shift+B),
or from the command line:

```cmd
"C:\Program Files\Microsoft Visual Studio\18\Community\MSBuild\Current\Bin\MSBuild.exe" ^
    plugin\SprinklerPlugin.csproj -p:Configuration=Debug -p:Platform=x64
```

Output: `plugin\bin\Debug\SprinklerPlugin.dll`.

If the build fails, edit the two `<HintPath>` values in
`plugin/SprinklerPlugin.csproj` to match your ZWCAD install (default expected:
`C:\Program Files\ZWSOFT\ZWCAD 2026\`).

### 3. Load in ZWCAD

1. Open ZWCAD with any drawing
2. `NETLOAD` → browse to `plugin\bin\Debug\SprinklerPlugin.dll`
3. Type `HELLOSPK` to confirm
4. Type `SPKHEALTH` to confirm backend connectivity
5. Type `/AUTO-SPRINKLER` (or its alias `SPK`):
   - Select an existing rectangle polyline (the boundary)
   - Enter the layer name where the room polylines live (required)
   - Enter the obstacle layer name (optional — Enter to skip). Sprinklers
     will not be placed inside any closed polyline on this layer.
   - Answer `Architecture orientation [Straight/Tilted] <Straight>`.
     **Straight** (default) runs the original axis-aligned placement and
     leaves blocks upright. **Tilted** asks the backend to detect each
     room's longest-edge angle and rotate both the placement grid and
     the inserted blocks to align with the walls.
   - Answer `Enable gap-fill? [Yes/No] <Yes>` — `No` skips the expensive
     gap-fill phase on the backend for a noticeably faster response.
   - A modal progress dialog appears while the backend computes the
     three scenarios **in parallel** (worker processes, total wall time
     ≈ slowest scenario, not sum). The bar uses a two-phase curve (fast
     0→60% in 3 s, then a slow asymptotic rise toward 99%, τ ≈ 120 s)
     so it never plateaus on long requests, and the label shows live
     elapsed time (`elapsed mm:ss`). The dialog has a **Cancel** button
     (Esc also cancels) which aborts the in-flight HTTP request.
   - Choose scenario [1/2/3/Exit] — `1`→id 1, `2`→id 4, `3`→id 10. The
     plugin immediately inserts `PP-CEILING PENDANT` block inserts on
     layer `SPRINKLERS` at every returned point. Picking another scenario
     erases the previous heads first.
   - When you `Exit` the picker, a **Summary** dialog appears showing
     "Thank you for using…", the total number of heads placed, and a
     top-10 table of rooms by area with per-room head counts and
     density (m² per head).
6. The block definition is built in code by `DrawingHelper.EnsureBlockBuilt`
   — no external DXF library is needed.

### Placement rules at a glance

- **Rooms with at least one bbox dimension ≥ 2000 mm always get heads.**
  A 1500 × 10000 mm corridor previously received zero sprinklers (the
  centered row was too close to the side walls). Now the wall-distance
  check is locally relaxed for narrow rooms so a single centered row
  can pass — e.g. that corridor now gets 4 heads down its middle.
- **Rooms below 2000 mm in both dimensions are skipped** (treated as
  closets / voids).
- **Single-row / single-sprinkler placements always sit in the middle**
  of the room — never biased to the right or left wall.

> The `/AUTO-SPRINKLER` command is registered with a leading `/` and a
> hyphen — type it exactly as shown, or use the alias `SPK`.

For auto-load on every ZWCAD start, double-click `plugin/install_autoload.reg`.

## Architecture (one diagram)

```
ZWCAD (UI thread)                          backend/ (uvicorn :8000)
  │                                              │
  │ /AUTO-SPRINKLER  (alias: SPK)                │
  │  ⇣ pick rectangle polyline                   │
  │  ⇣ prompt for room-polyline layer (required) │
  │  ⇣ prompt for obstacle layer (optional)      │
  │  ⇣ prompt for architecture Straight/Tilted   │
  │  ⇣ prompt for gap-fill Yes/No                │
  │  ⇣ collect closed polylines on each layer    │
  │    whose centroid is inside the rectangle    │
  │  ⇣ build ZwcadScenarioRequest { room_polys,  │
  │    obs_polys, scenario_ids=[1,4,10],         │
  │    enable_gap_fill, tilted }                 │
  │  ⇣ ProgressDialog spawns background Thread:  │
  │    HTTP POST /api/zwcad/scenarios            │
  │    (Cancel button → ApiClient.AbortCurrent)  │
  ├──────────────────────────────────────────────►│
  │                                               │ run scenarios 1, 4, 10
  │                                               │ in parallel (one process
  │                                               │ each, ProcessPoolExecutor)
  │                                               │ if tilted: per-room
  │                                               │   longest-edge angle
  │                                               │   detected; placement
  │                                               │   grid rotated to match
  │◄──────────────────────────────────────────────┤ return LISP S-expression
  │ LispParser → Dictionary<int, List<double[]>>  │
  │ where each point = (x, y, rotation_radians)   │
  │  ⇣                                            │
  │ Pick 1/2/3 → DrawingHelper.EnsureBlockBuilt   │
  │ (in-code primitives) → InsertBlockAt for each │
  │ point with that point's rotation              │
  │ (Transaction on UI thread, layer SPRINKLERS,  │
  │ PP-CEILING PENDANT blocks)                    │
  │  ⇣                                            │
  │ Exit picker → SummaryDialog (Thank you,       │
  │ total heads, top-10 rooms by area)            │
```

ZWCAD's database is only touched inside a `Transaction` opened from the
`[CommandMethod]` on the UI thread. The single off-UI work is the HTTP POST
to `/api/zwcad/scenarios`, which `ProgressDialog.RunWithPolling` runs on a
background `Thread` while a Forms.Timer drives the modal progress bar; the
parsed result is handed back to the command method before any drawing
happens. Cancel calls `ApiClient.AbortCurrent()` to abort the in-flight
`HttpWebRequest`; the worker's resulting `WebException` is swallowed and
`OperationCanceledException` is surfaced to the command method.

## Commands

| Command           | Purpose |
|-------------------|---------|
| `HELLOSPK`        | Sanity check after NETLOAD |
| `SPKHEALTH`       | Probe backend `/api/health`, print version & scenarios |
| `/AUTO-SPRINKLER` | Pick rectangle + room layer + obstacle layer → fetch scenarios 1/4/10 → user picks one → directly insert `PP-CEILING PENDANT` blocks on layer `SPRINKLERS` |
| `SPK`             | Alias for `/AUTO-SPRINKLER` (matches `backend/zwcad_plugin.lsp`) |

## Where to look

- **Backend setup / endpoint details:** `backend/README.md`
- **Authoritative API spec:** `backend/API_CONTRACT.md`
- **Plugin build / load / threading:** `plugin/README.md`

## Disclaimer

This tool assists **layout exploration**. Always verify designs against
applicable codes, insurance, and project specifications.

---

© 2026 Shreshtha Consultants. All rights reserved.
