"""VIDEO GATEWAY: renders the generator's JSON scene plan with pre-tested templates.

Second-level routing: the router's ``video_engine`` choice picks the renderer family
(Manim for equations/graphs/algorithms, HyperFrames for text/process/charts/code); each beat's
``template`` picks the scene class. If a beat cannot be rendered it degrades to a bullet slide,
then to the other engine, so one bad beat never sinks the video.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from ...config import Config
from ...runtime.events import EventLog
from .assemble import assemble, probe_duration, write_subtitles


def _lines(beat: dict, n: int = 6) -> list[str]:
    lines = [str(x).strip() for x in (beat.get("lines") or []) if str(x).strip()]
    if not lines:  # fall back to the narration, one short sentence per line
        parts = re.split(r"(?<=[.!?।॥。])\s+", (beat.get("narration") or "").strip())
        lines = [p[:120] for p in parts if p][:n]
    return lines[:n] or [beat.get("title") or "…"]


def _split_step(line: str) -> dict:
    for sep in (": ", " - ", " – ", " — "):
        if sep in line:
            a, b = line.split(sep, 1)
            return {"label": a.strip()[:60], "detail": b.strip()[:110]}
    return {"label": line[:60]}


def hf_scene(beat: dict, lang: str, lesson_title: str) -> tuple[str, dict]:
    """Map a plan beat onto a HyperFrames template + payload."""
    t, title = beat.get("template"), beat.get("title", "")
    base = {"lang": lang}
    if t == "title":
        return "title", {**base, "kicker": lesson_title if lesson_title != title else "", "title": title,
                         "subtitle": (beat.get("lines") or [""])[0]}
    if t == "process_steps":
        return "steps", {**base, "heading": title, "steps": [_split_step(s) for s in _lines(beat)]}
    if t == "bar_chart":
        labels, values = beat.get("labels") or [], beat.get("values") or []
        bars = [{"label": str(lab), "value": v} for lab, v in zip(labels, values)]
        return "bars", {**base, "heading": title, "bars": bars, "caption": (beat.get("lines") or [""])[0]}
    if t == "code_walkthrough":
        notes = beat.get("lines") or []
        hl = [{"lines": [int(h)], "note": notes[i] if i < len(notes) else ""}
              for i, h in enumerate(beat.get("highlight") or [])]
        return "code", {**base, "heading": title, "language": "python", "code": beat.get("code", ""), "highlights": hl}
    if t in ("equation_steps",):
        return "bullets", {**base, "heading": title, "bullets": (beat.get("equations") or _lines(beat))[:6]}
    # bullets, definition, and anything else
    return "bullets", {**base, "heading": title, "bullets": _lines(beat)}


class VideoGateway:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._engines: dict | None = None

    @property
    def engines(self) -> dict:
        if self._engines is None:
            found = {}
            enabled = self.cfg.get("video.enabled_engines", [])
            if "manim" in enabled:
                try:
                    from .manim_engine import ManimEngine
                    found["manim"] = ManimEngine(self.cfg)
                except Exception:   # manim not installed
                    pass
            if "hyperframes" in enabled:
                from .hyperframes import HyperFramesEngine
                found["hyperframes"] = HyperFramesEngine(self.cfg)
            self._engines = found
        return self._engines

    def available_engines(self) -> list[str]:
        return [name for name, eng in self.engines.items() if eng.available()]

    # -- one beat ----------------------------------------------------------------------------
    def _render_beat(self, engine: str, beat: dict, lang: str, title: str, out: Path, duration: float,
                     work: Path) -> dict:
        eng = self.engines[engine]
        if engine == "hyperframes":
            tpl, data = hf_scene(beat, lang, title)
            return {"template": tpl, **eng.render(tpl, data, out, duration, work)}
        return eng.render(beat, lang, out, duration, work)

    def _safe_beat(self, beat: dict) -> dict:
        return {"template": "bullets", "title": beat.get("title", ""), "lines": _lines(beat),
                "narration": beat.get("narration", "")}

    # -- whole video -------------------------------------------------------------------------
    def render(self, plan: dict, narration: list[dict] | None, lang: str, engine: str, out_dir: Path,
               events: EventLog | None = None) -> dict:
        beats = plan.get("beats") or []
        if not beats:
            raise ValueError("video plan has no beats")
        avail = self.available_engines()
        if engine not in avail:
            if not avail:
                raise RuntimeError("no video engine available")
            engine = avail[0]
        others = [e for e in avail if e != engine]
        work = out_dir / "video_work"
        work.mkdir(parents=True, exist_ok=True)
        tail = float(self.cfg["video.tail_pad_s"])
        fps = int(self.cfg["video.fps"])
        clips, durs, wavs, texts, report = [], [], [], [], []
        for i, beat in enumerate(beats):
            if events:
                events.check_cancel()
            wav = None
            if narration and i < len(narration):
                wav = narration[i]["wav"]
                d = narration[i]["duration_s"] + tail
            else:  # no voice: give the viewer time to read the caption
                d = max(3.0, len(beat.get("narration") or "") / 14.0)
            out = work / f"beat{i:02d}.mp4"
            t0 = time.perf_counter()
            info, errors = None, []
            for eng_name, b in [(engine, beat), (engine, self._safe_beat(beat))] + [(o, self._safe_beat(beat)) for o in others]:
                try:
                    info = self._render_beat(eng_name, b, lang, plan.get("title", ""), out, d, work)
                    info["engine"] = eng_name
                    break
                except Exception as exc:
                    errors.append(f"{eng_name}/{b.get('template')}: {type(exc).__name__}: {str(exc)[:300]}")
            rec = {"index": i, "template": beat.get("template"), "seconds": round(time.perf_counter() - t0, 2),
                   "ok": info is not None, "errors": errors}
            if info:
                rec.update(engine=info["engine"], rendered_as=info.get("template"), renderer=info.get("renderer"))
                real = probe_duration(out, self.cfg["paths.ffprobe"]) or d
                clips.append(out)
                durs.append(round(real * fps) / fps)
                wavs.append(wav)
                texts.append(beat.get("narration", ""))
            report.append(rec)
            if events:
                events.emit("video_beat", **rec)
        if not clips:
            raise RuntimeError("every beat failed to render: " + "; ".join(e for r in report for e in r["errors"])[:1500])
        final = out_dir / "video.mp4"
        assemble(clips, durs, wavs, final, self.cfg["paths.ffmpeg"])
        write_subtitles(texts, durs, out_dir / "video.vtt", out_dir / "video.srt")
        for c in clips:
            c.unlink(missing_ok=True)
        return {"video": "video.mp4", "subtitles": "video.vtt", "srt": "video.srt",
                "duration_s": round(sum(durs), 2), "engine": engine, "beats": report}
