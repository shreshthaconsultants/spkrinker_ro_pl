# spriro — one ZWCAD plugin, three engines

`spriro` merges the three separate Sprinkler plugins into a **single assembly**
(`Spriro.dll`) so you NETLOAD once and get three commands. It also fixes the old
"don't load v1 and v2 together — same assembly name" problem, because all three
engines now live in their own namespaces inside one DLL.

| Command          | Engine            | Talks to backend | What it does |
|------------------|-------------------|------------------|--------------|
| `-routing`       | R1.0 (`Spriro.Routing`) | **:9000** `/route*` | Route **existing** sprinklers into a pipe network (Rooms = rooms + corridor header, Open = one open space). |
| `-sprinkler_p1`  | v-1.0 (`Spriro.P1`)     | **:9001** `/api/zwcad/scenarios` | Place sprinklers with the **scenario picker** (pick one of 3 grid layouts). |
| `-sprinkler_p2`  | v-2.0 (`Spriro.P2`)     | **:9002** `/api/zwcad/auto` | Place sprinklers with the **universal model** (classify → place → verify → autofix → minimise → GA), one compliant layout + validation report. |

> The three commands are typed **exactly** as shown, including the leading
> hyphen and the underscores: `-routing`, `-sprinkler_p1`, `-sprinkler_p2`.

---

## Point the plugin at your backend — `spriro.config.json`

The plugin reads the backend address from **`spriro.config.json`**, shipped next
to `Spriro.dll` (in `bin\Release\`). Edit it to point at your server — **no
rebuild needed**, just re-`NETLOAD` (or restart ZWCAD):

```json
{
  "host": "146.190.72.89",
  "routing_port": 9000,
  "p1_port": 9001,
  "p2_port": 9002
}
```

- Ships pointing at the VPS IP **146.190.72.89**. For local testing set
  `"host": "127.0.0.1"`.
- All three engines share `host`; ports default to 9000/9001/9002 if omitted.
- Per-engine full-URL overrides also work: `"routing_url"`, `"p1_url"`, `"p2_url"`.
- Lookup order: `SPRIRO_CONFIG` env var (full path) → next to `Spriro.dll` →
  current dir. If none is found/parseable it falls back to `localhost`.
- On `NETLOAD` the plugin prints the resolved URLs and which file they came from,
  so you can confirm at a glance.

**Ship the plugin as three files together:** `Spriro.dll`, `Newtonsoft.Json.dll`
and `spriro.config.json`.

---

## 1. Start the backends

All three FastAPI servers start from one launcher at the repo root:

```powershell
cd C:\Users\shres\Desktop\Sprinkler_full_code
python backend_run.py
```

```
  key      plugin cmd      url                     health
  v1       -sprinkler_p1   http://127.0.0.1:9001   /api/health
  v2       -sprinkler_p2   http://127.0.0.1:9002   /api/health
  routing  -routing        http://127.0.0.1:9000   /health
```

Each backend runs as its own uvicorn subprocess (they share module names like
`main.py`/`geometry.py`, so they cannot share one interpreter). Ctrl+C stops all
of them. Useful flags:

```powershell
python backend_run.py --list             # print the table and exit
python backend_run.py --only v2,routing  # start a subset
python backend_run.py --reload           # uvicorn autoreload (dev)
```

The launcher uses the Python you run it with, so install each backend's
dependencies into that interpreter first (once):

```powershell
python -m pip install -r Sprinkler_placement_v-1.0\backend\requirements.txt
python -m pip install -r Sprinkler_placement_v-2.0\backend\requirements.txt
python -m pip install -r R1.0\backend\requirements.txt
```

## 2. Build the plugin

Requires the .NET SDK and ZWCAD 2026 at `C:\Program Files\ZWSOFT\ZWCAD 2026`
(edit the two `<HintPath>` entries in `Spriro.csproj` if yours is elsewhere).

```powershell
cd spriro
dotnet build Spriro.csproj -c Release
```

Output: `spriro\bin\Release\Spriro.dll` **plus `Newtonsoft.Json.dll`** — keep
the two files together when you copy the plugin anywhere.

## 3. Load and run in ZWCAD

1. Start the backends (step 1) and ZWCAD 2026.
2. `NETLOAD` → `spriro\bin\Release\Spriro.dll`. On load it prints the three
   commands.
3. Type `-routing`, `-sprinkler_p1`, or `-sprinkler_p2`.

---

## Command → engine mapping (and how to change it)

Each command is a thin wrapper in `src/SpriroCommands.cs` that calls the
original per-version flow:

| Command          | Calls                                   | Original command it replaces |
|------------------|-----------------------------------------|------------------------------|
| `-routing`       | `Spriro.Routing.Commands.SpkRoute()`    | R1.0 `SPKROUTE` |
| `-sprinkler_p1`  | `Spriro.P1.Commands.AutoSprinkler()`    | v1 `/AUTO-SPRINKLER` |
| `-sprinkler_p2`  | `Spriro.P2.Commands.AutoSprinkler2()`   | v2 `AUTOSPRINKLER2` |

If you want different behaviour, change the one line in `SpriroCommands.cs`:

- `-routing` routes sprinklers that **already exist** in the drawing. For full
  design from a boundary (place + validate + route), call
  `Spriro.Routing.Commands.SpkAuto()` instead.
- `-sprinkler_p2` runs v2's universal one-shot. For v2's scenario picker
  (same UX as `-sprinkler_p1`, but the v2 backend), call
  `Spriro.P2.Commands.AutoSprinkler()` instead.

## How the merge works (for maintainers)

- Each old plugin's sources were copied under `src/p1`, `src/p2`, `src/routing`
  and re-homed to namespaces `Spriro.P1`, `Spriro.P2`, `Spriro.Routing`. The
  original folders are untouched.
- **Only `Spriro.SpriroCommands` is registered** (the single
  `[assembly: CommandClass(...)]` in `SpriroCommands.cs`). Because that
  attribute exists, ZWCAD scans only that class for commands, so the engines'
  own `[CommandMethod]` names (`SPK`, `SPKAUTO`, `/AUTO-SPRINKLER`, …) stay
  dormant and never collide — even though all three define a `SPK`.
- `Spriro.csproj` is SDK-style and auto-globs every `.cs` under `src/`. It
  references both JSON stacks the engines use (Newtonsoft for routing,
  `System.Web.Extensions`/`JavaScriptSerializer` for v1/v2) and WinForms for
  the v1/v2 progress + summary dialogs.

To re-sync after editing an original plugin, re-copy its `.cs` into the matching
`src/` folder and change `namespace SprinklerPlugin` → `namespace Spriro.<X>`,
then remove any `[assembly: …]` lines from its `Commands.cs`.

## Note on the command names

`-routing`, `-sprinkler_p1`, `-sprinkler_p2` are registered verbatim. A leading
hyphen on a **custom** command is unusual (AutoCAD/ZWCAD use it for the no-dialog
form of built-ins). It should register fine, but if your ZWCAD build rejects it
at NETLOAD, rename the three `[CommandMethod("…")]` strings in
`SpriroCommands.cs` to non-hyphen names (e.g. `SPRIRO_ROUTING`) and rebuild.
