#!/usr/bin/env python3
"""Send educational routing requests to llama-server /v1/systemone and time them (stdlib only)."""
import json, sys, time, urllib.request

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8091"
OUT = sys.argv[2] if len(sys.argv) > 2 else None
PLAN = sys.argv[3] if len(sys.argv) > 3 else "full"

NOUL = lambda q: {"type": "noul", "instructions": q}
QUESTIONS = {
    "needs_notes":      NOUL("Should the system produce written study notes for this learner?"),
    "needs_flashcards": NOUL("Should the system produce flashcards for memorising facts or vocabulary?"),
    "needs_quiz":       NOUL("Should the system produce a quiz or practice questions?"),
    "needs_podcast":    NOUL("Should the system produce an audio podcast the learner can listen to?"),
    "needs_video":      NOUL("Should the system produce an animated explainer video?"),
    "needs_code":       NOUL("Should the system produce runnable code examples or coding exercises?"),
    "subject": {
        "type": "choice",
        "instructions": "Which subject area is the learner asking about?",
        "criteria": {"cs": "computer science and programming", "maths": "mathematics",
                     "physics": None, "chemistry": None, "biology": None,
                     "history": None, "language": "foreign languages and linguistics", "other": None},
    },
    "difficulty": {
        "type": "score",
        "instructions": "What level should the material be pitched at?",
        "criteria": ["beginner", "intermediate", "advanced"],
    },
    "video_style": {
        "type": "choice",
        "instructions": "If a video is made, which renderer fits the topic best?",
        "criteria": {"manim_math": "Manim animation for equations, graphs and geometry",
                     "html_motion": "HTML/CSS/JS motion graphics for diagrams, timelines and text",
                     "none": "no video"},
    },
    "voice_mode": {
        "type": "choice",
        "instructions": "If audio is made, how many voices should it use?",
        "criteria": {"single_narrator": "one narrator reading", "two_speaker_dialogue": "two hosts in conversation"},
    },
}

REQS = {
    "binary_search": "I want to learn how binary search works, with a short quiz and something I can listen to on the bus.",
    "photosynthesis_podcast": "Can you make me a podcast-style conversation explaining photosynthesis? I'm in year 9.",
    "derivative_anim": "Show me why the derivative of x^2 is 2x, with an animation.",
    "python_listcomp": "I want to practise Python list comprehensions with some coding exercises.",
    "french_flashcards": "Make me flashcards for basic French greetings.",
    "ww1_summary": "Give me a short summary of the causes of World War 1 for my history exam tomorrow.",
}


def post(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(URL + path, data=data, headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=600) as r:
        raw = r.read()
    return json.loads(raw), (time.perf_counter() - t0) * 1000.0


def metrics():
    try:
        with urllib.request.urlopen(URL + "/metrics", timeout=30) as r:
            txt = r.read().decode()
    except Exception:
        return {}
    out = {}
    for line in txt.splitlines():
        if line.startswith("llamacpp:"):
            k, v = line.split(" ")
            out[k.split(":", 1)[1]] = float(v)
    return out


def tokenize(text):
    try:
        res, _ = post("/tokenize", {"content": text})
        return len(res.get("tokens", []))
    except Exception as e:
        return f"err:{e}"


def summarize(ans):
    s = {}
    for k, a in ans.items():
        if a["type"] == "noul":
            s[k] = round(a["noul"], 3)
        elif a["type"] == "choice":
            s[k] = f'{a["choice"]}({a["probabilities"][a["choice"]]:.2f})'
        else:
            s[k] = f'{a["score"]:.2f}'
    return s


def run(label, state, questions=QUESTIONS):
    m0 = metrics()
    res, ms = post("/v1/systemone", {"state": "Learner message: " + state, "questions": questions})
    m1 = metrics()
    d = {k: m1.get(k, 0) - m0.get(k, 0) for k in ("prompt_tokens_total", "prompt_seconds_total", "tokens_predicted_total")}
    # cached-prompt counter name varies; record any *cached* counter delta
    for k in m1:
        if "cache" in k:
            d[k] = m1[k] - m0.get(k, 0)
    pp_tps = d["prompt_tokens_total"] / d["prompt_seconds_total"] if d.get("prompt_seconds_total") else None
    rec = {"label": label, "latency_ms": round(ms, 1), "input_tokens": res.get("usage", {}).get("input_tokens"),
           "metrics_delta": d, "prefill_tps_metrics": round(pp_tps, 2) if pp_tps else None,
           "decisions": summarize(res.get("answers", {}))}
    print(json.dumps(rec), flush=True)
    return rec


if __name__ == "__main__":
    recs = []
    if PLAN in ("full",):
        recs.append(run("cold:binary_search", REQS["binary_search"]))
        for i in range(3):
            recs.append(run(f"warm_repeat{i+1}:binary_search", REQS["binary_search"]))
        for k in list(REQS)[1:]:
            recs.append(run(f"new_state:{k}", REQS[k]))
        recs.append(run("repeat_after_others:binary_search", REQS["binary_search"]))
        # token accounting: static schema vs dynamic state
        st = {k: tokenize("Learner message: " + v) for k, v in REQS.items()}
        print(json.dumps({"state_tokens_via_tokenize": st}), flush=True)
        # scaling probes: fewer questions
        recs.append(run("probe:1_question", REQS["binary_search"], {"subject": QUESTIONS["subject"]}))
        six = {k: QUESTIONS[k] for k in list(QUESTIONS)[:6]}
        recs.append(run("probe:6_noul_only", REQS["binary_search"], six))
        # long state probe (~4x state length)
        long_state = REQS["binary_search"] + " " + " ".join([
            "I am a second-year student and I already know arrays and loops but recursion confuses me.",
            "I commute for forty minutes each way and prefer listening rather than reading on my phone.",
            "My exam covers searching and sorting algorithms, big-O notation and simple proofs of correctness.",
            "Please keep things practical and include a worked example with a sorted list of numbers."])
        recs.append(run("probe:long_state", long_state))
    elif PLAN == "quick":
        recs.append(run("quick:binary_search", REQS["binary_search"]))
    elif PLAN == "six":
        for k, v in REQS.items():
            recs.append(run(f"q:{k}", v))
    elif PLAN == "probes":
        q = QUESTIONS
        recs.append(run("probe:1_noul", REQS["binary_search"], {"needs_quiz": q["needs_quiz"]}))
        recs.append(run("probe:2_noul", REQS["binary_search"], {"needs_quiz": q["needs_quiz"], "needs_podcast": q["needs_podcast"]}))
        recs.append(run("probe:9q_no_voice", REQS["binary_search"], {k: v for k, v in q.items() if k != "voice_mode"}))
        # same 10 questions, terse wording and null option descriptions
        terse = {
            "notes": NOUL("Make study notes?"), "flashcards": NOUL("Make flashcards?"), "quiz": NOUL("Make a quiz?"),
            "podcast": NOUL("Make a podcast?"), "video": NOUL("Make a video?"), "code": NOUL("Make code?"),
            "subject": {"type": "choice", "instructions": "Subject?", "criteria": {k: None for k in ["cs", "maths", "physics", "chemistry", "biology", "history", "language", "other"]}},
            "difficulty": {"type": "score", "instructions": "Level?", "criteria": ["beginner", "intermediate", "advanced"]},
            "video_style": {"type": "choice", "instructions": "Video renderer?", "criteria": {"manim_math": None, "html_motion": None, "none": None}},
            "voice_mode": {"type": "choice", "instructions": "Audio voices?", "criteria": {"single_narrator": None, "two_speaker_dialogue": None}},
        }
        for k in ("binary_search", "ww1_summary"):
            recs.append(run(f"terse10:{k}", REQS[k], terse))
    if OUT:
        with open(OUT, "w") as f:
            json.dump(recs, f, indent=1)
