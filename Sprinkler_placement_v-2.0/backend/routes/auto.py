"""
/api/zwcad/auto — v2 universal one-shot placement endpoint.

The v1 /api/zwcad/scenarios route makes the user pick among Fixed-spacing
scenarios. v2's universal model removes that choice: send rooms (+ optional
labels + obstacles) and get back ONE compliant, minimal, verified layout
plus a validation report — zero human tuning.

Response (LISP S-expression, parsed by the plugin):

    ((0 ((x y rot) ...) () (passed total_heads flagged_count))
     REPORT-LINES)

  * pseudo-scenario id 0 carries every placed head as (x y rot) so the
    existing LispParser/ScenarioPoints consumer reads it unchanged
    (outside list empty; stats list = pass flag + head count + flagged).
  * the trailing REPORT-LINES is a list of quoted strings the plugin can
    echo to the ZWCAD terminal (the validation report).

Runs the full pipeline (classify → place → verify → self-fix → minimise →
GA) in a worker process, so the FastAPI event loop stays responsive.
"""

import atexit
import os
from concurrent.futures import ProcessPoolExecutor

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from geometry import point_in_poly, poly_area, poly_centroid
from config import DROP_CONTAINER_ROOMS

router = APIRouter()


_POOL: "ProcessPoolExecutor | None" = None


def _pool() -> ProcessPoolExecutor:
    global _POOL
    if _POOL is None:
        _POOL = ProcessPoolExecutor(max_workers=min(4, max(1, os.cpu_count() or 1)))
        atexit.register(_POOL.shutdown, wait=False)
    return _POOL


class AutoRequest(BaseModel):
    room_polys: list[list[list[float]]] = Field(default_factory=list)
    obs_polys:  list[list[list[float]]] = Field(default_factory=list)
    labels:     list[list] = Field(
        default_factory=list,
        description="[[x, y, text], ...] TEXT/MTEXT for hazard classification.",
    )
    obs_min_offset: float = 150.0
    default_hazard: str = "ordinary_2"
    conservative:   bool = False
    tilted:         bool = False
    minimise:       bool = False   # keep the dense clean grid by default;
                                   # on = fewest heads but circles separate
    enable_ga:      bool = True


def _ensure_closed(poly):
    out = [(float(p[0]), float(p[1])) for p in poly if len(p) >= 2]
    if len(out) < 3:
        return []
    if out[0] != out[-1]:
        out.append(out[0])
    return out


def _drop_containers(polys):
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
    return kept or polys


def _run_pipeline(room_polys, obs_polys, labels, kw):
    """Top-level so ProcessPoolExecutor can pickle it. Returns a plain dict
    (BuildingResult.to_dict) + the placed heads, so nothing FastAPI-bound
    crosses the process boundary."""
    from autoplace import pipeline as P
    res = P.autoplace_building(
        room_polys=room_polys, labels=labels, obs_polys=obs_polys, **kw,
    )
    heads = [list(h) for r in res.rooms for h in r.heads]
    return {"heads": heads, "report": res.to_dict(),
            "lines": res.summary_lines()}


def _heads_to_lisp(heads):
    if not heads:
        return "()"
    return "(" + " ".join(
        f"({round(h[0], 3)} {round(h[1], 3)} {round(h[2] if len(h) >= 3 else 0.0, 6)})"
        for h in heads
    ) + ")"


def _lines_to_lisp(lines):
    # quote each line so the plugin can read them as strings
    esc = [l.replace('"', "'") for l in lines]
    return "(" + " ".join(f'"{l}"' for l in esc) + ")"


@router.post("/api/zwcad/auto", response_class=PlainTextResponse)
def zwcad_auto(req: AutoRequest):
    rooms = []
    for rp in req.room_polys:
        cp = _ensure_closed(rp)
        if cp:
            rooms.append(cp)
    if not rooms:
        raise HTTPException(400, "No valid room polylines supplied.")
    rooms = _drop_containers(rooms)

    obs = []
    for op in req.obs_polys:
        cp = _ensure_closed(op)
        if cp:
            obs.append(cp)

    labels = []
    for lab in req.labels:
        if len(lab) >= 3:
            try:
                labels.append((float(lab[0]), float(lab[1]), str(lab[2])))
            except (TypeError, ValueError):
                continue

    kw = dict(
        obs_min_offset=float(req.obs_min_offset),
        default_hazard=req.default_hazard,
        force_conservative_if_any_unknown=bool(req.conservative),
        tilted=bool(req.tilted),
        minimise=bool(req.minimise),
        enable_ga=bool(req.enable_ga),
    )

    out = _pool().submit(_run_pipeline, rooms, obs, labels, kw).result()
    rep = out["report"]
    heads = out["heads"]

    # Console echo (uvicorn log) — same info the plugin will print.
    for line in out["lines"]:
        print(line)

    # stats = (passed total flagged coverage_pct) — the plugin draws these
    # in an NFPA validation stamp.
    stats = (f"({1 if rep['all_passed'] else 0} {rep['total_heads']} "
             f"{len(rep['flagged_rooms'])} {rep.get('coverage_pct', 0)})")
    scenario_chunk = f"(0 {_heads_to_lisp(heads)} () {stats})"
    return f"({scenario_chunk} {_lines_to_lisp(out['lines'])})"
