"""Pluggable TTS engines, configured in YAML — no code change needed to add a new voice model.

Two kinds:

* ``http``: POST a JSON body to a local TTS server and receive audio bytes. Works with the
  Fish Speech / Fish Audio API server (``POST /v1/tts``), Coqui/XTTS servers, Kokoro-FastAPI,
  OpenAI-style ``/v1/audio/speech`` servers, ... The body is a template whose string values may
  contain ``{text}``, ``{voice}`` and ``{lang}``.
* ``command``: run any program that writes a WAV. argv may contain ``{text}``, ``{text_file}``,
  ``{out_wav}``, ``{voice}`` and ``{lang}``.

Example (speech.plugins in your gatemoe.yaml)::

    speech:
      plugins:
        - name: fish-s2
          type: http
          url: http://127.0.0.1:8080/v1/tts
          body: {text: "{text}", reference_id: "{voice}", format: wav}
          languages: ["*"]           # or a list such as [en, hi, kn]
          voices: {A: host_a, B: host_b}
          prefer: true               # try before the built-in engines for these languages
        - name: espeak
          type: command
          argv: [espeak-ng, -v, "{lang}", -w, "{out_wav}", -f, "{text_file}"]
          languages: [en]
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import urllib.request
import wave
from pathlib import Path

import numpy as np


def _fill(obj, values: dict):
    if isinstance(obj, str):
        out = obj
        for k, v in values.items():
            out = out.replace("{" + k + "}", str(v))
        return out
    if isinstance(obj, dict):
        return {k: _fill(v, values) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fill(v, values) for v in obj]
    return obj


def _to_pcm16_mono(src: Path, ffmpeg: str) -> tuple[np.ndarray, int]:
    """Decode whatever the engine produced (wav/mp3/ogg/float wav) into 16-bit mono."""
    dst = src.with_suffix(".pcm16.wav")
    subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(src), "-ac", "1", "-c:a", "pcm_s16le", str(dst)],
                   check=True, capture_output=True, timeout=300)
    with wave.open(str(dst), "rb") as w:
        sr, n = w.getframerate(), w.getnframes()
        x = np.frombuffer(w.readframes(n), dtype="<i2").astype(np.float32) / 32768.0
    return x, sr


class PluginTTS:
    def __init__(self, spec: dict, ffmpeg: str = "ffmpeg"):
        if spec.get("type") not in ("http", "command"):
            raise ValueError(f"TTS plugin {spec.get('name')!r}: type must be 'http' or 'command'")
        self.spec = spec
        self.ffmpeg = ffmpeg
        self.timeout = float(spec.get("timeout_s", 600))
        self.last_info: dict = {}

    def synth(self, text: str, lang: str = "en", voice=None, speed: float = 1.0) -> tuple[np.ndarray, int]:
        values = {"text": text, "voice": voice or "", "lang": lang}
        with tempfile.TemporaryDirectory(prefix="gm_plugin_tts_") as tmp:
            raw = Path(tmp) / "out.audio"
            if self.spec["type"] == "http":
                body = json.dumps(_fill(self.spec.get("body", {"text": "{text}"}), values)).encode("utf-8")
                headers = {"Content-Type": "application/json", **self.spec.get("headers", {})}
                req = urllib.request.Request(self.spec["url"], data=body, headers=headers, method="POST")
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # local server only
                with opener.open(req, timeout=self.timeout) as resp:
                    raw.write_bytes(resp.read())
            else:
                text_file = Path(tmp) / "text.txt"
                text_file.write_text(text, encoding="utf-8")
                out_wav = Path(tmp) / "out.wav"
                argv = _fill(list(self.spec["argv"]), {**values, "text_file": text_file, "out_wav": out_wav})
                subprocess.run(argv, check=True, capture_output=True, timeout=self.timeout)
                raw = out_wav
            return _to_pcm16_mono(raw, self.ffmpeg)


def plugin_for(lang: str, plugins: list[dict]) -> dict | None:
    for p in plugins or []:
        langs = p.get("languages") or ["*"]
        if p.get("prefer", True) and ("*" in langs or lang in langs):
            return p
    return None
