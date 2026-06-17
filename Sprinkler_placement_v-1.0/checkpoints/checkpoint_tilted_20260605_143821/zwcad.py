"""ZWCAD plugin endpoint — accepts JSON room polylines, returns LISP S-expression.

Scenarios are executed in parallel across worker processes (one per scenario,
capped at 4) so the overall wall-clock time is roughly that of the single
slowest scenario instead of the sum of all four.
"""

import atexit
import os
from concurrent.futures import ProcessPoolExecutor

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from placement import SCENARIOS, run_scenario_for_floors
from geometry import point_in_poly, poly_area, poly_centroid
from config import DROP_CONTAINER_ROOMS

from models import ZWCADScenarioRequest

router = APIRouter()


# ── Global worker pool ────────────────────────────────────────────
#
# A single ProcessPoolExecutor is created lazily on first request and reused
# across requests. Each child process imports placement.py + geometry.py
# (no FastAPI), so spawn cost is paid once for the lifetime of the server.
# max_workers = min(4, available CPUs) — covers the plugin's current
# scenario count (3: Fixed 3000 / Fixed 2700 / 3050-3100 mm) so all run
# concurrently.

_PROCESS_POOL: ProcessPoolExecutor | None = None


def _get_pool() -> ProcessPoolExecutor:
    global _PROCESS_POOL
    if _PROCESS_POOL is None:
        workers = min(4, max(1, (os.cpu_count() or 1)))
        _PROCESS_POOL = ProcessPoolExecutor(max_workers=workers)
        atexit.register(_PROCESS_POOL.shutdown, wait=False)
    return _PROCESS_POOL


# ── Helpers ───────────────────────────────────────────────────────

def _ensure_closed_poly(poly: list[list[float]]) -> list[tuple[float, float]]:
    out = [(float(p[0]), float(p[1])) for p in poly if len(p) >= 2]
    if len(out) < 3:
        return []
    if out[0] != out[-1]:
        out.append(out[0])
    return out


def _drop_container_polys(polys: list) -> list:
    """Remove polylines that fully CONTAIN another polyline — e.g. a
    bounding rectangle the user drew around the real rooms on the same
    layer, or the building outline. A bigger polygon whose interior holds
    a smaller polygon's centroid is treated as a container and skipped,
    so only the real rooms receive sprinklers.

    Toggle via DROP_CONTAINER_ROOMS in config.py.
    """
    if not DROP_CONTAINER_ROOMS or len(polys) < 2:
        return polys
    info = [(p, poly_area(p), poly_centroid(p)) for p in polys]
    kept = []
    for i, (a, area_a, _ca) in enumerate(info):
        is_container = False
        for j, (_b, area_b, cb) in enumerate(info):
            if i == j or area_b >= area_a:
                continue
            if point_in_poly(cb[0], cb[1], a):
                is_container = True
                break
        if not is_container:
            kept.append(a)
    return kept or polys   # never drop everything


def _points_to_lisp(points: list[tuple[float, float, float]]) -> str:
    """
    Each point is (x, y, rotation_radians). Rotation is the room's
    principal angle so the plugin can rotate inserted blocks to align
    with tilted walls. Axis-aligned rooms send rotation 0.0.
    """
    if not points:
        return "()"
    chunks = [
        f"({round(x, 3)} {round(y, 3)} {round(r, 6)})"
        for x, y, r in points
    ]
    return "(" + " ".join(chunks) + ")"


def _outside_to_lisp(points: list[tuple[float, float]]) -> str:
    """
    Each entry is (x, y) — a bbox-grid intersection that fell outside the
    architecture polyline beyond the nudge margin. No rotation needed; the
    plugin draws these as simple green debug markers, not full blocks.
    """
    if not points:
        return "()"
    chunks = [f"({round(x, 3)} {round(y, 3)})" for x, y in points]
    return "(" + " ".join(chunks) + ")"


# ── Route ─────────────────────────────────────────────────────────

# Sync def — FastAPI runs it in its worker threadpool, so the .result()
# calls below don't block the event loop.
@router.post("/api/zwcad/scenarios", response_class=PlainTextResponse)
def zwcad_scenarios(req: ZWCADScenarioRequest):
    """Run requested scenarios against given room polylines, return LISP text.

    Response shape:  ((sid ((x y rot) ...) ((x y) ...)) ...)
    The ZWCAD/LISP plugin parses this directly with `(read response)`.

    Requested scenarios run concurrently in worker processes; total wall
    time ≈ slowest scenario, not sum. enable_gap_fill is honoured per request
    so callers can opt out of the (expensive) gap-fill phase for speed.

    `scenario_ids` is validated against the SCENARIOS list defined in
    placement.py — any id in that list is accepted. If the client sends an
    empty/unknown list, the route falls back to running every defined
    scenario so the response is never empty.
    """
    allowed_ids = [s["id"] for s in SCENARIOS]
    requested_ids = [sid for sid in req.scenario_ids if sid in allowed_ids] or allowed_ids

    floor_polys = []
    for rp in req.room_polys:
        cp = _ensure_closed_poly(rp)
        if cp:
            floor_polys.append(cp)
    if not floor_polys:
        raise HTTPException(400, "No valid room polylines supplied.")
    floor_polys = _drop_container_polys(floor_polys)

    obstacle_polys: list[list[tuple[float, float]]] = []
    for op in req.obs_polys:
        cp = _ensure_closed_poly(op)
        if cp:
            obstacle_polys.append(cp)

    scenario_defs = {s["id"]: s for s in SCENARIOS if s["id"] in allowed_ids}
    pool = _get_pool()

    # Submit each scenario to the worker pool, then collect in request order.
    obs_min_offset  = float(req.obs_min_offset)
    enable_gap_fill = bool(req.enable_gap_fill)
    tilted          = bool(req.tilted)

    futures: dict[int, object] = {}
    for sid in requested_ids:
        sc = scenario_defs.get(sid)
        if sc is None:
            continue
        futures[sid] = pool.submit(
            run_scenario_for_floors,
            sc,
            floor_polys,
            obstacle_polys,
            obs_min_offset,
            enable_gap_fill,
            tilted,
        )

    payload_chunks: list[str] = []
    for sid in requested_ids:
        f = futures.get(sid)
        if f is None:
            continue
        deduped, culled = f.result()
        # Format: (sid (heads) (outside))
        # heads:   (x y rot) triples — what the plugin places as PP-CEILING blocks
        # outside: (x y) pairs       — bbox-grid points the plugin draws as green
        #                              debug markers so the user can see what
        #                              fell outside the architecture polyline.
        # Older plugins read only (sid heads) and ignore the third element.
        payload_chunks.append(
            f"({sid} {_points_to_lisp(deduped)} {_outside_to_lisp(culled)})"
        )

    return "(" + " ".join(payload_chunks) + ")"
