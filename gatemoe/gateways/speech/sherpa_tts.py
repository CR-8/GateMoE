"""Thin wrapper over sherpa-onnx OfflineTts for Piper/VITS voices (pip sherpa-onnx).

A model dir is auto-detected:
  * Piper (espeak):  <name>.onnx + tokens.txt + espeak-ng-data/
  * zh Piper/VITS:   <name>.onnx + tokens.txt + lexicon.txt [+ *.fst rule files]
  * MMS (export_mms_onnx.py output) also loads: model.onnx + tokens.txt

    t = SherpaTTS("/models/piper/te_IN-venkatesh-medium", threads=2)
    wav, sr = t.synth("...", sid=0, speed=1.0)
"""
from __future__ import annotations

import glob
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import sherpa_onnx


class SherpaTTS:
    def __init__(self, model_dir: str | os.PathLike, threads: int = 2, onnx_file: str | None = None):
        d = Path(model_dir)
        onnx = Path(onnx_file) if onnx_file else sorted(
            p for p in d.glob("*.onnx") if not p.name.endswith(".int8.onnx"))[0]
        vits = dict(model=str(onnx), tokens=str(d / "tokens.txt"))
        if (d / "espeak-ng-data").exists():
            vits["data_dir"] = str(d / "espeak-ng-data")
        if (d / "lexicon.txt").exists():
            vits["lexicon"] = str(d / "lexicon.txt")
        rule_fsts = ",".join(str(d / f) for f in ("phone.fst", "date.fst", "number.fst") if (d / f).exists())
        cfg = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                vits=sherpa_onnx.OfflineTtsVitsModelConfig(**vits),
                provider="cpu", num_threads=int(threads), debug=False),
            rule_fsts=rule_fsts, max_num_sentences=1)
        if not cfg.validate():
            raise ValueError(f"invalid sherpa-onnx config for {d}")
        t0 = time.perf_counter()
        self.tts = sherpa_onnx.OfflineTts(cfg)
        self.load_seconds = time.perf_counter() - t0
        self.sample_rate = self.tts.sample_rate
        self.num_speakers = self.tts.num_speakers
        self.last_info: dict = {}

    def synth(self, text: str, sid: int = 0, speed: float = 1.0) -> tuple[np.ndarray, int]:
        t0 = time.perf_counter()
        a = self.tts.generate(text, sid=sid, speed=speed)
        wav = np.asarray(a.samples, dtype=np.float32)
        el = time.perf_counter() - t0
        if wav.size == 0:
            raise ValueError("sherpa-onnx produced no audio (unsupported text?)")
        dur = wav.size / a.sample_rate
        self.last_info = {"synth_s": el, "audio_s": dur, "rtf": el / dur}
        return wav, a.sample_rate


if __name__ == "__main__":
    import argparse
    from gatemoe.gateways.speech.supertonic_tts import write_wav
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--sid", type=int, default=0)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    t = SherpaTTS(a.model_dir, threads=a.threads)
    w, sr = t.synth(a.text, sid=a.sid, speed=a.speed)
    write_wav(a.out, w, sr)
    print(json.dumps(dict(t.last_info, load_s=round(t.load_seconds, 2), sr=sr, speakers=t.num_speakers)))
