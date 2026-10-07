"""MASTER ROUTER: one Clef-flash decision per learner request (llama-server POST /v1/systemone).

Clef reads ``[system][STATE][SCHEMA][suffix]`` and returns a probability for every allowed
option of every question in a single forward pass. llama.cpp keeps no KV cache for Clef,
so every call re-reads the whole schema: the schema below is deliberately compact.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field

from ..config import Config
from ..runtime.events import EventLog
from ..runtime.http import post_json
from ..runtime.model_manager import ModelManager

GATEWAYS = ("notes", "flashcards", "quiz", "podcast", "video", "code")


@dataclass
class RouteDecision:
    mode: str
    selected: list[str]
    gateways: dict[str, float] = field(default_factory=dict)   # P(yes) per gateway
    subject: str = "other"
    subject_probs: dict[str, float] = field(default_factory=dict)
    level: str = "beginner"
    level_probs: dict[str, float] = field(default_factory=dict)
    video_engine: str = "manim"
    video_engine_probs: dict[str, float] = field(default_factory=dict)
    in_scope: float | None = None
    input_tokens: int | None = None
    latency_s: float = 0.0
    cached: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def build_questions(cfg: Config) -> dict:
    """The routing schema sent to Clef (choice/score/noul questions)."""
    yes_no = {"true": "yes", "false": "no"}
    q: dict = {}
    for name in GATEWAYS:
        q[name] = {"type": "noul", "instructions": cfg[f"router.gateways.{name}"], "criteria": yes_no}
    q["subject"] = {"type": "choice", "instructions": "Subject?",
                    "criteria": {s: None for s in cfg["router.subjects"]}}
    q["level"] = {"type": "score", "instructions": "Level?", "criteria": list(cfg["router.levels"])}
    q["video_engine"] = {"type": "choice", "instructions": "Best video style?",
                         "criteria": dict(cfg["router.video_engines"])}
    q["in_scope"] = {"type": "noul", "instructions": "Is this a request to learn a technical or scientific topic?",
                     "criteria": yes_no}
    return q


def build_state(text: str, lang_name: str, max_chars: int = 600) -> str:
    text = " ".join(text.split())
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + " …"
    return f"Learner request ({lang_name}): {text}"


def _choice(ans: dict) -> tuple[str, dict[str, float]]:
    probs = {k: float(v) for k, v in (ans.get("probabilities") or {}).items()}
    choice = ans.get("choice") or (max(probs, key=probs.get) if probs else "")
    return choice, probs


def _score(ans: dict, levels: list[str]) -> tuple[str, dict[str, float]]:
    raw = ans.get("probabilities") or {}
    probs: dict[str, float] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            label = levels[int(k)] if str(k).isdigit() and int(k) < len(levels) else str(k)
            probs[label] = float(v)
    elif isinstance(raw, list):
        probs = {levels[i]: float(v) for i, v in enumerate(raw[: len(levels)])}
    if probs:
        return max(probs, key=probs.get), probs
    score = ans.get("score")
    if isinstance(score, (int, float)):
        idx = min(len(levels) - 1, max(0, int(round(float(score)))))
        return levels[idx], probs
    return levels[0], probs


def parse_answers(answers: dict, cfg: Config) -> RouteDecision:
    threshold = float(cfg["router.threshold"])
    gw = {}
    for name in GATEWAYS:
        a = answers.get(name) or {}
        p = a.get("noul")
        if p is None and isinstance(a.get("probabilities"), dict):
            p = a["probabilities"].get("true")
        gw[name] = round(float(p), 4) if p is not None else 0.0
    selected = [g for g in GATEWAYS if gw[g] >= threshold]
    note = ""
    if not selected:
        selected = list(cfg.get("router.min_gateways", ["notes"]))
        note = "no gateway above threshold; using router.min_gateways"
    subject, sp = _choice(answers.get("subject") or {})
    level, lp = _score(answers.get("level") or {}, list(cfg["router.levels"]))
    engine, ep = _choice(answers.get("video_engine") or {})
    scope = answers.get("in_scope") or {}
    in_scope = scope.get("noul")
    return RouteDecision(mode="clef", selected=selected, gateways=gw, subject=subject or "other",
                         subject_probs=sp, level=level, level_probs=lp,
                         video_engine=engine or "manim", video_engine_probs=ep,
                         in_scope=None if in_scope is None else round(float(in_scope), 4), note=note)


class ClefRouter:
    def __init__(self, cfg: Config, models: ModelManager):
        self.cfg = cfg
        self.models = models
        self.cache_path = cfg.path("paths.cache_dir") / "route_cache.json"

    # -- memo cache (exact-match only: identical request + schema + model file) ------------
    def _key(self, state: str, questions: dict) -> str:
        blob = json.dumps({"s": state, "q": questions, "m": self.cfg["models.router.file"]},
                          sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode()).hexdigest()

    def _cache_get(self, key: str) -> dict | None:
        try:
            return json.loads(self.cache_path.read_text()).get(key)
        except (OSError, json.JSONDecodeError):
            return None

    def _cache_put(self, key: str, value: dict) -> None:
        try:
            data = json.loads(self.cache_path.read_text())
        except (OSError, json.JSONDecodeError):
            data = {}
        data[key] = value
        if len(data) > 500:
            for k in list(data)[: len(data) - 500]:
                del data[k]
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False))
        tmp.replace(self.cache_path)

    # -- routing ----------------------------------------------------------------------------
    def route(self, text: str, lang_name: str, events: EventLog | None = None,
              mode: str | None = None, manual: list[str] | None = None) -> RouteDecision:
        mode = mode or self.cfg["router.mode"]
        if mode in ("all", "manual"):
            selected = list(GATEWAYS) if mode == "all" else [g for g in (manual or []) if g in GATEWAYS]
            if not selected:
                selected = list(self.cfg.get("router.min_gateways", ["notes"]))
            return RouteDecision(mode=mode, selected=selected,
                                 gateways={g: (1.0 if g in selected else 0.0) for g in GATEWAYS},
                                 note=f"router bypassed ({mode})")
        questions = build_questions(self.cfg)
        state = build_state(text, lang_name, int(self.cfg.get("router.max_state_chars", 600)))
        key = self._key(state, questions)
        if self.cfg["router.memo_cache"]:
            hit = self._cache_get(key)
            if hit:
                dec = parse_answers(hit["answers"], self.cfg)
                dec.input_tokens, dec.cached = hit.get("input_tokens"), True
                if events:
                    events.emit("route_cache_hit", key=key[:12])
                return dec
        server = self.models.acquire("router", events)
        t = time.perf_counter()
        resp = post_json(server.base_url + "/v1/systemone", {"state": state, "questions": questions},
                         timeout=float(self.cfg["models.router.request_timeout_s"]))
        latency = time.perf_counter() - t
        answers = resp.get("answers") or {}
        dec = parse_answers(answers, self.cfg)
        dec.input_tokens = (resp.get("usage") or {}).get("input_tokens")
        dec.latency_s = round(latency, 3)
        if events:
            events.emit("route_decision", latency_s=dec.latency_s, input_tokens=dec.input_tokens,
                        selected=dec.selected, gateways=dec.gateways, subject=dec.subject,
                        level=dec.level, video_engine=dec.video_engine, in_scope=dec.in_scope)
        if self.cfg["router.memo_cache"]:
            self._cache_put(key, {"answers": answers, "input_tokens": dec.input_tokens})
        return dec
