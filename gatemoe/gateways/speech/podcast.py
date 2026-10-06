"""Two-speaker podcast assembly for GateMoE (Pi-5, fully offline).

turns = [{"speaker": "A"|"B", "text": "...", "lang": "en"}, ...]
    -> per-turn WAV (engine chosen per language, see ENGINE_TABLE)
    -> speaker-B differentiation (other voice / other sid / FFmpeg pitch shift)
    -> edge-silence trim, gaps (same speaker 250-300 ms, change 450-600 ms)
    -> concat at 44.1 kHz mono -> 2-pass EBU R128 loudnorm (-16 LUFS, -1.5 dBTP)
    -> MP3 (libmp3lame) or Opus (libopus)
returns {"output", "duration_s", "turns": [{start_s, end_s, duration_s, engine, ...}], ...}

Engines (all CPU, all offline):
  supertonic  supertonic_tts.SupertonicTTS   31 languages, voices F1-F5/M1-M5
  mms         mms_tts.MMSTTS                 Meta MMS VITS exported with export_mms_onnx.py
  piper       piper_engine.PiperEngine       Piper voices te/ml/mr/bn (piper-tts, GPL-3.0)
  sherpa      sherpa_tts.SherpaTTS           zh (k2-fsa Piper zh voice with lexicon + FSTs)

Model layout expected under --models (= fetch_speech_models.sh output):
  supertonic3/onnx/*.onnx + supertonic3/voice_styles/*.json
  mms/<iso3>/model.onnx (+ vocab.json, tokenizer_config.json, config.json, tokens.txt)
  piper/<voice>/<voice>.onnx + <voice>.onnx.json   (rhasspy/piper-voices files as-is; piper-tts
                                                    bundles espeak-ng-data)
  sherpa/<release-dir>/...  (untouched k2-fsa tarballs, e.g. vits-piper-zh_CN-chaowen-medium)
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

from .supertonic_tts import SUPPORTED_LANGUAGES as ST_LANGS

log = logging.getLogger("podcast")
OUT_SR = 44100

# lang -> ordered candidates (engine, model, speaker-A, speaker-B[, extra B pitch]).
# The first candidate whose files exist is used.  A/B are voice names
# (supertonic), speaker ids (multi-speaker piper) or None (single voice ->
# speaker B = FFmpeg pitch shift).  Median F0 measured per speaker id:
#   mr_IN-google: all 9 female (184-248 Hz) -> A=2 (245 Hz), B=1 (184 Hz) - 2 st
#   bn_BD-google: sid 12 = 253 Hz (female), sid 0 = 122 Hz (male)
ENGINE_TABLE: dict[str, list[tuple]] = {
    "kn": [("mms", "kan", None, None)],
    "ta": [("mms", "tam", None, None)],
    "te": [("piper", "piper/te_IN-venkatesh-medium", 0, None), ("mms", "tel", None, None)],
    "ml": [("piper", "piper/ml_IN-arjun-medium", 0, None),
           ("sherpa", "sherpa/vits-piper-ml_IN-arjun-medium", 0, None), ("mms", "mal", None, None)],
    "mr": [("piper", "piper/mr_IN-google-medium", 2, 1, -2.0), ("mms", "mar", None, None)],
    "bn": [("piper", "piper/bn_BD-google-medium", 12, 0), ("mms", "ben", None, None)],
    "gu": [("mms", "guj", None, None)],
    "pa": [("mms", "pan", None, None)],
    "or": [("mms", "ory", None, None)],
    "as": [("mms", "asm", None, None)],
    "zh": [("sherpa", "sherpa/vits-piper-zh_CN-chaowen-medium", 0, None)],
}


def _latin_words(s: str) -> list[str]:
    return re.findall(r"[A-Za-z]{2,}", s)


@dataclass
class PodcastConfig:
    models: str                               # root dir of the layout above
    threads: int = 4
    steps: int = 5                            # Supertonic flow-matching steps
    voice_a: str = "F2"
    voice_b: str = "M1"
    speed: float = 1.0
    seed: int = 7
    gap_same: tuple = (0.25, 0.30)
    gap_change: tuple = (0.45, 0.60)
    lufs: float = -16.0
    true_peak: float = -1.5
    pitch_b_semitones: Optional[float] = None  # None = automatic (from voice A's F0)
    fmt: str = "mp3"                          # mp3 | opus | wav
    bitrate: str = "64k"
    keep_turn_wavs: bool = False
    max_loaded_engines: int = 2               # LRU cap on TTS models held in RAM
    st_variant: str = "fp32"                  # "int8" -> *.int8.onnx (k2-fsa quantised) if present
    ffmpeg: str = "ffmpeg"


# ----------------------------------------------------------------- audio utils
def read_wav(path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        sr, n, ch, sw = w.getframerate(), w.getnframes(), w.getnchannels(), w.getsampwidth()
        assert sw == 2, "16-bit PCM expected"
        x = np.frombuffer(w.readframes(n), dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr


def write_wav(path, x: np.ndarray, sr: int) -> None:
    pcm = (np.clip(x, -1, 1) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def trim_silence(x: np.ndarray, sr: int, thresh_db: float = -45.0, pad: float = 0.03) -> np.ndarray:
    """Trim leading/trailing silence (10 ms RMS frames below thresh_db dBFS)."""
    hop = max(1, int(0.01 * sr))
    n = len(x) // hop
    if n == 0:
        return x
    rms = np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(axis=1) + 1e-12)
    loud = np.nonzero(20 * np.log10(rms) > thresh_db)[0]
    if loud.size == 0:
        return x
    a = max(0, loud[0] * hop - int(pad * sr))
    b = min(len(x), (loud[-1] + 1) * hop + int(pad * sr))
    return x[a:b]


def level(x: np.ndarray, sr: int, target_db: float = -20.0, gate_db: float = -45.0) -> np.ndarray:
    """Scale so the RMS of active (non-silent) 10 ms frames is target_db dBFS; peak <= 0.95."""
    hop = max(1, int(0.01 * sr))
    n = len(x) // hop
    if n == 0:
        return x
    fr = x[: n * hop].reshape(n, hop)
    rms = np.sqrt((fr ** 2).mean(axis=1) + 1e-12)
    act = rms[20 * np.log10(rms) > gate_db]
    if act.size == 0:
        return x
    g = 10 ** (target_db / 20) / np.sqrt((act ** 2).mean())
    pk = np.abs(x).max() * g
    if pk > 0.95:
        g *= 0.95 / pk
    return (x * g).astype(np.float32)


def median_f0(x: np.ndarray, sr: int, max_seconds: float = 6.0) -> float:
    """Crude autocorrelation pitch estimate (Hz) over voiced 40 ms frames."""
    x = x[: int(max_seconds * sr)]
    fl, hop = int(0.04 * sr), int(0.01 * sr)
    lo, hi = int(sr / 400), int(sr / 70)
    vals = []
    for i in range(0, len(x) - fl, hop):
        fr = x[i:i + fl]
        if np.sqrt((fr ** 2).mean()) < 0.02:
            continue
        fr = fr - fr.mean()
        ac = np.correlate(fr, fr, "full")[fl - 1:]
        lag = lo + int(np.argmax(ac[lo:hi]))
        if ac[0] > 0 and ac[lag] / ac[0] > 0.5:
            vals.append(sr / lag)
    return float(np.median(vals)) if vals else 0.0


_FILTERS: Optional[str] = None


def ffmpeg_has_filter(ffmpeg: str, name: str) -> bool:
    global _FILTERS
    if _FILTERS is None:
        _FILTERS = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    return re.search(rf"\s{name}\s", _FILTERS) is not None


def pitch_filter(ffmpeg: str, semitones: float, sr: int, method: str = "asetrate") -> str:
    """FFmpeg filter shifting pitch AND formants by `semitones`, keeping duration.

    "asetrate" (default): resample trick + atempo (WSOLA) tempo correction.  Measured
    on MMS-kan: -3 st -> 139.7 Hz vs 138.7 expected, +3 st -> 195.1 vs 196.2, duration
    within 0.1 %, 0.13-0.15 s per 10 s clip; works in every FFmpeg build.
    "rubberband": librubberband phase vocoder (needs --enable-librubberband); landed
    further from the target F0 in the same test (148 vs 139 Hz at -3 st)."""
    f = 2.0 ** (semitones / 12.0)
    if method == "rubberband" and ffmpeg_has_filter(ffmpeg, "rubberband"):
        return f"rubberband=pitch={f:.5f}"
    return f"asetrate={int(round(sr * f))},aresample={sr},atempo={1.0 / f:.5f}"


def run_ffmpeg(ffmpeg: str, args: list[str]) -> subprocess.CompletedProcess:
    p = subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-y", *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {' '.join(args)}\n{p.stderr[-2000:]}")
    return p


# --------------------------------------------------------------------- engines
class Engines:
    """Lazy-loading engine registry. Call unload() to free RAM before the LLM loads."""

    def __init__(self, cfg: PodcastConfig):
        self.cfg = cfg
        self.root = Path(cfg.models)
        from collections import OrderedDict
        self._cache: "OrderedDict[tuple, object]" = OrderedDict()

    def unload(self) -> None:
        self._cache.clear()

    def _exists(self, engine: str, model: str) -> bool:
        if engine == "supertonic":
            return (self.root / "supertonic3" / "onnx" / "vector_estimator.onnx").exists()
        if engine == "mms":
            return (self.root / "mms" / model / "model.onnx").exists()
        d = self.root / model
        return d.is_dir() and any(d.glob("*.onnx"))

    def route(self, lang: str) -> tuple:
        if lang in ST_LANGS:
            return ("supertonic", "supertonic3", self.cfg.voice_a, self.cfg.voice_b)
        for cand in ENGINE_TABLE.get(lang, []):
            if self._exists(cand[0], cand[1]):
                return cand
        if self._exists("supertonic", ""):
            log.warning("no dedicated TTS for %r - using Supertonic 'na' fallback", lang)
            return ("supertonic", "supertonic3", self.cfg.voice_a, self.cfg.voice_b)
        raise RuntimeError(f"no TTS engine available for language {lang!r}")

    def get(self, engine: str, model: str):
        key = (engine, model)
        if key in self._cache:
            self._cache.move_to_end(key)
        else:
            while len(self._cache) >= max(1, self.cfg.max_loaded_engines):
                old, _ = self._cache.popitem(last=False)     # evict least recently used
                log.info("unloading TTS engine %s", old)
            th = self.cfg.threads
            if engine == "supertonic":
                from .supertonic_tts import SupertonicTTS
                self._cache[key] = SupertonicTTS(self.root / "supertonic3", threads=th, variant=self.cfg.st_variant)
            elif engine == "mms":
                from .mms_tts import MMSTTS
                self._cache[key] = MMSTTS(self.root / "mms" / model, threads=th, onnx_file="model.onnx")
            elif engine == "sherpa":
                from .sherpa_tts import SherpaTTS
                self._cache[key] = SherpaTTS(self.root / model, threads=th)
            elif engine == "piper":
                from .piper_engine import PiperEngine
                d = self.root / model
                onnx = sorted(d.glob("*.onnx"))[0]
                self._cache[key] = PiperEngine(str(onnx), threads=th)
            else:
                raise ValueError(engine)
        return self._cache[key]

    def synth(self, text: str, lang: str, speaker: str) -> tuple[np.ndarray, int, dict]:
        engine, model, va, vb, *rest = self.route(lang)
        t0 = time.perf_counter()
        eng = self.get(engine, model)
        voice = va if speaker == "A" else vb
        info = {"engine": engine, "model": model, "voice": voice,
                "load_s": round(time.perf_counter() - t0, 3)}
        if engine == "supertonic":
            st_lang = lang if lang in ST_LANGS else "na"
            wav, sr = eng.synth(text, lang=st_lang, voice=voice, steps=self.cfg.steps,
                                speed=self.cfg.speed, seed=self.cfg.seed)
        elif engine == "mms":
            from .numbers_indic import verbalize_numbers      # MMS cannot read digits
            text = verbalize_numbers(text, lang)
            info["text_spoken"] = text
            wav, sr = eng.synth(text, speed=self.cfg.speed)
        else:
            sid = voice if voice is not None else va
            wav, sr = eng.synth(text, sid=int(sid or 0), speed=self.cfg.speed)
        info["dropped_chars"] = getattr(eng, "last_info", {}).get("dropped_chars", [])
        info["needs_pitch_shift"] = speaker == "B" and voice is None
        info["extra_pitch_b"] = float(rest[0]) if (rest and speaker == "B") else 0.0
        return wav, sr, info


# --------------------------------------------------------------------- podcast
def make_podcast(turns: list[dict], out_path: str | os.PathLike, cfg: PodcastConfig,
                 engines: Optional[Engines] = None) -> dict:
    if not turns:
        raise ValueError("no turns")
    t_all = time.perf_counter()
    engines = engines or Engines(cfg)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="podcast_", dir=str(out_path.parent)))
    rng = np.random.default_rng(cfg.seed)
    warnings: list[str] = []
    a_f0: dict[str, float] = {}           # per (engine,model): F0 of speaker A
    segs, meta = [], []
    try:
        for i, t in enumerate(turns):
            spk = str(t.get("speaker", "A")).upper()
            if spk not in ("A", "B"):
                raise ValueError(f"turn {i}: speaker must be A or B")
            text, lang = (t.get("text") or "").strip(), (t.get("lang") or "en").strip().lower()
            if not text:
                warnings.append(f"turn {i}: empty text skipped")
                continue
            t0 = time.perf_counter()
            wav, sr, info = engines.synth(text, lang, spk)
            synth_s = time.perf_counter() - t0 - info["load_s"]
            if info["engine"] != "supertonic" and _latin_words(text):
                warnings.append(f"turn {i}: Latin-script words {_latin_words(text)[:5]} may be "
                                f"skipped by {info['engine']}/{info['model']}; transliterate them")
            if info["dropped_chars"]:
                warnings.append(f"turn {i}: dropped chars {info['dropped_chars'][:20]}")
            key = f"{info['engine']}:{info['model']}"
            raw = work / f"turn{i:03d}_raw.wav"
            write_wav(raw, wav, sr)
            filt = []
            semis = 0.0
            if info["needs_pitch_shift"]:
                if cfg.pitch_b_semitones is not None:
                    semis = cfg.pitch_b_semitones
                else:
                    f0 = a_f0.get(key) or median_f0(wav, sr)
                    # higher voice -> make B deeper; lower voice -> make B higher
                    semis = -3.0 if f0 >= 165.0 else 3.0
                filt.append(pitch_filter(cfg.ffmpeg, semis, sr))
            elif info["extra_pitch_b"]:
                semis = info["extra_pitch_b"]
                filt.append(pitch_filter(cfg.ffmpeg, semis, sr))
            elif spk == "A" and key not in a_f0:
                a_f0[key] = median_f0(wav, sr)
            filt.append(f"aresample={OUT_SR}")
            proc = work / f"turn{i:03d}.wav"
            t1 = time.perf_counter()
            run_ffmpeg(cfg.ffmpeg, ["-i", str(raw), "-af", ",".join(filt), "-ac", "1",
                                    "-ar", str(OUT_SR), "-c:a", "pcm_s16le", str(proc)])
            post_s = time.perf_counter() - t1
            y, _ = read_wav(proc)
            y = level(trim_silence(y, OUT_SR), OUT_SR)
            segs.append((spk, y))
            meta.append({"index": i, "speaker": spk, "lang": lang, **{k: info[k] for k in ("engine", "model", "voice")},
                         "pitch_shift_semitones": semis, "load_s": info["load_s"], "synth_s": round(synth_s, 3),
                         "post_s": round(post_s, 3), "native_sr": sr})
        if not segs:
            raise ValueError("no speakable turns")
        # ---- gaps + concat
        parts, pos = [], 0
        for j, (spk, y) in enumerate(segs):
            if j > 0:
                lo, hi = cfg.gap_same if segs[j - 1][0] == spk else cfg.gap_change
                g = int(rng.uniform(lo, hi) * OUT_SR)
                parts.append(np.zeros(g, np.float32))
                pos += g
            meta[j]["start_s"] = round(pos / OUT_SR, 3)
            meta[j]["duration_s"] = round(len(y) / OUT_SR, 3)
            pos += len(y)
            meta[j]["end_s"] = round(pos / OUT_SR, 3)
            parts.append(y)
        master = np.concatenate(parts)
        master_wav = work / "master.wav"
        write_wav(master_wav, master, OUT_SR)
        # ---- 2-pass loudnorm
        t2 = time.perf_counter()
        ln = f"loudnorm=I={cfg.lufs}:TP={cfg.true_peak}:LRA=11"
        p1 = run_ffmpeg(cfg.ffmpeg, ["-i", str(master_wav), "-af", ln + ":print_format=json", "-f", "null", "-"])
        m = json.loads(p1.stderr[p1.stderr.rfind("{"): p1.stderr.rfind("}") + 1])
        ln2 = (f"{ln}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
               f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
        codec = {"mp3": ["-c:a", "libmp3lame", "-b:a", cfg.bitrate],
                 "opus": ["-c:a", "libopus", "-b:a", cfg.bitrate, "-application", "audio"],
                 "wav": ["-c:a", "pcm_s16le"]}[cfg.fmt]
        # loudnorm upsamples to 192 kHz internally -> resample back explicitly
        out_sr = 48000 if cfg.fmt == "opus" else OUT_SR
        p2 = run_ffmpeg(cfg.ffmpeg, ["-i", str(master_wav), "-af", f"{ln2}:print_format=json,aresample={out_sr}",
                                     "-ac", "1", "-ar", str(out_sr), *codec, str(out_path)])
        m2 = json.loads(p2.stderr[p2.stderr.rfind("{"): p2.stderr.rfind("}") + 1])
        loud_s = time.perf_counter() - t2
        # ---- verify output loudness
        p3 = run_ffmpeg(cfg.ffmpeg, ["-i", str(out_path), "-af", ln + ":print_format=json", "-f", "null", "-"])
        m3 = json.loads(p3.stderr[p3.stderr.rfind("{"): p3.stderr.rfind("}") + 1])
        total = len(master) / OUT_SR
        synth_total = sum(x["synth_s"] for x in meta)
        result = {
            "output": str(out_path), "format": cfg.fmt, "duration_s": round(total, 3),
            "turns": meta, "warnings": warnings,
            "loudness": {"input_i": float(m["input_i"]), "output_i": float(m3["input_i"]),
                         "output_tp": float(m3["input_tp"]), "normalization_type": m2.get("normalization_type")},
            "timing": {"engine_load_s": round(sum(x["load_s"] for x in meta), 2), "synth_s": round(synth_total, 2), "post_s": round(sum(x["post_s"] for x in meta), 2),
                       "loudnorm_encode_s": round(loud_s, 2), "total_s": round(time.perf_counter() - t_all, 2),
                       "synth_rtf": round(synth_total / total, 3)},
        }
        if cfg.keep_turn_wavs:
            result["work_dir"] = str(work)
        return result
    finally:
        if not cfg.keep_turn_wavs:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="turns JSON -> podcast MP3/Opus")
    ap.add_argument("--models", required=True)
    ap.add_argument("--turns", required=True, help="JSON file: [{speaker, text, lang}, ...]")
    ap.add_argument("--out", required=True)
    ap.add_argument("--fmt", default="mp3", choices=["mp3", "opus", "wav"])
    ap.add_argument("--bitrate", default="64k")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--steps", type=int, default=5)
    ap.add_argument("--voice-a", default="F2")
    ap.add_argument("--voice-b", default="M1")
    ap.add_argument("--pitch-b", type=float, default=None)
    ap.add_argument("--st-variant", default="fp32", choices=["fp32", "int8"])
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    cfg = PodcastConfig(models=a.models, threads=a.threads, steps=a.steps, voice_a=a.voice_a,
                        voice_b=a.voice_b, pitch_b_semitones=a.pitch_b, fmt=a.fmt, bitrate=a.bitrate,
                        keep_turn_wavs=a.keep, st_variant=a.st_variant)
    turns = json.loads(Path(a.turns).read_text(encoding="utf-8"))
    print(json.dumps(make_podcast(turns, a.out, cfg), ensure_ascii=False, indent=1))
