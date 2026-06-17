"""Genetic-algorithm placement endpoints + Server-Sent Events progress stream.

The progress callback installed by /api/ga/optimise and /api/ga/download writes
events into a module-level `_progress_queue`, and /api/ga/progress drains the
queue as an SSE stream. This is the same pattern as the original main.py — only
one GA run can be tracked at a time.
"""

import asyncio
import io
import json
import queue
import traceback
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from dxf_loader import load_dxf_bytes, load_polys, load_wall_segs
from placement  import WALL_MIN, WALL_MAX, SPACE_MIN, SPACE_MAX
from area_stats import compute_scenario_stats
from lsp_writer import make_lsp_files, build_scenario_zip

from ._shared import safe_json, zip_download_headers, run_placement

try:
    from genetic_placement import optimise_placement, GA_PRESETS
    _GA_AVAILABLE = True
except ImportError:
    _GA_AVAILABLE = False


router = APIRouter()


# ── Progress queue (single global, matches original behavior) ──────

_progress_queue: Optional[queue.Queue] = None


def _make_progress_callback(zone_idx: int, total_zones: int):
    """Factory for progress callbacks that feed the SSE queue."""
    def callback(generation: int, best_fitness: float, coverage_pct: float):
        if _progress_queue:
            _progress_queue.put({
                "type":         "ga_progress",
                "zone":         zone_idx + 1,
                "total_zones":  total_zones,
                "generation":   generation,
                "fitness":      round(best_fitness, 4),
                "coverage_pct": round(coverage_pct, 2),
            })
    return callback


@router.get("/api/ga/progress")
async def ga_progress_stream():
    """SSE: drain `_progress_queue`, yield each item as a `ga_progress` event.

    Frontend usage:
        const es = new EventSource('/api/ga/progress');
        es.addEventListener('ga_progress', e => console.log(JSON.parse(e.data)));
    """
    global _progress_queue
    _progress_queue = queue.Queue()

    async def event_generator():
        try:
            while True:
                try:
                    msg = _progress_queue.get(timeout=0.1)
                    yield f"event: {msg['type']}\ndata: {json.dumps(msg)}\n\n"
                except queue.Empty:
                    yield ": heartbeat\n\n"
                    await asyncio.sleep(0.5)
        except Exception as e:
            print(f"[SSE ERROR] {e}")
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type = "text/event-stream",
        headers    = {
            "Cache-Control":     "no-cache",
            "Connection":        "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/ga/presets")
async def get_ga_presets():
    """Available GA preset configurations (fast / balanced / thorough)."""
    if not _GA_AVAILABLE:
        raise HTTPException(503, "Genetic algorithm module not available.")
    return safe_json({
        "available": True,
        "presets":   {k: v for k, v in GA_PRESETS.items()},
    })


@router.post("/api/ga/optimise")
async def ga_optimise(
    file:            UploadFile = File(...),
    floor_layers:    str   = Form("ZONE-01,ZONE-02"),
    wall_layers:     str   = Form("S-A-WALL,wall"),
    excl_layers:     str   = Form("S-S-COLS,S-A-STAIRS"),
    obs_layers:      str   = Form("obs"),
    obs_min_offset:  float = Form(150.0),
    radius:          int   = Form(1500),
    wall_min:        int   = Form(WALL_MIN),
    wall_max:        int   = Form(WALL_MAX),
    space_min:       int   = Form(SPACE_MIN),
    space_max:       int   = Form(SPACE_MAX),
    coverage_radius: int   = Form(1500),
    enable_gap_fill: bool  = Form(True),
    ga_preset:       str   = Form("balanced"),
    ga_seed:         int   = Form(-1),
):
    """Run grid placement THEN GA optimisation. Returns both for comparison."""
    if not _GA_AVAILABLE:
        raise HTTPException(503, "genetic_placement.py not found. Place it next to main.py.")
    if ga_preset not in GA_PRESETS:
        raise HTTPException(400, f"Unknown GA preset '{ga_preset}'. "
                                 f"Choose from: {list(GA_PRESETS.keys())}")

    content = await file.read()
    seed    = None if ga_seed < 0 else ga_seed

    grid_result = run_placement(
        content, floor_layers, wall_layers, excl_layers,
        obs_layers, obs_min_offset, radius,
        wall_min, wall_max, space_min, space_max,
        coverage_radius, enable_gap_fill,
    )

    grid_stats = compute_scenario_stats(
        scenario_id=0, scenario_name="Grid (before GA)",
        all_points=grid_result["all_points"],
        extra_points=grid_result["extra_points"],
        floor_polys=grid_result["floor_polys"],
        excl_polys=grid_result["excl_polys"],
        obs_polys=grid_result["obs_polys"],
        coverage_radius=float(coverage_radius),
        zone_reports=grid_result["zone_reports"],
        fast_mode=False,
    )

    try:
        doc = load_dxf_bytes(content)
        msp = doc.modelspace()
        fl = [l.strip() for l in floor_layers.split(",") if l.strip()]
        wl = [l.strip() for l in wall_layers.split(",")  if l.strip()]
        el = [l.strip() for l in excl_layers.split(",")  if l.strip()]
        ol = [l.strip() for l in obs_layers.split(",")   if l.strip()]

        floor_polys = load_polys(msp, fl)
        excl_polys  = load_polys(msp, el)
        wall_polys  = load_polys(msp, wl)
        wall_segs   = load_wall_segs(msp, wl)
        obs_polys   = load_polys(msp, ol)

        from placement import build_zone_wall_segs
        from geometry  import point_in_poly as pip

        ga_all_points   = []
        ga_extra_points = list(grid_result["extra_points"])
        ga_zone_reports = []
        all_fitness_log = []
        all_cov_log     = []

        for i, fp in enumerate(floor_polys):
            zone_grid_pts = [p for p in grid_result["all_points"] if pip(p[0], p[1], fp)]
            zone_wall_segs = build_zone_wall_segs(fp, wall_segs, wall_polys)
            progress_cb = _make_progress_callback(i, len(floor_polys))

            ga_out = optimise_placement(
                zone_result    = {"points": zone_grid_pts, "extra_points": []},
                floor_poly     = fp,
                excl_polys     = excl_polys,
                obs_polys      = obs_polys,
                zone_wall_segs = zone_wall_segs,
                coverage_radius= float(coverage_radius),
                wall_min       = float(wall_min),
                space_min      = float(space_min),
                obs_min_offset = float(obs_min_offset),
                preset         = ga_preset,
                seed           = seed,
                progress_cb    = progress_cb,
            )

            ga_all_points.extend(ga_out["points"])
            gs = ga_out.get("ga_stats", {})
            all_fitness_log.append(ga_out.get("ga_fitness_log", []))
            all_cov_log.append(ga_out.get("ga_coverage_log", []))

            ga_zone_reports.append({
                "zone":              i + 1,
                "initial_count":     gs.get("initial_count", 0),
                "optimised_count":   gs.get("optimised_count", 0),
                "count_delta":       gs.get("count_delta", 0),
                "final_coverage_pct": gs.get("final_coverage_pct", 0),
                "generations_run":   gs.get("generations_run", 0),
                "converged":         gs.get("converged", False),
            })

        ga_stats = compute_scenario_stats(
            scenario_id=99, scenario_name=f"GA Optimised ({ga_preset})",
            all_points=ga_all_points,
            extra_points=ga_extra_points,
            floor_polys=grid_result["floor_polys"],
            excl_polys=grid_result["excl_polys"],
            obs_polys=grid_result["obs_polys"],
            coverage_radius=float(coverage_radius),
            zone_reports=ga_zone_reports,
            fast_mode=False,
        )

    except Exception as e:
        tb = traceback.format_exc()
        print(f"[GA ERROR] {e}\n{tb}")
        raise HTTPException(500, f"GA optimisation failed: {e}")

    return safe_json({
        "grid": {
            "points":       grid_result["all_points"],
            "extra_points": grid_result["extra_points"],
            "stats":        grid_stats,
            "floor_bbox":   grid_result["floor_bbox"],
            "floor_polys":  grid_result["floor_polys"],
            "excl_polys":   grid_result["excl_polys"],
            "obs_polys":    grid_result["obs_polys"],
            "wall_segs":    grid_result["wall_segs"],
            "zones":        grid_result["zone_reports"],
        },
        "ga": {
            "points":        ga_all_points,
            "extra_points":  ga_extra_points,
            "stats":         ga_stats,
            "zone_reports":  ga_zone_reports,
            "fitness_logs":  all_fitness_log,
            "coverage_logs": all_cov_log,
            "preset":        ga_preset,
        },
        "comparison": {
            "grid_count":        len(grid_result["all_points"]),
            "ga_count":          len(ga_all_points),
            "count_delta":       len(ga_all_points) - len(grid_result["all_points"]),
            "grid_coverage_pct": grid_stats["coverage_pct"],
            "ga_coverage_pct":   ga_stats["coverage_pct"],
            "coverage_delta":    round(ga_stats["coverage_pct"] - grid_stats["coverage_pct"], 2),
        },
    })


@router.post("/api/ga/download")
async def download_ga(
    file:            UploadFile = File(...),
    floor_layers:    str   = Form("ZONE-01,ZONE-02"),
    wall_layers:     str   = Form("S-A-WALL,wall"),
    excl_layers:     str   = Form("S-S-COLS,S-A-STAIRS"),
    obs_layers:      str   = Form("obs"),
    obs_min_offset:  float = Form(150.0),
    radius:          int   = Form(1500),
    wall_min:        int   = Form(WALL_MIN),
    wall_max:        int   = Form(WALL_MAX),
    space_min:       int   = Form(SPACE_MIN),
    space_max:       int   = Form(SPACE_MAX),
    coverage_radius: int   = Form(1500),
    enable_gap_fill: bool  = Form(True),
    ga_preset:       str   = Form("balanced"),
    ga_seed:         int   = Form(-1),
    chunk_size:      int   = Form(2000),
    use_block:       bool  = Form(False),
    block_name:      str   = Form("SPRINKLER"),
):
    """Run grid + GA optimisation, return downloadable ZIP of LSP files."""
    if not _GA_AVAILABLE:
        raise HTTPException(503, "genetic_placement.py not found.")

    content = await file.read()
    seed    = None if ga_seed < 0 else ga_seed

    fl = [l.strip() for l in floor_layers.split(",") if l.strip()]
    el = [l.strip() for l in excl_layers.split(",")  if l.strip()]

    grid_result = run_placement(
        content, floor_layers, wall_layers, excl_layers,
        obs_layers, obs_min_offset, radius,
        wall_min, wall_max, space_min, space_max,
        coverage_radius, enable_gap_fill,
    )

    doc = load_dxf_bytes(content)
    msp = doc.modelspace()
    wl  = [l.strip() for l in wall_layers.split(",") if l.strip()]
    ol  = [l.strip() for l in obs_layers.split(",")  if l.strip()]

    floor_polys = load_polys(msp, fl)
    excl_polys  = load_polys(msp, el)
    wall_polys  = load_polys(msp, wl)
    wall_segs   = load_wall_segs(msp, wl)
    obs_polys   = load_polys(msp, ol)

    from placement import build_zone_wall_segs
    from geometry  import point_in_poly as pip

    ga_all_points   = []
    ga_extra_points = list(grid_result["extra_points"])
    ga_zone_reports = []

    for i, fp in enumerate(floor_polys):
        zone_grid_pts  = [p for p in grid_result["all_points"] if pip(p[0], p[1], fp)]
        zone_wall_segs = build_zone_wall_segs(fp, wall_segs, wall_polys)
        progress_cb    = _make_progress_callback(i, len(floor_polys))

        ga_out = optimise_placement(
            zone_result    = {"points": zone_grid_pts, "extra_points": []},
            floor_poly     = fp,
            excl_polys     = excl_polys,
            obs_polys      = obs_polys,
            zone_wall_segs = zone_wall_segs,
            coverage_radius= float(coverage_radius),
            wall_min       = float(wall_min),
            space_min      = float(space_min),
            obs_min_offset = float(obs_min_offset),
            preset         = ga_preset,
            seed           = seed,
            progress_cb    = progress_cb,
        )
        ga_all_points.extend(ga_out["points"])
        gs = ga_out.get("ga_stats", {})
        ga_zone_reports.append({
            "zone":          i + 1,
            "count":         gs.get("optimised_count", 0),
            "extra_count":   0,
            "rejected":      0,
            "warnings":      [],
            "width_m":       0,
            "height_m":      0,
            "spacing_x":     {},
            "spacing_y":     {},
            "grid_cols":     0,
            "grid_rows":     0,
            "x_lines":       [],
            "y_lines":       [],
            "x_offset_mm":   0,
            "y_offset_mm":   0,
        })

    ga_stats = compute_scenario_stats(
        scenario_id     = 99,
        scenario_name   = f"GA Optimised ({ga_preset})",
        all_points      = ga_all_points,
        extra_points    = ga_extra_points,
        floor_polys     = grid_result["floor_polys"],
        excl_polys      = grid_result["excl_polys"],
        obs_polys       = grid_result["obs_polys"],
        coverage_radius = float(coverage_radius),
        zone_reports    = ga_zone_reports,
        fast_mode       = False,
    )
    ga_stats["spacing_rules"] = {
        "wall_band": f"{wall_min}–{wall_max}",
        "spacing":   f"{space_min}–{space_max}",
    }

    lsp_files = make_lsp_files(
        all_points    = ga_all_points,
        extra_points  = ga_extra_points,
        radius        = radius,
        chunk_size    = chunk_size,
        floor_layers  = fl,
        excl_layers   = el,
        wall_min      = wall_min,
        wall_max      = wall_max,
        space_min     = space_min,
        space_max     = space_max,
        scenario_name = f"GA Optimised ({ga_preset})",
        stats         = ga_stats,
        use_block     = use_block,
        block_name    = block_name,
        cmd_prefix    = "SPKL_GA",
    )

    zip_bytes = build_scenario_zip(
        scenario_stats = ga_stats,
        lsp_files      = lsp_files,
        all_points     = ga_all_points,
        extra_points   = ga_extra_points,
    )

    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type = "application/zip",
        headers    = zip_download_headers("sprinklers_ga_optimised.zip", lsp_files),
    )
