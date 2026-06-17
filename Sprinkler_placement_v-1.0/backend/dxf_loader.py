"""
dxf_loader.py — DXF file reading helpers.
Depends on: ezdxf, geometry.py
"""

import io
import math
import os
import tempfile

try:
    import ezdxf
    from ezdxf import recover as ezdxf_recover
except ImportError:
    raise SystemExit(
        "\n[ERROR] ezdxf not installed.\n  Run: pip install ezdxf\n"
    )

from geometry import poly_vertices



# ── Robust DXF loader ─────────────────────────────────────────────

def load_dxf_bytes(content: bytes):
    """4-strategy robust DXF loader. Returns ezdxf Document."""
    last_error = None

    # Strategy 1: temp file + ezdxf.recover
    try:
        with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            doc, _ = ezdxf_recover.readfile(tmp_path)
            return doc
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except Exception as e:
        last_error = e

    # Strategy 2: BytesIO
    try:
        return ezdxf.read(io.BytesIO(content))
    except Exception as e:
        last_error = e

    # Strategy 3: UTF-8 string
    try:
        return ezdxf.read(io.StringIO(content.decode("utf-8", errors="replace")))
    except Exception as e:
        last_error = e

    # Strategy 4: Latin-1 string
    try:
        return ezdxf.read(io.StringIO(content.decode("latin-1", errors="replace")))
    except Exception as e:
        last_error = e

    raise ValueError(
        f"Cannot parse DXF: {last_error}. "
        "Save as: File > Save As > AutoCAD 2010 DXF"
    )


# ── Polygon loaders ───────────────────────────────────────────────

def load_polys(msp, layer_names: list) -> list:
    """
    Load closed polygons from named layers.
    Handles LWPOLYLINE, POLYLINE (≥3 pts) and CIRCLE (32-segment approx).
    """
    targets = {l.upper() for l in layer_names}
    result = []

    for e in msp:
        if e.dxf.layer.upper() not in targets:
            continue

        if e.dxftype() in ("LWPOLYLINE", "POLYLINE"):
            pts = poly_vertices(e)
            unique = list(dict.fromkeys(pts))
            if len(unique) >= 3:
                if unique[0] != unique[-1]:
                    unique.append(unique[0])
                result.append(unique)

        elif e.dxftype() == "CIRCLE":
            cx, cy = float(e.dxf.center.x), float(e.dxf.center.y)
            r = float(e.dxf.radius)
            n = 32
            circle_pts = [
                (cx + r * math.cos(2 * math.pi * i / n),
                 cy + r * math.sin(2 * math.pi * i / n))
                for i in range(n)
            ]
            circle_pts.append(circle_pts[0])
            result.append(circle_pts)

    return result


def load_wall_segs(msp, layer_names: list) -> list:
    """
    Load wall geometry from named layers as flat (x1,y1,x2,y2) segments.
    Handles: LINE, LWPOLYLINE (open or closed), POLYLINE.
    """
    targets = {l.upper() for l in layer_names}
    segs = []

    for e in msp:
        if e.dxf.layer.upper() not in targets:
            continue

        if e.dxftype() == "LINE":
            segs.append((
                float(e.dxf.start.x), float(e.dxf.start.y),
                float(e.dxf.end.x),   float(e.dxf.end.y),
            ))

        elif e.dxftype() in ("LWPOLYLINE", "POLYLINE"):
            pts = poly_vertices(e)
            if len(pts) < 2:
                continue
            for i in range(len(pts) - 1):
                x1, y1 = pts[i]
                x2, y2 = pts[i + 1]
                if math.hypot(x2 - x1, y2 - y1) > 1e-6:
                    segs.append((x1, y1, x2, y2))

    return segs


def get_all_layers(msp) -> list:
    """Return sorted list of all layer names in modelspace."""
    return sorted({e.dxf.layer for e in msp})
