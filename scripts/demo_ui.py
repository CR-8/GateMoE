#!/usr/bin/env python3
"""Run the web app on sample data, without models: for UI work (``cd web && npm run dev``) and demos.

    .venv/bin/python scripts/demo_ui.py [--port 8765] [--data /tmp/gatemoe-demo] [--video path/to/video.mp4]

Creates a few finished lessons (notes, flashcards, quiz, podcast, video, code, handout, concept map,
sources), a roster with sections, posted lessons, a feedback bank and quiz submissions, then keeps
one lesson "generating" (a stub pipeline that walks through the stages slowly) and one queued.
Nothing here touches a real node: everything lives in --data (deleted and rebuilt on each start).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OHM_QUIZ = [
    {"question": "What does Ohm's law state?", "options": ["V = I × R", "V = I / R", "P = V × I", "R = V × I"], "answer_index": 0,
     "explanation": "Voltage equals current times resistance."},
    {"question": "A 12 V battery drives current through a 4 Ω resistor. What is the current?", "options": ["48 A", "3 A", "8 A", "0.33 A"],
     "answer_index": 1, "explanation": "I = V / R = 12 / 4 = 3 A."},
    {"question": "What is the unit of resistance?", "options": ["Volt", "Ampere", "Ohm", "Watt"], "answer_index": 2,
     "explanation": "Resistance is measured in ohms (Ω)."},
    {"question": "If the voltage across a fixed resistor doubles, the current…", "options": ["halves", "stays the same", "doubles", "becomes zero"],
     "answer_index": 2, "explanation": "For a fixed R, current is proportional to voltage."},
    {"question": "Which instrument measures current?", "options": ["Voltmeter", "Ammeter", "Ohmmeter", "Thermometer"], "answer_index": 1,
     "explanation": "An ammeter is connected in series to measure current."},
]

LESSONS = [
    {
        "request": "Explain Ohm's law for class 10 with a quiz, podcast and a short video",
        "board": "published", "days_ago": 2, "lang": ("en", "English", "English"), "subject": "physics", "level": "secondary",
        "route": {"notes": 0.97, "flashcards": 0.81, "quiz": 0.95, "podcast": 0.74, "video": 0.69, "code": 0.04},
        "notes": {"title": "Ohm's Law", "sections": [
            {"heading": "What the law says", "body": "**Ohm's law** relates three quantities in a circuit: voltage (V), current (I) and resistance (R).\n- Voltage is the push that drives charge.\n- Current is the rate at which charge flows.\n- Resistance is how strongly a material opposes that flow."},
            {"heading": "The equation", "body": "The law is written **V = I × R**. Rearranged, I = V / R and R = V / I. For a fixed resistor, doubling the voltage doubles the current."},
            {"heading": "Measuring it", "body": "Connect a resistor to a battery. Measure the voltage across it with a voltmeter (in parallel) and the current with an ammeter (in series). Divide V by I to get R."},
        ], "key_points": ["V = I × R links voltage, current and resistance", "Resistance is measured in ohms (Ω)",
                          "For a fixed resistor, current is proportional to voltage", "Ammeters go in series, voltmeters in parallel"],
            "glossary": [{"term": "Voltage", "definition": "The energy per unit charge that pushes current around a circuit (volts, V)."},
                         {"term": "Current", "definition": "The rate of flow of electric charge (amperes, A)."},
                         {"term": "Resistance", "definition": "Opposition to the flow of current (ohms, Ω)."}],
            "concept_map": [{"from": "Ohm's law", "label": "relates", "to": "Voltage"}, {"from": "Ohm's law", "label": "relates", "to": "Current"},
                            {"from": "Ohm's law", "label": "relates", "to": "Resistance"}, {"from": "Voltage", "label": "drives", "to": "Current"},
                            {"from": "Resistance", "label": "opposes", "to": "Current"}, {"from": "Ammeter", "label": "measures", "to": "Current"},
                            {"from": "Voltmeter", "label": "measures", "to": "Voltage"}]},
        "flashcards": [{"front": "State Ohm's law", "back": "V = I × R: voltage equals current times resistance."},
                       {"front": "Unit of resistance", "back": "The ohm (Ω)."},
                       {"front": "How is an ammeter connected?", "back": "In series with the component."},
                       {"front": "How is a voltmeter connected?", "back": "In parallel across the component."},
                       {"front": "I for V = 9 V and R = 3 Ω", "back": "I = V / R = 3 A."},
                       {"front": "What happens to I if R doubles at fixed V?", "back": "The current halves."}],
        "quiz": OHM_QUIZ,
        "podcast": [("A", "Welcome! Today we're talking about Ohm's law, one of the first rules every electronics student meets."),
                    ("B", "So what does it actually say?"),
                    ("A", "It says voltage equals current times resistance. V equals I R."),
                    ("B", "So if I push harder with more voltage, more current flows?"),
                    ("A", "Exactly, as long as the resistance stays the same. Double the voltage and you double the current."),
                    ("B", "And resistance is what slows the current down."),
                    ("A", "Right. Thin, long wires resist more. That's measured in ohms.")],
        "video": True,
        "sources": [{"title": "Ohm's law", "zim": "wikipedia_en_physics_nopic", "path": "A/Ohm's_law", "lang": "en", "score": 12.4,
                     "excerpt": "Ohm's law states that the current through a conductor between two points is directly proportional to the voltage across the two points."},
                    {"title": "Electrical resistance and conductance", "zim": "wikipedia_en_physics_nopic", "path": "A/Electrical_resistance_and_conductance",
                     "lang": "en", "score": 9.1, "excerpt": "The electrical resistance of an object is a measure of its opposition to the flow of electric current."},
                    {"title": "A survey of memristive devices beyond Ohmic conduction", "zim": "gatemoe_delta_en_2026-10", "path": "A/arxiv-2610.01234",
                     "lang": "en", "score": 6.2, "excerpt": "We review non-Ohmic conduction in memristive devices and its use in neuromorphic circuits."}],
        "wall": 412.6,
    },
    {
        "request": "Binary search in Python with a short video",
        "board": None, "days_ago": 1, "lang": ("en", "English", "English"), "subject": "computer_science", "level": "secondary",
        "route": {"notes": 0.88, "flashcards": 0.22, "quiz": 0.71, "podcast": 0.11, "video": 0.64, "code": 0.96},
        "notes": {"title": "Binary Search", "sections": [
            {"heading": "Idea", "body": "Binary search finds a value in a **sorted** list by halving the search range at every step."},
            {"heading": "Cost", "body": "Each step discards half the list, so a list of n items needs about log₂ n steps."},
            {"heading": "Steps", "body": "- Look at the middle item.\n- If it is the target, stop.\n- If the target is smaller, keep the left half; otherwise the right half."},
        ], "key_points": ["Works only on sorted lists", "Halves the range each step", "About log₂ n comparisons"],
            "glossary": [{"term": "Sorted", "definition": "Arranged in increasing (or decreasing) order."}]},
        "quiz": [{"question": "Binary search requires the list to be…", "options": ["sorted", "short", "unique", "numeric"], "answer_index": 0,
                  "explanation": "Halving only works if order tells you which half to keep."},
                 {"question": "How many steps for 1024 items, at most?", "options": ["1024", "512", "10", "32"], "answer_index": 2,
                  "explanation": "log₂ 1024 = 10."}],
        "code": {"language": "python", "title": "Binary search", "explanation": "The function keeps two indices, **lo** and **hi**, and narrows them until the target is found or the range is empty.",
                 "code": "def binary_search(items, target):\n    lo, hi = 0, len(items) - 1\n    while lo <= hi:\n        mid = (lo + hi) // 2\n        if items[mid] == target:\n            return mid\n        if items[mid] < target:\n            lo = mid + 1\n        else:\n            hi = mid - 1\n    return -1\n\nprint(binary_search([1, 3, 5, 7, 9, 11, 13], 9))",
                 "expected_output": "4"},
        "video": True, "wall": 298.0,
    },
    {
        "request": "प्रकाश संश्लेषण को सरल भाषा में समझाइए, फ्लैशकार्ड के साथ",
        "board": None, "days_ago": 0.3, "lang": ("hi", "Hindi", "हिन्दी"), "subject": "biology", "level": "secondary",
        "route": {"notes": 0.93, "flashcards": 0.89, "quiz": 0.31, "podcast": 0.58, "video": 0.12, "code": 0.01},
        "notes": {"title": "प्रकाश संश्लेषण", "sections": [
            {"heading": "यह क्या है", "body": "पौधे सूर्य के प्रकाश, जल और कार्बन डाइऑक्साइड से अपना भोजन (ग्लूकोज़) बनाते हैं। इस प्रक्रिया को **प्रकाश संश्लेषण** कहते हैं।"},
            {"heading": "कहाँ होता है", "body": "यह पत्तियों की कोशिकाओं में मौजूद हरितलवक (क्लोरोप्लास्ट) में होता है।"},
        ], "key_points": ["प्रकाश ऊर्जा रासायनिक ऊर्जा में बदलती है", "ऑक्सीजन एक उप-उत्पाद है", "क्लोरोफ़िल प्रकाश को सोखता है"]},
        "flashcards": [{"front": "प्रकाश संश्लेषण का उत्पाद", "back": "ग्लूकोज़ और ऑक्सीजन"}, {"front": "कहाँ होता है?", "back": "हरितलवक में"},
                       {"front": "हरा वर्णक", "back": "क्लोरोफ़िल"}],
        "podcast": [("A", "नमस्ते! आज हम प्रकाश संश्लेषण की बात करेंगे।"), ("B", "यानी पौधे अपना खाना कैसे बनाते हैं?"),
                    ("A", "हाँ, सूर्य के प्रकाश से।")],
        "wall": 236.4,
    },
    {
        "request": "Make flashcards on the parts of a cell",
        "board": "published", "days_ago": 5, "lang": ("en", "English", "English"), "subject": "biology", "level": "primary",
        "route": {"notes": 0.41, "flashcards": 0.98, "quiz": 0.62, "podcast": 0.05, "video": 0.08, "code": 0.0},
        "flashcards": [{"front": "Nucleus", "back": "Controls the cell and holds DNA."}, {"front": "Cell membrane", "back": "Controls what enters and leaves."},
                       {"front": "Mitochondria", "back": "Release energy from food (respiration)."}, {"front": "Cytoplasm", "back": "Jelly where reactions happen."}],
        "quiz": [{"question": "Which part holds the DNA?", "options": ["Nucleus", "Membrane", "Cytoplasm", "Vacuole"], "answer_index": 0, "explanation": "The nucleus contains the genetic material."},
                 {"question": "Where does respiration release energy?", "options": ["Ribosome", "Mitochondria", "Cell wall", "Nucleus"], "answer_index": 1, "explanation": "Mitochondria are the site of aerobic respiration."},
                 {"question": "What controls what enters the cell?", "options": ["Cytoplasm", "Nucleus", "Cell membrane", "Chloroplast"], "answer_index": 2, "explanation": "The membrane is selectively permeable."}],
        "wall": 141.9,
    },
]


def _id() -> str:
    return uuid.uuid4().hex[:12]


def tiny_pdf(path: Path, title: str, lines: list[str]) -> None:
    """A one-page PDF with plain Helvetica text (no dependencies), standing in for the Typst handout."""
    def esc(s: str) -> str:
        return s.encode("latin-1", "replace").decode("latin-1").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    body = ["BT /F1 22 Tf 56 770 Td (" + esc(title) + ") Tj ET"]
    y = 730
    for ln in lines:
        body.append(f"BT /F1 12 Tf 56 {y} Td (" + esc(ln) + ") Tj ET")
        y -= 20
    stream = "\n".join(body).encode("latin-1")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"]
    out, offs = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    out += f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))


def tone(path: Path, seconds: float) -> bool:
    if not shutil.which("ffmpeg"):
        return False
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds}",
                        "-af", "volume=0.05", "-ar", "22050", str(path)], capture_output=True)
    return r.returncode == 0


def make_lesson(cfg, spec: dict, jobs_dir: Path, video: Path | None) -> str:
    from gatemoe.gateways.conceptmap import ConceptMapGateway
    jid = _id()
    d = jobs_dir / jid
    d.mkdir(parents=True)
    created = time.time() - spec["days_ago"] * 86400
    code, name, native = spec["lang"]
    selected = [g for g, p in spec["route"].items() if p >= 0.5]
    lesson: dict = {"request": spec["request"], "created": created, "language": {"lang": code, "name": name, "native": native},
                    "route": {"mode": "clef", "selected": selected, "gateways": spec["route"], "subject": spec["subject"],
                              "level": spec["level"], "video_engine": "manim", "in_scope": 0.97, "input_tokens": 212, "latency_s": 6.8},
                    "plan": {"title": (spec.get("notes") or {}).get("title") or spec["request"][:60]}, "errors": {}}
    files = []
    if spec.get("notes"):
        n = dict(spec["notes"])
        cmap = n.pop("concept_map", None)
        lesson["notes"] = n
        (d / "notes.md").write_text(f"# {n['title']}\n\n" + "\n\n".join(f"## {s['heading']}\n\n{s['body']}" for s in n["sections"]), encoding="utf-8")
        if cmap and shutil.which("dot"):
            try:
                lesson["concept_map"] = ConceptMapGateway(cfg).render(cmap, code, d, n["title"])
            except Exception as exc:  # noqa: BLE001 - demo only
                lesson["errors"]["concept_map"] = str(exc)
        files.append("notes")
    if spec.get("flashcards"):
        lesson["flashcards"] = {"cards": spec["flashcards"]}
        (d / "flashcards_anki.tsv").write_text("\n".join(f"{c['front']}\t{c['back']}" for c in spec["flashcards"]), encoding="utf-8")
    if spec.get("quiz"):
        lesson["quiz"] = {"questions": spec["quiz"]}
    if spec.get("podcast"):
        turns = [{"speaker": s, "text": t} for s, t in spec["podcast"]]
        lesson["podcast"] = {"title": f"{lesson['plan']['title']}: a conversation", "turns": turns}
        secs, t, timing = 0.0, 0.0, []
        for tr in turns:
            dur = max(2.0, len(tr["text"]) / 14)
            timing.append({"speaker": tr["speaker"], "start_s": round(t, 2), "end_s": round(t + dur, 2), "engine": "demo-tone"})
            t += dur + 0.3
        secs = t
        if tone(d / "podcast.wav", secs):
            lesson["podcast_audio"] = {"audio": "podcast.wav", "duration_s": round(secs, 1), "engine": "demo-tone", "turns": timing}
        else:
            lesson["errors"]["podcast_audio"] = "ffmpeg not found (demo)"
    if spec.get("code"):
        lesson["code"] = spec["code"]
        (d / "example.py").write_text(spec["code"]["code"], encoding="utf-8")
    if spec.get("video"):
        beats = [{"template": "title", "title": lesson["plan"]["title"], "narration": "Today we learn " + lesson["plan"]["title"] + "."},
                 {"template": "bullets", "title": "Key ideas", "narration": "Three ideas matter."},
                 {"template": "equation_steps", "title": "The rule", "narration": "We can rearrange it."},
                 {"template": "process_steps", "title": "Try it", "narration": "Four steps."}]
        lesson["video"] = {"title": lesson["plan"]["title"], "beats": beats}
        if video and video.exists():
            shutil.copy(video, d / "video.mp4")
            for ext in (".vtt", ".srt"):
                if video.with_suffix(ext).exists():
                    shutil.copy(video.with_suffix(ext), d / f"video{ext}")
            lesson["video_render"] = {"video": "video.mp4", "subtitles": "video.vtt", "srt": "video.srt", "duration_s": 41.5,
                                      "engine": "manim", "render_s": 188.2, "beats": [{"errors": []} for _ in beats]}
        else:
            lesson["errors"]["video_render"] = "no video engine on this demo node (pass --video to show one)"
    if spec.get("sources"):
        lesson["sources"] = spec["sources"]
    else:
        lesson["sources"] = []
    if lesson.get("notes"):
        tiny_pdf(d / "handout.pdf", lesson["notes"]["title"], [f"- {k}" for k in lesson["notes"].get("key_points", [])])
        lesson["handout"] = {"pdf": "handout.pdf", "bytes": (d / "handout.pdf").stat().st_size, "seconds": 3.1}
    wall = spec["wall"]
    stages = {"language": 0.02, "route": 6.8, "plan": 31.0, "retrieve": 2.4, "gen_notes": 88.0, "gen_flashcards": 30.5, "gen_quiz": 44.0,
              "unload_generator": 0.6, "tts_podcast": 52.1, "render_video": 120.4, "concept_map": 0.8, "handout": 3.1}
    lesson["metrics"] = {"wall_seconds": wall, "stage_seconds": {k: v for k, v in stages.items() if k in ("language", "route", "plan", "retrieve", "unload_generator")
                                                                 or k.removeprefix("gen_") in selected or (k == "tts_podcast" and "podcast" in selected)
                                                                 or (k == "render_video" and "video" in selected) or (k in ("concept_map", "handout") and lesson.get("notes"))},
                         "model_loads": 2, "model_load_seconds": 14.2, "model_peak_rss_mb": 3420, "llm_calls": 2 + len(selected),
                         "prompt_tokens": 5120, "cached_prompt_tokens": 1800, "generated_tokens": 2240, "min_mem_available_mb": 2210, "max_temp_c": 61.0}
    lesson["specialists"] = {"available": [{"id": f"x{i}", "available": True} for i in range(14)],
                             "activated": ["router:clef-flash", "generator:qwen3.5-4b-q4_0.gguf"] + [f"gateway:{g}" for g in selected]
                             + (["knowledge:wikipedia_en_physics_nopic"] if spec.get("sources") else [])}
    (d / "lesson.json").write_text(json.dumps(lesson, ensure_ascii=False, indent=1), encoding="utf-8")
    job = {"id": jid, "request": spec["request"], "options": {"language": "auto", "mode": None, "gateways": None}, "status": "done",
           "created": created, "started": created + 1, "finished": created + wall, "error": None, "stage": None}
    (d / "job.json").write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    return jid


class SlowPipeline:
    """Walks through the real stage names slowly, so the board shows a lesson being generated."""

    class _M:
        def unload(self, *a):
            pass

        def status(self):
            return {"loaded": "generator", "pid": None, "history": [{"event": "load", "role": "router", "seconds": 4.1},
                                                                     {"event": "unload", "role": "router", "seconds": 0.3},
                                                                     {"event": "load", "role": "generator", "seconds": 9.8}]}

    models = _M()

    def run(self, request, out, ev, options):
        with ev.stage("language"):
            time.sleep(1)
        with ev.stage("route"):
            time.sleep(2)
        ev.emit("route_decision", latency_s=6.1, input_tokens=180, selected=["notes", "quiz", "podcast"], subject="chemistry",
                level="secondary", video_engine="manim", in_scope=0.95,
                gateways={"notes": 0.94, "flashcards": 0.37, "quiz": 0.88, "podcast": 0.66, "video": 0.21, "code": 0.02})
        ev.emit("model_load", role="generator", seconds=9.8)
        with ev.stage("plan"):
            time.sleep(3)
        ev.emit("llm_call", predicted_n=310)
        with ev.stage("retrieve"):
            time.sleep(1)
        with ev.stage("gen_notes"):
            time.sleep(3600)        # stays "generating" while you look at the UI
        return {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data", default="/tmp/gatemoe-demo")
    ap.add_argument("--video", help="an MP4 rendered earlier (its .vtt/.srt next to it are copied too)")
    ap.add_argument("--no-live", action="store_true", help="do not start the slow 'generating' job")
    args = ap.parse_args()

    import uvicorn
    from gatemoe.classroom import Classroom
    from gatemoe.config import load_config
    from gatemoe.jobs import JobManager
    from gatemoe.server.app import create_app

    data = Path(args.data)
    if data.exists():
        shutil.rmtree(data)
    cfg = load_config(overrides={"paths": {"data_dir": str(data)}, "server": {"auth": {"password": ""}}})
    cfg.ensure_dirs()
    jobs_dir = cfg.path("paths.jobs_dir")
    video = Path(args.video) if args.video else None
    ids = [make_lesson(cfg, s, jobs_dir, video) for s in LESSONS]
    failed = _id()
    (jobs_dir / failed).mkdir()
    (jobs_dir / failed / "job.json").write_text(json.dumps({
        "id": failed, "request": "Explain the Krebs cycle with a video", "options": {"language": "auto"}, "status": "failed",
        "created": time.time() - 3 * 86400, "started": None, "finished": None, "stage": None,
        "error": "generator: llama-server exited (out of memory); lower models.generator.ctx"}), encoding="utf-8")

    cls = Classroom(data / "classroom.json")
    roster = cls.add_students(["Aarav Sharma", "Diya Patel", "Ishaan Verma", "Ananya Iyer", "Kabir Singh", "Meera Nair", "Rohan Das",
                               "Saanvi Gupta", "Vihaan Reddy", "Zara Khan", "Arjun Mehta", "Priya Menon"])
    sid = {s["name"]: s["id"] for s in roster}
    names = list(sid)
    p1 = cls.add_section("Period 1 · Physics", [sid[n] for n in names[:7]], kind="period")
    p3 = cls.add_section("Period 3 · Physics", [sid[n] for n in names[6:]], kind="period")
    club = cls.add_section("Robotics club", [sid[n] for n in ("Kabir Singh", "Zara Khan", "Arjun Mehta", "Diya Patel")], kind="club")
    cls.add_section("Tutoring · Tue", [sid["Rohan Das"], sid["Meera Nair"]], kind="tutoring")
    old = cls.add_section("Summer camp 2026", [sid[n] for n in names[:5]], kind="group")
    cls.update_section(old["id"], archived=True)
    a1 = cls.assign(ids[0], [p1["id"], p3["id"]], "Ohm's law: notes and quiz", "2026-10-20", "Watch the video before the quiz.")
    a2 = cls.assign(ids[3], [p1["id"], club["id"]], "Parts of a cell", None, "")
    fb = [cls.add_feedback("Great reasoning, well explained", 1), cls.add_feedback("Show your working for full marks", -1),
          cls.add_feedback("Revise: series vs parallel meters", 0), cls.add_feedback("Late submission", -2)]
    answers = {"Aarav Sharma": [0, 1, 2, 2, 1], "Diya Patel": [0, 1, 2, 0, 1], "Ishaan Verma": [1, 1, 2, 2, 0], "Ananya Iyer": [0, 3, 2, 2, 1],
               "Kabir Singh": [0, 1, 0, 2, None], "Rohan Das": [0, 1, 2, 2, 1], "Saanvi Gupta": [2, 1, 2, 1, 1], "Zara Khan": [0, 1, 2, 2, 1]}
    lesson0 = json.loads((jobs_dir / ids[0] / "lesson.json").read_text())
    for i, (n, ans) in enumerate(answers.items()):
        sub = cls.submit(a1["id"], sid[n], ans, lesson0["quiz"])
        if i % 3 == 0:
            cls.grade(sub["id"], [fb[0]["id"]] if sub["auto"] >= 8 else [fb[1]["id"]], "")
    lesson3 = json.loads((jobs_dir / ids[3] / "lesson.json").read_text())
    for n, ans in {"Kabir Singh": [0, 1, 2], "Diya Patel": [0, 1, 1], "Aarav Sharma": [0, 0, 2]}.items():
        cls.submit(a2["id"], sid[n], ans, lesson3["quiz"])

    jobs = JobManager(cfg, SlowPipeline)
    if not args.no_live:
        jobs.submit("Explain chemical bonding (ionic and covalent) for class 9 with a quiz and a podcast", {"language": "auto"})
        jobs.submit("Newton's laws of motion: flashcards and a short video", {"language": "auto"})
    app = create_app(cfg, jobs)
    print(f"GateMoE demo UI on http://127.0.0.1:{args.port}/  (data in {data})")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
