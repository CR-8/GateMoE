"""One llama.cpp ``llama-server`` subprocess per model (router or generator)."""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable

from .http import get_json
from .sysinfo import proc_status_mb


class ServerError(RuntimeError):
    pass


class LlamaServer:
    def __init__(self, name: str, argv: list[str], host: str, port: int, log_path: Path,
                 ready_timeout_s: float = 600, stop_timeout_s: float = 20):
        self.name = name
        self.argv = argv
        self.base_url = f"http://{host}:{port}"
        self.log_path = log_path
        self.ready_timeout_s = ready_timeout_s
        self.stop_timeout_s = stop_timeout_s
        self.proc: subprocess.Popen | None = None
        self.load_seconds: float | None = None

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc and self.proc.poll() is None else None

    def running(self) -> bool:
        return self.pid is not None

    def _log_tail(self, n: int = 30) -> str:
        try:
            lines = self.log_path.read_text(errors="replace").splitlines()
            return "\n".join(lines[-n:])
        except OSError:
            return ""

    def start(self, cancel_check: Callable[[], None] | None = None) -> float:
        """Spawn the server and block until /health is OK. Returns load seconds."""
        if self.running():
            return 0.0
        exe = Path(self.argv[0])
        if not exe.exists():
            raise ServerError(f"llama-server not found at {exe} (set paths.llama_server)")
        model = Path(self.argv[self.argv.index("-m") + 1])
        if not model.exists():
            raise ServerError(f"model file not found: {model}")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log = self.log_path.open("ab")
        env = dict(os.environ)
        for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
            env.pop(key, None)  # fully offline; the server never needs the network
        t0 = time.perf_counter()
        self.proc = subprocess.Popen(self.argv, stdout=log, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, env=env, start_new_session=True)
        log.close()
        deadline = t0 + self.ready_timeout_s
        while True:
            if self.proc.poll() is not None:
                raise ServerError(f"{self.name} server exited with code {self.proc.returncode}:\n{self._log_tail()}")
            try:
                status, body = get_json(self.base_url + "/health", timeout=2)
                if status == 200:
                    break
            except OSError:
                pass
            if time.perf_counter() > deadline:
                self.stop()
                raise ServerError(f"{self.name} server not ready after {self.ready_timeout_s}s:\n{self._log_tail()}")
            if cancel_check:
                try:
                    cancel_check()
                except BaseException:
                    self.stop()
                    raise
            time.sleep(0.5)
        self.load_seconds = time.perf_counter() - t0
        return self.load_seconds

    def stop(self) -> dict:
        """Terminate the server; returns peak/last RSS (MB) read just before exit."""
        stats: dict = {}
        if not self.proc:
            return stats
        if self.proc.poll() is None:
            st = proc_status_mb(self.proc.pid)
            stats = {"peak_rss_mb": round(st.get("VmHWM", 0)), "rss_mb": round(st.get("VmRSS", 0))}
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.proc.wait(timeout=self.stop_timeout_s)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.proc.wait(timeout=10)
        self.proc = None
        return stats
