"""Tiny stdlib JSON-over-HTTP helpers (keeps the Pi install small)."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from typing import Any, Callable


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


def _open_cancellable(req: urllib.request.Request, timeout: float,
                      cancel_check: Callable[[], None]) -> tuple[int, bytes]:
    """Run the request in a helper thread and poll ``cancel_check`` (which raises to abort).
    The abandoned request ends when the caller unloads the model server it was talking to."""
    box: dict = {}

    def run():
        try:
            box["result"] = _open(req, timeout)
        except BaseException as exc:          # delivered to the waiting thread
            box["error"] = exc

    th = threading.Thread(target=run, name="http-call", daemon=True)
    th.start()
    while th.is_alive():
        th.join(0.25)
        if th.is_alive():
            cancel_check()
    if "error" in box:
        raise box["error"]
    return box["result"]


def post_json(url: str, body: Any, timeout: float = 600.0,
              cancel_check: Callable[[], None] | None = None) -> Any:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    status, raw = _open_cancellable(req, timeout, cancel_check) if cancel_check else _open(req, timeout)
    text = raw.decode("utf-8", "replace")
    if status >= 400:
        raise HTTPError(status, text, url)
    return json.loads(text)
