"""Meta MMS-TTS (VITS) runner for ONNX exports - numpy + onnxruntime + stdlib.

Preferred input: files written by ``export_mms_onnx.py`` (sherpa-onnx style
signature x, x_length, noise_scale, length_scale, noise_scale_w -> y, so speed
and noise are runtime inputs).  The transformers.js/optimum signature
(input_ids, attention_mask -> waveform) is also accepted, but note that the
public ``onnx-community/mms-tts-kan-ONNX`` export FAILS under onnxruntime
1.30 for every input length (broadcast error 180 by 181 in
text_encoder/encoder/layers.0/feed_forward/Mul_1), so re-export instead.  The model dir must contain ``vocab.json``,
``tokenizer_config.json`` and ``model.onnx`` (or ``onnx/model*.onnx``).

The tokenizer is a port of transformers' ``VitsTokenizer`` (Apache-2.0) for
the ``is_uroman == False`` / ``phonemize == False`` case, which is what the
Indic MMS checkpoints (kan, tam, tel, ben, mar, mal, guj, pan, ory, asm, hin)
use: lower-case, drop characters that are not in the vocabulary, intersperse
the blank id 0 (``add_blank``).

MMS model weights are CC-BY-NC-4.0 (non-commercial use only).

Punctuation is not in the MMS vocabularies, so this module splits text into
sentences / clauses itself and inserts silences between them.
"""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
import wave
from pathlib import Path
from typing import Optional

import numpy as np
import onnxruntime as ort

log = logging.getLogger("mms_tts")

# Sentence and clause boundaries (Latin, Devanagari/Bengali danda, Arabic, CJK).
_SENT_END = re.compile(r"(?<=[.!?।॥؟。！？])\s+|(?<=[।॥。！？])|\n+")
_CLAUSE_END = re.compile(r"(?<=[,;:،、，])\s+")


class MMSTTS:
    def __init__(self, model_dir: str | Path, threads: int = 4, onnx_file: Optional[str] = None):
        d = Path(model_dir)
        self.vocab: dict[str, int] = json.loads((d / "vocab.json").read_text(encoding="utf-8"))
        tc = json.loads((d / "tokenizer_config.json").read_text(encoding="utf-8"))
        if tc.get("is_uroman") or tc.get("phonemize"):
            raise ValueError(f"{d}: uroman/phonemize MMS models are not supported by this runner")
        self.add_blank = bool(tc.get("add_blank", True))
        self.normalize = bool(tc.get("normalize", True))
        self.language = tc.get("language", "")
        cfg_p = d / "config.json"
        self.sample_rate = int(json.loads(cfg_p.read_text())["sampling_rate"]) if cfg_p.exists() else 16000
        if onnx_file is None:
            for cand in ("model.onnx", "model.int8.onnx", "model_quantized.onnx",
                         "onnx/model_quantized.onnx", "onnx/model.onnx"):
                if (d / cand).exists():
                    onnx_file = cand
                    break
        self.onnx_path = d / onnx_file
        so = ort.SessionOptions()
        so.intra_op_num_threads = int(threads)
        so.inter_op_num_threads = 1
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        t0 = time.perf_counter()
        self.sess = ort.InferenceSession(str(self.onnx_path), so, providers=["CPUExecutionProvider"])
        self.load_seconds = time.perf_counter() - t0
        self._sherpa_sig = "x" in {i.name for i in self.sess.get_inputs()}
        self._multi = sorted((k for k in self.vocab if len(k) > 1), key=len, reverse=True)
        self.last_info: dict = {}

    # ----------------------------------------------------------- tokenizer
    def normalize_text(self, text: str) -> str:
        """VitsTokenizer.normalize_text: keep vocab tokens verbatim, lower-case the rest."""
        out, i = [], 0
        while i < len(text):
            for w in self._multi:
                if text.startswith(w, i):
                    out.append(w)
                    i += len(w)
                    break
            else:
                ch = text[i]
                out.append(ch if ch in self.vocab else ch.lower())
                i += 1
        return "".join(out)

    def _fix_unicode(self, text: str) -> str:
        """NFC, then decompose (NFD) only the characters the vocab lacks
        (e.g. precomposed nukta letters U+0958.. vs. base + U+093C)."""
        text = unicodedata.normalize("NFC", text)
        out = []
        for ch in text:
            if ch not in self.vocab:
                d = unicodedata.normalize("NFD", ch)
                if d != ch and all(c in self.vocab for c in d):
                    out.append(d)
                    continue
            out.append(ch)
        return "".join(out)

    def tokenize(self, text: str) -> tuple[list[int], list[str]]:
        text = self._fix_unicode(re.sub(r"\s+", " ", text))
        if self.normalize:
            text = self.normalize_text(text)
        dropped = sorted({c for c in text if c not in self.vocab})
        kept = "".join(c for c in text if c in self.vocab).strip()
        ids = [self.vocab[c] for c in kept]
        if self.add_blank:
            inter = [0] * (len(ids) * 2 + 1)
            inter[1::2] = ids
            ids = inter
        return ids, dropped

    # ----------------------------------------------------------- synthesis
    def _run(self, ids: list[int], speed: float = 1.0, noise_scale: float = 0.667,
             noise_scale_w: float = 0.8) -> np.ndarray:
        x = np.asarray([ids], dtype=np.int64)
        if self._sherpa_sig:
            f = lambda v: np.asarray([v], dtype=np.float32)
            wav = self.sess.run(None, {"x": x, "x_length": np.asarray([x.shape[1]], dtype=np.int64),
                                       "noise_scale": f(noise_scale), "length_scale": f(1.0 / speed),
                                       "noise_scale_w": f(noise_scale_w)})[0]
        else:
            wav = self.sess.run(["waveform"], {"input_ids": x, "attention_mask": np.ones_like(x)})[0]
        return wav.reshape(-1).astype(np.float32, copy=False)

    @staticmethod
    def split(text: str) -> list[tuple[str, float]]:
        """-> [(piece, pause_after_seconds)] (sentence 0.30 s, clause 0.12 s)."""
        out = []
        for sent in _SENT_END.split(text.strip()):
            if not sent or not sent.strip():
                continue
            clauses = [c for c in _CLAUSE_END.split(sent.strip()) if c.strip()]
            for j, c in enumerate(clauses):
                out.append((c.strip(), 0.12 if j < len(clauses) - 1 else 0.30))
        return out

    def synth(self, text: str, speed: float = 1.0, max_tokens: int = 600,
              noise_scale: float = 0.667, noise_scale_w: float = 0.8) -> tuple[np.ndarray, int]:
        """Text -> (float32 mono 16 kHz waveform, sample_rate).

        ``speed`` maps to length_scale = 1/speed (only for export_mms_onnx.py
        files; the optimum signature has the scales baked in)."""
        if speed != 1.0 and not self._sherpa_sig:
            log.info("this ONNX has a fixed speaking_rate; apply speed with atempo")
        t0 = time.perf_counter()
        parts, dropped_all, used = [], set(), []
        pieces = self.split(text)
        for k, (piece, pause) in enumerate(pieces):
            ids, dropped = self.tokenize(piece)
            dropped_all.update(dropped)
            if len(ids) <= 1 or not any(i != 0 and i != self.vocab.get(" ") for i in ids):
                continue
            # very long clause without punctuation: split on spaces
            if len(ids) > max_tokens:
                words, cur, subs = piece.split(), "", []
                for w in words:
                    if len(cur) + len(w) + 1 > max_tokens // 2:
                        subs.append(cur)
                        cur = w
                    else:
                        cur = (cur + " " + w).strip()
                subs.append(cur)
                id_lists = [self.tokenize(s)[0] for s in subs if s]
            else:
                id_lists = [ids]
            for il in id_lists:
                parts.append(self._run(il, speed, noise_scale, noise_scale_w))
            used.append(piece)
            if k < len(pieces) - 1:
                parts.append(np.zeros(int(pause * self.sample_rate), dtype=np.float32))
        if not parts:
            raise ValueError("no speakable text for MMS-%s (dropped: %s)" % (self.language, sorted(dropped_all)))
        wav = np.concatenate(parts)
        el = time.perf_counter() - t0
        dur = len(wav) / self.sample_rate
        # drop characters that are just punctuation / spaces from the report
        dropped_all = {c for c in dropped_all if not re.match(r"[\s.,;:!?\-'\"()।॥]", c)}
        self.last_info = {"pieces": used, "dropped_chars": sorted(dropped_all),
                          "synth_s": el, "audio_s": dur, "rtf": el / max(dur, 1e-9)}
        if dropped_all:
            log.warning("MMS-%s dropped chars not in vocab: %s", self.language, sorted(dropped_all))
        return wav, self.sample_rate


def write_wav(path, wav: np.ndarray, sr: int) -> None:
    pcm = (np.clip(np.asarray(wav, dtype=np.float32).reshape(-1), -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(int(sr))
        f.writeframes(pcm.tobytes())


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--onnx-file")
    ap.add_argument("--text", required=True)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    t = MMSTTS(a.model_dir, threads=a.threads, onnx_file=a.onnx_file)
    w, sr = t.synth(a.text)
    write_wav(a.out, w, sr)
    print(json.dumps(dict(t.last_info, load_s=round(t.load_seconds, 2), onnx=str(t.onnx_path)), ensure_ascii=False))
