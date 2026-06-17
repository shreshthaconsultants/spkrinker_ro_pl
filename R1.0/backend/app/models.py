"""Pydantic request/response models — the JSON contract (see docs/json-contract.md)."""

from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field

Point = Annotated[list[float], Field(min_length=2, max_length=2)]  # [x, y] mm
Hazard = Literal["Light", "Ordinary", "Extra"]


class PlaceRequest(BaseModel):
    boundary: Annotated[list[Point], Field(min_length=3)]
    hazard: Hazard
    rotation: float = 0.0  # degrees CCW: tilt of the building grid vs the X axis


class PlaceResponse(BaseModel):
    points: list[Point]
    spacing: float
    coverage_radius: float
    count: int


class ValidateRequest(BaseModel):
    boundary: Annotated[list[Point], Field(min_length=3)]
    points: list[Point]
    hazard: Hazard


class RuleResultModel(BaseModel):
    rule: str
    passed: bool
    detail: str


class ValidateResponse(BaseModel):
    passed: bool
    rules: list[RuleResultModel]
    failing_heads: list[Point]


class RouteRequest(BaseModel):
    points: Annotated[list[Point], Field(min_length=1)]
    hazard: Optional[Hazard] = None   # omitted -> spacing inferred from the heads
    riser: Optional[Point] = None     # legacy single shaft
    risers: Optional[list[Point]] = None  # multiple shafts; wins over riser
    boundary: Optional[Annotated[list[Point], Field(min_length=3)]] = None
    # boundary: room polygon; heads outside it are ignored and the pipe runs
    # are kept inside it where possible
    branch_width: Annotated[float, Field(gt=0)] = 32.0  # mm, double-line width
    main_width: Annotated[float, Field(gt=0)] = 65.0    # mm (riser + main)
    rotation: float = 0.0  # degrees CCW: tilt of the building grid vs the X axis


class SegmentModel(BaseModel):
    start: Point                       # upstream (toward riser)
    end: Point                         # downstream (flow direction: start -> end)
    kind: Literal["riser", "main", "branch"]
    length: float
    shaft: int                         # index into risers; colour pipes per shaft


class RouteGroupModel(BaseModel):
    riser: Point                       # the shaft feeding this group
    head_count: int                    # heads assigned to it (nearest-shaft)
    length: float                      # pipe length of this group's tree


class OutlineModel(BaseModel):
    shaft: int                         # which shaft's network this ring belongs to
    points: list[Point]                # closed ring vertices (closing point omitted)


class RouteResponse(BaseModel):
    segments: list[SegmentModel]       # centrelines: lengths, kinds, flow arrows
    outlines: list[OutlineModel]       # merged pipe outlines: clean elbows/tees/crosses
    risers: list[Point]                # effective tree roots, one per shaft
    groups: list[RouteGroupModel]
    total_length: float
    skipped_heads: int                 # heads outside the boundary, ignored


class ErrorResponse(BaseModel):
    error: str
    message: str
