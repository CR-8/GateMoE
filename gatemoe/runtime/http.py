"""Tiny stdlib JSON-over-HTTP helpers (keeps the Pi install small)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class HTTPError(RuntimeError):
    def __init__(self, status: int, body: str, url: str):
        super().__init__(f"HTTP {status} from {url}: {body[:500]}")
        self.status = status
        self.body = body


def _open(req: urllib.request.Request, timeout: float) -> tuple[int, bytes]:
    # Local servers only: never route 127.0.0.1 traffic through an HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def get_json(url: str, timeout: float = 10.0) -> tuple[int, Any]:
    status, raw = _open(urllib.request.Request(url, method="GET"), timeout)
    try:
        return status, json.loads(raw.decode("utf-8") or "null")
    except json.JSONDecodeError:
        return status, raw.decode("utf-8", "replace")


def post_json(url: str, body: Any, timeout: float = 600.0) -> Any:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    status, raw = _open(req, timeout)
    text = raw.decode("utf-8", "replace")
    if status >= 400:
        raise HTTPError(status, text, url)
    return json.loads(text)
