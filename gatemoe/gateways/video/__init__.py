"""VIDEO GATEWAY: renders the generator's JSON scene plan with pre-tested templates.

Second-level routing: the router's ``video_engine`` choice picks the renderer family
(Manim for equations/graphs/algorithms, HyperFrames for text/process/charts/code); each beat's
``template`` picks the scene class. If a beat cannot be rendered it degrades to a bullet slide,
then to the other engine, so one bad beat never sinks the video.
"""
from __future__ import annotations

import shutil

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

    # -- validation (run while the generator is still loaded, so bad beats can be repaired) ----
    def validate(self, plan: dict, lang: str, engine: str) -> dict[int, str]:
        """{beat index: error} for beats the chosen engine would reject."""
        beats = plan.get("beats") or []
        if engine == "manim" and "manim" in self.engines:
            from .manim_engine import beat_to_spec
            errs = self.engines["manim"].check([beat_to_spec(b, lang) for b in beats])
            return {i: e for i, e in enumerate(errs) if e}
        if engine == "hyperframes":
            from .hyperframes import build_payload
            bad = {}
            for i, b in enumerate(beats):
                try:
                    build_payload(*hf_scene(b, lang, plan.get("title", "")))
                except ValueError as exc:
                    bad[i] = str(exc)
            return bad
        return {}

    # -- rendering ---------------------------------------------------------------------------
    def _safe_beat(self, beat: dict) -> dict:
        return {"template": "bullets", "title": beat.get("title", ""), "lines": _lines(beat),
                "narration": beat.get("narration", "")}

    def _render_pass(self, engine: str, jobs: list[tuple], lang: str, title: str, work: Path) -> dict:
        """jobs = [(index, beat, out_path, duration)] -> {index: info dict | error string}."""
        eng, results = self.engines[engine], {}
        if engine == "manim":
            from .manim_engine import beat_to_spec
            res = eng.render_many([(beat_to_spec(b, lang), out, d) for (_, b, out, d) in jobs], work)
            for (i, b, _, _), r in zip(jobs, res):
                results[i] = ({"template": r.get("template"), "renderer": "manim", **r} if r.get("ok") else
                              f"manim/{b.get('template')}: {r.get('kind')}: {str(r.get('error', ''))[:300]}")
            return results
        for i, b, out, d in jobs:
            try:
                tpl, data = hf_scene(b, lang, title)
                results[i] = {"template": tpl, **eng.render(tpl, data, out, d, work)}
            except Exception as exc:
                results[i] = f"{engine}/{b.get('template')}: {type(exc).__name__}: {str(exc)[:300]}"
        return results

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
        try:
            return self._render(plan, beats, narration, lang, engine, others, work, out_dir, events)
        finally:   # success or failure: beat clips are scratch; narration WAVs are muxed or useless
            shutil.rmtree(work, ignore_errors=True)
            voiced = [n["wav"] for n in narration or [] if n.get("wav")]
            if voiced:
                shutil.rmtree(Path(voiced[0]).parent, ignore_errors=True)

    def _render(self, plan: dict, beats: list, narration: list[dict] | None, lang: str, engine: str,
                others: list[str], work: Path, out_dir: Path, events: EventLog | None) -> dict:
        tail, fps = float(self.cfg["video.tail_pad_s"]), int(self.cfg["video.fps"])
        pending, wavs = [], {}
        for i, beat in enumerate(beats):
            if narration and i < len(narration) and narration[i].get("wav"):
                wavs[i], d = narration[i]["wav"], narration[i]["duration_s"] + tail
            else:  # no voice: give the viewer time to read the caption
                wavs[i], d = None, max(3.0, len(beat.get("narration") or "") / 14.0)
            pending.append((i, beat, work / f"beat{i:02d}.mp4", d))
        infos: dict[int, dict] = {}
        errors: dict[int, list[str]] = {i: [] for i in range(len(beats))}
        t0 = time.perf_counter()
        # pass 1: router's engine as planned; pass 2: safe bullet slide; pass 3: the other engine(s)
        for eng_name, safe in [(engine, False), (engine, True)] + [(o, True) for o in others]:
            if not pending:
                break
            if events:
                events.check_cancel()
            jobs = [(i, self._safe_beat(b) if safe else b, out, d) for (i, b, out, d) in pending]
            res = self._render_pass(eng_name, jobs, lang, plan.get("title", ""), work)
            still = []
            for job in pending:
                r = res.get(job[0])
                if isinstance(r, dict):
                    infos[job[0]] = {**r, "engine": eng_name, "fallback": safe}
                else:
                    errors[job[0]].append(r or f"{eng_name}: no result")
                    still.append(job)
            pending = still
        clips, durs, wav_list, texts, report = [], [], [], [], []
        for i, beat in enumerate(beats):
            info = infos.get(i)
            rec = {"index": i, "template": beat.get("template"), "ok": info is not None, "errors": errors[i]}
            if info:
                out = work / f"beat{i:02d}.mp4"
                rec.update(engine=info["engine"], rendered_as=info.get("template"), renderer=info.get("renderer"),
                           fallback=info["fallback"])
                real = probe_duration(out, self.cfg["paths.ffprobe"]) or pending_duration(beat)
                clips.append(out)
                durs.append(round(real * fps) / fps)
                wav_list.append(wavs[i])
                texts.append(beat.get("narration", ""))
            report.append(rec)
            if events:
                events.emit("video_beat", **rec)
        if not clips:
            raise RuntimeError("every beat failed to render: " + "; ".join(e for r in report for e in r["errors"])[:1500])
        final = out_dir / "video.mp4"
        assemble(clips, durs, wav_list, final, self.cfg["paths.ffmpeg"])
        write_subtitles(texts, durs, out_dir / "video.vtt", out_dir / "video.srt")
        return {"video": "video.mp4", "subtitles": "video.vtt", "srt": "video.srt",
                "duration_s": round(sum(durs), 2), "engine": engine, "render_s": round(time.perf_counter() - t0, 2),
                "beats": report}


def pending_duration(beat: dict) -> float:
    return max(3.0, len(beat.get("narration") or "") / 14.0)
