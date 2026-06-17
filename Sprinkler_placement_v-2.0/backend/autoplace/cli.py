"""
cli.py — Step 8: headless batch runner.

    python -m autoplace.cli --input building.dxf --out result.dxf --report report.json

DXF in → auto-detect layers → extract rooms / labels / obstacles → run the
universal pipeline → write a DXF with sprinkler blocks + a JSON compliance
report. No ZWCAD, no human. The plugin becomes a one-click wrapper around
this same pipeline.

Layer auto-detection (overridable with flags):
  rooms     = the layer holding the most CLOSED polylines (≥3 verts)
  obstacles = a layer named like obs/column/col (optional)
  labels    = all TEXT / MTEXT entities, assigned to rooms by position

Run from the backend/ directory (so `autoplace`, `placement`, `geometry`
import cleanly).
"""

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

from . import pipeline as P
from . import nfpa_rules as R
from . import feedback_log as L


# ── DXF extraction ────────────────────────────────────────────────

def _load(path: str):
    import ezdxf
    from ezdxf import recover
    try:
        doc, _ = recover.readfile(path)
    except Exception:
        doc = ezdxf.readfile(path)
    return doc


def _closed_polys_by_layer(msp):
    """layer -> list of closed polygons [(x,y), ...]."""
    from geometry import poly_vertices
    out = defaultdict(list)
    for e in msp:
        if e.dxftype() in ("LWPOLYLINE", "POLYLINE"):
            pts = poly_vertices(e)
            if len(pts) >= 4:          # poly_vertices closes the ring
                out[e.dxf.layer].append(pts)
    return out


def _labels(msp):
    """All TEXT/MTEXT as (x, y, text)."""
    out = []
    for e in msp:
        t = e.dxftype()
        if t == "TEXT":
            p = e.dxf.insert
            out.append((float(p[0]), float(p[1]), str(e.dxf.text)))
        elif t == "MTEXT":
            p = e.dxf.insert
            out.append((float(p[0]), float(p[1]), str(e.text)))
    return out


def _auto_layers(by_layer):
    """Pick the room layer = layer with the most closed polylines."""
    if not by_layer:
        return None
    return max(by_layer.items(), key=lambda kv: len(kv[1]))[0]


# ── DXF output ────────────────────────────────────────────────────

def _write_dxf(out_path, rooms_heads, radius=1500.0):
    import ezdxf
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    if "SPRINKLERS" not in doc.layers:
        doc.layers.add("SPRINKLERS", color=1)
    if "SPRINKLER-COVERAGE" not in doc.layers:
        doc.layers.add("SPRINKLER-COVERAGE", color=8)
    for (x, y, _r) in rooms_heads:
        msp.add_circle((x, y), 80.0, dxfattribs={"layer": "SPRINKLERS"})
        msp.add_circle((x, y), radius, dxfattribs={"layer": "SPRINKLER-COVERAGE"})
    doc.saveas(out_path)


# ── Main ──────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="autoplace.cli",
        description="Universal sprinkler auto-placement (DXF in → DXF + report).",
    )
    ap.add_argument("--input", required=True, help="source DXF")
    ap.add_argument("--out", required=True, help="output DXF")
    ap.add_argument("--report", default=None, help="JSON report path")
    ap.add_argument("--room-layer", default=None, help="override room layer")
    ap.add_argument("--obs-layer", default=None, help="obstacle layer")
    ap.add_argument("--obs-offset", type=float, default=150.0)
    ap.add_argument("--default-hazard", default=R.ORDINARY_2)
    ap.add_argument("--conservative", action="store_true",
                    help="force tightest hazard if any room is unlabeled")
    ap.add_argument("--tilted", action="store_true")
    ap.add_argument("--no-minimise", action="store_true")
    ap.add_argument("--no-ga", action="store_true")
    args = ap.parse_args(argv)

    src = Path(args.input)
    if not src.is_file():
        print(f"error: input not found: {src}", file=sys.stderr)
        return 1

    doc = _load(str(src))
    msp = doc.modelspace()
    by_layer = _closed_polys_by_layer(msp)
    if not by_layer:
        print("error: no closed polylines found in DXF", file=sys.stderr)
        return 1

    room_layer = args.room_layer or _auto_layers(by_layer)
    rooms = by_layer.get(room_layer, [])
    if not rooms:
        print(f"error: no rooms on layer {room_layer!r}. "
              f"Layers with polylines: {sorted(by_layer)}", file=sys.stderr)
        return 1

    obs_polys = []
    if args.obs_layer:
        obs_polys = by_layer.get(args.obs_layer, [])
    else:
        for lyr, polys in by_layer.items():
            low = lyr.lower()
            if lyr != room_layer and ("obs" in low or "col" in low):
                obs_polys.extend(polys)

    labels = _labels(msp)

    print(f"rooms layer: {room_layer!r}  rooms: {len(rooms)}  "
          f"obstacles: {len(obs_polys)}  labels: {len(labels)}")

    result = P.autoplace_building(
        room_polys=rooms, labels=labels, obs_polys=obs_polys,
        obs_min_offset=args.obs_offset, default_hazard=args.default_hazard,
        force_conservative_if_any_unknown=args.conservative,
        tilted=args.tilted, minimise=not args.no_minimise,
        enable_ga=not args.no_ga,
    )

    all_heads = [h for r in result.rooms for h in r.heads]
    _write_dxf(args.out, all_heads)

    for line in result.summary_lines():
        print(line)

    if args.report:
        Path(args.report).write_text(
            json.dumps(result.to_dict(), indent=2), encoding="utf-8")
        print(f"report -> {args.report}")

    L.log_run(src.stem, result, meta={"src": str(src), "via": "cli"})
    return 0 if result.all_passed else 2


if __name__ == "__main__":
    sys.exit(main())
