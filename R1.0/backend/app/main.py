"""Sprinkler design automation backend.

Run:  py -m uvicorn app.main:app --host 127.0.0.1 --port 9000
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import placement, routing, validation
from .geometry import rotate
from .joint import api as joint_api
from .models import (
    PlaceRequest,
    PlaceResponse,
    RouteRequest,
    RouteResponse,
    ValidateRequest,
    ValidateResponse,
)
from .nfpa import coverage_radius, spacing_for

app = FastAPI(title="Sprinkler Design Backend", version="1.0.0")
app.include_router(joint_api.router)


@app.exception_handler(RequestValidationError)
async def on_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"error": "validation_error", "message": str(exc.errors()[:3])},
    )


@app.exception_handler(ValueError)
async def on_geometry_error(_: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"error": "geometry_error", "message": str(exc)},
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/place", response_model=PlaceResponse)
async def place(req: PlaceRequest) -> PlaceResponse:
    # Tilted building: place in the axis-aligned frame, rotate the heads back.
    tilt = req.rotation % 360.0
    boundary = rotate(req.boundary, -tilt) if tilt else req.boundary
    points = placement.place(boundary, req.hazard)
    if tilt:
        points = rotate(points, tilt)
    return PlaceResponse(
        points=points,
        spacing=spacing_for(req.hazard),
        coverage_radius=coverage_radius(req.hazard),
        count=len(points),
    )


@app.post("/validate", response_model=ValidateResponse)
async def validate(req: ValidateRequest) -> ValidateResponse:
    report = validation.validate(req.boundary, req.points, req.hazard)
    return ValidateResponse(
        passed=report.passed,
        rules=[r.__dict__ for r in report.rules],
        failing_heads=report.failing_heads,
    )


@app.post("/route", response_model=RouteResponse)
async def route(req: RouteRequest) -> RouteResponse:
    risers = req.risers if req.risers else ([req.riser] if req.riser else None)

    # Tilted building: rotate everything by -tilt so the building grid is
    # axis-aligned, route there, then rotate all geometry back by +tilt.
    tilt = req.rotation % 360.0
    points = rotate(req.points, -tilt) if tilt else req.points
    if tilt and risers:
        risers = rotate(risers, -tilt)
    boundary = rotate(req.boundary, -tilt) if (tilt and req.boundary) else req.boundary

    plan = routing.route(points, hazard=req.hazard, risers=risers, boundary=boundary)
    outlines = routing.build_outlines(plan.segments, req.branch_width, req.main_width)

    if tilt:
        for seg in plan.segments:
            seg.start, seg.end = rotate([seg.start, seg.end], tilt)
        outlines = [(shaft, rotate(pts, tilt)) for shaft, pts in outlines]
        plan.risers = rotate(plan.risers, tilt)
        for group in plan.groups:
            group.riser = rotate([group.riser], tilt)[0]

    return RouteResponse(
        segments=[s.__dict__ for s in plan.segments],
        outlines=[{"shaft": shaft, "points": pts} for shaft, pts in outlines],
        risers=plan.risers,
        groups=[g.__dict__ for g in plan.groups],
        total_length=plan.total_length,
        skipped_heads=plan.skipped_heads,
    )
