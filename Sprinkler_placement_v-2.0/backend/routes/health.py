"""Liveness, version, and scenario metadata endpoints."""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from placement import SCENARIOS

from ._shared import safe_json

router = APIRouter()


@router.get("/", response_class=PlainTextResponse)
async def root_info():
    return "Sprinkler backend is running. Use API endpoints or ZWCAD LISP plugin."


@router.get("/api/health")
async def health():
    """Liveness probe — also exposes the list of scenario IDs/names."""
    import ezdxf
    return {
        "status":    "ok",
        "version":   "1.0.0",
        "ezdxf":     ezdxf.__version__,
        "scenarios": [{"id": s["id"], "name": s["name"]} for s in SCENARIOS],
    }


@router.get("/api/scenarios")
async def list_scenarios():
    """Full scenario definitions (id, name, spacing rules, coverage_radius)."""
    return safe_json(SCENARIOS)
