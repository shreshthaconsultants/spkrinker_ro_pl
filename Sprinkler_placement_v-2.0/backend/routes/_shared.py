"""Helpers shared across route modules.

Kept tiny on purpose — only the JSON-safety + zip-header helpers and the
DXF-driven placement runner used by /api/preview, /api/generate, and
/api/scenarios/{id}/download.
"""

from typing import Optional

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from dxf_loader import load_dxf_bytes, load_polys, load_wall_segs, get_all_layers
from placement  import generate_zone_sprinklers, WALL_MIN, WALL_MAX, SPACE_MIN, SPACE_MAX
from geometry   import bbox


def _safe(obj):
    """Coerce numpy scalars / arrays / NaN / inf into JSON-friendly Python types."""
    try:
        import numpy as np
        if isinstance(obj, np.integer):  return int(obj)
        if isinstance(obj, np.floating): return float(obj)
        if isinstance(obj, np.bool_):    return bool(obj)
        if isinstance(obj, np.ndarray):  return [_safe(v) for v in obj.tolist()]
    except ImportError:
        pass
    if isinstance(obj, dict):  return {k: _safe(v) for k, v in obj.items()}
    if isinstance(obj, list):  return [_safe(v) for v in obj]
    if isinstance(obj, tuple): return [_safe(v) for v in obj]
    if isinstance(obj, float):
        if obj != obj:           return None
        if obj == float("inf"):  return 9e15
        if obj == float("-inf"): return -9e15
    return obj


def safe_json(data, **kwargs):
    return JSONResponse(content=_safe(data), **kwargs)


def zip_download_headers(filename: str, lsp_files: list) -> dict:
    """Expose AutoCAD command names (one per .lsp file) to the web UI."""
    cmds = ",".join(lf["cmd"] for lf in lsp_files)
    return {
        "Content-Disposition":   f"attachment; filename={filename}",
        "X-Sprinkler-Commands":  cmds,
    }


def _bbox_merge(a: Optional[list], b: Optional[list]) -> Optional[list]:
    if a is None: return b
    if b is None: return a
    return [min(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), max(a[3], b[3])]


def _bbox_from_polys(polys: list) -> Optional[list]:
    if not polys: return None
    xs, ys = [], []
    for poly in polys:
        for p in poly:
            xs.append(float(p[0]))
            ys.append(float(p[1]))
    if not xs: return None
    return [min(xs), max(xs), min(ys), max(ys)]


def _bbox_from_segs(segs: list) -> Optional[list]:
    if not segs: return None
    xs, ys = [], []
    for s in segs:
        xs.extend([float(s[0]), float(s[2])])
        ys.extend([float(s[1]), float(s[3])])
    return [min(xs), max(xs), min(ys), max(ys)]


def run_placement(
    content:        bytes,
    floor_layers:   str,
    wall_layers:    str,
    excl_layers:    str,
    obs_layers:     str,
    obs_min_offset: float,
    radius:         int,
    wall_min:       int   = WALL_MIN,
    wall_max:       int   = WALL_MAX,
    space_min:      int   = SPACE_MIN,
    space_max:      int   = SPACE_MAX,
    coverage_radius: int  = 1500,
    enable_gap_fill: bool = True,
) -> dict:
    """Parse DXF, run grid placement for every floor zone, return result dict.

    Used by /api/preview, /api/scenarios/generate, /api/scenarios/{id}/download,
    /api/generate, /api/ga/optimise, /api/ga/download.
    """
    try:
        doc = load_dxf_bytes(content)
    except Exception as e:
        raise HTTPException(400, f"Could not parse DXF: {e}")

    msp = doc.modelspace()
    fl  = [l.strip() for l in floor_layers.split(",") if l.strip()]
    wl  = [l.strip() for l in wall_layers.split(",")  if l.strip()]
    el  = [l.strip() for l in excl_layers.split(",")  if l.strip()]
    ol  = [l.strip() for l in obs_layers.split(",")   if l.strip()]

    floor_polys = load_polys(msp, fl)
    excl_polys  = load_polys(msp, el)
    wall_polys  = load_polys(msp, wl)
    wall_segs   = load_wall_segs(msp, wl)
    obs_polys   = load_polys(msp, ol)

    if not floor_polys:
        available = get_all_layers(msp)
        raise HTTPException(404,
            f"No floor polygons on layers {fl}. Available layers: {available}"
        )

    if wall_min >= wall_max:
        raise HTTPException(400, f"wall_min ({wall_min}) must be < wall_max ({wall_max})")
    if space_min >= space_max:
        raise HTTPException(400, f"space_min ({space_min}) must be < space_max ({space_max})")

    all_points   = []
    all_extra    = []
    zone_reports = []
    floor_bbox   = [9e15, -9e15, 9e15, -9e15]

    for i, fp in enumerate(floor_polys):
        result = generate_zone_sprinklers(
            floor_poly      = fp,
            excl_polys      = excl_polys,
            wall_segs       = wall_segs,
            wall_polys      = wall_polys,
            obs_polys       = obs_polys,
            obs_min_offset  = float(obs_min_offset),
            all_placed      = all_points,
            wall_min        = wall_min,
            wall_max        = wall_max,
            space_min       = space_min,
            space_max       = space_max,
            coverage_radius = float(coverage_radius),
            enable_gap_fill = enable_gap_fill,
        )

        all_points.extend(result["points"])
        all_extra.extend(result["extra_points"])

        mnx, mxx, mny, mxy = bbox(fp)
        floor_bbox[0] = min(floor_bbox[0], mnx)
        floor_bbox[1] = max(floor_bbox[1], mxx)
        floor_bbox[2] = min(floor_bbox[2], mny)
        floor_bbox[3] = max(floor_bbox[3], mxy)

        zone_reports.append({
            "zone":           i + 1,
            "count":          len(result["points"]),
            "extra_count":    len(result["extra_points"]),
            "rejected":       result["rejected"],
            "warnings":       result["warnings"],
            "width_m":        result["width_m"],
            "height_m":       result["height_m"],
            "spacing_x":      result["spacing_x"],
            "spacing_y":      result["spacing_y"],
            "grid_cols":      result["grid_cols"],
            "grid_rows":      result["grid_rows"],
            "x_lines":        result["x_lines"],
            "y_lines":        result["y_lines"],
            "x_offset_mm":    result["x_offset_mm"],
            "y_offset_mm":    result["y_offset_mm"],
        })

    seen, deduped = set(), []
    for pt in all_points + all_extra:
        if pt not in seen:
            seen.add(pt)
            deduped.append(pt)

    grid_set = set(all_points)
    final_grid  = [p for p in deduped if p in grid_set]
    final_extra = [p for p in deduped if p not in grid_set]

    return {
        "all_points":   final_grid,
        "extra_points": final_extra,
        "zone_reports": zone_reports,
        "floor_polys":  floor_polys,
        "excl_polys":   excl_polys,
        "wall_polys":   wall_polys,
        "wall_segs":    wall_segs,
        "obs_polys":    obs_polys,
        "floor_bbox":   floor_bbox,
        "layers":       {"floor": fl, "wall": wl, "excl": el, "obs": ol},
    }
