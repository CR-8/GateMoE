#!/usr/bin/env python3
"""Stand-in for llama.cpp's llama-server used by the test-suite (no model needed).

Serves /health, /v1/systemone (Clef-style answers) and /v1/chat/completions (returns a
minimal instance of the requested JSON schema), so the whole pipeline can be exercised.
"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

args = sys.argv[1:]
port = int(args[args.index("--port") + 1])
model = args[args.index("-m") + 1]
STARTED = time.time()
LOAD_DELAY = 0.3


def instance(schema, key=""):
    t = schema.get("type")
    if "enum" in schema:
        return schema["enum"][0]
    if t == "object":
        return {k: instance(v, k) for k, v in schema.get("properties", {}).items()
                if k in schema.get("required", [])}
    if t == "array":
        return [instance(schema["items"], key) for _ in range(max(1, schema.get("minItems", 1)))]
    if t == "string":
        base = {"narration": "This is spoken narration.", "title": "Test title"}.get(key, f"{key or 'text'} value")
        return base.ljust(schema.get("minLength", 0), ".")[: schema.get("maxLength", 10_000)]
    if t == "integer":
        return schema.get("minimum", 0)
    if t == "number":
        return float(schema.get("minimum", 0))
    if t == "boolean":
        return True
    return None


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/health":
            ready = time.time() - STARTED > LOAD_DELAY
            return self._send(200 if ready else 503, {"status": "ok" if ready else "loading"})
        self._send(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        if self.path == "/v1/systemone":
            answers = {}
            for qid, q in body["questions"].items():
                if q["type"] == "noul":
                    answers[qid] = {"type": "noul", "noul": 0.9 if qid in ("notes", "quiz", "video", "in_scope") else 0.1}
                elif q["type"] == "choice":
                    opts = list(q["criteria"])
                    probs = {o: (0.7 if i == 0 else 0.3 / max(1, len(opts) - 1)) for i, o in enumerate(opts)}
                    answers[qid] = {"type": "choice", "choice": opts[0], "confidence": 0.7, "probabilities": probs}
                else:
                    n = len(q["criteria"])
                    answers[qid] = {"type": "score", "score": 1.0, "legend": q["criteria"],
                                    "probabilities": {str(i): (0.6 if i == 1 else 0.4 / (n - 1)) for i in range(n)}}
            return self._send(200, {"model": model, "answers": answers, "usage": {"input_tokens": 512}})
        if self.path == "/v1/chat/completions":
            schema = body["response_format"]["json_schema"]["schema"]
            content = json.dumps(instance(schema))
            return self._send(200, {
                "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                "timings": {"prompt_n": 100, "cache_n": 50, "predicted_n": 40,
                            "prompt_per_second": 100.0, "predicted_per_second": 20.0},
            })
        self._send(404, {})


ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
