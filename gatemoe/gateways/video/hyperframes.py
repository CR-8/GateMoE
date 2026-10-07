"""HyperFrames specialist: fills a pre-tested HTML template with JSON and renders an MP4 offline.

Text safety: templates insert text with ``textContent`` only and HyperFrames hands variables to
the page through ``JSON.parse``, so text is never parsed as HTML. We normalise (NFC), strip
control/bidi-override characters and cap lengths/counts.

Only the animated ~3.5 s go through Chrome; the narration-length hold is appended with FFmpeg
``tpad`` (≈ 9x cheaper than rendering a static hold in the browser).
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
from pathlib import Path

from ...config import Config
from .sandbox import clean_env, run_limited

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "hf_templates"
FALLBACK = HERE / "hf_fallback_render.mjs"
TEMPLATE_FILES = {"title": "title.html", "bullets": "bullets.html", "steps": "steps.html",
                  "bars": "barchart.html", "code": "code.html"}
RTL_LANGS = {"ar", "ur", "fa", "he", "ps", "sd", "ckb", "dv", "yi", "ug", "ks"}
# C0/C1 controls (except \n, \t), zero-width no-break, bidi embedding/override/isolate controls.
# ZWJ/ZWNJ (U+200C/U+200D) are KEPT: they are required for correct Indic shaping.
_BAD_CHARS = re.compile("[\u0000-\u0008\u000b-\u001f\u007f-\u009f‪-‮⁦-⁩﻿]")
_FONT_DIRS = [Path("/usr/share/fonts/truetype/noto"), Path("/usr/share/fonts/opentype/noto"),
              Path("/usr/share/fonts/truetype"), Path("/usr/share/fonts")]
BROWSER_CANDIDATES = ["/usr/bin/chromium-headless-shell", "/usr/bin/chromium", "/usr/bin/chromium-browser",
                      "/opt/pw-browsers/chromium_headless_shell-1194/chrome-linux/headless_shell"]


def clean_text(s, max_len: int = 400, keep_newlines: bool = False) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFC", str(s))
    s = _BAD_CHARS.sub("", s)
    if not keep_newlines:
        s = re.sub(r"\s+", " ", s).strip()
    return s[:max_len]


def build_payload(template: str, data: dict) -> dict:
    """Validate + sanitise a scene payload for one template (raises ValueError if unusable)."""
    lang = clean_text(data.get("lang") or "en", 16)
    out = {"lang": lang, "dir": "rtl" if lang.split("-")[0].lower() in RTL_LANGS else "ltr",
           "footer": clean_text(data.get("footer"), 120)}
    if template == "title":
        out.update(kicker=clean_text(data.get("kicker"), 60), title=clean_text(data.get("title"), 140),
                   subtitle=clean_text(data.get("subtitle"), 220))
        if not out["title"]:
            raise ValueError("title: empty title")
    elif template == "bullets":
        items = [clean_text(b, 160) for b in (data.get("bullets") or [])]
        items = [b for b in items if b][:6]
        if not items:
            raise ValueError("bullets: need 1-6 non-empty bullets")
        out.update(heading=clean_text(data.get("heading"), 120), bullets=items)
    elif template == "steps":
        steps = []
        for s in (data.get("steps") or [])[:6]:
            if isinstance(s, str):
                s = {"label": s}
            lab = clean_text(s.get("label"), 60)
            if lab:
                steps.append({"label": lab, "detail": clean_text(s.get("detail"), 110), "tag": clean_text(s.get("tag"), 6)})
        if len(steps) < 2:
            raise ValueError("steps: need 2-6 steps with a label")
        out.update(heading=clean_text(data.get("heading"), 120), steps=steps)
    elif template == "bars":
        bars = []
        for b in (data.get("bars") or [])[:8]:
            try:
                v = float(b.get("value"))
            except (TypeError, ValueError):
                continue
            if v != v or v in (float("inf"), float("-inf")):
                continue
            bars.append({"label": clean_text(b.get("label"), 40), "value": v})
        if not bars:
            raise ValueError("bars: need 1-8 bars with numeric values")
        if any(b["value"] < 0 for b in bars):   # the template draws upward bars only: never clamp data
            raise ValueError("bars: negative values cannot be drawn by this template")
        out.update(heading=clean_text(data.get("heading"), 120), caption=clean_text(data.get("caption"), 160),
                   unit=clean_text(data.get("unit"), 8), bars=bars)
    elif template == "code":
        code = clean_text(data.get("code"), 4000, keep_newlines=True).replace("\r\n", "\n")
        if not code.strip():
            raise ValueError("code: empty code")
        hls = []
        for h in (data.get("highlights") or [])[:6]:
            lines = [int(x) for x in (h.get("lines") or []) if isinstance(x, (int, float))][:2]
            if lines:
                hls.append({"lines": lines, "note": clean_text(h.get("note"), 140)})
        out.update(heading=clean_text(data.get("heading"), 120),
                   language=clean_text(data.get("language") or "text", 16).lower(), code=code, highlights=hls)
    else:
        raise ValueError(f"unknown template {template!r}")
    return out


def anim_seconds(template: str) -> float:
    return {"title": 2.4, "bullets": 3.0, "steps": 3.0, "bars": 3.0, "code": 3.0}[template]


def extend_hold(ffmpeg: str, src: Path, dst: Path, total: float, fps: int, crf: str = "20",
                size: tuple[int, int] = (1280, 720)) -> None:
    """Clone the last frame until ``total`` seconds (and match the lesson's frame size, so the
    concat demuxer can join HyperFrames and Manim beats without re-encoding)."""
    w, h = size
    scale = "" if (w, h) == (1280, 720) else (f",scale={w}:{h}:force_original_aspect_ratio=decrease,"
                                             f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1")
    cmd = [ffmpeg, "-v", "error", "-y", "-i", str(src), "-vf",
           f"tpad=stop_mode=clone:stop_duration={total:.3f},trim=duration={total:.3f},fps={fps}{scale}",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", crf, "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", "-an", str(dst)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=600)


class HyperFramesEngine:
    name = "hyperframes"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.hf_dir = cfg.path("paths.hyperframes_dir")
        self.bin = self.hf_dir / "node_modules" / ".bin" / "hyperframes"
        self.node = cfg["paths.node"]
        self.ffmpeg = cfg["paths.ffmpeg"]
        self.assets = cfg.path("paths.cache_dir") / "hf_assets"
        self.fps = int(cfg["video.fps"])
        self.timeout = float(cfg["video.render_timeout_s"])
        self.cpu_limit = int(cfg["video.sandbox_cpu_seconds"])
        self.browser = self._find_browser()

    def _find_browser(self) -> str | None:
        for cand in [self.cfg.get("paths.chrome"), *BROWSER_CANDIDATES]:
            if cand and Path(cand).exists():
                return str(cand)
        return None

    def available(self) -> bool:
        return bool(self.bin.exists() and self.browser and shutil.which(self.node))

    def ensure_assets(self) -> None:
        """Runtime asset dir: our gm.js/base.css + vendored GSAP + local Noto fonts (no CDN)."""
        fonts = self.assets / "fonts"
        fonts.mkdir(parents=True, exist_ok=True)
        for name in ("gm.js", "base.css"):
            src, dst = TEMPLATES / "assets" / name, self.assets / name
            if not dst.exists() or dst.read_bytes() != src.read_bytes():
                shutil.copyfile(src, dst)
        gsap = self.assets / "gsap.min.js"
        if not gsap.exists():
            src = self.hf_dir / "node_modules" / "gsap" / "dist" / "gsap.min.js"
            if not src.exists():
                raise RuntimeError(f"GSAP not found at {src}; run: npm install gsap@3.15.0 in {self.hf_dir}")
            shutil.copyfile(src, gsap)
        css = (TEMPLATES / "assets" / "base.css").read_text(encoding="utf-8")
        for fname in sorted(set(re.findall(r'url\("fonts/([^"]+)"\)', css))):
            if (fonts / fname).exists():
                continue
            for d in _FONT_DIRS:
                hits = list(d.rglob(fname)) if d.exists() else []
                if hits:
                    shutil.copyfile(hits[0], fonts / fname)
                    break

    def _env(self) -> dict:
        return clean_env({"HYPERFRAMES_NO_TELEMETRY": "1", "DO_NOT_TRACK": "1", "HYPERFRAMES_NO_UPDATE_CHECK": "1",
                          "HYPERFRAMES_NO_AUTO_INSTALL": "1", "HYPERFRAMES_SKIP_SKILLS": "1",
                          "HYPERFRAMES_BROWSER_PATH": self.browser or "",
                          "GATEMOE_NODE_MODULES": str(self.hf_dir / "node_modules")})

    def _job_dir(self, template: str, duration: float, workdir: Path) -> Path:
        workdir.mkdir(parents=True, exist_ok=True)
        job = Path(tempfile.mkdtemp(prefix=f"hf-{template}-", dir=workdir))
        html = (TEMPLATES / TEMPLATE_FILES[template]).read_text(encoding="utf-8")
        html, n = re.subn(r'(<div id="root"[^>]*?data-duration=")[0-9.]+(")', rf"\g<1>{duration:.3f}\g<2>", html, count=1)
        if n != 1:
            raise RuntimeError("template root has no data-duration")
        (job / "index.html").write_text(html, encoding="utf-8")
        try:
            os.symlink(self.assets, job / "assets", target_is_directory=True)
        except OSError:
            shutil.copytree(self.assets, job / "assets")
        return job

    def render(self, template: str, data: dict, out_path: Path, duration: float, workdir: Path) -> dict:
        payload = build_payload(template, data)
        self.ensure_assets()
        fps = self.fps
        # a beat lasts at least as long as its entrance animation (short narration is padded with
        # silence at assembly), otherwise the held last frame shows half-drawn bars or bullets
        duration = max(float(duration), anim_seconds(template) + 0.5)
        duration = math.ceil(max(1.0, min(duration, 600.0)) * fps - 1e-6) / fps
        out_path = Path(out_path).resolve()
        render_len = min(duration, round(anim_seconds(template) + 0.5, 3))
        anim_path = out_path.with_name(out_path.stem + ".anim.mp4") if duration - render_len > 1.0 / fps else out_path
        if anim_path == out_path:
            render_len = duration
        job = self._job_dir(template, render_len, workdir)
        vars_file = job / "vars.json"
        vars_file.write_text(json.dumps({"payload": json.dumps(payload, ensure_ascii=False)}, ensure_ascii=False),
                             encoding="utf-8")
        env = self._env()
        attempts = [
            ("hyperframes", [str(self.bin), "render", str(job), "--variables-file", str(vars_file), "--strict-variables",
                             "-o", str(anim_path), "--fps", str(fps), "--quality", "standard", "--workers", "1", "--quiet"]
             + (["--no-low-memory-mode"] if "headless" in (self.browser or "") else [])),
            ("fallback", [self.node, str(FALLBACK), "--html", str(job / "index.html"), "--vars", str(vars_file),
                          "--node-modules", str(self.hf_dir / "node_modules"), "--out", str(anim_path),
                          "--fps", str(fps), "--duration", str(render_len), "--browser", self.browser or "", "--crf", "20"]),
        ]
        errors, used, wall = [], None, 0.0
        try:
            for name, cmd in attempts:
                t0 = time.time()
                try:
                    proc = run_limited(cmd, env=env, cwd=str(job), timeout=self.timeout, cpu_seconds=self.cpu_limit)
                except subprocess.TimeoutExpired:
                    errors.append(f"{name}: timeout after {self.timeout}s")
                    continue
                wall += time.time() - t0
                if proc.returncode == 0 and anim_path.exists():
                    used = name
                    break
                errors.append(f"{name} rc={proc.returncode}: {(proc.stderr or proc.stdout)[-800:]}")
        finally:
            shutil.rmtree(job, ignore_errors=True)
        if not used:
            raise RuntimeError("hyperframes render failed: " + " | ".join(errors))
        hold_s = 0.0
        size = (int(self.cfg.get("video.width", 1280)), int(self.cfg.get("video.height", 720)))
        if anim_path != out_path or size != (1280, 720):   # templates are laid out for 1280x720
            t1 = time.time()
            src = anim_path
            if anim_path == out_path:
                src = out_path.with_name(out_path.stem + ".anim.mp4")
                out_path.replace(src)
            extend_hold(self.ffmpeg, src, out_path, duration, fps, size=size)
            src.unlink(missing_ok=True)
            hold_s = time.time() - t1
        return {"out": str(out_path), "duration": duration, "renderer": used, "render_s": round(wall, 2),
                "hold_s": round(hold_s, 2), "errors": errors}
