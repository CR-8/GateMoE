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
