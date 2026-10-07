from gatemoe.gateways.speech import SpeechGateway
from gatemoe.gateways.text import TextGateway, pack_sources
from gatemoe.gateways.video import _lines, hf_scene
from gatemoe.gateways.video.assemble import write_subtitles
from gatemoe.gateways.video.hyperframes import build_payload, clean_text
from gatemoe.router.clef import RouteDecision, build_state


def test_hf_scene_mapping():
    t, d = hf_scene({"template": "bar_chart", "title": "I vs R", "labels": ["2", "5"], "values": [5, 2]}, "en", "L")
    assert t == "bars" and d["bars"] == [{"label": "2", "value": 5}, {"label": "5", "value": 2}]
    t, d = hf_scene({"template": "process_steps", "title": "Steps", "lines": ["Measure: use a meter", "Divide"]}, "kn", "L")
    assert t == "steps" and d["steps"][0] == {"label": "Measure", "detail": "use a meter"} and d["lang"] == "kn"
    t, d = hf_scene({"template": "code_walkthrough", "title": "c", "code": "x=1", "highlight": [1], "lines": ["n"]}, "en", "L")
    assert t == "code" and d["highlights"] == [{"lines": [1], "note": "n"}]
    t, d = hf_scene({"template": "definition", "title": "Ohm", "lines": ["unit of resistance"]}, "en", "L")
    assert t == "bullets" and d["bullets"] == ["unit of resistance"]


def test_lines_fall_back_to_narration():
    assert _lines({"narration": "First sentence. Second one!"}) == ["First sentence.", "Second one!"]


def test_payload_sanitising():
    assert clean_text("a‮b\u0007c‍d") == "abc‍d"   # bidi override + BEL removed, ZWJ kept
    p = build_payload("bars", {"bars": [{"label": "x", "value": "nan"}, {"label": "y", "value": 3}]})
    assert p["bars"] == [{"label": "y", "value": 3.0}]
    p = build_payload("title", {"title": "<script>x</script>", "lang": "ar"})
    assert p["title"] == "<script>x</script>" and p["dir"] == "rtl"     # shown via textContent, never parsed
    for bad in ({"bullets": []}, {"steps": ["only one"]}):
        try:
            build_payload("bullets" if "bullets" in bad else "steps", bad)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError")


def test_subtitles_escape_vtt(tmp_path):
    write_subtitles(["a <b> & c", "ಕನ್ನಡ"], [1.5, 2.0], tmp_path / "s.vtt", tmp_path / "s.srt")
    vtt = (tmp_path / "s.vtt").read_text()
    assert vtt.startswith("WEBVTT") and "a &lt;b&gt; &amp; c" in vtt and "00:00:01.500 --> 00:00:03.500" in vtt
    assert "00:00:01,500 --> 00:00:03,500" in (tmp_path / "s.srt").read_text()


def test_speech_routing_without_models(cfg):
    sg = SpeechGateway(cfg)
    assert sg.engine_for("en") is None and sg.engine_for("kn") is None
    assert "supertonic-3" in sg.available()


def test_spoken_rules_only_for_limited_voices(cfg):
    dec = RouteDecision(mode="clef", selected=["podcast"])
    assert TextGateway(cfg, None, "en", dec, "x", tts_engine="supertonic-3").spoken_rules() == ""
    assert TextGateway(cfg, None, "hi", dec, "x", tts_engine="supertonic-3").spoken_rules() == ""
    assert "Kannada" in TextGateway(cfg, None, "kn", dec, "x", tts_engine="mms:kan").spoken_rules()


def test_pack_sources_caps_length():
    text = pack_sources([{"title": "T", "zim": "z", "text": "w " * 5000}], 300)
    assert len(text) <= 300 and text.startswith("[1] T (z)")


def test_router_state_is_capped():
    s = build_state("word " * 500, "English", 100)
    assert len(s) < 140 and s.endswith("…")


def test_handout_data_is_plain_and_safe():
    from gatemoe.gateways.handout import _blocks, handout_data
    lesson = {"request": "x", "notes": {"title": "**T** #import \"@preview/x\"", "sections": [
        {"heading": "H", "body": "Intro **bold**\n- one\n- two\nEnd"}], "key_points": ["k"], "glossary": []},
        "quiz": {"questions": [{"question": "q", "options": ["a", "b", "c", "d"], "answer_index": 5,
                                "explanation": "e"}]}}
    d = handout_data(lesson, {"font": "Noto Sans Kannada"}, "kn")
    assert d["title"].startswith("T #import") and d["fonts"][0] == "Noto Sans Kannada"
    assert d["quiz"][0]["answer_index"] == 1                   # always a valid option index
    assert _blocks("a\n- b\n- c\nd") == [{"kind": "par", "text": "a"}, {"kind": "list", "items": ["b", "c"]},
                                        {"kind": "par", "text": "d"}]


def test_manim_beat_mapping_and_search_steps():
    from gatemoe.gateways.video.manim_engine import beat_to_spec, binary_search_steps
    s = beat_to_spec({"template": "array_steps", "title": "BS", "values": [9, 1, 5, 5, 3], "target": 5}, "kn")
    assert s["type"] == "array_steps" and s["values"] == [1, 3, 5, 9] and s["lang"] == "kn"
    assert s["steps"][-1].get("found") == [2]
    miss = binary_search_steps([1, 3, 5, 9], 4)
    assert "found" not in miss[-1] and len(miss) <= 12
    g = beat_to_spec({"template": "function_graph", "title": "f", "expression": "x**2", "x_min": 5, "x_max": 1}, "en")
    assert g["x_range"] == [-5.0, 5.0]                             # invalid range repaired
    e = beat_to_spec({"template": "equation_steps", "title": "d", "equations": ["a", "", "b"], "lines": ["c1"]}, "en")
    assert [st["math"] for st in e["steps"]] == ["a", "b"] and e["steps"][0]["caption"] == "c1"
    b = beat_to_spec({"template": "bar_chart", "title": "x", "narration": "Only narration."}, "en")
    assert b["type"] == "bullets" and b["bullets"] == ["Only narration."]
