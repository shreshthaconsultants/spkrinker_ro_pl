"""Pydantic request/response models."""

from pydantic import BaseModel, Field


class CreateBlocksRequest(BaseModel):
    """Body for POST /api/blocks/create-from-points."""
    room_points: list[list[float]] = Field(
        description="Points to place blocks at: [[x1,y1], [x2,y2], ...]"
    )
    block_lib_path: str = Field(default="blockssprinkler.dxf")
    block_name: str = Field(default="PP-CEILING PENDANT")
    insert_layer: str = Field(default="SPRINKLERS")


class ZWCADScenarioRequest(BaseModel):
    """Body for POST /api/zwcad/scenarios."""
    room_polys: list[list[list[float]]] = Field(
        default_factory=list,
        description="Closed room polylines selected in CAD, each as [[x,y], ...].",
    )
    obs_polys: list[list[list[float]]] = Field(
        default_factory=list,
        description="Closed obstacle polylines (e.g. columns, equipment) where "
                    "no sprinkler should be placed. Each as [[x,y], ...].",
    )
    scenario_ids: list[int] = Field(
        default_factory=lambda: [1, 4, 10],
        description="Allowed scenario IDs. Defaults to 1,4,10.",
    )
    obs_min_offset: float = 150.0
    enable_gap_fill: bool = Field(
        default=True,
        description="If False, skip the (expensive) gap-fill phase. The plugin "
                    "exposes a Yes/No prompt for this so users can opt out for "
                    "faster responses on large drawings.",
    )
    tilted: bool = Field(
        default=False,
        description="If True, detect each room's longest-edge angle and rotate "
                    "placement to align with it (blocks inserted at that "
                    "rotation). If False (default), use axis-aligned placement "
                    "(original behaviour) and emit zero rotation per point.",
    )
