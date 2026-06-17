"""FastAPI router for the joint-architecture endpoint."""

from fastapi import APIRouter

from ..geometry import rotate
from ..routing import build_outlines
from . import routing as joint_routing
from .models import RouteJointRequest, RouteJointResponse

router = APIRouter()


@router.post("/route-joint", response_model=RouteJointResponse)
async def route_joint(req: RouteJointRequest) -> RouteJointResponse:
    # Tilted building: rotate everything by -tilt so the building grid is
    # axis-aligned, route there, then rotate all geometry back by +tilt
    # (same wrapper as /route in app.main).
    tilt = req.rotation % 360.0
    points = rotate(req.points, -tilt) if tilt else req.points
    rooms = [rotate(ring, -tilt) for ring in req.rooms] if tilt else req.rooms
    corridor = rotate(req.corridor, -tilt) if tilt else req.corridor
    risers = rotate(req.risers, -tilt) if tilt else req.risers

    plan = joint_routing.route_joint(
        points, rooms, corridor, risers,
        hazard=req.hazard, header_offset=req.header_offset,
        auto_tilt=req.auto_tilt,
    )
    outlines = build_outlines(plan.segments, req.branch_width, req.main_width)

    if tilt:
        for seg in plan.segments:
            seg.start, seg.end = rotate([seg.start, seg.end], tilt)
        outlines = [(shaft, rotate(pts, tilt)) for shaft, pts in outlines]
        plan.risers = rotate(plan.risers, tilt)
        for group in plan.groups:
            group.riser = rotate([group.riser], tilt)[0]

    return RouteJointResponse(
        segments=[s.__dict__ for s in plan.segments],
        outlines=[{"shaft": shaft, "points": pts} for shaft, pts in outlines],
        risers=plan.risers,
        groups=[g.__dict__ for g in plan.groups],
        rooms=[r.__dict__ for r in plan.rooms],
        total_length=plan.total_length,
        skipped_heads=plan.skipped_heads,
        skipped_rooms=plan.skipped_rooms,
    )
