"""Run a renderer subprocess with a CPU-time cap, a wall-clock timeout and no proxy/network env.

* ``prlimit --cpu`` (util-linux) caps CPU seconds per process without ``preexec_fn``
  (which is unsafe in a multi-threaded server).
* The child gets its own process group so a timeout kills Chrome/Node/Manim children too.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess


def clean_env(extra: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in ("http_proxy", "https_proxy", "all_proxy", "no_proxy")}
    env.update(extra or {})
    return env


def run_limited(cmd: list[str], *, cwd: str | None = None, env: dict | None = None,
                timeout: float = 600, cpu_seconds: int | None = None) -> subprocess.CompletedProcess:
    if cpu_seconds and shutil.which("prlimit"):
        cmd = ["prlimit", f"--cpu={int(cpu_seconds)}", "--", *cmd]
    proc = subprocess.Popen(cmd, cwd=cwd, env=env if env is not None else clean_env(),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            stdin=subprocess.DEVNULL, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = proc.communicate()
        raise subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err)
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
