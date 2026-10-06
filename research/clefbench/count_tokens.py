"""Count Clef-flash prompt tokens for a router schema, mirroring joint_schema_model.encode_record
(and llama.cpp fill_task_joint: pieces tokenized one by one). Reads tokenizer.json path from argv[1]."""
import json
import sys

from tokenizers import Tokenizer

tok = Tokenizer.from_file(sys.argv[1])


def t(text):
    return tok.encode(text, add_special_tokens=False).ids


def render(v):
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


SYSTEM = ("Read the complete state and schema. Decide every field jointly. Each answer "
          "must be exactly one of that field's allowed options.")


def opts(q):
    if q["type"] == "noul":
        c = {"true": "The proposition is true or the answer is yes.",
             "false": "The proposition is false or the answer is no."}
        c.update(q.get("criteria") or {})
        return [(k, c[k]) for k in ("true", "false")]
    if q["type"] == "choice":
        return sorted((str(k), v) for k, v in q["criteria"].items())
    return [(str(i), v) for i, v in enumerate(q["criteria"])]


def encode(state, questions):
    schema = t("\n\nSCHEMA FIELDS:\n")
    for i, (qid, q) in enumerate(questions.items()):
        schema += t(f"\nFIELD {i+1}\nID: {qid}\nTYPE: {q['type']}\nINSTRUCTION: ")
        schema += t(render(q.get("instructions") or qid))
        schema += t("\nALLOWED OPTIONS:\n")
        for j, (oid, d) in enumerate(opts(q)):
            schema += t(f"OPTION {j+1}: ")
            sem = {"option_id": oid}
            if d is not None:
                sem["description"] = d
            schema += t(render(sem))
            schema += t("\n")
        schema += t("END FIELD\n")
    prefix = t(f"<|im_start|>system\n{SYSTEM}<|im_end|>\n<|im_start|>user\nSTATE:\n")
    suffix = t("\n<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\nJOINT SCHEMA DECISIONS:")
    st = t(render(state))
    return len(prefix), len(st), len(schema), len(suffix)


NOUL = lambda ins: {"type": "noul", "instructions": ins}

router_full = {
    "domain": {"type": "choice", "instructions": "Which subject area is the learner asking about?",
               "criteria": {"math": "Mathematics, statistics, geometry, algebra, calculus",
                            "physics": "Physics and astronomy",
                            "chemistry": "Chemistry",
                            "biology": "Biology, medicine, life science",
                            "computing": "Programming, software, computer science, AI",
                            "history": "History, politics, society",
                            "geography": "Geography, earth science, environment",
                            "language": "Languages, literature, writing",
                            "economics": "Economics, business, finance",
                            "arts": "Art, music, design",
                            "other": "Anything else"}},
    "level": {"type": "score", "instructions": "What level of explanation does the learner need?",
              "criteria": ["complete beginner", "some background", "advanced"]},
    "notes": NOUL("Should the system produce written study notes?"),
    "flashcards": NOUL("Should the system produce flashcards for memorisation?"),
    "quiz": NOUL("Should the system produce a quiz to test understanding?"),
    "podcast": NOUL("Should the system produce a two-voice audio podcast explanation?"),
    "video": NOUL("Should the system produce an explainer video?"),
    "video_engine": {"type": "choice", "instructions": "If a video is produced, which renderer fits the topic best?",
                     "criteria": {"manim": "Mathematical animation: equations, graphs, geometry, physics motion",
                                  "hyperframes": "HTML motion graphics: diagrams, timelines, text, processes",
                                  "none": "No video is needed"}},
    "code": NOUL("Should the system produce runnable code examples?"),
    "needs_lookup": NOUL("Does answering need facts from the offline encyclopedia rather than general knowledge?"),
    "out_of_scope": NOUL("Is the request unsafe, not about learning, or impossible to serve?"),
}

router_lean = {  # same 11 questions, no option descriptions beyond keys, short instructions
    "domain": {"type": "choice", "instructions": "Subject?",
               "criteria": {k: None for k in ["math", "physics", "chemistry", "biology", "computing", "history",
                                              "geography", "language", "economics", "arts", "other"]}},
    "level": {"type": "score", "instructions": "Level?", "criteria": ["beginner", "intermediate", "advanced"]},
    "notes": NOUL("Notes?"), "flashcards": NOUL("Flashcards?"), "quiz": NOUL("Quiz?"),
    "podcast": NOUL("Podcast?"), "video": NOUL("Video?"),
    "video_engine": {"type": "choice", "instructions": "Video renderer?",
                     "criteria": {"manim": None, "hyperframes": None, "none": None}},
    "code": NOUL("Code?"), "needs_lookup": NOUL("Needs encyclopedia lookup?"), "out_of_scope": NOUL("Out of scope?"),
}

states = {
    "short": "I want to learn how photosynthesis works.",
    "medium": ("I want to learn how photosynthesis works. I'm in 11th grade, I understand basic chemistry. "
               "Can you make me a short video and some flashcards so I can revise before my exam on Friday?"),
}

for name, qs in [("full (11 q, described options)", router_full), ("lean (11 q, bare keys)", router_lean)]:
    for sname, s in states.items():
        p, st, sc, sf = encode(s, qs)
        n_opt = sum(len(opts(q)) for q in qs.values())
        print(f"{name:34s} state={sname:6s} prefix={p:3d} state={st:3d} schema={sc:4d} suffix={sf:3d} "
              f"TOTAL={p+st+sc+sf:4d} options={n_opt}")

# per-question cost of a single noul with default descriptions
p, st, sc1, sf = encode("x", {"a": NOUL("Should the system produce a quiz?")})
p, st, sc0, sf = encode("x", {"a": NOUL("Should the system produce a quiz?"), "b": NOUL("Should the system produce a quiz?")})
print("one noul question costs ~", sc0 - sc1, "tokens (default true/false descriptions)")
