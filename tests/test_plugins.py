import io
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

from gatemoe.config import load_config
from gatemoe.gateways.speech import SpeechGateway
from gatemoe.gateways.speech.plugin_tts import PluginTTS, plugin_for


def _wav_bytes(seconds=0.5, sr=16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        t = np.arange(int(seconds * sr)) / sr
        w.writeframes((0.3 * np.sin(2 * np.pi * 220 * t) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def test_command_plugin_synthesises(tmp_path):
    spec = {"name": "tone", "type": "command",
            "argv": ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=0.7", "-y", "{out_wav}"]}
    wav, sr = PluginTTS(spec).synth("hello", lang="en")
    assert sr > 0 and 0.6 < len(wav) / sr < 0.8


def test_http_plugin_posts_template_and_decodes():
    seen = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            import json
            seen.update(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = _wav_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        spec = {"name": "fish", "type": "http", "url": f"http://127.0.0.1:{srv.server_port}/v1/tts",
                "body": {"text": "{text}", "reference_id": "{voice}", "format": "wav"}}
        wav, sr = PluginTTS(spec).synth("ನಮಸ್ಕಾರ", lang="kn", voice="host_a")
        assert seen == {"text": "ನಮಸ್ಕಾರ", "reference_id": "host_a", "format": "wav"}
        assert sr == 16000 and len(wav) == 8000
    finally:
        srv.shutdown()


def test_plugins_route_before_builtins(tmp_path):
    cfg = load_config(overrides={"paths": {"data_dir": str(tmp_path)},
                                 "speech": {"plugins": [{"name": "fish", "type": "http", "url": "http://x",
                                                         "languages": ["kn", "ta"], "voices": {"A": "a", "B": "b"},
                                                             "prefer": True}]}})
    sg = SpeechGateway(cfg)
    assert sg.engine_for("kn") == "plugin:fish" and sg.engine_for("en") is None   # no built-in models installed
    assert "plugin:fish" in sg.available()
    assert plugin_for("ta", cfg["speech.plugins"])["name"] == "fish" and plugin_for("en", cfg["speech.plugins"]) is None


def test_failing_preferred_plugin_falls_back(tmp_path):
    from gatemoe.gateways.speech.podcast import Engines
    tone = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=0.5", "-y", "{out_wav}"]
    cfg = load_config(overrides={"paths": {"data_dir": str(tmp_path)}, "speech": {"plugins": [
        {"name": "gpu-box", "type": "http", "url": "http://127.0.0.1:9/v1/tts", "languages": ["*"], "prefer": True,
         "timeout_s": 2},
        {"name": "tone", "type": "command", "argv": tone, "languages": ["*"]}]}})
    engines = SpeechGateway(cfg).engines
    assert isinstance(engines, Engines)
    wav, sr, info = engines.synth("hello", "en", "A")      # gpu-box refuses the connection
    assert info["model"] == "tone" and len(wav) > 0 and "gpu-box" in engines._down
