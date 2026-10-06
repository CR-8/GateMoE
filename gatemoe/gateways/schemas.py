"""JSON schemas for every generator task (llama.cpp turns these into a decoding grammar).

Video beats use a FLAT schema: the planner picks a template id and fills only the slots that
template needs; deterministic, pre-tested templates do the rendering (small models write
free-form Manim/HTML that renders only ~27-40% of the time).
"""
from __future__ import annotations

# template id -> engines that can render it, and the slots it reads
VIDEO_TEMPLATES: dict[str, dict] = {
    "title":            {"engines": ["manim", "hyperframes"], "slots": "title, lines[0] = subtitle"},
    "bullets":          {"engines": ["manim", "hyperframes"], "slots": "title, lines (2-6 short bullets)"},
    "equation_steps":   {"engines": ["manim"], "slots": "title, equations (2-4 Typst math steps), lines[0] = caption"},
    "function_graph":   {"engines": ["manim"], "slots": "title, expression (in x, e.g. x**2 - 1), x_min, x_max, lines[0] = caption"},
    "array_steps":      {"engines": ["manim"], "slots": "title, values (sorted integers, 5-12), target (integer) - animates binary search"},
    "process_steps":    {"engines": ["hyperframes"], "slots": "title, lines (3-6 steps in order)"},
    "bar_chart":        {"engines": ["hyperframes"], "slots": "title, labels (2-8 short category names, e.g. \"10 V\"), values (exactly one plain number per label), lines[0] = caption"},
    "code_walkthrough": {"engines": ["hyperframes"], "slots": "title, code (<= 14 lines), highlight (1-based line numbers)"},
    "definition":       {"engines": ["hyperframes"], "slots": "title = term, lines[0] = definition, lines[1:] = examples"},
}


def templates_for(engine: str) -> list[str]:
    return [t for t, spec in VIDEO_TEMPLATES.items() if engine in spec["engines"]]


def _str(max_len: int, min_len: int = 1) -> dict:
    return {"type": "string", "minLength": min_len, "maxLength": max_len}


def _arr(items: dict, lo: int, hi: int) -> dict:
    return {"type": "array", "items": items, "minItems": lo, "maxItems": hi}


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props,
            "required": list(props) if required is None else required, "additionalProperties": False}


def plan_schema() -> dict:
    return _obj({
        "title": _str(120),
        "search_queries_en": _arr(_str(80), 1, 3),
        "search_queries_native": _arr(_str(80), 0, 3),
        "key_terms": _arr(_str(60), 3, 8),
        "outline": _arr(_str(140), 3, 6),
    })


def notes_schema() -> dict:
    return _obj({
        "title": _str(120),
        "sections": _arr(_obj({"heading": _str(100), "body": _str(700, 20)}), 3, 4),
        "key_points": _arr(_str(160), 3, 6),
        "glossary": _arr(_obj({"term": _str(60), "definition": _str(200)}), 2, 6),
    })


def flashcards_schema(n: int) -> dict:
    return _obj({"cards": _arr(_obj({"front": _str(100), "back": _str(180)}), n, n)})


def quiz_schema(n: int) -> dict:
    q = _obj({
        "question": _str(240),
        "options": _arr(_str(120), 4, 4),
        "answer_index": {"type": "integer", "minimum": 0, "maximum": 3},
        "explanation": _str(300),
    })
    return _obj({"questions": _arr(q, n, n)})


def podcast_schema(n: int) -> dict:
    turn = _obj({"speaker": {"type": "string", "enum": ["A", "B"]}, "text": _str(300)})
    return _obj({"title": _str(120), "turns": _arr(turn, max(4, n - 2), n + 2)})


def code_schema() -> dict:
    return _obj({
        "language": {"type": "string", "enum": ["python"]},
        "title": _str(120),
        "explanation": _str(1200),
        "code": _str(2500),
        "expected_output": _str(800, 0),
    })


def video_schema(engine: str, max_beats: int) -> dict:
    beat = _obj({
        "template": {"type": "string", "enum": templates_for(engine)},
        "title": _str(90),
        "narration": _str(300),
        "lines": _arr(_str(140), 0, 6),
        "equations": _arr(_str(120), 0, 4),
        "expression": _str(80, 0),
        "x_min": {"type": "number"},
        "x_max": {"type": "number"},
        "values": _arr({"type": "number"}, 0, 12),
        "labels": _arr(_str(40), 0, 8),
        "target": {"type": "number"},
        "code": _str(900, 0),
        "highlight": _arr({"type": "integer", "minimum": 1, "maximum": 40}, 0, 6),
    }, required=["template", "title", "narration"])
    return _obj({"title": _str(120), "beats": _arr(beat, 3, max_beats)})
