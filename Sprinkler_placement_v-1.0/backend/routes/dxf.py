"""DXF inspection endpoints — parse uploaded DXF and return layer / geometry info."""

from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from dxf_loader import load_dxf_bytes, load_polys, load_wall_segs, get_all_layers

from ._shared import safe_json, _bbox_merge, _bbox_from_polys, _bbox_from_segs

router = APIRouter()


@router.post("/api/layers")
async def get_layers(file: UploadFile = File(...)):
    """List every layer in the DXF with entity count and entity types."""
    if not file.filename.lower().endswith(".dxf"):
        raise HTTPException(400, "Only .dxf files are supported.")
    content = await file.read()
    try:
        doc = load_dxf_bytes(content)
        msp = doc.modelspace()
        layer_info = {}
        for e in msp:
            l = e.dxf.layer
            if l not in layer_info:
                layer_info[l] = {"count": 0, "types": set()}
            layer_info[l]["count"] += 1
            layer_info[l]["types"].add(e.dxftype())
        layer_detail = {
            l: {"count": v["count"], "types": sorted(v["types"])}
            for l, v in layer_info.items()
        }
        return safe_json({
            "layers":       get_all_layers(msp),
            "count":        len(layer_detail),
            "layer_detail": layer_detail,
        })
    except Exception as e:
        raise HTTPException(400, f"Failed to parse DXF: {e}")


@router.post("/api/geometry")
async def geometry_preview(
    file: UploadFile = File(...),
    floor_layers: str = Form(""),
    wall_layers: str = Form(""),
    excl_layers: str = Form(""),
    obs_layers: str = Form(""),
):
    """Return closed polylines and wall segments for canvas drawing (no placement)."""
    content = await file.read()
    try:
        doc = load_dxf_bytes(content)
    except Exception as e:
        raise HTTPException(400, f"Could not parse DXF: {e}")

    msp = doc.modelspace()
    fl = [l.strip() for l in floor_layers.split(",") if l.strip()]
    wl = [l.strip() for l in wall_layers.split(",")  if l.strip()]
    el = [l.strip() for l in excl_layers.split(",")  if l.strip()]
    ol = [l.strip() for l in obs_layers.split(",")   if l.strip()]

    floor_polys = load_polys(msp, fl) if fl else []
    excl_polys  = load_polys(msp, el) if el else []
    obs_polys   = load_polys(msp, ol) if ol else []
    wall_segs   = load_wall_segs(msp, wl) if wl else []

    bb: Optional[list] = None
    bb = _bbox_merge(bb, _bbox_from_polys(floor_polys))
    bb = _bbox_merge(bb, _bbox_from_polys(excl_polys))
    bb = _bbox_merge(bb, _bbox_from_polys(obs_polys))
    bb = _bbox_merge(bb, _bbox_from_segs(wall_segs))

    return safe_json({
        "floor_polys": floor_polys,
        "excl_polys":  excl_polys,
        "obs_polys":   obs_polys,
        "wall_segs":   wall_segs,
        "floor_bbox":  bb,
    })
