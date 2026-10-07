"""``gatemoe doctor``: check that everything a Pi-only offline lesson needs is installed."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .config import Config
from .runtime import sysinfo

OK, WARN, FAIL = "ok  ", "warn", "FAIL"


def _line(status: str, what: str, detail: str = "") -> tuple[str, str, str]:
    print(f"[{status}] {what}{(' - ' + detail) if detail else ''}")
    return status, what, detail


def run_doctor(cfg: Config) -> int:
    results = []
    snap = sysinfo.snapshot()
    total_gb = snap["mem_total_mb"] / 1024
    results.append(_line(OK if total_gb >= 7 else WARN, "RAM", f"{total_gb:.1f} GB total, "
                         f"{snap['mem_available_mb'] / 1024:.1f} GB available"))
    if snap["swap_used_mb"] > 512:
        results.append(_line(WARN, "swap in use", f"{snap['swap_used_mb']} MB - prefer zram, never swap to the USB HDD"))
    if snap["throttled"] not in (None, "0x0"):
        results.append(_line(WARN, "Pi throttling flags", snap["throttled"] + " (check power supply / cooling)"))

    exe = cfg.path("paths.llama_server")
    results.append(_line(OK if exe.exists() else FAIL, "llama-server", str(exe)))
    for role in ("router", "generator"):
        p = cfg.model_path(role)
        size = f"{p.stat().st_size / 2**30:.2f} GB" if p.exists() else "missing"
        results.append(_line(OK if p.exists() else FAIL, f"{role} model", f"{p} ({size})"))

    dot = shutil.which(cfg.get("paths.dot", "dot"))
    results.append(_line(OK if dot else WARN, "graphviz (concept maps)", dot or "not found - apt install graphviz"))
    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(cfg[f"paths.{tool}"]) or (cfg[f"paths.{tool}"] if Path(cfg[f"paths.{tool}"]).exists() else None)
        results.append(_line(OK if path else FAIL, tool, path or "not found"))

    try:
        from .gateways.speech import SpeechGateway
        sg = SpeechGateway(cfg)
        voiced = {lang: sg.engine_for(lang) for lang in cfg.languages}
        engines = sorted({e for e in voiced.values() if e})
        missing = sorted(lang for lang, e in voiced.items() if not e)
        results.append(_line(OK if engines else WARN, "voices",
                             f"{len(voiced) - len(missing)}/{len(voiced)} languages; engines: {', '.join(engines) or 'none'}"))
        if missing:
            results.append(_line(WARN, "no offline voice for", ", ".join(missing) + " (text + captions only)"))
    except Exception as exc:
        results.append(_line(WARN, "speech gateway", str(exc)))

    try:
        from .gateways.video import VideoGateway
        engines = VideoGateway(cfg).available_engines()
        results.append(_line(OK if engines else WARN, "video engines", ", ".join(engines) or "none"))
    except Exception as exc:
        results.append(_line(WARN, "video gateway", str(exc)))

    try:
        from .knowledge import KnowledgeBase
        zims = KnowledgeBase(cfg).catalog()
        text = [z for z in zims if z.get("kind") == "text"]
        sims = [z for z in zims if z.get("kind") == "simulations"]
        results.append(_line(OK if text else WARN, "offline knowledge (ZIM)",
                             f"{len(text)} archives in {cfg['paths.zim_dir']}"))
        results.append(_line(OK if sims else WARN, "simulations (PhET ZIM)",
                             ", ".join(z["name"] for z in sims) or "none - add phet_<lang>_all ZIMs"))
        broken = [z["file"] for z in zims if "error" in z]
        if broken:
            results.append(_line(WARN, "unreadable ZIM files", ", ".join(broken)))
    except Exception as exc:
        results.append(_line(WARN, "knowledge", str(exc)))

    if shutil.which("fc-list"):
        fonts = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True).stdout
        missing = sorted({v["font"] for v in cfg.languages.values() if v["font"] not in fonts})
        results.append(_line(OK if not missing else WARN, "fonts", "missing: " + ", ".join(missing) if missing else "all language fonts present"))

    data = cfg.path("paths.data_dir")
    if data.exists():
        free = shutil.disk_usage(data).free / 2**30
        results.append(_line(OK if free > 20 else WARN, "data disk", f"{data} ({free:.0f} GB free)"))
    else:
        results.append(_line(FAIL, "data disk", f"{data} does not exist (set paths.data_dir)"))
    return 1 if any(r[0] == FAIL for r in results) else 0
