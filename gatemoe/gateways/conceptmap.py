"""CONCEPT-MAP SPECIALIST: turns the notes' concept links into a Graphviz diagram (SVG + PNG).

The generator only writes data ({from, label, to} triples inside the notes JSON). This module
builds the DOT text itself: node ids are generated, every label is escaped, and ``dot`` runs as a
sandboxed child process (CPU cap, timeout, no network). It costs milliseconds of CPU on a Pi.
"""
from __future__ import annotations

import re
import shutil
import time
import unicodedata
from pathlib import Path

from ..config import Config
from .video.sandbox import run_limited

MAX_NODES = 14
MAX_EDGES = 16


def _clean(s, limit: int) -> str:
    """Plain one-line text: no control/format characters except joiners (Indic scripts need ZWJ/ZWNJ)."""
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", str(s or ""))
    s = "".join(ch for ch in s if ch in "‌‍" or unicodedata.category(ch)[0] != "C")
    return " ".join(s.split())[:limit].strip()


def _wrap(s: str, width: int) -> list[str]:
    lines, cur = [], ""
    for word in s.split():
        if cur and len(cur) + 1 + len(word) > width:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        lines.append(cur)
    return lines or [""]


def dot_string(s: str, width: int = 18) -> str:
    """A DOT double-quoted string. Backslashes are replaced (DOT reads escapes such as \\N or \\G in
    labels), quotes escaped, and our own line breaks inserted as centred \\n."""
    s = "".join(ch for ch in s if ch in "\u200c\u200d" or unicodedata.category(ch)[0] != "C")
    s = s.replace("\\", "/").replace('"', '\\"')
    return '"' + "\\n".join(_wrap(s, width)) + '"'


def normalise(edges) -> tuple[list[str], list[tuple[int, int, str]]]:
    """Deduplicate concepts (case-insensitive), drop self-loops/duplicates, cap the size."""
    nodes: list[str] = []
    index: dict[str, int] = {}
    out: list[tuple[int, int, str]] = []
    seen = set()

    def node(label: str) -> int | None:
        key = label.casefold()
        if key not in index:
            if len(nodes) >= MAX_NODES:
                return None
            index[key] = len(nodes)
            nodes.append(label)
        return index[key]

    for e in edges or []:
        if not isinstance(e, dict):
            continue
        a, b = _clean(e.get("from"), 48), _clean(e.get("to"), 48)
        if not a or not b or a.casefold() == b.casefold():
            continue
        ia, ib = node(a), node(b)
        if ia is None or ib is None or (ia, ib) in seen:
            continue
        seen.add((ia, ib))
        out.append((ia, ib, _clean(e.get("label"), 32)))
        if len(out) >= MAX_EDGES:
            break
    used = sorted({i for a, b, _ in out for i in (a, b)})
    remap = {old: new for new, old in enumerate(used)}
    return [nodes[i] for i in used], [(remap[a], remap[b], lab) for a, b, lab in out]


def build_dot(edges, font: str = "Noto Sans", title: str = "") -> str:
    nodes, links = normalise(edges)
    if len(links) < 2:
        raise ValueError("concept map needs at least two valid links")
    font = dot_string(_clean(font, 60) or "Noto Sans", 200)
    lines = [
        "digraph concept_map {",
        f'  graph [rankdir=TB, bgcolor="white", pad=0.25, nodesep=0.35, ranksep=0.45, fontname={font}];',
        f'  node [shape=box, style="rounded,filled", fillcolor="#eef2ff", color="#2563eb", penwidth=1.2, '
        f'fontname={font}, fontsize=13, margin="0.14,0.07"];',
        f'  edge [color="#5b6673", fontcolor="#374151", fontname={font}, fontsize=10, arrowsize=0.7];',
    ]
    if title:
        lines.append(f"  labelloc=t; fontsize=15; label={dot_string(_clean(title, 80), 60)};")
    for i, label in enumerate(nodes):
        lines.append(f"  n{i} [label={dot_string(label)}];")
    for a, b, label in links:
        attr = f" [label={dot_string(label, 16)}]" if label else ""
        lines.append(f"  n{a} -> n{b}{attr};")
    lines.append("}")
    return "\n".join(lines) + "\n"


class ConceptMapGateway:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.exe = cfg.get("paths.dot", "dot")

    def enabled(self) -> bool:
        return bool(self.cfg.get("generation.concept_map", True))

    def available(self) -> bool:
        return self.enabled() and bool(shutil.which(self.exe) or Path(self.exe).exists())

    def render(self, edges, lang: str, out_dir: Path, title: str = "") -> dict:
        font = self.cfg.language(lang).get("font", "Noto Sans")
        dot = build_dot(edges, font=font, title=title)
        t0 = time.perf_counter()
        src = out_dir / "concept_map.dot"
        src.write_text(dot, encoding="utf-8")
        dpi = int(self.cfg.get("concept_map.png_dpi", 150))
        # SVG at natural size (web page + handout), PNG at print resolution (-Gdpi would scale the SVG too).
        # svg:cairo draws text as glyph outlines shaped by Pango (correct Indic conjuncts on any device,
        # no fonts needed by the browser or Typst); plain SVG is the fallback for builds without cairo.
        jobs = [([self.exe, "-Tsvg:cairo"], "concept_map.svg"), ([self.exe, "-Tsvg"], "concept_map.svg"),
                ([self.exe, f"-Gdpi={dpi}", "-Tpng"], "concept_map.png")]
        done: set[str] = set()
        for argv, out in jobs:
            if out in done:
                continue
            proc = run_limited([*argv, "-o", out, src.name], cwd=str(out_dir),
                               timeout=float(self.cfg.get("concept_map.timeout_s", 60)),
                               cpu_seconds=int(self.cfg.get("concept_map.cpu_seconds", 30)))
            if proc.returncode == 0 and (out_dir / out).exists():
                done.add(out)
        if "concept_map.svg" not in done:
            raise RuntimeError(f"dot failed ({proc.returncode}): {proc.stderr[-400:]}")
        nodes, links = normalise(edges)
        return {"svg": "concept_map.svg", "png": "concept_map.png" if "concept_map.png" in done else None,
                "dot": src.name, "nodes": len(nodes),
                "edges": len(links), "seconds": round(time.perf_counter() - t0, 2)}
