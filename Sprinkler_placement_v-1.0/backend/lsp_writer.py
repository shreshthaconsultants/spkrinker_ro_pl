"""
lsp_writer.py — AutoLISP file generator for sprinkler placement.

Generates .lsp files that, when loaded in AutoCAD (APPLOAD),
draw sprinkler symbols (circle + X cross) on layer SPRINKLERS.
"""

import io
import zipfile


# ── Single LSP file builder ───────────────────────────────────────

def build_lsp(
    points:       list,
    extra_points: list,
    cmd_name:     str,
    radius:       int,
    total:        int,
    part:         int,
    num_parts:    int,
    floor_layers: list,
    excl_layers:  list,
    is_first:     bool,
    wall_min:     int   = 1000,
    wall_max:     int   = 1500,
    space_min:    int   = 2400,
    space_max:    int   = 3200,
    scenario_name: str  = "",
    stats:        dict  = None,
    use_block:    bool  = False,
    block_name:   str   = "SPRINKLER",
) -> str:
    """
    Generate a complete AutoLISP script.

    Grid sprinklers → layer SPRINKLERS (Red, color 1)
    Extra gap-fill sprinklers → layer SPRINKLERS-EXTRA (Cyan, color 4)
    Both use X-cross + circle symbol.
    """
    arm = round(radius / 8, 3)

    lines = []
    lines.append(";; =====================================================")
    lines.append(";; SPRINKLER AUTO-PLACEMENT v1.0")
    lines.append(f";; Scenario     : {scenario_name or cmd_name}")
    lines.append(f";; Wall band    : {wall_min}–{wall_max} mm")
    lines.append(f";; Spacing      : {space_min}–{space_max} mm")
    lines.append(f";; Radius (mm)  : {radius}")
    if num_parts > 1:
        lines.append(f";; Part         : {part} of {num_parts}")
    lines.append(f";; Grid heads   : {len(points)}")
    lines.append(f";; Gap-fill hdrs: {len(extra_points)}")
    lines.append(f";; Total heads  : {total}")
    lines.append(f";; Floor layers : {', '.join(floor_layers)}")
    lines.append(f";; Excl layers  : {', '.join(excl_layers)}")
    if stats:
        lines.append(f";; Floor area   : {stats.get('floor_area_m2', '?')} m²")
        lines.append(f";; Circle area  : {stats.get('single_circle_area_m2', '?')} m² each")
        lines.append(f";; Avg cov/spr  : {stats.get('avg_sprinkler_area_m2', '?')} m²")
        lines.append(f";; Coverage     : {stats.get('coverage_pct', '?')}%")
        lines.append(f";; Uncovered    : {stats.get('uncovered_m2', '?')} m²")
    lines.append(f";; Command      : {cmd_name}")
    lines.append(";; =====================================================")
    lines.append("")
    lines.append(f"(defun c:{cmd_name} ( / cx cy x1 y1 x2 y2)")
    lines.append("")

    if is_first:
        # Set up layers
        lines.append("  ;; ---- Layer setup ----")
        lines.append('  (command "._LAYER" "M" "SPRINKLERS" "C" "1" "SPRINKLERS" "")')
        lines.append('  (command "._LAYER" "M" "SPRINKLERS-EXTRA" "C" "4" "SPRINKLERS-EXTRA" "")')
        lines.append('  (command "._LAYER" "SET" "SPRINKLERS" "")')
        lines.append("")

    if use_block:
        # ── Block inserts ────────────────────────────────────────
        if points:
            lines.append(f"  ;; ---- Insert {len(points)} grid sprinkler blocks ----")
            for x, y in points:
                lines.append(f'  (command "._INSERT" "{block_name}" "{x},{y},0" 1 1 0)')

        if extra_points:
            lines.append("")
            lines.append('  (command "._LAYER" "SET" "SPRINKLERS-EXTRA" "")')
            lines.append(f"  ;; ---- Insert {len(extra_points)} gap-fill sprinkler blocks ----")
            for x, y in extra_points:
                lines.append(f'  (command "._INSERT" "{block_name}" "{x},{y},0" 1 1 0)')

    else:
        # ── Draw symbols ─────────────────────────────────────────
        if points:
            lines.append(f"  ;; ---- Draw {len(points)} GRID sprinklers (layer: SPRINKLERS) ----")
            lines.append(f"  ;; Symbol: circle r={radius}mm + X cross arm={arm}mm")
            lines.append("")
            lines.append('  (command "._LAYER" "SET" "SPRINKLERS" "")')
            for x, y in points:
                lines.append(f'  (command "._CIRCLE" "{x},{y},0" "{radius}")')
                lines.append(f'  (command "._LINE" "{x-arm},{y+arm},0" "{x+arm},{y-arm},0" "")')
                lines.append(f'  (command "._LINE" "{x-arm},{y-arm},0" "{x+arm},{y+arm},0" "")')

        if extra_points:
            lines.append("")
            lines.append(f"  ;; ---- Draw {len(extra_points)} GAP-FILL sprinklers (layer: SPRINKLERS-EXTRA) ----")
            lines.append(f"  ;; These fill uncovered areas; placed with reduced spacing")
            lines.append("")
            lines.append('  (command "._LAYER" "SET" "SPRINKLERS-EXTRA" "")')
            for x, y in extra_points:
                lines.append(f'  (command "._CIRCLE" "{x},{y},0" "{radius}")')
                lines.append(f'  (command "._LINE" "{x-arm},{y+arm},0" "{x+arm},{y-arm},0" "")')
                lines.append(f'  (command "._LINE" "{x-arm},{y-arm},0" "{x+arm},{y+arm},0" "")')

    lines.append("")
    g_count = len(points)
    e_count = len(extra_points)
    lines.append(f'  (princ "\\n[DONE] {g_count} grid + {e_count} gap-fill = {g_count+e_count} sprinklers placed")')
    lines.append("  (princ)")
    lines.append(")")
    lines.append("")
    lines.append(f";; AutoCAD: APPLOAD this file, then type: {cmd_name}")
    return "\n".join(lines)


# ── Multi-chunk LSP file set ──────────────────────────────────────

def make_lsp_files(
    all_points:    list,
    extra_points:  list,
    radius:        int,
    chunk_size:    int,
    floor_layers:  list,
    excl_layers:   list,
    wall_min:      int  = 1000,
    wall_max:      int  = 1500,
    space_min:     int  = 2400,
    space_max:     int  = 3200,
    scenario_name: str  = "",
    stats:         dict = None,
    use_block:     bool = False,
    block_name:    str  = "SPRINKLER",
    cmd_prefix:    str  = "SPRINKLERS",
) -> list:
    """
    Split all_points + extra_points into chunks and build LSP files.
    Extra points are always appended to the last file to keep them separate.
    """
    # Chunk grid points only; extras go in their own logical group
    grid_chunks = [
        all_points[i:i + chunk_size]
        for i in range(0, len(all_points), chunk_size)
    ] or [[]]

    n = len(grid_chunks)
    total = len(all_points) + len(extra_points)
    files = []

    for idx, chunk in enumerate(grid_chunks):
        is_last = idx == n - 1
        extras_here = extra_points if is_last else []

        cmd  = cmd_prefix if n == 1 else f"{cmd_prefix}{idx + 1}"
        name = f"{cmd_prefix.lower()}.lsp" if n == 1 else f"{cmd_prefix.lower()}_{idx + 1}.lsp"

        content = build_lsp(
            points=chunk,
            extra_points=extras_here,
            cmd_name=cmd,
            radius=radius,
            total=total,
            part=idx + 1,
            num_parts=n,
            floor_layers=floor_layers,
            excl_layers=excl_layers,
            is_first=(idx == 0),
            wall_min=wall_min,
            wall_max=wall_max,
            space_min=space_min,
            space_max=space_max,
            scenario_name=scenario_name,
            stats=stats,
            use_block=use_block,
            block_name=block_name,
        )
        files.append({"filename": name, "content": content, "cmd": cmd})

    return files


# ── ZIP builder ───────────────────────────────────────────────────

def build_scenario_zip(
    scenario_stats:  dict,
    lsp_files:       list,
    all_points:      list,
    extra_points:    list,
) -> bytes:
    """Package all LSP files + points + summary into a ZIP."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:

        for lf in lsp_files:
            zf.writestr(lf["filename"], lf["content"])

        # Grid points
        grid_txt = "\n".join(f"{x},{y}" for x, y in all_points)
        zf.writestr("grid_sprinklers.txt", grid_txt)

        # Extra (gap-fill) points
        extra_txt = "\n".join(f"{x},{y}" for x, y in extra_points)
        zf.writestr("gap_fill_sprinklers.txt", extra_txt)

        # Stats JSON
        import json
        zf.writestr("scenario_stats.json", json.dumps(scenario_stats, indent=2))

        # Readme
        s = scenario_stats
        cmds = "\n".join(
            f"  {i+1}. APPLOAD → {lf['filename']}   then type: {lf['cmd']}"
            for i, lf in enumerate(lsp_files)
        )
        readme = f"""SPRINKLER AUTO-PLACEMENT v1.0
==============================
Scenario        : {s.get('scenario_name', '?')}
Total sprinklers: {s.get('total_sprinklers', '?')}
  Grid heads    : {s.get('grid_sprinklers', '?')}
  Gap-fill heads: {s.get('extra_sprinklers', '?')}
Floor area      : {s.get('floor_area_m2', '?')} m²
Single circle   : {s.get('single_circle_area_m2', '?')} m²
Avg per spklr   : {s.get('avg_sprinkler_area_m2', '?')} m²
Coverage        : {s.get('coverage_pct', '?')}%
Uncovered       : {s.get('uncovered_m2', '?')} m²
LSP files       : {len(lsp_files)}

AUTOCAD STEPS:
{cmds}

LAYERS:
  SPRINKLERS       = Grid sprinklers (Red)
  SPRINKLERS-EXTRA = Gap-fill sprinklers (Cyan)

RULES:
  Wall band   : {s.get('spacing_rules', {}).get('wall_band', '?')} mm
  Spacing     : {s.get('spacing_rules', {}).get('spacing', '?')} mm
"""
        zf.writestr("README.txt", readme)

    buf.seek(0)
    return buf.read()