"""Specialist registry: everything the router/gateways *could* activate, with availability.

Used for the "available vs activated" numbers shown per lesson (conditional computation made
visible) and by ``gatemoe doctor``.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .config import Config


def _file(p: Path) -> dict:
    return {"path": str(p), "available": p.exists(),
            "size_mb": round(p.stat().st_size / 2**20) if p.exists() else None}


def specialists(cfg: Config, pipeline=None) -> list[dict]:
    out: list[dict] = []
    out.append({"id": "router:clef-flash", "gateway": "router", **_file(cfg.model_path("router"))})
    out.append({"id": "generator:" + cfg["models.generator.file"], "gateway": "text",
                **_file(cfg.model_path("generator"))})
    if pipeline is not None:
        try:
            for z in pipeline.kb.catalog():
                sims = z.get("kind") == "simulations"
                out.append({"id": ("simulations:" if sims else "knowledge:") + z["name"],
                            "gateway": "knowledge", "available": "error" not in z,
                            "size_mb": z.get("size_mb"), "lang": z.get("lang")})
        except Exception as exc:  # catalogue problems must not break the registry
            out.append({"id": "knowledge:error", "gateway": "knowledge", "available": False, "error": str(exc)})
        try:
            for name, info in pipeline.speech.available().items():
                out.append({"id": "tts:" + name, "gateway": "speech", **info})
        except Exception as exc:
            out.append({"id": "tts:error", "gateway": "speech", "available": False, "error": str(exc)})
        try:
            engines = pipeline.video.available_engines()
        except Exception:
            engines = []
        for eng in cfg.get("video.enabled_engines", []):
            out.append({"id": "video:" + eng, "gateway": "video", "available": eng in engines})
        try:
            out.append({"id": "handout:typst", "gateway": "handout", "available": pipeline.handout.available()})
            out.append({"id": "concept_map:graphviz", "gateway": "text",
                        "available": pipeline.concept_map.available()})
        except Exception:
            pass
    ffmpeg = cfg.get("paths.ffmpeg", "ffmpeg")
    out.append({"id": "assembler:ffmpeg", "gateway": "video",
                "available": bool(shutil.which(ffmpeg) or Path(ffmpeg).exists())})
    return out
