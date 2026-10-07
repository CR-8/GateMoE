"""HANDOUT SPECIALIST: typesets notes, glossary, flashcards and quiz (+ answer key) into a PDF.

Deterministic (no LLM time): Typst runs in-process from the ``typst`` wheel (no LaTeX, no
network). The lesson is passed as JSON through ``sys.inputs`` and inserted as text only.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from ..config import Config

TEMPLATE = Path(__file__).with_name("handout_template.typ")
_FONTS = ["Noto Sans", "Noto Sans Devanagari", "Noto Sans Kannada", "Noto Sans Tamil", "Noto Sans Telugu",
          "Noto Sans Bengali", "Noto Sans Malayalam", "Noto Sans Gujarati", "Noto Sans Gurmukhi",
          "Noto Sans Arabic", "Noto Sans CJK SC", "Noto Sans CJK JP", "Noto Sans CJK KR", "Noto Sans Math"]
LABELS = {"key_points": "Key points", "glossary": "Glossary", "flashcards": "Flashcards", "concept_map": "Concept map",
          "simulations": "Interactive simulations",
          "sim_hint": "Open these in GateMoE (Simulations tab) on a phone, tablet or laptop - they run offline.",
          "cut_hint": "Cut along the dashed lines and fold: question on the front, answer on the back.",
          "quiz": "Quiz", "answers": "Answer key", "sources": "Offline sources"}


def _plain(s) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"\1", str(s or "")).strip()


def _blocks(body: str) -> list[dict]:
    blocks, items = [], []
    for raw in str(body or "").splitlines():
        line = raw.strip()
        if re.match(r"^[-*•]\s+", line):
            items.append(_plain(re.sub(r"^[-*•]\s+", "", line)))
            continue
        if items:
            blocks.append({"kind": "list", "items": items})
            items = []
        if line:
            blocks.append({"kind": "par", "text": _plain(re.sub(r"^#+\s*", "", line))})
    if items:
        blocks.append({"kind": "list", "items": items})
    return blocks


def svg_size_pt(svg: str) -> tuple[float, float] | None:
    m = re.search(r'<svg[^>]*?width="([\d.]+)(?:pt)?"[^>]*?height="([\d.]+)(?:pt)?"', svg[:2000], re.S)
    return (float(m.group(1)), float(m.group(2))) if m else None


def concept_map_data(out_dir: Path | None, lesson: dict) -> tuple[str | None, float]:
    """The Graphviz SVG (passed to Typst as text, drawn with Typst's own fonts) and a print width:
    natural size, shrunk to fit the text width (~500 pt) and ~330 pt of height."""
    cm = lesson.get("concept_map") or {}
    if not out_dir or not cm.get("svg"):
        return None, 0.0
    p = out_dir / cm["svg"]
    if not p.is_file() or p.stat().st_size > 2_000_000:
        return None, 0.0
    svg = p.read_text(encoding="utf-8")
    size = svg_size_pt(svg)
    if not size:
        return None, 0.0
    w, h = size
    scale = min(1.0, 500 / w, 330 / h)
    return svg, round(w * scale, 1)


def handout_data(lesson: dict, lang_info: dict, lang: str, out_dir: Path | None = None) -> dict:
    notes = lesson.get("notes") or {}
    title = notes.get("title") or (lesson.get("plan") or {}).get("title") or lesson.get("request", "")[:100]
    font = lang_info.get("font", "Noto Sans")
    cmap_svg, cmap_w = concept_map_data(out_dir, lesson)
    return {
        "title": _plain(title), "subtitle": _plain(lesson.get("request", ""))[:200], "lang": lang,
        "fonts": [font] + [f for f in _FONTS if f != font],
        "footer": "GateMoE · offline lesson · AI-generated: check important facts",
        "labels": LABELS,
        "sections": [{"heading": _plain(s.get("heading")), "blocks": _blocks(s.get("body"))}
                     for s in notes.get("sections", [])],
        "key_points": [_plain(k) for k in notes.get("key_points", [])],
        "glossary": [{"term": _plain(g.get("term")), "definition": _plain(g.get("definition"))}
                     for g in notes.get("glossary", [])],
        "flashcards": [{"front": _plain(c.get("front")), "back": _plain(c.get("back"))}
                       for c in (lesson.get("flashcards") or {}).get("cards", [])],
        "quiz": [{"question": _plain(q.get("question")), "options": [_plain(o) for o in q.get("options", [])][:4],
                  "answer_index": int(q.get("answer_index", 0)) % 4, "explanation": _plain(q.get("explanation"))}
                 for q in (lesson.get("quiz") or {}).get("questions", []) if len(q.get("options", [])) == 4],
        "sources": [f"{s.get('title', '')} ({s.get('zim', '')})" for s in lesson.get("sources", [])],
        "concept_map": cmap_svg, "concept_map_w": cmap_w,
        "simulations": [f"{_plain(s.get('title'))} - PhET Interactive Simulations ({s.get('zim', '')})"
                        for s in lesson.get("simulations", [])],
    }


class HandoutGateway:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def available(self) -> bool:
        if not self.cfg.get("handout.enabled", True):
            return False
        import importlib.util
        return importlib.util.find_spec("typst") is not None

    def render(self, lesson: dict, lang: str, out_dir: Path) -> dict:
        import typst

        data = handout_data(lesson, self.cfg.language(lang), lang, out_dir)
        if not (data["sections"] or data["quiz"] or data["flashcards"]):
            raise ValueError("nothing to typeset")
        t0 = time.perf_counter()
        out = out_dir / "handout.pdf"
        typst.compile(str(TEMPLATE), output=str(out), root=str(TEMPLATE.parent),
                      sys_inputs={"data": json.dumps(data, ensure_ascii=False)})
        return {"pdf": out.name, "bytes": out.stat().st_size, "seconds": round(time.perf_counter() - t0, 2)}
