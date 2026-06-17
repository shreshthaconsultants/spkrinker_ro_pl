"""Grid placement endpoints — preview, multi-scenario generate, scenario download, legacy generate."""

import io

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from placement import SCENARIOS, WALL_MIN, WALL_MAX, SPACE_MIN, SPACE_MAX
from area_stats import compute_scenario_stats
from lsp_writer import make_lsp_files, build_scenario_zip

from ._shared import safe_json, zip_download_headers, run_placement

router = APIRouter()


@router.post("/api/preview")
async def preview_sprinklers(
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
):
    """Run grid placement once and return points + zone reports + coverage stats."""
    content = await file.read()
    result  = run_placement(
        content, floor_layers, wall_layers, excl_layers,
        obs_layers, obs_min_offset, radius,
        wall_min, wall_max, space_min, space_max,
        coverage_radius, enable_gap_fill,
    )
    stats = compute_scenario_stats(
        scenario_id     = 0,
        scenario_name   = "Preview",
        all_points      = result["all_points"],
        extra_points    = result["extra_points"],
        floor_polys     = result["floor_polys"],
        excl_polys      = result["excl_polys"],
        obs_polys       = result["obs_polys"],
        coverage_radius = float(coverage_radius),
        zone_reports    = result["zone_reports"],
        fast_mode       = False,
    )
    return safe_json({
        "points":       result["all_points"],
        "extra_points": result["extra_points"],
        "zones":        result["zone_reports"],
        "total":        len(result["all_points"]) + len(result["extra_points"]),
        "floor_bbox":   result["floor_bbox"],
        "stats":        stats,
        "floor_polys":  result["floor_polys"],
        "excl_polys":   result["excl_polys"],
        "obs_polys":    result["obs_polys"],
        "wall_segs":    result["wall_segs"],
    })


@router.post("/api/scenarios/generate")
async def generate_all_scenarios(
    file:           UploadFile = File(...),
    floor_layers:   str   = Form("ZONE-01,ZONE-02"),
    wall_layers:    str   = Form("S-A-WALL,wall"),
    excl_layers:    str   = Form("S-S-COLS,S-A-STAIRS"),
    obs_layers:     str   = Form("obs"),
    obs_min_offset: float = Form(150.0),
    radius:         int   = Form(1500),
    chunk_size:     int   = Form(2000),
    use_block:      bool  = Form(False),
    block_name:     str   = Form("SPRINKLER"),
):
    """Run every entry in SCENARIOS and return stats for each.

    Does NOT generate ZIP here — that is done per scenario via /api/scenarios/{id}/download.
    """
    content = await file.read()

    results = []
    for sc in SCENARIOS:
        try:
            result = run_placement(
                content       = content,
                floor_layers  = floor_layers,
                wall_layers   = wall_layers,
                excl_layers   = excl_layers,
                obs_layers    = obs_layers,
                obs_min_offset = obs_min_offset,
                radius        = radius,
                wall_min      = sc["wall_min"],
                wall_max      = sc["wall_max"],
                space_min     = sc["space_min"],
                space_max     = sc["space_max"],
                coverage_radius = sc["coverage_radius"],
                enable_gap_fill = True,
            )
            stats = compute_scenario_stats(
                scenario_id     = sc["id"],
                scenario_name   = sc["name"],
                all_points      = result["all_points"],
                extra_points    = result["extra_points"],
                floor_polys     = result["floor_polys"],
                excl_polys      = result["excl_polys"],
                obs_polys       = result["obs_polys"],
                coverage_radius = float(sc["coverage_radius"]),
                zone_reports    = result["zone_reports"],
                fast_mode       = False,
            )
            stats["scenario"]      = sc
            stats["spacing_rules"] = {
                "wall_band": f"{sc['wall_min']}–{sc['wall_max']}",
                "spacing":   f"{sc['space_min']}–{sc['space_max']}",
            }
            stats["points"]       = result["all_points"]
            stats["extra_points"] = result["extra_points"]
            stats["floor_bbox"]   = result["floor_bbox"]
            results.append(stats)
        except Exception as e:
            results.append({
                "scenario_id":   sc["id"],
                "scenario_name": sc["name"],
                "error":         str(e),
            })

    return safe_json({"scenarios": results})


@router.post("/api/scenarios/{scenario_id}/download")
async def download_scenario(
    scenario_id:    int,
    file:           UploadFile = File(...),
    floor_layers:   str   = Form("ZONE-01,ZONE-02"),
    wall_layers:    str   = Form("S-A-WALL,wall"),
    excl_layers:    str   = Form("S-S-COLS,S-A-STAIRS"),
    obs_layers:     str   = Form("obs"),
    obs_min_offset: float = Form(150.0),
    radius:         int   = Form(1500),
    chunk_size:     int   = Form(2000),
    use_block:      bool  = Form(False),
    block_name:     str   = Form("SPRINKLER"),
):
    """Generate and download ZIP (LSP files + stats) for a single scenario."""
    sc = next((s for s in SCENARIOS if s["id"] == scenario_id), None)
    if sc is None:
        raise HTTPException(404, f"Scenario {scenario_id} not found")

    content = await file.read()
    fl = [l.strip() for l in floor_layers.split(",") if l.strip()]
    el = [l.strip() for l in excl_layers.split(",")  if l.strip()]

    result = run_placement(
        content         = content,
        floor_layers    = floor_layers,
        wall_layers     = wall_layers,
        excl_layers     = excl_layers,
        obs_layers      = obs_layers,
        obs_min_offset  = obs_min_offset,
        radius          = radius,
        wall_min        = sc["wall_min"],
        wall_max        = sc["wall_max"],
        space_min       = sc["space_min"],
        space_max       = sc["space_max"],
        coverage_radius = sc["coverage_radius"],
        enable_gap_fill = True,
    )

    stats = compute_scenario_stats(
        scenario_id     = sc["id"],
        scenario_name   = sc["name"],
        all_points      = result["all_points"],
        extra_points    = result["extra_points"],
        floor_polys     = result["floor_polys"],
        excl_polys      = result["excl_polys"],
        obs_polys       = result["obs_polys"],
        coverage_radius = float(sc["coverage_radius"]),
        zone_reports    = result["zone_reports"],
        fast_mode       = False,
    )
    stats["spacing_rules"] = {
        "wall_band": f"{sc['wall_min']}–{sc['wall_max']}",
        "spacing":   f"{sc['space_min']}–{sc['space_max']}",
    }

    cmd_prefix = f"SPKL_SC{scenario_id}"
    lsp_files = make_lsp_files(
        all_points    = result["all_points"],
        extra_points  = result["extra_points"],
        radius        = radius,
        chunk_size    = chunk_size,
        floor_layers  = fl,
        excl_layers   = el,
        wall_min      = sc["wall_min"],
        wall_max      = sc["wall_max"],
        space_min     = sc["space_min"],
        space_max     = sc["space_max"],
        scenario_name = sc["name"],
        stats         = stats,
        use_block     = use_block,
        block_name    = block_name,
        cmd_prefix    = cmd_prefix,
    )

    zip_bytes = build_scenario_zip(
        scenario_stats = stats,
        lsp_files      = lsp_files,
        all_points     = result["all_points"],
        extra_points   = result["extra_points"],
    )

    filename = f"sprinklers_scenario_{scenario_id}.zip"
    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type = "application/zip",
        headers    = zip_download_headers(filename, lsp_files),
    )


@router.post("/api/generate")
async def generate_sprinklers(
    file:            UploadFile = File(...),
    floor_layers:    str   = Form("ZONE-01,ZONE-02"),
    wall_layers:     str   = Form("S-A-WALL,S-A-WALL-,wall"),
    excl_layers:     str   = Form("S-S-COLS,S-A-STAIRS,S-A-ELE-3,S-A-HACH,cut"),
    obs_layers:      str   = Form("obs"),
    obs_min_offset:  float = Form(150.0),
    radius:          int   = Form(1500),
    chunk_size:      int   = Form(2000),
    wall_min:        int   = Form(WALL_MIN),
    wall_max:        int   = Form(WALL_MAX),
    space_min:       int   = Form(SPACE_MIN),
    space_max:       int   = Form(SPACE_MAX),
    coverage_radius: int   = Form(1500),
    enable_gap_fill: bool  = Form(True),
    use_block:       bool  = Form(False),
    block_name:      str   = Form("SPRINKLER"),
):
    """Legacy: run placement and return ZIP of LSP files (used by older UI)."""
    if not file.filename.lower().endswith(".dxf"):
        raise HTTPException(400, "Only .dxf files supported.")

    content = await file.read()
    fl = [l.strip() for l in floor_layers.split(",") if l.strip()]
    el = [l.strip() for l in excl_layers.split(",")  if l.strip()]

    result = run_placement(
        content, floor_layers, wall_layers, excl_layers,
        obs_layers, obs_min_offset, radius,
        wall_min, wall_max, space_min, space_max,
        coverage_radius, enable_gap_fill,
    )

    stats = compute_scenario_stats(
        scenario_id     = 0,
        scenario_name   = "Custom",
        all_points      = result["all_points"],
        extra_points    = result["extra_points"],
        floor_polys     = result["floor_polys"],
        excl_polys      = result["excl_polys"],
        obs_polys       = result["obs_polys"],
        coverage_radius = float(coverage_radius),
        zone_reports    = result["zone_reports"],
        fast_mode       = False,
    )
    stats["spacing_rules"] = {
        "wall_band": f"{wall_min}–{wall_max}",
        "spacing":   f"{space_min}–{space_max}",
    }

    lsp_files = make_lsp_files(
        all_points    = result["all_points"],
        extra_points  = result["extra_points"],
        radius        = radius,
        chunk_size    = chunk_size,
        floor_layers  = fl,
        excl_layers   = el,
        wall_min      = wall_min,
        wall_max      = wall_max,
        space_min     = space_min,
        space_max     = space_max,
        scenario_name = "Custom",
        stats         = stats,
        use_block     = use_block,
        block_name    = block_name,
        cmd_prefix    = "SPRINKLERS",
    )

    zip_bytes = build_scenario_zip(
        scenario_stats = stats,
        lsp_files      = lsp_files,
        all_points     = result["all_points"],
        extra_points   = result["extra_points"],
    )

    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type = "application/zip",
        headers    = zip_download_headers("sprinklers.zip", lsp_files),
    )
