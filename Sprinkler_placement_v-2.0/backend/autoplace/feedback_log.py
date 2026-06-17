"""
feedback_log.py — Step 10: the feedback flywheel.

Every placement run is logged to disk (input + output + report). When a
human later edits the result, record the diff. Two payoffs:

  * intervention rate — runs where a human moved/added/removed a head /
    total runs. The metric that defines "universal": drive it to 0.
  * regression seeds — each human-edited room becomes a test polygon; the
    edit is the expected correction the engine should learn to make.

Deliberately simple: append-only JSONL under logs/. No DB. Safe to call
from the request path (best-effort; never raises into the caller).
"""

import json
import os
from typing import List, Optional

_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
_RUNS = os.path.join(_LOG_DIR, "runs.jsonl")
_EDITS = os.path.join(_LOG_DIR, "edits.jsonl")


def _ensure_dir():
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
    except OSError:
        pass


def log_run(run_id: str, building_result, meta: Optional[dict] = None) -> None:
    """Append one placement run. Best-effort — swallows all I/O errors so
    logging never breaks a placement request."""
    try:
        _ensure_dir()
        rec = {
            "run_id": run_id,
            "meta":   meta or {},
            "result": building_result.to_dict(),
        }
        with open(_RUNS, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def log_human_edit(run_id: str, room_index: int,
                   added: int = 0, removed: int = 0, moved: int = 0,
                   note: str = "") -> None:
    """Record that a human edited a placed room — the intervention signal.
    `added/removed/moved` are head-count deltas the human made."""
    try:
        _ensure_dir()
        rec = {
            "run_id": run_id, "room_index": room_index,
            "added": added, "removed": removed, "moved": moved, "note": note,
        }
        with open(_EDITS, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def intervention_rate() -> dict:
    """Compute the headline metric from the logs: fraction of runs that
    received any human edit. Returns counts + rate (0.0 = universal)."""
    runs = _count_lines(_RUNS)
    edited_runs = set()
    try:
        with open(_EDITS, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    edited_runs.add(json.loads(line).get("run_id"))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    edited = len(edited_runs)
    rate = (edited / runs) if runs else 0.0
    return {"runs": runs, "edited_runs": edited,
            "intervention_rate": round(rate, 4)}


def _count_lines(path: str) -> int:
    try:
        with open(path, encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0
