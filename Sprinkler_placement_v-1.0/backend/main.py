"""Sprinkler Auto-Placement API v1.0 — FastAPI entry point.

Run:
    uvicorn main:app --reload --port 9001
Open:
    http://localhost:9001/docs

This module wires the FastAPI app and global exception handler, then includes
each route module from routes/. Business logic lives in:
    geometry.py, dxf_loader.py, placement.py, area_stats.py,
    lsp_writer.py, genetic_placement.py
Pydantic request models live in models.py.
"""

import ctypes
import gc
import sys
import traceback

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from routes import health, dxf, placement, ga, blocks, zwcad


# On Linux, glibc's allocator caches freed memory in per-thread arenas and
# only returns it to the OS lazily. After a big DXF parse + polygon build,
# RSS stays elevated even though Python has dropped the objects.
# malloc_trim(0) forces glibc to release what it can. macOS/Windows don't
# expose this call — we skip the trim there.
try:
    _libc = ctypes.CDLL("libc.so.6") if sys.platform.startswith("linux") else None
except OSError:
    _libc = None


app = FastAPI(
    title       = "Sprinkler Auto-Placement API v1.0",
    description = "NFPA-13 sprinkler placement with gap detection & coverage stats",
    version     = "1.0.0",
)


class ReleaseMemoryMiddleware(BaseHTTPMiddleware):
    """gc.collect() + malloc_trim() after every response so RSS doesn't
    climb monotonically on small-memory hosts (e.g. 2 GB droplet)."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        gc.collect()
        if _libc is not None:
            _libc.malloc_trim(0)
        return response


app.add_middleware(ReleaseMemoryMiddleware)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all: log full traceback to console, return short JSON to client."""
    tb = traceback.format_exc()
    print(f"[ERROR] {exc}\n{tb}")
    return JSONResponse(
        status_code = 500,
        content     = {
            "detail": f"{type(exc).__name__}: {str(exc)}",
            "hint":   "Check server console for full traceback",
        },
    )


# Order doesn't matter for FastAPI dispatch — kept logical:
app.include_router(health.router)      # /, /api/health, /api/scenarios
app.include_router(dxf.router)         # /api/layers, /api/geometry
app.include_router(placement.router)   # /api/preview, /api/scenarios/generate, /api/scenarios/{id}/download, /api/generate
app.include_router(ga.router)          # /api/ga/*
app.include_router(blocks.router)      # /api/blocks/create-from-points
app.include_router(zwcad.router)       # /api/zwcad/scenarios
