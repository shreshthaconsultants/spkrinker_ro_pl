"""Pydantic models for the joint-architecture endpoint (/route-joint)."""

from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field

from ..models import Hazard, OutlineModel, Point, RouteGroupModel

Ring = Annotated[list[Point], Field(min_length=3)]


class RouteJointRequest(BaseModel):
    points: Annotated[list[Point], Field(min_length=1)]   # sprinkler heads
    rooms: Annotated[list[Ring], Field(min_length=1)]     # room polygons
    corridor: Ring                                        # corridor polygon
    risers: Annotated[list[Point], Field(min_length=1)]   # shaft start points
    hazard: Optional[Hazard] = None   # omitted -> spacing inferred per room
    branch_width: Annotated[float, Field(gt=0)] = 32.0    # mm
    main_width: Annotated[float, Field(gt=0)] = 65.0      # mm (header + subheader)
    # the room sub-header sits this far BESIDE the sprinkler column
    header_offset: Annotated[float, Field(gt=0)] = 300.0  # mm
    rotation: float = 0.0  # degrees CCW: manual tilt override (rarely needed)
    # measure the grid angle automatically from the sprinklers - globally
    # from the corridor heads and per room, so mixed straight/tilted
    # rooms each route in their own frame; the user never types the tilt
    auto_tilt: bool = False


class JointSegmentModel(BaseModel):
    start: Point                       # upstream (toward the shaft)
    end: Point                         # downstream (flow direction: start -> end)
    kind: Literal["riser", "header", "subheader", "branch"]
    length: float
    shaft: int                         # index into risers; colour pipes per shaft


class RoomStatusModel(BaseModel):
    index: int                         # order of the room in the request
    head_count: int                    # heads inside this room
    # tapped: shared wall found | fallback: connected by nearest points |
    # empty: no heads, not piped | outline: ring covers the corridor (a
    # building outline, ignored) | skipped: unusable polygon / no corridor reach
    status: Literal["tapped", "fallback", "empty", "outline", "skipped"]
    shaft: int                         # feeding shaft index, -1 when not connected


class RouteJointResponse(BaseModel):
    segments: list[JointSegmentModel]
    outlines: list[OutlineModel]       # merged pipe outlines, per shaft
    risers: list[Point]                # the shaft points (tree roots)
    groups: list[RouteGroupModel]      # one per shaft
    rooms: list[RoomStatusModel]       # per-room connection report
    total_length: float
    skipped_heads: int                 # heads outside every room and the corridor
    skipped_rooms: int                 # rooms that could not be connected
