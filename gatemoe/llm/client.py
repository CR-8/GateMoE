"""Generator client: OpenAI-compatible /v1/chat/completions on llama-server, JSON-schema constrained."""
from __future__ import annotations

import json
import re
import time
from typing import Any

from ..runtime.events import EventLog
from ..runtime.http import post_json
from .jsonschema_lite import validate

_THINK = re.compile(r"<think>.*?</think>", re.S)


class GenerationError(RuntimeError):
    pass


def _extract_json(text: str) -> Any:
    text = _THINK.sub("", text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = min([i for i in (text.find("{"), text.find("[")) if i >= 0] or [-1])
        if start < 0:
            raise
        return json.loads(text[start:])


class Generator:
    def __init__(self, base_url: str, *, timeout: float = 3600, temperature: float = 0.4,
                 disable_thinking: bool = True, events: EventLog | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.temperature = temperature
        self.disable_thinking = disable_thinking
        self.events = events

    def chat_json(self, messages: list[dict], schema: dict, *, name: str, max_tokens: int,
                  temperature: float | None = None, seed: int = 7, retries: int = 1) -> dict:
        body: dict = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": self.temperature if temperature is None else temperature,
            "seed": seed,
            "cache_prompt": True,
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": name, "schema": schema, "strict": True}},
        }
        if self.disable_thinking:
            body["chat_template_kwargs"] = {"enable_thinking": False}
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            t = time.perf_counter()
            resp = post_json(self.base_url + "/v1/chat/completions", body, timeout=self.timeout)
            secs = time.perf_counter() - t
            choice = (resp.get("choices") or [{}])[0]
            content = (choice.get("message") or {}).get("content") or ""
            timings = resp.get("timings") or {}
            usage = resp.get("usage") or {}
            if self.events:
                self.events.emit(
                    "llm_call", task=name, attempt=attempt, seconds=round(secs, 3),
                    prompt_n=timings.get("prompt_n", usage.get("prompt_tokens")),
                    cache_n=timings.get("cache_n"),
                    predicted_n=timings.get("predicted_n", usage.get("completion_tokens")),
                    prompt_tps=round(timings.get("prompt_per_second", 0) or 0, 2),
                    gen_tps=round(timings.get("predicted_per_second", 0) or 0, 2),
                    finish=choice.get("finish_reason"))
            try:
                obj = _extract_json(content)
            except (json.JSONDecodeError, ValueError) as exc:
                last_err = GenerationError(f"{name}: invalid JSON ({exc}); finish={choice.get('finish_reason')}")
            else:
                errs = validate(obj, schema)
                if not errs:
                    return obj
                last_err = GenerationError(f"{name}: schema errors: {errs[:5]}")
            if choice.get("finish_reason") == "length":   # truncated: a same-size retry would fail again
                body["max_tokens"] = int(body["max_tokens"] * 1.6)
            body["temperature"] = 0.2
            body["seed"] = seed + attempt + 1
        raise last_err or GenerationError(name)
