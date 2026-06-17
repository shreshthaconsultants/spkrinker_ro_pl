"""DXF block insertion endpoint."""

import io
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from models import CreateBlocksRequest

router = APIRouter()


@router.post("/api/blocks/create-from-points")
async def create_blocks_from_points(req: CreateBlocksRequest):
    """Create a DXF with block inserts at the given points; return DXF bytes.

    If the configured block library / block name aren't found, fall back to circles.
    """
    if not req.room_points:
        raise HTTPException(400, "room_points required")

    try:
        import ezdxf
        from ezdxf.addons.importer import Importer

        block_lib = None
        if Path(req.block_lib_path).exists():
            block_lib = ezdxf.readfile(req.block_lib_path)

        doc = ezdxf.new("R2000")
        msp = doc.modelspace()

        if block_lib and req.block_name in block_lib.blocks:
            imp = Importer(block_lib, doc)
            imp.import_block(req.block_name, rename=False)
            imp.finalize()

            for pt in req.room_points:
                x, y = float(pt[0]), float(pt[1])
                msp.add_blockref(
                    req.block_name,
                    insert=(x, y, 0),
                    dxfattribs={"layer": req.insert_layer},
                )
        else:
            for pt in req.room_points:
                x, y = float(pt[0]), float(pt[1])
                msp.add_circle((x, y, 0), radius=80.0, dxfattribs={"layer": req.insert_layer})

        with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            doc.saveas(tmp_path)
            with open(tmp_path, "rb") as f:
                dxf_bytes = f.read()
            return StreamingResponse(
                io.BytesIO(dxf_bytes),
                media_type = "application/vnd.opendesign",
                headers    = {"Content-Disposition": "attachment; filename=sprinklers_blocks.dxf"},
            )
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    except Exception as e:
        print(f"[BLOCK ERROR] {e}")
        raise HTTPException(500, f"Block generation failed: {e}")
