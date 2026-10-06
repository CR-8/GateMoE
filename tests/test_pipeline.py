"""Whole pipeline against the fake llama-server (no models, no voices, no video engines installed)."""
import json

from gatemoe.knowledge import bm25_rank, split_passages, tokenize
from gatemoe.knowledge.htmltext import html_to_paragraphs
from gatemoe.pipeline import LessonPipeline
from gatemoe.runtime.events import EventLog


def test_pipeline_routes_and_generates(cfg, tmp_path):
    pipe = LessonPipeline(cfg)
    out = tmp_path / "lesson"
    out.mkdir()
    ev = EventLog(out / "events.jsonl")
    lesson = pipe.run("Explain Ohm's law with a quiz", out, ev, {"language": "auto"})
    # the fake router fires notes, quiz and video; video needs an engine, none is installed here
    assert lesson["route"]["selected"] == ["notes", "quiz", "video"]
    assert "notes" in lesson and "quiz" in lesson
    assert "video" in lesson["errors"]
    assert lesson["language"]["lang"] == "en"
    assert (out / "lesson.json").exists() and (out / "notes.md").exists()
    acts = lesson["specialists"]["activated"]
    assert "router:clef-flash" in acts and any(a.startswith("generator:") for a in acts)
    m = lesson["metrics"]
    assert m["model_loads"] == 2 and m["llm_calls"] >= 3      # router + generator, plan + 2 tasks
    kinds = [json.loads(line)["kind"] for line in (out / "events.jsonl").read_text().splitlines()]
    assert kinds.index("route_decision") < kinds.index("llm_call")
    assert pipe.models.current is None                       # generator unloaded before speech/video


def test_out_of_scope_stops_before_generator(cfg, tmp_path, monkeypatch):
    from gatemoe.router import clef

    real = clef.parse_answers

    def low_scope(answers, c):
        d = real(answers, c)
        d.in_scope = 0.01
        return d

    monkeypatch.setattr(clef, "parse_answers", low_scope)
    pipe = LessonPipeline(cfg)
    lesson = pipe.run("What's the best pizza topping?", tmp_path, EventLog(None), {})
    assert lesson.get("out_of_scope") and "scope" in lesson["errors"]
    assert lesson["metrics"]["model_loads"] == 1               # only the router was ever loaded
    assert "notes" not in lesson


def test_manual_mode_skips_router(cfg, tmp_path):
    pipe = LessonPipeline(cfg)
    lesson = pipe.run("Explain diodes", tmp_path, EventLog(None), {"mode": "manual", "gateways": ["flashcards"]})
    assert lesson["route"]["mode"] == "manual" and lesson["route"]["selected"] == ["flashcards"]
    assert "router:clef-flash" not in lesson["specialists"]["activated"]
    assert lesson["metrics"]["model_loads"] == 1               # generator only
    assert (tmp_path / "flashcards_anki.tsv").exists()


def test_tokenize_keeps_indic_marks_and_cjk_bigrams():
    assert tokenize("ವಿದ್ಯುತ್ ಪ್ರವಾಹ") == ["ವಿದ್ಯುತ್", "ಪ್ರವಾಹ"]
    assert "光合" in tokenize("光合作用") and "作用" in tokenize("光合作用")
    assert tokenize("Ohm's law, V = I·R") == ["ohm", "law"]


def test_bm25_and_passages():
    docs = [tokenize("ohm law relates voltage current resistance"), tokenize("photosynthesis in plants")]
    s = bm25_rank(tokenize("ohm law"), docs)
    assert s[0] > s[1] == 0
    parts = split_passages(["one two three"] * 10, words=7)
    assert len(parts) == 5


def test_html_to_paragraphs_drops_refs_and_tables():
    html = """<html><body><p>Ohm's law states that the current through a conductor is proportional to voltage.<sup class="reference">[1]</sup></p>
    <table class="infobox"><tr><td>junk junk junk junk junk junk junk junk junk</td></tr></table>
    <h2>References</h2><p>Some reference text that should not be included in passages at all.</p></body></html>"""
    paras = html_to_paragraphs(html)
    assert len(paras) == 1 and "[1]" not in paras[0] and "junk" not in paras[0]
