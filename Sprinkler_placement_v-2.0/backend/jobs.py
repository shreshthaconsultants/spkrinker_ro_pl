"""
Job management for the sprinkler design engine.

The design endpoint used to do all the work in-line and return the answer
directly. That made the C# plugin freeze for the full duration. Now the
endpoint returns a job_id immediately, a background coroutine does the
work, and the plugin polls /status until the job is done.

This module owns:
  * The JSON contract DTOs (DesignRequest, Head, DesignResponse, JobStatus)
  * An in-memory JobStore (thread-safe dict wrapper)
  * The async worker run_design_job that walks through stages and updates
    progress as it goes

Kept deliberately simple: in-memory storage, no Redis, no Celery. Good
enough for a single-process uvicorn demo.
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel


# ---------- JSON contract DTOs ----------

class DesignRequest(BaseModel):
    """What the C# plugin sends us."""
    corner1: List[float]   # [x, y] of first corner
    corner2: List[float]   # [x, y] of opposite corner
    spacing: float = 3000  # head spacing in drawing units (mm)


class Head(BaseModel):
    """One sprinkler head position."""
    x: float
    y: float
    tag: str


class DesignResponse(BaseModel):
    """What we send back to the C# plugin once the job is done."""
    heads: List[Head]
    message: str
    head_count: int


JobStatusLiteral = Literal["queued", "running", "done", "error", "cancelled"]


class JobStatus(BaseModel):
    """Snapshot of a job's progress, returned by GET /status/{job_id}."""
    job_id: str
    status: JobStatusLiteral
    progress: float           # 0.0 .. 1.0
    stage: str                # short label, e.g. "placing"
    message: str              # human-readable line for the command bar
    warnings: List[str] = []
    done: bool = False


# ---------- Internal record (not serialized as-is) ----------

@dataclass
class JobRecord:
    job_id: str
    status: JobStatusLiteral = "queued"
    progress: float = 0.0
    stage: str = "queued"
    message: str = "Queued"
    warnings: List[str] = field(default_factory=list)
    result: Optional[DesignResponse] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None

    @property
    def done(self) -> bool:
        return self.status in ("done", "error", "cancelled")

    def to_status(self) -> JobStatus:
        return JobStatus(
            job_id=self.job_id,
            status=self.status,
            progress=self.progress,
            stage=self.stage,
            message=self.message,
            warnings=list(self.warnings),
            done=self.done,
        )


# ---------- JobStore ----------

class JobStore:
    """
    Thread-safe in-memory job dict.

    The asyncio event loop is single-threaded, but we still take a lock so
    nothing breaks if a sync endpoint or test harness reaches in. The lock
    is never held across `await`.
    """

    def __init__(self) -> None:
        self._jobs: Dict[str, JobRecord] = {}
        self._lock = threading.Lock()

    def create(self) -> str:
        job_id = str(uuid.uuid4())
        with self._lock:
            self._jobs[job_id] = JobRecord(job_id=job_id)
        return job_id

    def get(self, job_id: str) -> Optional[JobRecord]:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: str, **fields) -> None:
        with self._lock:
            rec = self._jobs.get(job_id)
            if rec is None:
                return
            for k, v in fields.items():
                setattr(rec, k, v)

    def set_result(self, job_id: str, result: DesignResponse) -> None:
        with self._lock:
            rec = self._jobs.get(job_id)
            if rec is None:
                return
            rec.result = result
            rec.status = "done"
            rec.progress = 1.0
            rec.stage = "complete"
            rec.message = "Complete"
            rec.completed_at = time.time()

    def set_error(self, job_id: str, msg: str) -> None:
        with self._lock:
            rec = self._jobs.get(job_id)
            if rec is None:
                return
            rec.status = "error"
            rec.error = msg
            rec.message = msg
            rec.completed_at = time.time()

    def purge_expired(self, ttl_seconds: float = 3600.0) -> int:
        """Drop completed jobs older than ttl_seconds. Returns count removed."""
        cutoff = time.time() - ttl_seconds
        with self._lock:
            stale = [
                jid for jid, rec in self._jobs.items()
                if rec.completed_at is not None and rec.completed_at < cutoff
            ]
            for jid in stale:
                del self._jobs[jid]
            return len(stale)


# Module-level singleton. The endpoints import this directly.
store = JobStore()


# ---------- Background worker ----------

async def run_design_job(job_id: str, req: DesignRequest) -> None:
    """
    The actual design work, broken into stages so the client sees progress.

    The asyncio.sleep calls are intentional - they pace the work so the
    polling client has something to display. In real life the placement
    stage would be doing CPU work and we'd yield via asyncio.sleep(0)
    between heads.
    """
    try:
        # ----- Stage 1: validate -----
        store.update(
            job_id,
            status="running",
            progress=0.10,
            stage="validating",
            message="Validating boundary",
        )
        await asyncio.sleep(0.2)

        # ----- Stage 2: compute the grid (positions only, no Head objects yet) -----
        store.update(
            job_id,
            progress=0.25,
            stage="calculating",
            message="Calculating head positions",
        )
        positions = _compute_grid(req)
        total = len(positions)
        await asyncio.sleep(0.2)

        # ----- Stage 3: place each head, updating progress per head -----
        heads: List[Head] = []
        if total == 0:
            store.update(
                job_id,
                progress=0.85,
                stage="placing",
                message="No heads fit in the given rectangle",
            )
        else:
            for i, (x, y) in enumerate(positions, start=1):
                heads.append(Head(x=x, y=y, tag=f"S-{i}"))
                # Map progress across the placement window 0.25 .. 0.75
                progress = 0.25 + 0.50 * (i / total)
                store.update(
                    job_id,
                    progress=progress,
                    stage="placing",
                    message=f"Placed {i} of {total} heads",
                )
                await asyncio.sleep(0.1)  # demo pacing - simulates real work

        # ----- Stage 4: finalize / "tagging" -----
        store.update(
            job_id,
            progress=0.85,
            stage="tagging",
            message="Generating tags",
        )
        await asyncio.sleep(0.2)

        # ----- Done -----
        result = DesignResponse(
            heads=heads,
            message=f"Placed {len(heads)} heads",
            head_count=len(heads),
        )
        store.set_result(job_id, result)

    except asyncio.CancelledError:
        store.update(job_id, status="cancelled", message="Job cancelled")
        raise
    except Exception as exc:  # noqa: BLE001 - surface any failure to the client
        store.set_error(job_id, f"{type(exc).__name__}: {exc}")


def _compute_grid(req: DesignRequest) -> List[tuple]:
    """Same centered-grid algorithm as the v0 sync endpoint."""
    x_min = min(req.corner1[0], req.corner2[0])
    x_max = max(req.corner1[0], req.corner2[0])
    y_min = min(req.corner1[1], req.corner2[1])
    y_max = max(req.corner1[1], req.corner2[1])

    width = x_max - x_min
    height = y_max - y_min

    cols = max(1, int(width // req.spacing))
    rows = max(1, int(height // req.spacing))

    x_offset = (width - (cols - 1) * req.spacing) / 2
    y_offset = (height - (rows - 1) * req.spacing) / 2

    out: List[tuple] = []
    for row in range(rows):
        for col in range(cols):
            x = x_min + x_offset + col * req.spacing
            y = y_min + y_offset + row * req.spacing
            out.append((x, y))
    return out
