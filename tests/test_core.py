import json

import pytest

from gatemoe.config import load_config
from gatemoe.gateways import schemas
from gatemoe.langid import detect_language
from gatemoe.llm.client import Generator, _extract_json
from gatemoe.llm.jsonschema_lite import validate
from gatemoe.router.clef import GATEWAYS, ClefRouter, build_questions, parse_answers
from gatemoe.runtime.events import Cancelled, EventLog
from gatemoe.runtime.model_manager import ModelManager


def test_config_references_resolve(tmp_path):
    c = load_config(overrides={"paths": {"data_dir": str(tmp_path)}})
    assert c["paths.models_dir"] == f"{tmp_path}/models"
    assert c.model_path("router").name == c["models.router.file"]
    assert c.language("kn")["font"] == "Noto Sans Kannada"
    assert c.language("xx")["tts"] == "none"


def test_router_schema_is_compact():
    c = load_config()
    q = build_questions(c)
    assert set(GATEWAYS) <= set(q)
    assert q["level"]["type"] == "score" and q["subject"]["type"] == "choice"
    # short instructions keep the per-call prefill small (Clef has no KV cache)
    assert sum(len(json.dumps(v)) for v in q.values()) < 2000


def test_parse_answers_threshold_and_fallback():
    c = load_config()
    answers = {g: {"type": "noul", "noul": p} for g, p in zip(GATEWAYS, [0.9, 0.2, 0.51, 0.49, 0.0, 0.7])}
    answers["subject"] = {"choice": "physics", "probabilities": {"physics": 0.8, "other": 0.2}}
    answers["level"] = {"score": 1.2, "probabilities": {"0": 0.1, "1": 0.7, "2": 0.2}}
    answers["video_engine"] = {"choice": "hyperframes", "probabilities": {"manim": 0.3, "hyperframes": 0.7}}
    d = parse_answers(answers, c)
    assert d.selected == ["notes", "quiz", "code"]
    assert d.subject == "physics" and d.level == "intermediate" and d.video_engine == "hyperframes"
    empty = parse_answers({g: {"noul": 0.1} for g in GATEWAYS}, c)
    assert empty.selected == ["notes"] and empty.note


def test_schema_validator():
    s = schemas.quiz_schema(2)
    good = {"questions": [{"question": "q", "options": ["a", "b", "c", "d"], "answer_index": 1, "explanation": "e"}] * 2}
    assert validate(good, s) == []
    bad = {"questions": [{"question": "q", "options": ["a"], "answer_index": 7, "explanation": "e"}]}
    errs = validate(bad, s)
    assert any("fewer than" in e for e in errs) and any("above" in e for e in errs)


def test_video_schema_templates_per_engine():
    m = schemas.video_schema("manim", 6)["properties"]["beats"]["items"]["properties"]["template"]["enum"]
    h = schemas.video_schema("hyperframes", 6)["properties"]["beats"]["items"]["properties"]["template"]["enum"]
    assert "equation_steps" in m and "equation_steps" not in h
    assert "bar_chart" in h and "title" in m and "title" in h


def test_extract_json_strips_think():
    assert _extract_json('<think>hmm</think>\n{"a": 1}') == {"a": 1}
    assert _extract_json('```json\n{"a": 2}\n```') == {"a": 2}


@pytest.mark.parametrize("text,lang", [
    ("I want to learn how transistors work", "en"),
    ("ಓಮ್ ನಿಯಮವನ್ನು ವಿವರಿಸಿ", "kn"),
    ("ஒளிச்சேர்க்கை எப்படி வேலை செய்கிறது", "ta"),
    ("मुझे न्यूटन के गति के नियम समझाइए", "hi"),
    ("Quiero aprender cómo funciona la fotosíntesis", "es"),
    ("光合成について学びたい", "ja"),
])
def test_langid(text, lang):
    assert detect_language(text, ["en", "hi", "mr", "kn", "ta", "es", "fr", "de", "ja", "zh", "pt"])["lang"] == lang


def test_event_log_stage_and_cancel(tmp_path):
    ev = EventLog(tmp_path / "e.jsonl")
    with ev.stage("a") as st:
        st["x"] = 1
    assert ev.stage_times["a"] >= 0
    ev.cancel_flag.set()
    with pytest.raises(Cancelled):
        with ev.stage("b"):
            pass
    lines = (tmp_path / "e.jsonl").read_text().splitlines()
    assert json.loads(lines[1])["x"] == 1


def test_model_manager_swaps_one_at_a_time(cfg):
    mm = ModelManager(cfg)
    ev = EventLog(None)
    try:
        r = mm.acquire("router", ev)
        assert r.running()
        g = mm.acquire("generator", ev)
        assert g.running() and not r.running()      # router was unloaded first
        assert mm.acquire("generator", ev) is g      # hit, no reload
    finally:
        mm.unload(ev)
    kinds = [e["kind"] for e in ev.events]
    assert kinds.count("model_load") == 2 and kinds.count("model_unload") == 2 and "model_hit" in kinds


def test_router_and_generator_against_fake_server(cfg):
    mm = ModelManager(cfg)
    ev = EventLog(None)
    try:
        dec = ClefRouter(cfg, mm).route("Explain Ohm's law", "English", ev)
        assert dec.selected == ["notes", "quiz", "video"] and dec.input_tokens == 512
        again = ClefRouter(cfg, mm).route("Explain Ohm's law", "English", ev)
        assert again.cached and again.selected == dec.selected
        srv = mm.acquire("generator", ev)
        out = Generator(srv.base_url, events=ev).chat_json(
            [{"role": "user", "content": "x"}], schemas.flashcards_schema(3), name="flashcards", max_tokens=50)
        assert len(out["cards"]) == 3
    finally:
        mm.unload(ev)
    assert any(e["kind"] == "llm_call" and e["cache_n"] == 50 for e in ev.events)


def test_router_bypass_modes(cfg):
    r = ClefRouter(cfg, ModelManager(cfg))
    assert r.route("x", "English", mode="all").selected == list(GATEWAYS)
    assert r.route("x", "English", mode="manual", manual=["quiz", "bogus"]).selected == ["quiz"]


def test_langid_portuguese_not_forced_to_english():
    from gatemoe.langid import detect_language
    langs = ["en", "pt", "es", "de", "kn", "hi"]
    assert detect_language("Me explique a lei de Ohm com um quiz", langs)["lang"] == "pt"
    assert detect_language("How does a capacitor work?", langs)["lang"] == "en"
    assert detect_language("ಓಮ್ ನಿಯಮ", langs)["lang"] == "kn"


def test_second_server_on_busy_port_is_refused(cfg):

    from gatemoe.runtime.llama_server import ServerError
    first = ModelManager(cfg)
    first.acquire("router")
    try:
        other = ModelManager(cfg)._server("router")      # e.g. an orphan of a crashed run holds the port
        with pytest.raises(ServerError, match="already in use"):
            other.start()
        assert first.current.running()                   # the real server is untouched
    finally:
        first.unload()
