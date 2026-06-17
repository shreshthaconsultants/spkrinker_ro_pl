# Sprinkler ZWCAD Plugin (C#)

Synchronous C# plugin that talks to the FastAPI backend in `../backend/`.
Flow:

1. Pick an existing rectangle polyline (the boundary)
2. Enter the layer name where the room polylines live (required)
3. Enter the layer name for obstacle polylines (optional — Enter to skip).
   Sprinklers will not be placed inside any closed polyline on this layer.
4. Answer `Architecture orientation [Straight/Tilted] <Straight>`.
   **Straight** runs the original axis-aligned placement (blocks upright);
   **Tilted** asks the backend to detect each room's longest-edge angle
   and rotate both the placement grid and the inserted blocks to match.
5. Answer `Enable gap-fill? [Yes/No] <Yes>`. `No` skips the expensive
   gap-fill phase on the backend for a noticeably faster response.
6. The plugin collects closed polylines on those layers whose centroid is
   inside the rectangle and POSTs them to `/api/zwcad/scenarios` as
   `room_polys`, `obs_polys`, `enable_gap_fill`, and `tilted`. The backend
   runs the three scenarios **in parallel worker processes** so the
   wall-clock wait is roughly the slowest scenario, not the sum of all
   three. The HTTP call shows a modal progress dialog with a
   continuous-style bar (two-phase curve: fast 0→60% in the first 3 s,
   then slow asymptotic rise toward 99%, τ ≈ 120 s, so it never plateaus
   on long requests) plus a live elapsed-time readout. A **Cancel**
   button (Esc also cancels) aborts the in-flight request.
7. Choose scenario `1`/`2`/`3` (mapped to backend ids 1, 4, 10) — the plugin
   immediately inserts `PP-CEILING PENDANT` block inserts on layer
   `SPRINKLERS` at every returned point, each rotated by its room's angle
   (0 in Straight mode). The block definition is built in code by
   `DrawingHelper.EnsureBlockBuilt`, so **no external DXF library is
   required** at runtime. Picking another scenario erases the current
   heads first.
8. `Exit` (or Enter at the scenario prompt) finishes the command. A
   modal **Summary** dialog then appears: "Thank you for using…", total
   sprinklers placed in the last scenario, and a top-10 table of rooms
   by area with per-room head counts and a density column (m² per head).

## Files

| File | Purpose |
|------|---------|
| `SprinklerPlugin.csproj` | .NET Framework 4.8, x64, library — references ZWCAD 2026 DLLs |
| `SprinklerPlugin.sln`    | Solution file for Visual Studio 2022 |
| `Commands.cs`            | `[CommandMethod]` entry points: HELLOSPK, SPKHEALTH, SPK, /AUTO-SPRINKLER |
| `ApiClient.cs`           | Synchronous HttpWebRequest wrapper for `/api/health` and `/api/zwcad/scenarios` |
| `Models.cs`              | DTOs (HealthResponse, ScenarioInfo, ZwcadScenarioRequest) — JSON keys exact |
| `LispParser.cs`          | Tiny S-expression parser for the LISP text returned by `/api/zwcad/scenarios` |
| `DrawingHelper.cs`       | EnsureLayer, EnsureBlockBuilt (constructs PP-CEILING PENDANT in code), InsertBlockAt |
| `ProgressDialog.cs`      | Modal progress bar with Cancel button; polls a background HTTP-call thread on a Forms.Timer |
| `SummaryDialog.cs`       | End-of-session "Thank you" modal: total heads + top-10 rooms by area (with per-room head counts and density) |
| `install_autoload.reg`   | Registry file that NETLOADs the DLL on every ZWCAD start |

The full backend contract is in `../backend/API_CONTRACT.md`.

## Build

### Visual Studio 2022

1. Open `SprinklerPlugin.sln`
2. (Optional) Adjust the two `<HintPath>` values in `SprinklerPlugin.csproj`
   if your ZWCAD install is not at `C:\Program Files\ZWSOFT\ZWCAD 2026\`
3. Build → `Ctrl + Shift + B`
4. Output: `plugin\bin\Debug\SprinklerPlugin.dll`

### MSBuild (CLI)

```cmd
"C:\Program Files\Microsoft Visual Studio\18\Community\MSBuild\Current\Bin\MSBuild.exe" ^
    SprinklerPlugin.csproj -p:Configuration=Debug -p:Platform=x64 -v:minimal
```

If build fails:
- ZWCAD DLL paths in the csproj `<HintPath>` lines don't match your install
- Target framework other than .NET Framework 4.8 is selected
- Platform other than x64

## Load in ZWCAD

### One-off load

1. Start the backend (`cd ../backend && uvicorn main:app --port 8000`)
2. Open ZWCAD with any drawing
3. Type `NETLOAD` → press Enter
4. Browse to `plugin\bin\Debug\SprinklerPlugin.dll` → OK
5. Type `HELLOSPK` to confirm load

### Auto-load on every ZWCAD start

Run `install_autoload.reg` (double-click) once. ZWCAD will run the embedded
`NETLOAD` on every startup. Edit the file first if you renamed the DLL path.

## Commands

### `HELLOSPK`
Sanity check. Prints `Sprinkler plugin loaded successfully!`.

### `SPKHEALTH`
Calls `GET http://localhost:8000/api/health` and prints status / version /
ezdxf version / available scenario IDs. Use this first if `/AUTO-SPRINKLER`
fails — it isolates connectivity issues from CAD ones.

### `SPK` / `/AUTO-SPRINKLER`
Both names trigger the same flow — `SPK` matches `backend/zwcad_plugin.lsp`.
The `/AUTO-SPRINKLER` form is registered with the literal leading `/` and
hyphen, so type it exactly as shown.


1. `Select rectangle polyline:` — click an existing rectangle/polygon polyline
2. `Enter room polyline layer name :` — type the layer where the room
   polylines live (case-insensitive). **Required.**
3. `Enter obstacle layer name (Enter to skip) :` — type the layer holding
   closed obstacle polylines (columns, equipment, etc.). Press Enter alone
   to skip if the room has no obstacles.
4. `Architecture orientation [Straight/Tilted] <Straight>:` — `Straight`
   (or Enter) runs axis-aligned placement (blocks upright, original
   behaviour). `Tilted` asks the backend to detect each room's longest-
   edge angle and rotate both the placement grid and the inserted blocks
   to align with the walls. Sent as `tilted` in the request body.
5. `Enable gap-fill? [Yes/No] <Yes>:` — `Yes` (or Enter) keeps the
   gap-fill phase that adds extra heads in uncovered pockets. `No` skips
   it for a noticeably faster backend run. Sent as `enable_gap_fill` in
   the request body.
6. The plugin gathers every closed Polyline on each layer whose centroid is
   inside the picked boundary and POSTs them to `/api/zwcad/scenarios` with
   `room_polys`, `obs_polys`, `enable_gap_fill`, `tilted`, and
   `scenario_ids=[1, 4, 10]`. The backend dispatches the three scenarios
   to a `ProcessPoolExecutor` so they run in parallel — wall time ≈
   slowest scenario, not sum.
7. A modal "Requesting scenarios from backend..." dialog appears with a
   continuous progress bar and a live elapsed-time readout
   (`Requesting scenarios from backend... 84%   elapsed 02:00`). The bar
   uses a two-phase curve (fast 0→60% in the first 3 s, then slow
   asymptotic toward 99%, τ ≈ 120 s) so it never plateaus on long
   requests, and only snaps to 100% when the worker actually finishes.
   A **Cancel** button (Esc also cancels) aborts the in-flight HTTP
   request and returns to the command line.
8. Prompt: `Choose scenario [1/2/3/Exit] <Exit>:`
   - `1` → scenario id `1` (Standard NFPA-13)
   - `2` → scenario id `4` (Compact tight)
   - `3` → scenario id `10` (Institutional)
   - `Exit` (or Enter) finishes the command
9. The plugin immediately inserts `PP-CEILING PENDANT` blocks on layer
   `SPRINKLERS` at every returned point, each rotated by its room's angle
   (0 in Straight mode, the room's longest-edge angle in Tilted mode).
   On first use it builds the block definition from hardcoded primitives
   via `DrawingHelper.EnsureBlockBuilt` (24 lines + 12 elliptical arcs +
   2 circles, on layers `PENDENT SPRINKLER` and `SPRINKLER COVERAGE`);
   subsequent inserts reuse the existing definition. **No external DXF
   library is needed.**
10. Returns to the scenario picker. Picking another scenario erases the
    current blocks first, then inserts the new scenario's blocks.
11. After `Exit`, a modal **Summary** dialog appears: "Thank you for
    using…", `Total sprinklers placed: N (scenario X)`, and a sortable
    table of the top 10 rooms by floor area showing `# / Area (m²) /
    Heads / Density (m²/head)`. Shows all rooms if there are fewer than
    10. The dialog is dismissed with **OK** (or Esc/Enter).

## Threading

Every ZWCAD database operation runs on the UI thread inside a `Transaction`
opened from the command method.

The single exception is the backend HTTP call in `ProgressDialog.cs`:
the `/api/zwcad/scenarios` POST runs on a background `Thread`, while a
modal `ProgressDialog` (Windows Forms) polls a `done` flag on a
`System.Windows.Forms.Timer` (UI thread, 100 ms interval) to drive the
progress bar.

The bar uses two phases:
1. **0–3 s** — linear ramp 0 → 60 % (fast at the start so the user sees
   immediate motion).
2. **>3 s** — asymptotic 60 → 99 %, τ = 120 s (slow at the end; ≈84 %
   at 2 min, ≈96 % at 5 min). Capped at 99 % so 100 % is reserved for
   actual completion.

The bar's `Maximum = 1000` (0.1 % resolution) so it visibly micro-ticks
every poll instead of looking frozen between integer percentages. The
label shows live elapsed time, e.g. `... 84%   elapsed 02:00`. No drawing
happens on the background thread — the result is handed back to the
command method, which then opens a `Transaction` to insert blocks.

Cancel: clicking the Cancel button (or pressing Esc) calls
`ApiClient.AbortCurrent()` which invokes `HttpWebRequest.Abort()` on the
in-flight request. The worker thread's `WebException` is caught and
discarded; `RunWithPolling` then throws `OperationCanceledException` to
the command method, which prints `Request cancelled by user.`.

## Error messages

`/AUTO-SPRINKLER` and `SPKHEALTH` print clean messages instead of throwing:

| Cause | Message |
|-------|---------|
| `WebExceptionStatus.ConnectFailure` / `NameResolutionFailure` | `Backend not reachable - is uvicorn running on http://localhost:8000?` |
| `WebExceptionStatus.Timeout` | `Backend is taking too long. Aborted.` |
| HTTP 404 from server | `Endpoint not found - is backend updated?` |
| Other HTTP error | `Backend HTTP <code>: <description>` |
| Bad LISP response | `Backend returned malformed response: <details>` |
| Anything else | `Unexpected error: <message>` |

## Limitations

- The boundary picker accepts any polyline ≥ 4 vertices, but the backend
  flow is currently driven by collecting room polylines on the supplied
  layer; arbitrary boundary shapes are not separately exercised.
- Only scenarios 1, 4, 10 are exposed; the backend supports all 10 via
  `/api/scenarios/generate` (DXF upload).
- Backend URL is hardcoded to `http://localhost:8000` in `ApiClient.BaseUrl`.
- Progress percentage is a heuristic (no SSE from the backend) — the
  elapsed-time readout is the honest signal.
