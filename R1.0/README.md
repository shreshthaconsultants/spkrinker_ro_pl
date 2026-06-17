# Sprinkler Design Automation — FastAPI Backend + ZWCAD 2026 Plugin

Automated NFPA-13 sprinkler design inside ZWCAD:

- **`backend/`** — Python FastAPI service that computes sprinkler placement,
  validates it against NFPA-13 rules, and routes the pipe network
  (branch lines + cross main + riser connection).
- **`plugin/`** — C# (.NET Framework 4.8) ZWCAD plugin that reads geometry
  from the drawing, calls the backend, and draws the results.
- **`docs/json-contract.md`** — the request/response schema shared by both.

All units are **millimetres** (1 drawing unit = 1 mm). No unit conversion is
performed anywhere.

---

## 1. Run the backend

```powershell
cd backend
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 9000
```

Check: <http://127.0.0.1:9000/health> → `{"status":"ok"}`.
The plugin expects the backend at `http://127.0.0.1:9000` (see
`plugin/src/BackendClient.cs` to change it).

Run the tests (optional):

```powershell
cd backend
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
```

## 2. Build the plugin

Requires the .NET SDK (any recent version; the project targets
.NET Framework 4.8) and ZWCAD 2026 installed at
`C:\Program Files\ZWSOFT\ZWCAD 2026` (edit the two `<HintPath>` entries in
`plugin/SprinklerPlugin.csproj` if yours lives elsewhere).

```powershell
cd plugin
dotnet build SprinklerPlugin.csproj -c Release
```

Output: `plugin\bin\Release\SprinklerPlugin.dll` (with `Newtonsoft.Json.dll`
alongside — keep them together).

## 3. Load and use in ZWCAD

1. Start the backend (step 1) and ZWCAD 2026.
2. Type `NETLOAD`, browse to `plugin\bin\Release\SprinklerPlugin.dll`.
   The command line lists the four commands (`SPKROUTE`, `SPK`, `SPKERASE`,
   `SPKAUTO`) on load.

### `SPKAUTO` — full design from a room boundary

1. *Select closed polyline room boundary* — pick a closed polyline (mm units).
2. *Hazard class [Light/Ordinary/Extra] \<Ordinary\>* — pick the NFPA-13 class.
3. *Branch pipe width (mm) \<32\>* / *Main pipe width (mm) \<65\>* — Enter
   accepts the defaults.
4. *Building tilt angle (pick two points along a wall, or Enter for default)* —
   the heads and pipes align to this angle; Enter uses the longest boundary
   edge (0° for an axis-aligned room).

The plugin then draws:

| Layer       | Colour | Content                                              |
|-------------|--------|------------------------------------------------------|
| `SPK-HEADS` | green  | sprinkler block (`SPK_HEAD`) at each head            |
| `SPK-COVER` | cyan   | coverage circle per head (radius `0.707*S`)          |
| `SPK-PIPE`  | blue   | **single centreline** pipes with **centred flow arrows** |
| `SPK-FAIL`  | red    | marker circle on each head failing validation        |

Failed NFPA rules are listed on the command line.

### `SPKROUTE` — route sprinklers that already exist in the drawing

Every answer is **remembered in the drawing**: the next run defaults to
your previous answers, so after the first time it's Enter-Enter-Enter.
Layer questions are answered by **clicking any object** on that layer
(or type `N` to enter the name). After each answer the command reports
what it found (e.g. `-> 4,213 sprinkler(s)`) and offers a Retry when a
layer is empty. If old pipes exist it asks *Erase previous pipe run?
\<Yes\>*. **Tilt is automatic** — the backend measures the building's grid
angle from the sprinklers themselves and reports it (`Building tilt: … (auto)`);
it is never asked.

0. *Layout [Rooms/Open] \<last used\>* — **Rooms** routes rooms around a
   corridor (see the next section); **Open** routes one open space per
   shaft (the flow below).
1. *Sprinklers \<SPK-HEADS\>* — the layer holding the sprinklers.
   Block references, circles and point entities on that layer all count
   (insertion point / centre = head position).
2. *Start point layer name \<SPK-RISER\>* — the layer holding the shaft /
   riser feed points. **Every** block/circle/point on it counts as a shaft,
   so put several entities there to route from several shafts. If the layer
   has none, the plugin asks you to **click** the shaft points (Enter
   finishes after the first one).
3. *Room boundary polyline layer name \<SPK-ROOM\>* — the layer holding the
   room outline as a **closed polyline**. Sprinklers outside it are ignored,
   and the pipe runs are kept inside the room (the cross main moves to a
   head column inside it; shaft connections take an L-run instead of a
   diagonal that would leave the room). If the layer has no closed polyline,
   routing simply runs unconstrained.
4. Branch / main pipe widths as above.

With more than one shaft the sprinklers are **divided by distance**: each
head is fed from its nearest shaft, and each shaft gets its own independent
cross main + branch tree drawn in its **own colour** — shaft 1 red, shaft 2
yellow, then green, cyan, magenta, blue, ... The command line reports heads
and pipe length per shaft (with its colour).

The pipe network is drawn on `SPK-PIPE` as single centrelines with flow arrows
pointing **away from each shaft** (riser → cross main → branches), and each
shaft is marked with a concentric double circle in its colour.

### `SPKROUTE` → `Rooms` — rooms around a corridor

For buildings drawn as **separate rooms connected by a corridor**. Answer
`Rooms` at the layout prompt (it's the default), then:

1. *Sprinklers \<SPK-HEADS\>* — click a sprinkler, or `N` to type the layer.
2. *Shafts \<SPK-SHAFT\>* — the shaft feed points (click fallback if the
   layer is empty).
3. *Rooms \<SPK-ROOM\>* — **every closed polyline** on this layer is one room.
4. *Corridor \<SPK-CORRIDOR\>* — the corridor as one closed polyline
   (largest wins if there are several; must differ from the rooms layer).
5. Branch / main pipe widths as above.
6. *Header offset from sprinklers (mm) \<300\>* — how far each room's
   sub-header sits **beside** its sprinkler column. The same value is the
   range within which the corridor header snaps onto an aligned shaft (see
   *What gets drawn* below).
7. *Erase previous pipe run? \<Yes\>* — only asked when old pipes exist.

**Tilt is fully automatic** — never asked. The backend measures the grid
angle from the sprinklers themselves: one global angle from the corridor
heads, plus one angle **per room** — so a building where some rooms are
straight and some are tilted routes every room in its own frame
(`backend/app/joint/angle.py`). Irregular placements where no grid
direction dominates safely fall back to 0°.

### `SPK` — repeat with zero questions

Re-runs the Rooms routing using everything you answered last time (saved
in the drawing): layers, widths, offset, auto tilt, auto erase. Perfect
after moving sprinklers or walls.

### `SPKERASE` — clean up

*Erase [Pipes/All] \<Pipes\>* — `Pipes` removes the pipe network
(lines, arrows, shaft markers on `SPK-PIPE`); `All` also removes SPKAUTO's
heads, coverage circles and fail marks. Your own layers are never touched.

What gets drawn:

- A **main header** runs from each shaft **through the corridor** (kept
  ≥ 100 mm off the corridor walls, clean 90° runs — no diagonals anywhere
  in joint mode) and taps into every room through its corridor-side wall.
- Each room gets a **sub-header** placed `offset` mm beside a sprinkler
  column — never on top of the heads — with branch rows teeing off to the
  sprinklers exactly like Full mode.
- Sprinklers inside the corridor get a **median header**: it runs along
  the corridor BETWEEN the sprinkler rows, splitting them as evenly as
  possible (2 rows → dead centre, odd counts → e.g. 5|6), and every
  sprinkler column tees into it. Single sprinklers hang off the nearest
  run with a short perpendicular stub. When a shaft sits within the header
  `offset` of a corridor sprinkler row, the median header runs **straight
  up that shaft's column** instead of stepping `offset` aside — one clean
  feed line, no two-bend jog; a shaft farther off keeps the tidy offset
  header and a short connector.
- With several shafts the **rooms** are divided by corridor distance (the
  path the pipe would actually take, not straight-line), one colour per
  shaft as in Full mode. Each shaft builds its own header tree; they meet
  mid-corridor rather than forming one continuous loop.

The command line reports per-shaft heads/length and a **per-room status**:
rooms with no shared corridor wall are connected through the nearest point
(with a note), rooms that cannot reach the corridor at all are skipped with
a warning so you can fix the drawing.

### Pipe rendering

Each pipe segment is drawn as a **single centreline** with a solid
triangular arrow at its midpoint showing the flow direction (arrow size
follows the pipe width: `branch` segments use the branch width, everything
else the main width). The backend still returns the merged double-line
outlines in `outlines` if you ever want them back — the plugin simply
draws the centrelines.

---

## Design rules (NFPA-13, mm)

| Hazard   | max area/head (mm²) | max spacing (mm) | spacing S used |
|----------|--------------------:|-----------------:|---------------:|
| Light    | 20 900 000          | 4600             | ≈ 4572         |
| Ordinary | 12 100 000          | 4600             | ≈ 3479         |
| Extra    |  9 300 000          | 3700             | ≈ 3050         |

`S = min(max_spacing, sqrt(max_area))`. Minimum head-to-head spacing 1800 mm;
minimum wall clearance 100 mm; maximum wall distance S/2; coverage radius
`0.707*S` per head.

## Troubleshooting

- **"Backend not reachable at http://127.0.0.1:9000"** — start the backend
  (step 1). If another process owns port 9000, stop it or change the port in
  both `BackendClient.cs` and the uvicorn command.
- **NETLOAD fails / commands missing** — make sure `Newtonsoft.Json.dll` sits
  next to `SprinklerPlugin.dll`, and that the DLLs are not blocked (right-click
  → Properties → Unblock) if you copied them from another machine.
- **Nothing drawn** — check the command line: backend validation errors
  (e.g. self-intersecting boundary) abort the command with a message.

## Known limitations (v1)

- Curved (arc) polyline segments are followed faithfully (tessellated at ~2°
  steps) for room boundaries.
- The boundary polygon must not self-intersect; holes (interior islands) are
  not supported by `/place`.
- Each shaft feeds one cross main; mains run vertically (Y) on the shaft side
  of their head group, branch lines run horizontally (X). Head-to-shaft
  assignment is purely by straight-line distance (walls are not considered).
- Joint mode: a corridor narrower than the main pipe width draws an outline
  that overflows the corridor walls (the centreline stays inside); several
  corridor polylines on the layer collapse to the largest one. The header
  prefers the corridor centreline (wall-biased routing); in corridors of
  varying width the narrower stretches may still run closer to a wall.
