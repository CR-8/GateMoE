"""Lightweight system probes (no psutil): memory, process RSS, CPU temperature, Pi throttling."""
from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path


def meminfo_mb() -> dict[str, float]:
    out: dict[str, float] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, rest = line.split(":", 1)
            out[key] = int(rest.strip().split()[0]) / 1024.0
    except OSError:
        pass
    return out


def proc_status_mb(pid: int) -> dict[str, float]:
    """VmRSS / VmHWM (peak RSS) of a process in MB."""
    out: dict[str, float] = {}
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith(("VmRSS:", "VmHWM:")):
                key, rest = line.split(":", 1)
                out[key] = int(rest.strip().split()[0]) / 1024.0
    except OSError:
        pass
    return out


def cpu_temp_c() -> float | None:
    for zone in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
        try:
            return int((zone / "temp").read_text().strip()) / 1000.0
        except (OSError, ValueError):
            continue
    return None


def throttled() -> str | None:
    """Raspberry Pi firmware throttle flags (e.g. '0x0'); None when not on a Pi."""
    if shutil.which("vcgencmd"):
        try:
            res = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=3)
            if res.returncode == 0 and "=" in res.stdout:
                return res.stdout.strip().split("=", 1)[1]
        except (OSError, subprocess.SubprocessError):
            return None
    return None


def snapshot() -> dict:
    mem = meminfo_mb()
    return {
        "mem_total_mb": round(mem.get("MemTotal", 0)),
        "mem_available_mb": round(mem.get("MemAvailable", 0)),
        "swap_used_mb": round(mem.get("SwapTotal", 0) - mem.get("SwapFree", 0)),
        "temp_c": cpu_temp_c(),
        "throttled": throttled(),
    }


class SysSampler:
    """Background thread that emits a 'sys' event every ``interval`` seconds during a job."""

    def __init__(self, events, interval: float = 5.0, rss_pid_fn=None):
        self.events = events
        self.interval = interval
        self.rss_pid_fn = rss_pid_fn
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "SysSampler":
        self._thread = threading.Thread(target=self._run, name="sys-sampler", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval + 1)

    def _run(self) -> None:
        while not self._stop.is_set():
            snap = snapshot()
            pid = self.rss_pid_fn() if self.rss_pid_fn else None
            if pid:
                snap["model_rss_mb"] = round(proc_status_mb(pid).get("VmRSS", 0))
            snap["stage"] = self.events.current_stage
            self.events.emit("sys", **snap)
            self._stop.wait(self.interval)
