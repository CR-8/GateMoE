"""SPEECH GATEWAY: picks an offline TTS specialist per language, then builds the podcast/narration.

Second-level routing (by learner language, first installed candidate wins):
  Supertonic 3   31 languages incl. Hindi; 10 voices -> two real hosts (F2 + M1)
  Meta MMS VITS  kn, ta, gu, pa, or, as (one voice; host B pitch-shifted -3/+3 semitones)
  Piper          te, ml, mr, bn (multi-speaker voices where available)
  sherpa-onnx    zh
Engines are loaded lazily (LRU, max 2) and released with ``close()`` before an LLM loads.
"""
from __future__ import annotations

import time
from pathlib import Path

from ...config import Config
from ...runtime.events import EventLog
from .podcast import (ENGINE_TABLE, OUT_SR, Engines, PodcastConfig, level, make_podcast, read_wav,
                      run_ffmpeg, trim_silence, write_wav)
from .supertonic_tts import SUPPORTED_LANGUAGES as ST_LANGS


class SpeechGateway:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.root = cfg.path("speech.models_dir")
        self.pcfg = PodcastConfig(
            models=str(self.root), threads=int(cfg["speech.threads"]), steps=int(cfg["speech.steps"]),
            voice_a=cfg["speech.voices.A"], voice_b=cfg["speech.voices.B"], speed=float(cfg["speech.speed"]),
            fmt=cfg.get("speech.format", "mp3"), bitrate=cfg["speech.bitrate"],
            st_variant=cfg.get("speech.supertonic_variant", "fp32"), ffmpeg=cfg["paths.ffmpeg"],
            plugins=list(cfg.get("speech.plugins") or []))
        self._engines: Engines | None = None

    @property
    def engines(self) -> Engines:
        if self._engines is None:
            self._engines = Engines(self.pcfg)
        return self._engines

    # -- routing -----------------------------------------------------------------------------
    def engine_for(self, lang: str) -> str | None:
        """'engine:model' that would speak ``lang``, or None if no voice is installed.
        (Supertonic's 'na' fallback is never used: it is unintelligible for other scripts.)"""
        try:
            engine, model, *_ = self.engines.route(lang)
        except RuntimeError:
            return None
        return "supertonic-3" if engine == "supertonic" else f"{engine}:{Path(model).name}"

    def available(self) -> dict:
        eng = self.engines
        out = {"supertonic-3": {"available": eng._exists("supertonic", ""), "languages": list(ST_LANGS),
                                "path": str(self.root / "supertonic3")}}
        for lang, cands in ENGINE_TABLE.items():
            for cand in cands:
                name = f"{cand[0]}:{Path(cand[1]).name}"
                rec = out.setdefault(name, {"available": eng._exists(cand[0], cand[1]), "languages": []})
                rec["languages"].append(lang)
        for p in self.pcfg.plugins:
            out[f"plugin:{p.get('name')}"] = {"available": True, "languages": p.get("languages") or ["*"],
                                              "type": p.get("type")}
        return out

    # -- podcast -----------------------------------------------------------------------------
    def podcast(self, turns: list[dict], lang: str, out_dir: Path, events: EventLog | None = None) -> dict:
        clean = [{"speaker": t.get("speaker", "A"), "text": t.get("text", ""), "lang": lang} for t in turns]
        name = f"podcast.{self.pcfg.fmt}"
        res = make_podcast(clean, out_dir / name, self.pcfg, self.engines)
        engine = res["turns"][0]["engine"] if res.get("turns") else None
        if events:
            events.emit("tts_podcast", engine=engine, audio_s=res["duration_s"], **res.get("timing", {}))
        return {"audio": name, "duration_s": res["duration_s"], "engine": engine,
                "turns": [{k: t.get(k) for k in ("speaker", "start_s", "end_s", "engine", "voice")} for t in res["turns"]],
                "warnings": res.get("warnings", []), "loudness": res.get("loudness")}

    # -- narration for video beats -----------------------------------------------------------
    def narrate(self, texts: list[str], lang: str, out_dir: Path, events: EventLog | None = None) -> list[dict]:
        """One 44.1 kHz mono WAV per beat (host A voice). Returns [{'wav', 'duration_s'}]."""
        out_dir.mkdir(parents=True, exist_ok=True)
        results = []
        t0 = time.perf_counter()
        for i, text in enumerate(texts):
            if events:
                events.check_cancel()
            text = (text or "").strip() or "."
            wav, sr, info = self.engines.synth(text, lang, "A")
            raw = out_dir / f"beat{i:02d}_raw.wav"
            write_wav(raw, wav, sr)
            path = out_dir / f"beat{i:02d}.wav"
            run_ffmpeg(self.pcfg.ffmpeg, ["-i", str(raw), "-af", f"aresample={OUT_SR}", "-ac", "1",
                                          "-ar", str(OUT_SR), "-c:a", "pcm_s16le", str(path)])
            raw.unlink(missing_ok=True)
            y, _ = read_wav(path)
            y = level(trim_silence(y, OUT_SR), OUT_SR, target_db=-18.0)
            write_wav(path, y, OUT_SR)
            results.append({"wav": str(path), "duration_s": round(len(y) / OUT_SR, 3), "engine": info["engine"]})
        if events:
            total = sum(r["duration_s"] for r in results)
            events.emit("tts_narration", beats=len(results), audio_s=round(total, 2),
                        seconds=round(time.perf_counter() - t0, 2))
        return results

    def close(self) -> None:
        if self._engines is not None:
            self._engines.unload()

