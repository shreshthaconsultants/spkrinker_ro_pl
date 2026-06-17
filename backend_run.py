#!/usr/bin/env python3
"""
backend_run.py - one launcher for all three Sprinkler backends.

Each FastAPI app starts as its OWN uvicorn subprocess. They have to run in
separate processes because all three packages contain identically named
modules (main.py, geometry.py, placement.py, models.py, ...) that would
collide if imported into a single interpreter.

    key      folder                              uvicorn target     port   health
    -------  ----------------------------------  -----------------  -----  -----------
    v1       Sprinkler_placement_v-1.0/backend   main:app           9001   /api/health
    v2       Sprinkler_placement_v-2.0/backend   main:app           9002   /api/health
    routing  R1.0/backend                        app.main:app       9000   /health

These line up with the `spriro` ZWCAD plugin commands:

    /sprinkler_p1  ->  v1       (http://127.0.0.1:9001)
    /sprinkler_p2  ->  v2       (http://127.0.0.1:9002)
    /routing       ->  routing  (http://127.0.0.1:9000)

Usage
-----
    python backend_run.py                    # start all three
    python backend_run.py --only v2,routing  # start a subset
    python backend_run.py --reload           # uvicorn autoreload (dev only)
    python backend_run.py --list             # print the table and exit
    python backend_run.py --host 0.0.0.0     # bind address (default 127.0.0.1)

Press Ctrl+C to stop everything. Every child shares this console's process
group, so Ctrl+C reaches each uvicorn directly and it shuts down gracefully;
this launcher then waits for them and force-kills any straggler.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_HOST = "127.0.0.1"


class Backend:
    """One backend definition + its launched process."""

    def __init__(self, key: str, label: str, folder: str, target: str,
                 port: int, health: str, color: str):
        self.key = key
        self.label = label
        self.dir = ROOT / folder
        self.target = target          # uvicorn "module:app" string
        self.port = port
        self.health = health          # health-check path
        self.color = color
        self.proc: subprocess.Popen | None = None

    @property
    def url(self) -> str:
        return f"http://{DEFAULT_HOST}:{self.port}"

    @property
    def health_url(self) -> str:
        return f"http://{DEFAULT_HOST}:{self.port}{self.health}"


# The three backends, in plugin order. Colors are ANSI; they degrade to plain
# text when the console can't do VT (see _enable_ansi).
BACKENDS = [
    Backend("v1",      "v1       :9001", "Sprinkler_placement_v-1.0/backend", "main:app",     9001, "/api/health", "36"),  # cyan
    Backend("v2",      "v2       :9002", "Sprinkler_placement_v-2.0/backend", "main:app",     9002, "/api/health", "33"),  # yellow
    Backend("routing", "routing  :9000", "R1.0/backend",                      "app.main:app", 9000, "/health",     "32"),  # green
]
BY_KEY = {b.key: b for b in BACKENDS}

_USE_COLOR = False
_GOT_SIGTERM = False


def _enable_ansi() -> bool:
    """Turn on ANSI/VT processing on Windows 10+ consoles. Returns True on success."""
    if os.name != "nt":
        return sys.stdout.isatty()
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        # STD_OUTPUT_HANDLE = -11 ; ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x4
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


def _paint(text: str, color: str) -> str:
    if _USE_COLOR:
        return f"\033[{color}m{text}\033[0m"
    return text


def _tag(b: Backend) -> str:
    return _paint(f"[{b.key:>7}]", b.color)


def _log(b: Backend, line: str) -> None:
    # One print per line; CPython's print is atomic enough for interleaved logs.
    print(f"{_tag(b)} {line}", flush=True)


def _info(msg: str) -> None:
    print(_paint("[ run ]", "1") + f" {msg}", flush=True)


# --------------------------------------------------------------------------
# Launch / stream / shutdown
# --------------------------------------------------------------------------

def _preflight(selected: list[Backend], reload: bool) -> bool:
    """Validate everything we can before spawning a single process."""
    ok = True

    # uvicorn must be importable in THIS interpreter (children use sys.executable).
    import importlib.util

    if importlib.util.find_spec("uvicorn") is None:
        _info(f"uvicorn is not installed for {sys.executable!r}.")
        _info("  Install the backend deps, e.g.:")
        for b in selected:
            _info(f"    python -m pip install -r \"{b.dir / 'requirements.txt'}\"")
        ok = False

    for b in selected:
        if not b.dir.is_dir():
            _info(f"{b.key}: backend folder not found: {b.dir}")
            ok = False
            continue
        # The first segment of "pkg.mod:app" / "mod:app" is the import root.
        module_root = b.target.split(":", 1)[0].split(".", 1)[0]
        if not (b.dir / module_root).exists() and not (b.dir / f"{module_root}.py").exists():
            _info(f"{b.key}: cannot find '{module_root}' under {b.dir}")
            ok = False

    if reload:
        _info("--reload is on: uvicorn will spawn a watcher child; expect extra "
              "processes and slightly messier shutdown.")
    return ok


def _spawn(b: Backend, host: str, reload: bool) -> None:
    cmd = [sys.executable, "-m", "uvicorn", b.target,
           "--host", host, "--port", str(b.port)]
    if reload:
        cmd.append("--reload")

    b.proc = subprocess.Popen(
        cmd,
        cwd=str(b.dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,                 # line-buffered
        encoding="utf-8",
        errors="replace",
        # NOTE: deliberately NO CREATE_NEW_PROCESS_GROUP. Sharing the console
        # group lets Ctrl+C reach each uvicorn so it shuts down gracefully.
    )

    t = threading.Thread(target=_pump, args=(b,), daemon=True)
    t.start()


def _pump(b: Backend) -> None:
    """Forward a child's stdout to our tagged console output."""
    assert b.proc and b.proc.stdout
    for line in b.proc.stdout:
        _log(b, line.rstrip("\n"))
    code = b.proc.wait()
    _log(b, _paint(f"exited (code {code})", "31"))  # red


def _probe_ready(selected: list[Backend], host: str) -> None:
    """Poll each health endpoint until it answers, then announce the URL."""
    deadline = time.time() + 40.0
    pending = {b.key: b for b in selected}
    while pending and time.time() < deadline:
        for key in list(pending):
            b = pending[key]
            if b.proc and b.proc.poll() is not None:
                pending.pop(key)        # process died; _pump already reported it
                continue
            try:
                url = f"http://{host}:{b.port}{b.health}"
                with urllib.request.urlopen(url, timeout=1.5) as r:
                    if r.status == 200:
                        _info(_paint(f"{b.key} ready -> {url}", b.color))
                        pending.pop(key)
            except (urllib.error.URLError, OSError, ValueError):
                pass                    # not up yet
        time.sleep(0.5)
    for b in pending.values():
        if b.proc and b.proc.poll() is None:
            _info(f"{b.key}: still no health response on {b.health} "
                  f"(it may just be slow to import; watch the log above).")


def _shutdown(selected: list[Backend], initiated_by_us: bool) -> None:
    _info("stopping backends ...")
    alive = [b for b in selected if b.proc and b.proc.poll() is None]
    # On an interactive Ctrl+C the children share our console group and already
    # got SIGINT, so we just wait. On SIGTERM (docker stop / systemd) only PID 1
    # was signalled, so forward a terminate() to each child ourselves.
    if initiated_by_us:
        for b in alive:
            b.proc.terminate()
    deadline = time.time() + 10.0
    for b in alive:
        try:
            b.proc.wait(timeout=max(0.5, deadline - time.time()))
        except subprocess.TimeoutExpired:
            _info(f"{b.key}: not responding, killing.")
            b.proc.kill()
    _info("all backends stopped.")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _print_table(selected: list[Backend], host: str) -> None:
    cmd_for = {"v1": "/sprinkler_p1", "v2": "/sprinkler_p2", "routing": "/routing"}
    print()
    print("  Sprinkler backends")
    print("  " + "-" * 68)
    print(f"  {'key':<8} {'plugin cmd':<15} {'url':<26} health")
    print("  " + "-" * 68)
    for b in selected:
        print(f"  {b.key:<8} {cmd_for.get(b.key, '-'):<15} "
              f"{'http://' + host + ':' + str(b.port):<26} {b.health}")
    print("  " + "-" * 68)
    print()


def _select(only: str | None) -> list[Backend]:
    if not only:
        return list(BACKENDS)
    keys = [k.strip() for k in only.split(",") if k.strip()]
    chosen: list[Backend] = []
    for k in keys:
        if k not in BY_KEY:
            _info(f"unknown backend {k!r}; valid keys: {', '.join(BY_KEY)}")
            sys.exit(2)
        chosen.append(BY_KEY[k])
    return chosen


def main() -> int:
    global _USE_COLOR

    # docker stop / systemd send SIGTERM to PID 1; turn it into the same graceful
    # shutdown path as Ctrl+C (which raises KeyboardInterrupt in the main thread).
    def _on_sigterm(_signum, _frame):
        global _GOT_SIGTERM
        _GOT_SIGTERM = True
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, _on_sigterm)
    except (ValueError, OSError):
        pass  # not the main thread / unsupported platform

    p = argparse.ArgumentParser(
        description="Run the v1, v2 and routing (R1.0) Sprinkler backends together.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--only", metavar="KEYS",
                   help="comma-separated subset of: v1,v2,routing")
    p.add_argument("--host", default=DEFAULT_HOST,
                   help=f"bind address (default {DEFAULT_HOST})")
    p.add_argument("--reload", action="store_true",
                   help="enable uvicorn autoreload (development)")
    p.add_argument("--no-color", action="store_true", help="disable colored output")
    p.add_argument("--list", action="store_true",
                   help="print the backend table and exit")
    args = p.parse_args()

    _USE_COLOR = (not args.no_color) and _enable_ansi()
    selected = _select(args.only)

    _print_table(selected, args.host)
    if args.list:
        return 0

    if not _preflight(selected, args.reload):
        return 1

    for b in selected:
        _info(f"starting {b.key}: uvicorn {b.target} --port {b.port}  (cwd {b.dir})")
        _spawn(b, args.host, args.reload)

    # Announce readiness on a side thread so logs keep flowing.
    threading.Thread(target=_probe_ready, args=(selected, args.host),
                     daemon=True).start()

    _info("backends launching - press Ctrl+C to stop them all.")
    try:
        # Idle until interrupted, or until every child has exited on its own.
        while any(b.proc and b.proc.poll() is None for b in selected):
            time.sleep(0.4)
        _info("every backend exited on its own.")
    except KeyboardInterrupt:
        print()
        _shutdown(selected, initiated_by_us=_GOT_SIGTERM)
        return 0

    # Reached only if all children died without a Ctrl+C - surface a non-zero
    # code so callers/CI notice the failure.
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
