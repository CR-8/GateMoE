"""Keeps at most ONE large model resident (8 GB Pi): loading one unloads the other.

Every load/unload is logged with its wall time and the process's peak RSS — this is the
model-level "expert swap" cost that the research compares against the compute that
routing saves.
"""
from __future__ import annotations

import atexit
import os
import threading
import time
from pathlib import Path

from ..config import Config
from .events import EventLog
from .llama_server import LlamaServer, ServerError

try:
    import fcntl
except ImportError:          # not on Linux: the cross-process guard is skipped
    fcntl = None


def cpu_has_amx(cpuinfo: str = "/proc/cpuinfo") -> bool:
    try:
        with open(cpuinfo, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("flags"):
                    return "amx_tile" in line.split()
    except OSError:
        pass
    return False


class ModelManager:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.current: LlamaServer | None = None
        self.lock = threading.RLock()
        self.log_dir = cfg.path("paths.cache_dir") / "logs"
        self.history: list[dict] = []   # recent loads/unloads, for /api/system
        self._host_fd: int | None = None
        atexit.register(self._atexit)    # Ctrl+C / normal exit must not orphan a 3-6 GB server

    # -- one model per MACHINE: a second GateMoE process (CLI next to `serve`) waits ----------
    def _take_host_lock(self, events: EventLog | None) -> None:
        if self._host_fd is not None or fcntl is None:
            return
        path = self.log_dir.parent / "models.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        deadline = time.monotonic() + float(self.cfg.get("llama.lock_timeout_s", 900))
        announced = False
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if events and not announced:
                        events.emit("model_wait", reason="another GateMoE process has a model loaded")
                        announced = True
                    if time.monotonic() > deadline:
                        raise ServerError("another GateMoE process keeps a model loaded (see llama.lock_timeout_s)")
                    if events:
                        events.check_cancel()
                    time.sleep(1)
        except BaseException:
            os.close(fd)
            raise
        self._host_fd = fd

    def _release_host_lock(self) -> None:
        fd, self._host_fd = self._host_fd, None
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _atexit(self) -> None:
        try:
            self.unload()
        except Exception:
            pass

    def argv(self, role: str) -> list[str]:
        m = self.cfg[f"models.{role}"]
        ctx = str(m.get("ctx", 4096))
        argv = [str(self.cfg.path("paths.llama_server")),
                "-m", str(self.cfg.model_path(role)),
                "--host", self.cfg["llama.host"], "--port", str(m["port"]),
                "-t", str(self.cfg["hardware.threads"]), "-c", ctx]
        if m.get("kind") == "decision":
            # Clef keeps no KV cache: the whole prompt must fit in a single micro-batch.
            argv += ["-b", ctx, "-ub", ctx]
        argv += [str(a) for a in m.get("args", [])]
        argv += [str(a) for a in self.cfg.get("llama.extra_args", []) or []]
        if self.cfg.get("llama.auto_no_repack", True) and cpu_has_amx() and not {"-nr", "--no-repack"} & set(argv):
            # llama.cpp aborts while repacking weights for AMX (Sapphire Rapids and newer: common
            # on cloud VMs); plain AVX-512/AVX2 kernels are used instead. Never needed on the Pi.
            argv.append("-nr")
        return argv

    def _server(self, role: str) -> LlamaServer:
        return LlamaServer(
            name=role, argv=self.argv(role), host=self.cfg["llama.host"],
            port=int(self.cfg[f"models.{role}.port"]), log_path=self.log_dir / f"{role}.log",
            ready_timeout_s=float(self.cfg["llama.ready_timeout_s"]),
            stop_timeout_s=float(self.cfg["llama.stop_timeout_s"]))

    def acquire(self, role: str, events: EventLog | None = None) -> LlamaServer:
        """Return a ready server for ``role``, swapping out whatever else is loaded."""
        with self.lock:
            if self.current and self.current.name == role and self.current.running():
                if events:
                    events.emit("model_hit", role=role)
                return self.current
            self._take_host_lock(events)
            self._unload_current(events)
            server = self._server(role)
            path: Path = self.cfg.model_path(role)
            if events:
                events.emit("model_loading", role=role, file=path.name)
            try:
                seconds = server.start(cancel_check=events.check_cancel if events else None)
            except BaseException:
                self._release_host_lock()
                raise
            self.current = server
            rec = {"role": role, "file": path.name, "size_mb": round(path.stat().st_size / 2**20),
                   "seconds": round(seconds, 3)}
            self.history.append({"event": "load", **rec})
            if events:
                events.emit("model_load", **rec)
            return server

    def unload(self, events: EventLog | None = None) -> None:
        with self.lock:
            try:
                self._unload_current(events)
            finally:
                self._release_host_lock()

    def _unload_current(self, events: EventLog | None = None) -> None:
        with self.lock:
            if not self.current:
                return
            role = self.current.name
            stats = self.current.stop()
            self.current = None
            self.history.append({"event": "unload", "role": role, **stats})
            del self.history[:-50]
            if events:
                events.emit("model_unload", role=role, **stats)

    def current_pid(self) -> int | None:
        cur = self.current
        return cur.pid if cur else None

    def status(self) -> dict:
        cur = self.current
        return {"loaded": cur.name if cur and cur.running() else None,
                "pid": self.current_pid(), "history": self.history[-10:]}
