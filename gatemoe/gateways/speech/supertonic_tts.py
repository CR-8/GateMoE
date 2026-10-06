"""Standalone Supertonic-3 TTS (numpy + onnxruntime + stdlib only).

Text preprocessing, language tags, sentence chunking and the flow-matching
inference loop are ported from the official ``supertonic`` PyPI package
v1.3.1 (core.py / utils.py / config.py / pipeline.py), which carries:

    MIT License

    Copyright (c) 2025 Supertone Inc.

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.

The model weights (Supertone/supertonic-3 on Hugging Face) are under the
BigScience OpenRAIL-M licence, not MIT.

Differences from the package (deliberate):
  * no ``supertonic`` / soundfile / huggingface_hub dependency;
  * output is trimmed to the predicted duration (the package returns the full
    vocoder frame, i.e. a few ms of tail);
  * sentence splitting also understands ``।`` ``॥`` ``。`` ``！`` ``？`` ``؟``
    and over-long sentences are split again at commas / spaces;
  * unsupported characters are replaced (small symbol map) or dropped with a
    report instead of raising ValueError;
  * the noise is drawn from a seeded ``np.random.Generator`` (reproducible).

Usage:
    tts = SupertonicTTS("/models/supertonic3", threads=4)
    wav, sr = tts.synth("नमस्ते दुनिया", lang="hi", voice="F2", steps=5, seed=1)
    write_wav("out.wav", wav, sr)
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import wave
from pathlib import Path
from typing import Optional
from unicodedata import normalize

import numpy as np
import onnxruntime as ort

log = logging.getLogger("supertonic_tts")

# ----------------------------------------------------------------- constants
SUPPORTED_LANGUAGES = [
    "en", "ko", "ja", "ar", "bg", "cs", "da", "de", "el", "es", "et", "fi",
    "fr", "hi", "hr", "hu", "id", "it", "lt", "lv", "nl", "pl", "pt", "ro",
    "ru", "sk", "sl", "sv", "tr", "uk", "vi",
]
UNKNOWN_LANGUAGE = "na"
AVAILABLE_LANGUAGES = SUPPORTED_LANGUAGES + [UNKNOWN_LANGUAGE]
VOICES = ["F1", "F2", "F3", "F4", "F5", "M1", "M2", "M3", "M4", "M5"]
MIN_SPEED, MAX_SPEED = 0.7, 2.0
DEFAULT_MAX_CHUNK = 300
SHORT_CHUNK_LANGS = {"ko": 120, "ja": 120}   # package uses 120 for ko only
EXPRESSION_TAGS = ("<laugh>", "<breath>", "<sigh>")

# ------------------------------------------------- preprocessing (from core.py)
_EMOJI_PATTERN = re.compile(
    "[\U0001f600-\U0001f64f"
    "\U0001f300-\U0001f5ff"
    "\U0001f680-\U0001f6ff"
    "\U0001f700-\U0001f77f"
    "\U0001f780-\U0001f7ff"
    "\U0001f800-\U0001f8ff"
    "\U0001f900-\U0001f9ff"
    "\U0001fa00-\U0001fa6f"
    "\U0001fa70-\U0001faff"
    "☀-⛿"
    "✀-➿"
    "\U0001f1e6-\U0001f1ff]+",
    flags=re.UNICODE,
)
_SYMBOL_REPLACEMENTS = {
    "–": "-", "‑": "-", "—": "-", "¯": " ", "_": " ",
    "“": '"', "”": '"', "‘": "'", "’": "'", "´": "'",
    "`": "'", "[": " ", "]": " ", "|": " ", "/": " ", "#": " ",
    "→": " ", "←": " ",
}
_SPECIAL_SYMBOLS_PATTERN = re.compile(r"[♥☆♡©\\]")
_PUNCTUATION_SPACING_PATTERNS = [
    (re.compile(r" ,"), ","), (re.compile(r" \."), "."), (re.compile(r" !"), "!"),
    (re.compile(r" \?"), "?"), (re.compile(r" ;"), ";"), (re.compile(r" :"), ":"),
    (re.compile(r" '"), "'"),
]
_DUPLICATE_QUOTES_PATTERN = re.compile(r'(["\'\`])\1+')
_WHITESPACE_PATTERN = re.compile(r"\s+")
_ENDING_PUNCTUATION_PATTERN = re.compile(r"[.!?;:,'\"')\]}…。」』】〉》›»]$")
_ABBREVIATIONS = {"@": " at ", "e.g.,": "for example, ", "i.e.,": "that is, "}

# Extra (GateMoE): characters the indexer lacks but that appear in technical
# text.  Applied *after* NFKD, only to characters the model cannot index.
_FALLBACK_CHARS = {
    "॥": "।",     # ॥ -> ।
    "≠": " != ",       # ≠ (NFKD gives '=' + U+0338)
    "̸": "",           # combining long solidus overlay (from ≠ ≮ ≯ ...)
    "≤": " <= ", "≥": " >= ",
    "∑": " ∑ ", "∫": " ∫ ", "∂": " ∂ ",   # spaced; en word map below, else dropped
    "−": "-",          # minus sign
    "·": " ", "•": " ", "●": " ",
    "„": '"', "«": '"', "»": '"',
}
# Negated relations decompose under NFKD into '=' + U+0338 etc.; map them first.
_PRE_NFKD = {"\u2260": " != ", "\u226e": " >= ", "\u226f": " <= "}
# English words for maths symbols (only applied when lang == "en").
_EN_SYMBOL_WORDS = [
    (re.compile(r"\s*<=\s*"), " less than or equal to "),
    (re.compile(r"\s*>=\s*"), " greater than or equal to "),
    (re.compile(r"\s*!=\s*"), " not equal to "),
    (re.compile(r"(?<=\w)\s*=\s*(?=[\w(\-])"), " equals "),
    (re.compile(r"(?<=\w)\s*\+\s*(?=[\w(])"), " plus "),
    (re.compile(r"(?<=\d)\s*[x×]\s*(?=\d)"), " times "),
    (re.compile(r"(?<=\w)\s*÷\s*(?=\w)"), " divided by "),
    (re.compile(r"(?<=\w)\s*\^\s*2\b"), " squared"),
    (re.compile(r"(?<=\w)\s*\^\s*3\b"), " cubed"),
    (re.compile(r"(?<=\w)\s*\^\s*(?=\w)"), " to the power "),
    (re.compile(r"√"), " square root of "),
    (re.compile(r"\u2211"), " sum "), (re.compile(r"\u222b"), " integral "),
    (re.compile(r"\u2202"), " partial "),
    (re.compile(r"(?<=\d)\s*%"), " percent"),
    (re.compile(r"(?<=\d)\s*°\s*C\b"), " degrees Celsius"),
    (re.compile(r"(?<=\d)\s*°"), " degrees"),
    (re.compile(r"\s*<\s*"), " less than "),
    (re.compile(r"\s*>\s*"), " greater than "),
]

# ------------------------------------------- sentence chunking (from utils.py)
_COMMON_ABBREVIATIONS_PATTERN = (
    r"(?<!Mr\.)(?<!Mrs\.)(?<!Ms\.)(?<!Dr\.)(?<!Prof\.)(?<!Sr\.)(?<!Jr\.)"
    r"(?<!Ph\.D\.)(?<!etc\.)(?<!e\.g\.)(?<!i\.e\.)(?<!vs\.)(?<!Inc\.)"
    r"(?<!Ltd\.)(?<!Co\.)(?<!Corp\.)(?<!St\.)(?<!Ave\.)(?<!Blvd\.)"
    r"(?<!\b[A-Z]\.)"
    r"(?<=[.!?])\s+"
)
# GateMoE extension: sentence enders of other scripts (no space needed after).
_EXTRA_SENTENCE_SPLIT = re.compile(r"(?<=[।॥。！？؟…])\s*")
_CLAUSE_SPLIT = re.compile(r"(?<=[,;:،、，；])\s*")


def _split_sentences(paragraph: str) -> list[str]:
    out = []
    for s in re.split(_COMMON_ABBREVIATIONS_PATTERN, paragraph):
        out.extend(x for x in _EXTRA_SENTENCE_SPLIT.split(s) if x and x.strip())
    return [s.strip() for s in out if s.strip()]


def _split_long(sentence: str, max_len: int) -> list[str]:
    """Split one over-long sentence at clause punctuation, then at spaces."""
    if len(sentence) <= max_len:
        return [sentence]
    pieces, cur = [], ""
    for part in _CLAUSE_SPLIT.split(sentence):
        if not part:
            continue
        if len(part) > max_len:            # still too long: split on spaces
            words = part.split(" ")
            if len(words) == 1:            # no spaces (CJK): hard cut
                words = [part[i:i + max_len] for i in range(0, len(part), max_len)]
            for w in words:
                if cur and len(cur) + len(w) + 1 > max_len:
                    pieces.append(cur)
                    cur = w
                else:
                    cur = (cur + " " + w) if cur else w
            continue
        if cur and len(cur) + len(part) + 1 > max_len:
            pieces.append(cur)
            cur = part
        else:
            cur = _join(cur, part)
    if cur:
        pieces.append(cur)
    return [p.strip() for p in pieces if p.strip()]


_NO_SPACE_AFTER = set("\u3002\uff01\uff1f\u3001\uff0c")


def _join(a: str, b: str) -> str:
    if not a:
        return b
    return a + ("" if a[-1] in _NO_SPACE_AFTER else " ") + b


def chunk_text(text: str, max_len: int = DEFAULT_MAX_CHUNK) -> list[str]:
    """Paragraph -> sentence -> greedy packing into chunks of <= max_len chars."""
    if max_len < 10:
        raise ValueError("max_len must be >= 10")
    chunks: list[str] = []
    for para in re.split(r"\n\s*\n+", text.strip()):
        para = para.strip()
        if not para:
            continue
        cur = ""
        for sent in _split_sentences(para):
            for s in _split_long(sent, max_len):
                if cur and len(cur) + len(s) + 1 > max_len:
                    chunks.append(cur)
                    cur = s
                else:
                    cur = _join(cur, s)
        if cur.strip():
            chunks.append(cur.strip())
    return chunks


# ------------------------------------------------------------------ helpers
def _length_to_mask(lengths: np.ndarray, max_len: Optional[int] = None) -> np.ndarray:
    max_len = int(max_len or lengths.max())
    ids = np.arange(0, max_len)
    mask = (ids < np.expand_dims(lengths, axis=1)).astype(np.float32)
    return mask.reshape(-1, 1, max_len)


def write_wav(path: str | os.PathLike, wav: np.ndarray, sr: int) -> None:
    """Write mono float32 [-1,1] audio as 16-bit PCM WAV (stdlib only)."""
    pcm = (np.clip(np.asarray(wav, dtype=np.float32).reshape(-1), -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(int(sr))
        f.writeframes(pcm.tobytes())


class Style:
    __slots__ = ("ttl", "dp")

    def __init__(self, ttl: np.ndarray, dp: np.ndarray):
        self.ttl, self.dp = ttl, dp


# --------------------------------------------------------------------- engine
class SupertonicTTS:
    """Load once, synthesize many.  Thread count is fixed at load time."""

    sample_rate: int

    def __init__(self, model_dir: str | os.PathLike, threads: int = 4,
                 voices_dir: str | os.PathLike | None = None, variant: str = "fp32"):
        """variant="int8" loads <name>.int8.onnx where present (k2-fsa's
        sherpa-onnx-supertonic-3-tts-int8 release: vector_estimator + vocoder
        dynamically quantised, same I/O) and falls back to fp32 <name>.onnx."""
        model_dir = Path(model_dir)
        onnx_dir = model_dir / "onnx" if (model_dir / "onnx").is_dir() else model_dir
        self.voices_dir = Path(voices_dir) if voices_dir else (
            model_dir / "voice_styles" if (model_dir / "voice_styles").is_dir()
            else onnx_dir.parent / "voice_styles")
        cfg = json.loads((onnx_dir / "tts.json").read_text())
        self.sample_rate = int(cfg["ae"]["sample_rate"])
        self.base_chunk_size = int(cfg["ae"]["base_chunk_size"])
        self.chunk_compress_factor = int(cfg["ttl"]["chunk_compress_factor"])
        self.ldim = int(cfg["ttl"]["latent_dim"])
        self.indexer = json.loads((onnx_dir / "unicode_indexer.json").read_text())
        self._idx = np.asarray(self.indexer, dtype=np.int64)
        so = ort.SessionOptions()
        so.intra_op_num_threads = int(threads)
        so.inter_op_num_threads = 1
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        prov = ["CPUExecutionProvider"]

        def path(name: str) -> str:
            q = onnx_dir / f"{name}.int8.onnx"
            return str(q if (variant == "int8" and q.exists()) else onnx_dir / f"{name}.onnx")

        t0 = time.perf_counter()
        self.files = {n: path(n) for n in ("duration_predictor", "text_encoder", "vector_estimator", "vocoder")}
        self.dp_ort = ort.InferenceSession(self.files["duration_predictor"], so, providers=prov)
        self.text_enc_ort = ort.InferenceSession(self.files["text_encoder"], so, providers=prov)
        self.vector_est_ort = ort.InferenceSession(self.files["vector_estimator"], so, providers=prov)
        self.vocoder_ort = ort.InferenceSession(self.files["vocoder"], so, providers=prov)
        self.load_seconds = time.perf_counter() - t0
        self.threads = int(threads)
        self._styles: dict[str, Style] = {}
        self.last_info: dict = {}

    # ------------------------------------------------------------ voices
    def voice(self, name: str) -> Style:
        if name not in self._styles:
            p = Path(name) if name.endswith(".json") else self.voices_dir / f"{name}.json"
            j = json.loads(Path(p).read_text())
            arr = lambda d: np.asarray(d["data"], dtype=np.float32).reshape(*d["dims"])
            self._styles[name] = Style(arr(j["style_ttl"]), arr(j["style_dp"]))
        return self._styles[name]

    @staticmethod
    def supports(lang: str) -> bool:
        return lang in SUPPORTED_LANGUAGES

    # ----------------------------------------------------- preprocessing
    def _indexable(self, ch: str) -> bool:
        o = ord(ch)
        return o < len(self.indexer) and self.indexer[o] != -1

    def preprocess(self, text: str, lang: str) -> tuple[str, list[str]]:
        """Faithful port of UnicodeProcessor._preprocess_text + GateMoE char fallback.

        Returns (tagged_text, dropped_chars)."""
        if lang not in AVAILABLE_LANGUAGES:
            raise ValueError(f"Invalid language {lang!r}; use one of {AVAILABLE_LANGUAGES}")
        for k, v in _PRE_NFKD.items():           # GateMoE: before NFKD splits them
            text = text.replace(k, v)
        try:
            text = normalize("NFKD", text)
        except Exception as e:  # pragma: no cover
            log.warning("NFKD failed: %s", e)
        text = _EMOJI_PATTERN.sub("", text)
        for old, new in _SYMBOL_REPLACEMENTS.items():
            text = text.replace(old, new)
        text = _SPECIAL_SYMBOLS_PATTERN.sub("", text)
        for k, v in _ABBREVIATIONS.items():
            text = text.replace(k, v)
        # --- GateMoE: protect expression tags, then handle maths symbols
        tags = {}
        for i, tag in enumerate(EXPRESSION_TAGS):
            if tag in text:
                key = f"\x00{i}\x00"
                tags[key] = tag
                text = text.replace(tag, key)
        text = "".join(_FALLBACK_CHARS.get(c, c) if not self._indexable(c) else c for c in text)
        if lang == "en":
            for pat, rep in _EN_SYMBOL_WORDS:
                text = pat.sub(rep, text)
        else:   # keep maths '<' '>' but strip anything that looks like a stray tag
            text = re.sub(r"<\s*/?\s*[A-Za-z]+\s*>", " ", text)
        text = _WHITESPACE_PATTERN.sub(" ", text)      # so \n, \t are not reported
        dropped = sorted({c for c in text if c != "\x00" and not self._indexable(c)})
        if dropped:
            bad = set(dropped)
            text = "".join(" " if c in bad else c for c in text)
        for key, tag in tags.items():
            text = text.replace(key, tag)
        text = text.replace("\x00", "")
        # --- back to the package order
        for pattern, replacement in _PUNCTUATION_SPACING_PATTERNS:
            text = pattern.sub(replacement, text)
        text = _DUPLICATE_QUOTES_PATTERN.sub(r"\1", text)
        text = _WHITESPACE_PATTERN.sub(" ", text).strip()
        if not _ENDING_PUNCTUATION_PATTERN.search(text):
            text += "."
        return f"<{lang}>{text}</{lang}>", dropped

    # --------------------------------------------------------- inference
    def _infer(self, tagged: str, style: Style, steps: int, speed: float,
               rng: np.random.Generator) -> np.ndarray:
        ids = self._idx[np.frombuffer(tagged.encode("utf-32-le"), dtype=np.uint32).astype(np.int64)]
        text_ids = ids.reshape(1, -1)
        text_mask = np.ones((1, 1, text_ids.shape[1]), dtype=np.float32)
        dur, *_ = self.dp_ort.run(None, {"text_ids": text_ids, "style_dp": style.dp, "text_mask": text_mask})
        dur = dur / speed
        text_emb, *_ = self.text_enc_ort.run(None, {"text_ids": text_ids, "style_ttl": style.ttl, "text_mask": text_mask})
        n_samples = int(dur.max() * self.sample_rate)
        chunk = self.base_chunk_size * self.chunk_compress_factor
        latent_len = max(1, (n_samples + chunk - 1) // chunk)
        xt = rng.standard_normal((1, self.ldim * self.chunk_compress_factor, latent_len), dtype=np.float32)
        latent_mask = np.ones((1, 1, latent_len), dtype=np.float32)
        total = np.array([steps], dtype=np.float32)
        for step in range(steps):
            xt, *_ = self.vector_est_ort.run(None, {
                "noisy_latent": xt, "text_emb": text_emb, "style_ttl": style.ttl,
                "text_mask": text_mask, "latent_mask": latent_mask,
                "current_step": np.array([step], dtype=np.float32), "total_step": total})
        wav, *_ = self.vocoder_ort.run(None, {"latent": xt})
        return wav[0, :n_samples].astype(np.float32, copy=False)

    def synth(self, text: str, lang: str = "en", voice: str = "F2", steps: int = 5,
              speed: float = 1.0, seed: Optional[int] = 0, silence: float = 0.3,
              max_chunk_len: Optional[int] = None) -> tuple[np.ndarray, int]:
        """Text -> (float32 mono waveform, sample_rate).  Long text is chunked."""
        if not text or not text.strip():
            raise ValueError("empty text")
        if lang not in AVAILABLE_LANGUAGES:
            raise ValueError(f"Supertonic-3 has no language {lang!r}")
        if not (1 <= steps <= 100):
            raise ValueError("steps must be 1..100")
        speed = float(min(MAX_SPEED, max(MIN_SPEED, speed)))
        style = self.voice(voice)
        rng = np.random.default_rng(seed)
        max_len = max_chunk_len or SHORT_CHUNK_LANGS.get(lang, DEFAULT_MAX_CHUNK)
        chunks = chunk_text(text, max_len)
        gap = np.zeros(int(silence * self.sample_rate), dtype=np.float32)
        parts, dropped_all, used = [], set(), []
        t0 = time.perf_counter()
        for ch in chunks:
            tagged, dropped = self.preprocess(ch, lang)
            dropped_all.update(dropped)
            body = tagged[len(lang) + 2: -(len(lang) + 3)]
            if not any(c.isalnum() for c in body):
                continue                       # nothing speakable left
            if parts:
                parts.append(gap)
            parts.append(self._infer(tagged, style, steps, speed, rng))
            used.append(tagged)
        if not parts:
            raise ValueError("no speakable text after preprocessing")
        wav = np.concatenate(parts)
        el = time.perf_counter() - t0
        dur = len(wav) / self.sample_rate
        self.last_info = {"chunks": used, "dropped_chars": sorted(dropped_all),
                          "synth_s": el, "audio_s": dur, "rtf": el / max(dur, 1e-9)}
        if dropped_all:
            log.warning("dropped unsupported chars for %s: %s", lang, sorted(dropped_all))
        return wav, self.sample_rate


# ---------------------------------------------------------------------- CLI
def _main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Supertonic-3 TTS (standalone)")
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--voices-dir")
    ap.add_argument("--text", required=True)
    ap.add_argument("--lang", default="en")
    ap.add_argument("--voice", default="F2")
    ap.add_argument("--steps", type=int, default=5)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--variant", default="fp32", choices=["fp32", "int8"])
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    tts = SupertonicTTS(a.model_dir, threads=a.threads, voices_dir=a.voices_dir, variant=a.variant)
    wav, sr = tts.synth(a.text, lang=a.lang, voice=a.voice, steps=a.steps, speed=a.speed, seed=a.seed)
    write_wav(a.out, wav, sr)
    info = dict(tts.last_info, load_s=round(tts.load_seconds, 2), out=a.out, sr=sr)
    print(json.dumps(info, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
