"""Manim specialist: renders validated beat specs (title / bullets / equation / graph / array_steps).

Rendering happens in ONE sandboxed child process per batch (pays the ~1 s Manim import once):
``prlimit`` caps CPU seconds, address space (≥ 1 GB needed), file size and core dumps, the
child runs niced with a minimal environment, and a wall-clock timeout kills its process group.
LLM text is only ever *data* in the spec: maths goes through a Typst denylist + precompile,
graph expressions through an AST whitelist (see manim_templates.py).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ...config import Config
from .sandbox import run_limited

HERE = Path(__file__).resolve().parent
CHILD = HERE / "manim_render_beat.py"


def _num(v, default: float) -> float:
    try:
        f = float(v)
        return f if f == f and abs(f) < 1e6 else default
    except (TypeError, ValueError):
        return default


def binary_search_steps(values: list[int], target: int) -> list[dict]:
    """Deterministic binary-search trace for the array_steps template (never written by the LLM)."""
    lo, hi, steps = 0, len(values) - 1, []
    while lo <= hi and len(steps) < 11:
        mid = (lo + hi) // 2
        dim = [i for i in range(len(values)) if i < lo or i > hi]
        v = values[mid]
        if v == target:
            steps.append({"pointers": {"lo": lo, "mid": mid, "hi": hi}, "highlight": [mid], "dim": dim,
                          "found": [mid], "caption": f"a[mid] = {v} = {target} → found"})
            return steps
        rel, nxt = ("<", "lo = mid + 1") if v < target else (">", "hi = mid - 1")
        steps.append({"pointers": {"lo": lo, "mid": mid, "hi": hi}, "highlight": [mid], "dim": dim,
                      "caption": f"a[mid] = {v} {rel} {target} → {nxt}"})
        lo, hi = (mid + 1, hi) if v < target else (lo, mid - 1)
    steps.append({"pointers": {}, "dim": list(range(len(values))), "caption": f"{target} ∉ a"})
    return steps


def _tidy(x: float) -> int | float:
    return int(x) if float(x).is_integer() else round(float(x), 3)


def _short(text: str, n: int) -> str:
    """Cut at a word boundary with an ellipsis instead of mid-word."""
    text = " ".join(str(text).split())
    if len(text) <= n:
        return text
    cut = text[: n - 1].rsplit(" ", 1)[0] if " " in text[: n - 1] else text[: n - 1]
    return cut.rstrip(" ,;:") + "…"


def beat_to_spec(beat: dict, lang: str) -> dict:
    """Map a planner beat (flat schema) onto a Manim template spec."""
    t, title = beat.get("template"), (beat.get("title") or "")[:90]
    lines = [str(x).strip() for x in (beat.get("lines") or []) if str(x).strip()]
    if t == "title":
        spec = {"type": "title", "title": title or "…", "subtitle": (lines[0] if lines else "")[:140]}
    elif t == "equation_steps":
        eqs = [str(e).strip() for e in (beat.get("equations") or []) if str(e).strip()][:6]
        spec = {"type": "equation", "title": title, "layout": "stack",
                "steps": [{"math": e[:200], "caption": (lines[i] if i < len(lines) else "")[:120]}
                          for i, e in enumerate(eqs)]}
    elif t == "function_graph":
        lo, hi = _num(beat.get("x_min"), -5.0), _num(beat.get("x_max"), 5.0)
        if hi <= lo:
            lo, hi = -5.0, 5.0
        spec = {"type": "graph", "title": title, "expr": (beat.get("expression") or "")[:160],
                "x_range": [lo, hi], "label": _short(lines[0] if lines else "", 40)}
    elif t == "array_steps":
        # keep the planner's numbers (3.5 stays 3.5) so the picture matches the narration
        vals = sorted({_tidy(_num(v, 0)) for v in (beat.get("values") or [])})[:16]
        target = _tidy(_num(beat.get("target"), vals[len(vals) // 2] if vals else 0))
        spec = {"type": "array_steps", "title": title, "values": vals, "step_seconds": 1.0,
                "steps": binary_search_steps(vals, target) if vals else []}
    else:  # bullets, definition, or a HyperFrames-only template
        if not lines:
            lines = [(beat.get("narration") or title or "…")[:140]]
        spec = {"type": "bullets", "title": title, "bullets": [ln[:140] for ln in lines[:6]]}
    spec["lang"] = lang
    return spec


class ManimEngine:
    name = "manim"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.python = cfg.get("paths.python") or sys.executable
        self.ffmpeg = cfg["paths.ffmpeg"]
        self.width, self.height, self.fps = int(cfg["video.width"]), int(cfg["video.height"]), int(cfg["video.fps"])
        self._ok: bool | None = None

    def available(self) -> bool:
        if self._ok is None:
            try:
                res = subprocess.run([self.python, "-c", "import manim, typst"], capture_output=True, timeout=120)
                self._ok = res.returncode == 0
            except (OSError, subprocess.SubprocessError):
                self._ok = False
        return self._ok

    def _env(self) -> dict:
        return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": os.environ.get("HOME", "/tmp"), "LANG": "C.UTF-8",
                "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
                "MALLOC_ARENA_MAX": "2", "GATEMOE_HOLD_IN_MANIM": "0"}

    def _run_child(self, args: list[str], n: int, cwd: str) -> list[dict]:
        cmd = [self.python, "-I", str(CHILD), *args]
        if shutil.which("prlimit"):
            cpu = int(self.cfg.get("video.manim_cpu_s_per_beat", 90)) * n + 60
            as_mb = int(self.cfg.get("video.manim_as_mb", 2048))
            cmd = ["prlimit", f"--cpu={cpu}", f"--as={as_mb * 2**20}", f"--fsize={512 * 2**20}", "--core=0",
                   "--", *(["nice", "-n", "10"] if shutil.which("nice") else []), *cmd]
        timeout = 60 + 60 * n
        try:
            proc = run_limited(cmd, cwd=cwd, env=self._env(), timeout=timeout)
            out, err = proc.stdout, proc.stderr
            why = {"kind": "crash", "error": f"exit {proc.returncode}: {(err or '')[-800:]}"}
        except subprocess.TimeoutExpired as exc:
            out, why = (exc.output or ""), {"kind": "timeout", "error": f"killed after {timeout}s"}
        beats: list[dict | None] = [None] * n
        for line in (out or "").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d.get("i"), int) and 0 <= d["i"] < n:
                beats[d["i"]] = d
        return [b if b is not None else {"i": i, "ok": False, **why} for i, b in enumerate(beats)]

    def check(self, specs: list[dict]) -> list[str | None]:
        """Validate specs in a child process; returns an error string (or None) per spec."""
        if not specs:
            return []
        tmp = tempfile.mkdtemp(prefix="gm_check_")
        try:
            args = ["--check"]
            for i, spec in enumerate(specs):
                p = Path(tmp) / f"s{i}.json"
                p.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
                args += ["--spec", str(p)]
            return [None if r.get("ok") else r.get("error", "invalid") for r in self._run_child(args, len(specs), tmp)]
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def render_many(self, items: list[tuple[dict, Path, float]], work: Path) -> list[dict]:
        """Render [(spec, out_path, duration)] in one sandboxed batch; each clip is then held
        (last frame cloned) until ``duration``. Returns one result dict per item."""
        from .hyperframes import extend_hold

        if not items:
            return []
        work.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix="gm_manim_", dir=work))
        try:
            args = ["--width", str(self.width), "--height", str(self.height), "--fps", str(self.fps),
                    "--work", str(tmp / "media"), "--cache", str(self.cfg.path("paths.cache_dir") / "manim_svg")]
            raws = []
            for i, (spec, _out, _d) in enumerate(items):
                sp = tmp / f"beat{i}.json"
                sp.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
                raw = tmp / f"raw{i}.mp4"
                raws.append(raw)
                args += ["--spec", str(sp), "--out", str(raw)]
            t0 = time.perf_counter()
            results = self._run_child(args, len(items), str(tmp))
            batch_s = time.perf_counter() - t0
            for (spec, out, dur), raw, res in zip(items, raws, results):
                res["template"], res["batch_s"] = spec.get("type"), round(batch_s, 2)
                if not res.get("ok"):
                    continue
                total = max(float(dur), float(res.get("duration_s") or 0))
                if total - float(res.get("duration_s") or 0) > 1.0 / self.fps:
                    extend_hold(self.ffmpeg, raw, Path(out), total, self.fps)
                else:
                    shutil.move(str(raw), str(out))
                res["out"], res["duration"] = str(out), total
            return results
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
