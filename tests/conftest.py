import os
import socket
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gatemoe.config import load_config  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def fake_server_exe(tmp_path: Path) -> Path:
    """A llama-server stand-in launched exactly like the real binary."""
    exe = tmp_path / "llama-server"
    exe.write_text(f"#!/bin/sh\nexec {sys.executable} {ROOT / 'tests' / 'fake_llama_server.py'} \"$@\"\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


@pytest.fixture
def cfg(tmp_path: Path, fake_server_exe: Path):
    data = tmp_path / "data"
    (data / "models").mkdir(parents=True)
    (data / "zim").mkdir()
    for name in ("router.gguf", "gen.gguf"):
        (data / "models" / name).write_bytes(b"GGUF" + b"\0" * 64)
    os.environ.pop("GATEMOE_CONFIG", None)
    os.environ.pop("GATEMOE_DATA_DIR", None)
    c = load_config(overrides={
        "paths": {"data_dir": str(data), "llama_server": str(fake_server_exe)},
        "llama": {"ready_timeout_s": 20, "stop_timeout_s": 5},
        "models": {"router": {"file": "router.gguf", "port": free_port()},
                   "generator": {"file": "gen.gguf", "port": free_port()}},
        "server": {"sys_interval_s": 0.5},
    })
    c.ensure_dirs()
    return c
