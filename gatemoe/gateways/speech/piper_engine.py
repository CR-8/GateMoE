"""Wrapper for the piper-tts package (GPL-3.0, bundles espeak-ng) - needed for
newer Piper voices whose phoneme map has multi-codepoint tokens (mr_IN-google,
bn_BD-google), which sherpa-onnx 1.13.8 cannot read."""
from __future__ import annotations
import time
import numpy as np
import onnxruntime as ort
from piper import PiperVoice, SynthesisConfig


class PiperEngine:
    def __init__(self, onnx_path: str, threads: int = 2):
        t0 = time.perf_counter()
        self.voice = PiperVoice.load(onnx_path)
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
        self.voice.session = ort.InferenceSession(onnx_path, so, providers=["CPUExecutionProvider"])
        self.load_seconds = time.perf_counter() - t0
        self.sample_rate = self.voice.config.sample_rate
        self.num_speakers = self.voice.config.num_speakers
        self.last_info = {}

    def synth(self, text: str, sid: int = 0, speed: float = 1.0) -> tuple[np.ndarray, int]:
        t0 = time.perf_counter()
        cfg = SynthesisConfig(speaker_id=sid if self.num_speakers > 1 else None,
                              length_scale=1.0 / speed, normalize_audio=False)
        parts = [c.audio_float_array for c in self.voice.synthesize(text, syn_config=cfg)]
        wav = np.concatenate(parts).astype(np.float32)
        el = time.perf_counter() - t0
        self.last_info = {"synth_s": el, "audio_s": len(wav) / self.sample_rate, "rtf": el / (len(wav) / self.sample_rate)}
        return wav, self.sample_rate
