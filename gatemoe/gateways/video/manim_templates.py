"""GateMoE Manim beat templates (Manim Community 0.21.x, Cairo renderer).

The generator LLM NEVER writes Python. It writes a small JSON "beat spec";
this module validates it (``validate_spec``) and maps it onto fixed,
pre-tested Scene classes. Nothing from the spec is ever passed to
``eval``/``exec``/``compile``: function plots go through a whitelisted
``ast`` tree-walker, Typst math is checked against a denylist (no ``#``
code mode, no ``$``), and all text goes through ``Text`` (no Pango markup).

Usage (inside the render subprocess, see render_beat.py)::

    GATEMOE_BEAT_JSON=/path/beat.json manim render ... templates.py AutoBeat

Environment variables
    GATEMOE_BEAT_JSON      path to the beat JSON (required)
    GATEMOE_HOLD_IN_MANIM  "1" -> end the scene with self.wait(spec["hold"]).
                           Default "0": the final frame is held later by
                           ffmpeg (tpad), which is much cheaper.

Beat types: title, equation, graph, array_steps, bullets (see BEAT_SCHEMAS).
"""

from __future__ import annotations

import ast
import json
import math
import os
import re
import unicodedata
from functools import lru_cache

import numpy as np
import typst as _typst
from manim import (
    DOWN,
    LEFT,
    ORIGIN,
    RIGHT,
    UP,
    Arrow,
    Axes,
    Create,
    Dot,
    FadeIn,
    FadeOut,
    FadeTransform,
    GrowFromCenter,
    Line,
    MathTypst,
    Scene,
    Square,
    SurroundingRectangle,
    Text,
    TransformMatchingShapes,
    VGroup,
    VMobject,
    Write,
    config,
)

# --------------------------------------------------------------------------
# Typst speed-up: every typst.compile() call rescans ALL system fonts
# (measured 110 ms/call here with ~440 fonts; 5 ms with ignore_system_fonts).
# Manim compiles one document per MathTypst *and per axis-number glyph*, and
# math only needs Typst's embedded New Computer Modern fonts -> skip the scan.
# --------------------------------------------------------------------------
if not getattr(_typst.compile, "_gatemoe", False):
    _typst_compile_orig = _typst.compile

    def _typst_compile_fast(*args, **kwargs):
        kwargs.setdefault("ignore_system_fonts", True)
        return _typst_compile_orig(*args, **kwargs)

    _typst_compile_fast._gatemoe = True
    _typst.compile = _typst_compile_fast

# --------------------------------------------------------------------------
# Visual constants (frame is 14.22 x 8 Manim units at 16:9 regardless of px)
# --------------------------------------------------------------------------
BG = "#0E1726"
FG = "#F5F7FA"
MUTED = "#9AA5B1"
ACCENT = "#FFC857"
CURVE = "#4FC3F7"
GOOD = "#7BD389"
POINTER_COLORS = ["#FF6B6B", "#FFC857", "#4FC3F7", "#C792EA"]
FRAME_W = 14.2
SAFE_W = 12.6  # usable width inside margins
SAFE_H = 7.0


class SpecError(ValueError):
    """Invalid beat spec. The message is meant to be fed back to the LLM."""


# --------------------------------------------------------------------------
# Script detection + font choice (fc-match :lang=hi gives FreeSans on many
# systems, so we use an explicit script -> Noto family table instead).
# --------------------------------------------------------------------------
_SCRIPT_RANGES = [
    ("Devanagari", 0x0900, 0x097F), ("Devanagari", 0xA8E0, 0xA8FF),
    ("Bengali", 0x0980, 0x09FF), ("Gurmukhi", 0x0A00, 0x0A7F),
    ("Gujarati", 0x0A80, 0x0AFF), ("Oriya", 0x0B00, 0x0B7F),
    ("Tamil", 0x0B80, 0x0BFF), ("Telugu", 0x0C00, 0x0C7F),
    ("Kannada", 0x0C80, 0x0CFF), ("Malayalam", 0x0D00, 0x0D7F),
    ("Sinhala", 0x0D80, 0x0DFF), ("Thai", 0x0E00, 0x0E7F),
    ("Arabic", 0x0600, 0x06FF), ("Arabic", 0x0750, 0x077F),
    ("Arabic", 0xFB50, 0xFDFF), ("Arabic", 0xFE70, 0xFEFF),
    ("OlChiki", 0x1C50, 0x1C7F), ("MeeteiMayek", 0xABC0, 0xABFF),
    ("Kana", 0x3040, 0x30FF), ("Hangul", 0xAC00, 0xD7AF),
    ("Hangul", 0x1100, 0x11FF), ("Hangul", 0x3130, 0x318F),
    ("Han", 0x4E00, 0x9FFF), ("Han", 0x3400, 0x4DBF),
    ("Han", 0x3000, 0x303F), ("Han", 0xFF00, 0xFFEF),
    ("Cyrillic", 0x0400, 0x04FF), ("Greek", 0x0370, 0x03FF),
    ("Hebrew", 0x0590, 0x05FF),
]

_SCRIPT_FONTS = {
    "Devanagari": ["Noto Sans Devanagari", "Noto Serif Devanagari"],
    "Bengali": ["Noto Sans Bengali", "Noto Serif Bengali"],
    "Gurmukhi": ["Noto Sans Gurmukhi", "Noto Serif Gurmukhi"],
    "Gujarati": ["Noto Sans Gujarati", "Noto Serif Gujarati"],
    "Oriya": ["Noto Sans Oriya", "Noto Sans Odia"],
    "Tamil": ["Noto Sans Tamil", "Noto Serif Tamil"],
    "Telugu": ["Noto Sans Telugu", "Noto Serif Telugu"],
    "Kannada": ["Noto Sans Kannada", "Noto Serif Kannada"],
    "Malayalam": ["Noto Sans Malayalam", "Noto Serif Malayalam"],
    "Sinhala": ["Noto Sans Sinhala", "Noto Serif Sinhala"],
    "Thai": ["Noto Sans Thai", "Noto Serif Thai"],
    "Arabic": ["Noto Naskh Arabic", "Noto Sans Arabic"],
    "OlChiki": ["Noto Sans Ol Chiki"],
    "MeeteiMayek": ["Noto Sans Meetei Mayek"],
    "Hebrew": ["Noto Sans Hebrew"],
    "Kana": ["Noto Sans CJK JP"],
    "Hangul": ["Noto Sans CJK KR"],
    "Han": ["Noto Sans CJK SC"],
    "Latin": ["Noto Sans", "DejaVu Sans"],
    "Cyrillic": ["Noto Sans", "DejaVu Sans"],
    "Greek": ["Noto Sans", "DejaVu Sans"],
}
# language overrides where the script alone is ambiguous
_LANG_FONTS = {
    "ur": ["Noto Nastaliq Urdu", "Noto Naskh Arabic"],
    "ja": ["Noto Sans CJK JP"],
    "ko": ["Noto Sans CJK KR"],
    "zh-tw": ["Noto Sans CJK TC"], "zh-hant": ["Noto Sans CJK TC"],
    "zh-hk": ["Noto Sans CJK HK"],
}
RTL_SCRIPTS = {"Arabic", "Hebrew"}
_TALL_SCRIPTS = {"Devanagari", "Bengali", "Gurmukhi", "Gujarati", "Oriya", "Tamil", "Telugu",
                 "Kannada", "Malayalam", "Sinhala", "Thai", "Arabic", "MeeteiMayek"}


def script_of_char(ch: str) -> str | None:
    cp = ord(ch)
    for name, lo, hi in _SCRIPT_RANGES:
        if lo <= cp <= hi:
            return name
    if ch.isalpha():
        return "Latin"
    return None


def dominant_script(text: str) -> str:
    counts: dict[str, int] = {}
    for ch in text:
        s = script_of_char(ch)
        if s:
            counts[s] = counts.get(s, 0) + 1
    if not counts:
        return "Latin"
    # Prefer any non-Latin script present (e.g. Hindi sentence with "DNA").
    non_latin = {k: v for k, v in counts.items() if k != "Latin"}
    pool = non_latin or counts
    # Japanese text mixes Han + Kana -> treat as Kana if any kana present.
    if "Kana" in pool and "Han" in pool:
        return "Kana"
    return max(pool, key=pool.get)


@lru_cache(maxsize=1)
def installed_fonts() -> frozenset[str]:
    import manimpango

    return frozenset(manimpango.list_fonts())


def pick_font(text: str, lang: str | None = None) -> str:
    """Return an installed font family for `text` (empty string = Pango default)."""
    have = installed_fonts()
    script = dominant_script(text)
    cands: list[str] = []
    if lang:
        lang_l = lang.lower()
        cands += _LANG_FONTS.get(lang_l, []) + _LANG_FONTS.get(lang_l.split("-")[0], [])
    cands += _SCRIPT_FONTS.get(script, []) + ["Noto Sans", "DejaVu Sans"]
    for fam in cands:
        if fam in have:
            return fam
    return ""


def is_rtl(text: str) -> bool:
    """True when most letters are in an RTL script (Arabic/Urdu/Hebrew)."""
    scripts = [script_of_char(c) for c in text]
    letters = [x for x in scripts if x]
    return bool(letters) and sum(x in RTL_SCRIPTS for x in letters) > len(letters) / 2


# --------------------------------------------------------------------------
# Line wrapping (Manim's Text has no wrap width). Width units: CJK=2,
# non-spacing marks (Indic virama, anusvara, nukta...)=0, everything else
# (incl. spacing vowel signs, category Mc) = 1. make_text() then measures
# the real Pango width and re-wraps proportionally if a line is too wide.
# --------------------------------------------------------------------------
_NO_LINE_START = set("，。、；：？！）」』】》,.;:?!)%")
_CJK_CLASS = "　-鿿가-힯＀-￯"
_TOKEN_RE = re.compile(rf"[{_CJK_CLASS}]|[^\s{_CJK_CLASS}]+|\s+")


def _char_units(ch: str) -> int:
    if unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    return 1


def text_units(s: str) -> int:
    return sum(_char_units(c) for c in s)


def wrap_text(text: str, max_units: int) -> str:
    """Greedy wrap at spaces (and between CJK chars). Never splits a word, so
    Indic conjuncts / grapheme clusters are never broken."""
    out_lines: list[str] = []
    for para in text.split("\n"):
        line, line_u = "", 0
        for tok in _TOKEN_RE.findall(para):
            if tok.isspace():
                if line:
                    line += " "
                    line_u += 1
                continue
            u = text_units(tok)
            if line and line_u + u > max_units and tok[0] not in _NO_LINE_START:
                out_lines.append(line.rstrip())
                line, line_u = "", 0
            line += tok
            line_u += u
        out_lines.append(line.rstrip())
    return "\n".join(out_lines)


def make_text(s: str, *, font_size: float, lang: str | None, color: str = FG,
              max_width: float = SAFE_W, max_units: int | None = None,
              weight: str = "NORMAL") -> Text:
    font = pick_font(s, lang)
    # scripts with stacked marks above/below need more leading than Latin
    spacing = 0.65 if dominant_script(s) in _TALL_SCRIPTS else 0.4  # extra leading x font_size (manim default 0.3)

    def build(src):
        return Text(src, font=font, font_size=font_size, color=color, line_spacing=spacing,
                    weight=weight, warn_missing_font=False)

    t = build(wrap_text(s, max_units) if max_units else s)
    # re-wrap using the measured width (scripts differ a lot in glyph width)
    for _ in range(2):
        if not max_units or t.width <= max_width:
            break
        max_units = max(4, int(max_units * max_width / t.width * 0.95))
        t = build(wrap_text(s, max_units))
    if t.width > max_width:  # one unbreakable long word: shrink
        t.scale_to_fit_width(max_width)
    return t


# --------------------------------------------------------------------------
# Safe function expressions for GraphBeat: ast whitelist + tree-walker.
# --------------------------------------------------------------------------
_FUNCS = {
    "sin": np.sin, "cos": np.cos, "tan": np.tan, "asin": np.arcsin,
    "acos": np.arccos, "atan": np.arctan, "sinh": np.sinh, "cosh": np.cosh,
    "tanh": np.tanh, "exp": np.exp, "log": np.log, "ln": np.log,
    "log10": np.log10, "log2": np.log2, "sqrt": np.sqrt, "abs": np.abs,
}
_CONSTS = {"pi": math.pi, "e": math.e}
_BINOPS = {ast.Add: np.add, ast.Sub: np.subtract, ast.Mult: np.multiply,
           ast.Div: np.divide, ast.Pow: np.power}
MAX_EXPR_CHARS = 160
MAX_EXPR_NODES = 80

_IMPLICIT_FN = "|".join(sorted(_FUNCS, key=len, reverse=True))


def normalize_expr(src: str) -> str:
    s = src.strip().replace("^", "**").replace("−", "-").replace("×", "*")
    s = re.sub(r"^\s*(?:y|f\s*\(\s*x\s*\))\s*=", "", s).strip()  # drop "y =" / "f(x) ="
    # implicit multiplication: 2x, 3(x+1), 2sin(x), 2pi, )(, )x, x(
    s = re.sub(rf"(?<![A-Za-z_\d.])(\d+(?:\.\d+)?)\s*(?=(?:x\b|pi\b|\(|(?:{_IMPLICIT_FN})\s*\())", r"\1*", s)
    s = re.sub(r"\)\s*(?=[(x\d])", ")*", s)
    s = re.sub(r"\bx\s*(?=\()", "x*", s)
    return s


def parse_expr(src: str) -> ast.Expression:
    if not isinstance(src, str) or not src.strip():
        raise SpecError("expr must be a non-empty string")
    if len(src) > MAX_EXPR_CHARS:
        raise SpecError(f"expr longer than {MAX_EXPR_CHARS} chars")
    s = normalize_expr(src)
    try:
        tree = ast.parse(s, mode="eval")
    except SyntaxError as e:
        raise SpecError(f"expr is not a valid formula: {src!r} ({e.msg})") from None
    n = 0
    for node in ast.walk(tree):
        n += 1
        if n > MAX_EXPR_NODES:
            raise SpecError("expr too complex")
        if isinstance(node, (ast.Expression, ast.Load)) or type(node) in _BINOPS:
            continue
        if isinstance(node, (ast.UAdd, ast.USub)):
            continue
        if isinstance(node, ast.BinOp):
            if type(node.op) not in _BINOPS:
                raise SpecError(f"operator {type(node.op).__name__} not allowed (use + - * / ** )")
            continue
        if isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, (ast.UAdd, ast.USub)):
                raise SpecError("only unary + and - allowed")
            continue
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise SpecError("only numeric constants allowed")
            continue
        if isinstance(node, ast.Name):
            if node.id != "x" and node.id not in _CONSTS and node.id not in _FUNCS:
                raise SpecError(f"unknown name {node.id!r}; allowed: x, pi, e, {', '.join(_FUNCS)}")
            continue
        if isinstance(node, ast.Call):
            if (not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS
                    or len(node.args) != 1 or node.keywords):
                raise SpecError("only single-argument calls to " + ", ".join(_FUNCS))
            continue
        raise SpecError(f"syntax element {type(node).__name__} not allowed")
    return tree


def eval_expr(tree: ast.Expression, x: np.ndarray) -> np.ndarray:
    """Vectorised evaluation of a pre-validated tree. All maths in float64,
    so 10**10**10 gives inf instead of hanging on a Python bigint."""

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            return np.float64(node.value)
        if isinstance(node, ast.Name):
            if node.id == "x":
                return x
            if node.id in _CONSTS:
                return np.float64(_CONSTS[node.id])
            raise SpecError(f"{node.id} used as a value")
        if isinstance(node, ast.UnaryOp):
            v = ev(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.BinOp):
            return _BINOPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.Call):
            return _FUNCS[node.func.id](ev(node.args[0]))
        raise SpecError("bad expression")

    with np.errstate(all="ignore"):
        y = ev(tree)
    return np.broadcast_to(np.asarray(y, dtype=np.float64), x.shape).copy()


def nice_axis(lo: float, hi: float, target_ticks: int = 8) -> tuple[float, float, float]:
    """Pick a 1/2/2.5/5 x 10^k tick step and snap [lo, hi] outward to it,
    preferring steps that need little overshoot and give ~target ticks."""
    span = hi - lo
    mag = 10 ** math.floor(math.log10(span / target_ticks))
    best = None
    for m in (0.5, 1, 2, 2.5, 5, 10, 20):
        step = m * mag
        a0, a1 = math.floor(lo / step + 1e-9) * step, math.ceil(hi / step - 1e-9) * step
        ticks = round((a1 - a0) / step)
        if not 3 <= ticks <= 14:
            continue
        score = (a1 - a0 - span) / span + 0.04 * abs(ticks - target_ticks)
        if best is None or score < best[0]:
            best = (score, a0, a1, step)
    if best is None:
        return lo, hi, span / target_ticks
    _, a0, a1, step = best
    if float(step).is_integer():  # integer steps -> integer tick labels ("2" not "2.0")
        step, a0, a1 = int(step), int(round(a0)), int(round(a1))
    return a0, a1, step


# --------------------------------------------------------------------------
# Typst math safety: math mode only. No '#' (code mode: #read, #import
# "@preview/..." would even try to download packages), no '$' (escape math
# mode), no comments, no raw/labels/refs. Verified: in math mode `read`,
# `eval`, `json` are "unknown variable" in typst 0.15.
# --------------------------------------------------------------------------
_TYPST_FORBIDDEN = ["#", "$", "`", "@", "//", "/*", "*/", "<<", ">>", "{{", "}}"]
MAX_MATH_CHARS = 200
_LATEX_BRACES_RE = re.compile(r"(?:\b(?:frac|dfrac|sqrt|vec|hat|bar|overline|underline|text|mathrm|mathbf|"
                              r"operatorname|binom|left|right)\s*\{)|[\^_]\s*\{|\}\s*\{")


def check_typst_math(s: str) -> str:
    if not isinstance(s, str) or not s.strip():
        raise SpecError("math must be a non-empty string")
    if len(s) > MAX_MATH_CHARS:
        raise SpecError(f"math longer than {MAX_MATH_CHARS} chars")
    for bad in _TYPST_FORBIDDEN:
        if bad in s:
            raise SpecError(f"math must be Typst math WITHOUT {bad!r} (no $ delimiters, no # code)")
    if "\\" in s and re.search(r"\\[A-Za-z]{2,}", s):
        raise SpecError("math looks like LaTeX (\\frac etc.); use Typst math: frac(a, b), sqrt(x), x^2, dif x")
    # LaTeX brace groups compile in Typst but render literal braces: frac{a}{b}, x^{2n}
    if _LATEX_BRACES_RE.search(s):
        raise SpecError("math uses LaTeX braces; in Typst write frac(a, b), sqrt(x), x^(2n), x_(i j) "
                        "(braces {} only for literal sets like {1, 2})")
    if s.count("(") != s.count(")") or s.count("[") != s.count("]") or s.count("{") != s.count("}"):
        raise SpecError("unbalanced brackets in math")
    return s.strip()


_UNKNOWN_VAR_RE = re.compile(r"unknown variable: ([A-Za-z]+)")


def typst_math_compiles(m: str) -> str:
    """Compile `$ m $` with Typst (~5 ms, embedded fonts only, sandbox root = a
    fresh temp dir). Auto-repairs the most common small-LLM mistake - unknown
    multi-letter identifiers: dx -> dif x, ab -> a b, speed -> "speed". Returns the (repaired)
    math or raises SpecError with Typst's message (feed it back to the LLM)."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="gm_typst_") as d:
        path = os.path.join(d, "m.typ")
        for _ in range(6):
            with open(path, "w", encoding="utf-8") as f:
                f.write("#set page(width: auto, height: auto, margin: 0pt)\n$ " + m + " $\n")
            try:
                _typst.compile(path, format="svg", root=d, ignore_system_fonts=True)
                return m
            except _typst.TypstError as e:
                msg = str(e).splitlines()[0] if str(e) else "Typst error"
                hit = _UNKNOWN_VAR_RE.search(str(e))
                if not hit:
                    raise SpecError(f"math does not compile: {msg}") from None
                name = hit.group(1)
                if re.fullmatch(r"d[a-z]", name):
                    fix = f"dif {name[1:]}"          # dx -> dif x
                elif len(name) == 2:
                    fix = " ".join(name)             # ab -> a b (product)
                else:
                    fix = f'"{name}"'                # speed -> "speed" (upright text)
                m2 = re.sub(rf"(?<![A-Za-z\"]){name}(?![A-Za-z\"])", fix, m)
                if m2 == m:
                    raise SpecError(f"math does not compile: {msg} (write multi-letter words as \"text\")") from None
                m = m2
    raise SpecError("math does not compile after repairs")


# --------------------------------------------------------------------------
# Spec validation
# --------------------------------------------------------------------------
BEAT_TYPES = ("title", "equation", "graph", "array_steps", "bullets")


_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\U000E0000-\U000E007F]")


def _str(d, key, maxlen, required=True, default=""):
    v = d.get(key, None)
    if v is None:
        if required:
            raise SpecError(f"missing '{key}'")
        return default
    if not isinstance(v, str):
        raise SpecError(f"'{key}' must be a string")
    v = v.strip()
    if required and not v:
        raise SpecError(f"'{key}' must not be empty")
    if len(v) > maxlen:
        raise SpecError(f"'{key}' longer than {maxlen} chars")
    if any(unicodedata.category(c) == "Cc" and c not in "\n\t" for c in v):
        raise SpecError(f"'{key}' contains control characters")
    # colour emoji render as blanks through Pango->SVG: drop them (keep ZWJ/ZWNJ for Indic)
    v = _EMOJI_RE.sub("", v).replace("\t", " ").strip()
    return v


def _num(d, key, lo, hi, default=None):
    v = d.get(key, default)
    if v is None:
        raise SpecError(f"missing '{key}'")
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise SpecError(f"'{key}' must be a number")
    if not (lo <= v <= hi):
        raise SpecError(f"'{key}' must be in [{lo}, {hi}]")
    return float(v)


def _const(v, key: str) -> float:
    """A finite number, or a constant expression string such as "-2*pi", "pi/2", "e^2"
    (same whitelist as graph expressions, but without x). JSON cannot express pi."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if math.isfinite(v):
            return float(v)
    elif isinstance(v, str) and 0 < len(v.strip()) <= 24:
        tree = parse_expr(v)
        if any(isinstance(n, ast.Name) and n.id == "x" for n in ast.walk(tree)):
            raise SpecError(f"'{key}' must not depend on x")
        val = float(eval_expr(tree, np.zeros(1))[0])
        if math.isfinite(val):
            return val
    raise SpecError(f"'{key}' values must be numbers or constant expressions like \"-2*pi\"")


def _range(d, key, required=True):
    v = d.get(key)
    if v is None and not required:
        return None
    if not isinstance(v, list) or len(v) != 2:
        raise SpecError(f"'{key}' must be [min, max] (numbers or constants like \"2*pi\")")
    a, b = _const(v[0], key), _const(v[1], key)
    if not (b > a) or (b - a) < 1e-3 or abs(a) > 1e4 or abs(b) > 1e4:
        raise SpecError(f"'{key}' needs min < max (span >= 0.001), |values| <= 1e4")
    return [a, b]


def validate_spec(spec: dict) -> dict:
    if not isinstance(spec, dict):
        raise SpecError("beat spec must be a JSON object")
    t = spec.get("type")
    if t not in BEAT_TYPES:
        raise SpecError(f"'type' must be one of {BEAT_TYPES}")
    out = {"type": t}
    lang = spec.get("lang")
    out["lang"] = lang if isinstance(lang, str) and re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*", lang) else None
    out["hold"] = _num(spec, "hold", 0, 120, 0)
    out["anim_seconds"] = _num(spec, "anim_seconds", 0.5, 6, 3.0)
    out["title"] = _str(spec, "title", 90, required=(t == "title"))

    if t == "title":
        out["subtitle"] = _str(spec, "subtitle", 140, required=False)
    elif t == "bullets":
        b = spec.get("bullets")
        if not isinstance(b, list) or not (1 <= len(b) <= 6):
            raise SpecError("'bullets' must be a list of 1-6 strings")
        out["bullets"] = [_str({"bullet": x}, "bullet", 140) for x in b]
    elif t == "equation":
        steps = spec.get("steps")
        if not isinstance(steps, list) or not (1 <= len(steps) <= 6):
            raise SpecError("'steps' must be a list of 1-6 objects {math, caption}")
        out["steps"] = []
        for st in steps:
            if not isinstance(st, dict):
                raise SpecError("each step must be an object {math, caption}")
            m = typst_math_compiles(check_typst_math(st.get("math")))
            if out["steps"] and re.sub(r"\s+", "", m) == re.sub(r"\s+", "", out["steps"][-1]["math"]):
                raise SpecError(f"step {len(out['steps']) + 1} repeats the previous step's math; "
                                "each step must change the expression (or merge them)")
            out["steps"].append({"math": m, "caption": _str(st, "caption", 120, required=False)})
        out["step_seconds"] = _num(spec, "step_seconds", 0.6, 6, 1.5)
        layout = spec.get("layout", "stack")
        if layout not in ("stack", "replace"):
            raise SpecError("'layout' must be 'stack' or 'replace'")
        out["layout"] = layout
    elif t == "graph":
        out["expr"] = spec.get("expr")
        tree = parse_expr(out["expr"])
        out["x_range"] = _range(spec, "x_range")
        out["y_range"] = _range(spec, "y_range", required=False)
        xs = np.linspace(*out["x_range"], 9)
        if not np.isfinite(eval_expr(tree, xs)).any() and not np.isfinite(
                eval_expr(tree, np.linspace(*out["x_range"], 401))).any():
            raise SpecError("expr is undefined everywhere on x_range")
        out["label"] = _str(spec, "label", 40, required=False)
        out["x_label"] = _str(spec, "x_label", 12, required=False, default="x")
        out["y_label"] = _str(spec, "y_label", 12, required=False, default="y")
        pts = spec.get("points", []) or []
        if not isinstance(pts, list) or len(pts) > 5:
            raise SpecError("'points' must be a list of at most 5 {x, label}")
        out["points"] = []
        for p in pts:
            if not isinstance(p, dict):
                raise SpecError("each point must be {x, label}")
            px = _const(p.get("x"), "points.x")
            if not out["x_range"][0] <= px <= out["x_range"][1]:
                raise SpecError(f"point x={px:g} is outside x_range")
            out["points"].append({"x": px, "label": _str(p, "label", 24, required=False)})
    elif t == "array_steps":
        vals = spec.get("values")
        if not isinstance(vals, list) or not (2 <= len(vals) <= 16):
            raise SpecError("'values' must be a list of 2-16 array elements (the data, e.g. [4, 9, 7, 1])")
        out["values"] = []
        for v in vals:
            if isinstance(v, bool) or not isinstance(v, (int, float, str)):
                raise SpecError("values must be numbers or short strings")
            sv = (f"{v:.4g}" if isinstance(v, float) else str(v)).strip()
            if not sv or len(sv) > 5:
                raise SpecError("each value must render to 1-5 characters")
            if re.search(r"[\s=\[\]{}()]", sv):
                raise SpecError(f"value {sv!r} is not an array element; 'values' holds the data only "
                                "(put pointer names in steps[].pointers, explanations in captions)")
            out["values"].append(sv)
        n = len(out["values"])
        steps = spec.get("steps")
        if not isinstance(steps, list) or not (1 <= len(steps) <= 12):
            raise SpecError("'steps' must be a list of 1-12 objects")
        out["steps"] = []
        names: list[str] = []
        for st in steps:
            if not isinstance(st, dict):
                raise SpecError("each step must be an object")
            ptrs = st.get("pointers", {}) or {}
            if not isinstance(ptrs, dict) or len(ptrs) > 4:
                raise SpecError("'pointers' must map up to 4 names to indices")
            clean_ptrs = {}
            for k, idx in ptrs.items():
                if not isinstance(k, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,5}", k):
                    raise SpecError("pointer names must be short identifiers like lo, mid, hi, i, j")
                if isinstance(idx, bool) or not isinstance(idx, int) or not (0 <= idx < n):
                    raise SpecError(f"pointer {k} index must be an integer in [0, {n - 1}]")
                clean_ptrs[k] = idx
                if k not in names:
                    names.append(k)
            if len(names) > 4:
                raise SpecError("at most 4 distinct pointer names per beat")

            def idx_list(key):
                v = st.get(key, []) or []
                if not isinstance(v, list) or not all(isinstance(i, int) and not isinstance(i, bool) and 0 <= i < n for i in v):
                    raise SpecError(f"'{key}' must be a list of indices in [0, {n - 1}]")
                return sorted(set(v))

            out["steps"].append({"pointers": clean_ptrs, "highlight": idx_list("highlight"),
                                 "dim": idx_list("dim"), "found": idx_list("found"),
                                 "caption": _str(st, "caption", 120, required=False)})
        out["pointer_names"] = names
        out["step_seconds"] = _num(spec, "step_seconds", 0.6, 6, 1.2)
    return out


def load_spec() -> dict:
    path = os.environ.get("GATEMOE_BEAT_JSON")
    if not path:
        raise SpecError("GATEMOE_BEAT_JSON not set")
    with open(path, encoding="utf-8") as f:
        raw = f.read(256 * 1024)
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SpecError(f"beat JSON does not parse: {e}") from None
    return validate_spec(spec)


# --------------------------------------------------------------------------
# Scenes
# --------------------------------------------------------------------------
class _BeatBase(Scene):
    spec: dict

    def setup(self):
        self.camera.background_color = BG
        if not hasattr(self, "spec") or self.spec is None:
            self.spec = load_spec()

    def finish(self):
        if os.environ.get("GATEMOE_HOLD_IN_MANIM", "0") == "1" and self.spec.get("hold", 0) > 0:
            self.wait(self.spec["hold"])
        elif not self.animations_played():
            self.wait(1 / config.frame_rate)

    def animations_played(self) -> bool:
        return getattr(self.renderer, "num_plays", 0) > 0

    def header(self):
        if not self.spec.get("title"):
            return None
        t = make_text(self.spec["title"], font_size=40, lang=self.spec["lang"],
                      color=ACCENT, max_units=46, weight="BOLD")
        t.to_edge(UP, buff=0.45)
        return t


class TitleBeat(_BeatBase):
    def construct(self):
        s, lang = self.spec, self.spec["lang"]
        a = s["anim_seconds"]
        title = make_text(s["title"], font_size=60, lang=lang, max_units=28, weight="BOLD")
        bar = Line(LEFT * 3, RIGHT * 3, color=ACCENT, stroke_width=6)
        group = [title, bar]
        sub = None
        if s.get("subtitle"):
            sub = make_text(s["subtitle"], font_size=32, lang=lang, color=MUTED, max_units=52)
            group.append(sub)
        VGroup(*group).arrange(DOWN, buff=0.45).move_to(ORIGIN)
        self.play(FadeIn(title, shift=UP * 0.3), run_time=a * 0.45)
        self.play(GrowFromCenter(bar), run_time=a * 0.2)
        if sub is not None:
            self.play(FadeIn(sub), run_time=a * 0.35)
        self.finish()


class BulletsBeat(_BeatBase):
    def construct(self):
        s, lang = self.spec, self.spec["lang"]
        a = s["anim_seconds"]
        head = self.header()
        rtl = is_rtl(" ".join(s["bullets"]))
        items = []
        for b in s["bullets"]:
            line = make_text(b, font_size=32, lang=lang, max_units=48, max_width=SAFE_W - 0.8)
            dot = Dot(radius=0.07, color=ACCENT)
            row = VGroup(line, dot) if rtl else VGroup(dot, line)
            row.arrange(RIGHT, buff=0.3, aligned_edge=UP)
            dot.shift(DOWN * 0.18)
            items.append(row)
        body = VGroup(*items).arrange(DOWN, buff=0.38, aligned_edge=RIGHT if rtl else LEFT)
        top = head.get_bottom()[1] - 0.5 if head is not None else 3.4
        max_h = top + 3.6
        if body.height > max_h:
            body.scale_to_fit_height(max_h)
        if body.width > SAFE_W:
            body.scale_to_fit_width(SAFE_W)
        body.next_to([0, top, 0], DOWN, buff=0)
        if rtl:
            body.to_edge(RIGHT, buff=0.8)
        else:
            body.to_edge(LEFT, buff=0.8)
        if head is not None:
            self.play(FadeIn(head, shift=DOWN * 0.2), run_time=min(0.6, a * 0.25))
            a -= min(0.6, a * 0.25)
        per = a / len(items)
        for row in items:
            self.play(FadeIn(row, shift=(LEFT if rtl else RIGHT) * 0.3), run_time=per)
        self.finish()


def split_lhs(m: str) -> tuple[str, str]:
    """Split Typst math at the first top-level '=' (not <=, >=, !=, :=, ==, =>)."""
    depth, in_str = 0, False
    for i, ch in enumerate(m):
        if ch == '"':
            in_str = not in_str
        elif in_str:
            continue
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "=" and depth == 0:
            prev = m[i - 1] if i else ""
            nxt = m[i + 1] if i + 1 < len(m) else ""
            if prev in "<>!:=" or nxt in "=>":
                continue
            return m[:i].strip(), m[i + 1:].strip()
    return "", m.strip()


def equation_lines(maths: list[str], font_size: float = 56) -> list[tuple[VGroup, VGroup]]:
    """Build one MathTypst per step. A continuation line ("= ...") is rendered
    as "<LHS of line 0> = ..." and the LHS glyphs are then hidden, so all '='
    signs line up when lines are left-aligned. (Manim's {{ }} grouping is NOT
    used: in 0.21 it changes Typst spacing, e.g. "5x" renders with upright x.)
    Returns (full, visible) pairs; only `visible` is ever added to the scene."""
    lhs0, _ = split_lhs(maths[0])
    use_align = bool(lhs0) and any(m.strip().startswith("=") for m in maths[1:])
    n_lhs = len(MathTypst(lhs0, font_size=font_size).family_members_with_points()) if use_align else 0
    out = []
    for i, m in enumerate(maths):
        m = m.strip()
        mob, hidden = None, []
        if use_align and i > 0 and m.startswith("="):
            mob = MathTypst(f"{lhs0} {m}", font_size=font_size, color=FG)
            leaves = mob.family_members_with_points()
            hidden, rest = leaves[:n_lhs], leaves[n_lhs:]
            # sanity check: hidden glyphs must all sit left of the '=' sign
            if not rest or max(h.get_right()[0] for h in hidden) > min(r.get_left()[0] for r in rest) + 1e-3:
                mob, hidden = None, []
        if mob is None:
            mob = MathTypst(m, font_size=font_size, color=FG)
        for h in hidden:
            h.set_opacity(0)
        hid = {id(h) for h in hidden}
        vis = VGroup(*[p for p in mob.family_members_with_points() if id(p) not in hid])
        out.append((mob, vis))
    return out


EQ_LINE_BUFF = 0.4


class EquationBeat(_BeatBase):
    """layout "stack" (default): derivation lines appear one under another,
    '=' aligned, older lines dimmed and scrolled out when space runs out.
    layout "replace": each step morphs into the next (TransformMatchingShapes)."""



    def construct(self):
        s, lang = self.spec, self.spec["lang"]
        st = s["step_seconds"]
        head = self.header()
        if head is not None:
            self.add(head)
        steps = s["steps"]

        def cap_mob(c):
            if not c:
                return None
            return make_text(c, font_size=30, lang=lang, color=MUTED, max_units=56).move_to(DOWN * 2.75)

        def swap_caption(cap, new_cap):
            if cap is not None and new_cap is not None:
                return [FadeTransform(cap, new_cap)]
            if cap is not None:
                return [FadeOut(cap)]
            if new_cap is not None:
                return [FadeIn(new_cap)]
            return []

        y_top = (head.get_bottom()[1] - 0.45) if head is not None else 3.3
        y_bot = -2.2
        avail = y_top - y_bot
        cap = None

        if s.get("layout") == "replace":
            def eq_mob(m):
                mob = MathTypst(m, font_size=64, color=FG)
                if mob.width > SAFE_W:
                    mob.scale_to_fit_width(SAFE_W)
                if mob.height > avail:
                    mob.scale_to_fit_height(avail)
                return mob.move_to([0, (y_top + y_bot) / 2, 0])

            eq = eq_mob(steps[0]["math"])
            cap = cap_mob(steps[0]["caption"])
            self.play(Write(eq), *swap_caption(None, cap), run_time=st * 0.7)
            self.wait(st * 0.3)
            for step in steps[1:]:
                new_eq = eq_mob(step["math"])
                new_cap = cap_mob(step["caption"])
                self.play(TransformMatchingShapes(eq, new_eq), *swap_caption(cap, new_cap), run_time=st * 0.6)
                self.wait(st * 0.4)
                eq, cap = new_eq, new_cap
            last_vis = eq
        else:
            lines = equation_lines([x["math"] for x in steps])
            maxw = max(f.width for f, _ in lines)
            maxh = max(f.height for f, _ in lines)
            k = min(1.0, SAFE_W / maxw, avail / maxh)
            for f, _ in lines:
                f.scale(k, about_point=ORIGIN)
            maxw = max(f.width for f, _ in lines)
            for f, _ in lines:  # common left edge, column centred
                f.align_to(np.array([-maxw / 2, 0, 0]), LEFT)
            heights = [f.height for f, _ in lines]
            total = sum(heights) + EQ_LINE_BUFF * (len(lines) - 1)
            if total <= avail:  # everything fits: centre the block vertically
                y_top = (y_top + y_bot) / 2 + total / 2
            cur_y: dict[int, float] = {}
            shown: list[int] = []
            for i, step in enumerate(steps):
                window = shown + [i]
                while len(window) > 1 and sum(heights[j] for j in window) + EQ_LINE_BUFF * (len(window) - 1) > avail:
                    window = window[1:]
                y, pos = y_top, {}
                for j in window:
                    pos[j] = y - heights[j] / 2
                    y -= heights[j] + EQ_LINE_BUFF
                full, vis = lines[i]
                full.set_y(pos[i])
                cur_y[i] = pos[i]
                anims = [Write(vis)]
                for j in shown:
                    vj = lines[j][1]
                    if j not in window:
                        anims.append(FadeOut(vj, shift=UP * 0.3))
                    else:
                        anims.append(vj.animate.shift(UP * (pos[j] - cur_y[j])).set_color(MUTED))
                        cur_y[j] = pos[j]
                new_cap = cap_mob(step["caption"])
                anims += swap_caption(cap, new_cap)
                self.play(*anims, run_time=st * 0.65)
                self.wait(st * 0.35)
                shown = window
                cap = new_cap
            last_vis = lines[-1][1]
        box = SurroundingRectangle(last_vis, color=ACCENT, buff=0.15)
        self.play(Create(box), run_time=0.4)
        self.finish()


GRAPH_SAMPLES = 400


class GraphBeat(_BeatBase):
    def construct(self):
        s, lang = self.spec, self.spec["lang"]
        a = s["anim_seconds"]
        tree = parse_expr(s["expr"])
        ax0, ax1, xstep = nice_axis(*s["x_range"])
        x0, x1 = float(ax0), float(ax1)
        xs = np.linspace(x0, x1, GRAPH_SAMPLES)
        ys = eval_expr(tree, xs)
        fin = np.isfinite(ys)
        if not fin.any():
            raise SpecError("expr is undefined everywhere on x_range")
        if s["y_range"]:
            y0, y1 = s["y_range"]
        else:  # robust auto range: ignore asymptote spikes
            lo, hi = (float(v) for v in np.percentile(ys[fin], [3, 97]))
            if hi - lo < 1e-9:
                lo, hi = lo - 1, hi + 1
            pad = 0.05 * (hi - lo)
            y0, y1 = lo - pad, hi + pad
            if lo >= 0 and (y0 < 0 or lo < 0.5 * (hi - lo)):
                y0 = 0.0  # non-negative function / x-axis nearby: start at 0
            if hi <= 0 and (y1 > 0 or -hi < 0.5 * (hi - lo)):
                y1 = 0.0
        ay0, ay1, ystep = nice_axis(y0, y1, 6)
        head = self.header()
        ax_h = 5.2 if head is not None else 6.0
        axes = Axes(
            x_range=[ax0, ax1, xstep], y_range=[ay0, ay1, ystep],
            x_length=11.0, y_length=ax_h, tips=False,
            axis_config={"color": MUTED, "include_numbers": True, "font_size": 22,
                         "label_constructor": MathTypst},
        )
        axes.move_to(DOWN * 0.35 if head is not None else ORIGIN)
        # tick numbers above the curve with a background-coloured halo, so a
        # curve passing through "-2.5" does not make it unreadable
        for nl in (axes.x_axis, axes.y_axis):
            for num in getattr(nl, "numbers", []) or []:
                num.set_stroke(BG, width=7, background=True).set_z_index(3)
        xl =make_text(s["x_label"], font_size=26, lang=lang, color=MUTED).next_to(axes.x_axis, RIGHT, buff=0.15)
        yl = make_text(s["y_label"], font_size=26, lang=lang, color=MUTED).next_to(axes.y_axis, UP, buff=0.15)

        # split into finite, in-range segments (asymptotes, log of negatives)
        ok = fin & (ys >= ay0) & (ys <= ay1)
        segs, cur = [], []
        for xi, yi, good in zip(xs, ys, ok):
            if good:
                cur.append(axes.c2p(xi, yi))
            elif cur:
                segs.append(cur)
                cur = []
        if cur:
            segs.append(cur)
        curve = VGroup(*[VMobject(stroke_color=CURVE, stroke_width=5).set_points_as_corners(seg)
                         for seg in segs if len(seg) >= 2])

        extras = []
        if s.get("label"):
            lab = make_text(s["label"], font_size=30, lang=lang, color=CURVE)
            anchor = segs[-1][-1] if segs else axes.c2p(x1, y1)
            lab.next_to(anchor, UP + LEFT, buff=0.15)
            lab.shift(RIGHT * min(0, 6.8 - lab.get_right()[0]) + DOWN * min(0, 3.8 - lab.get_top()[1]))
            extras.append(lab)
        for p in s["points"]:
            py = float(eval_expr(tree, np.array([p["x"]]))[0])
            if not (math.isfinite(py) and ay0 <= py <= ay1):
                continue
            d = Dot(axes.c2p(p["x"], py), color=ACCENT, radius=0.08)
            extras.append(d)
            if p["label"]:
                # put the label on the side away from the curve
                h = (x1 - x0) / 200
                yl_, yr_ = eval_expr(tree, np.array([p["x"] - h, p["x"] + h]))
                if yl_ > py and yr_ > py:
                    side = DOWN          # local minimum
                elif yl_ < py and yr_ < py:
                    side = UP            # local maximum
                elif yr_ > yl_:
                    side = UP + LEFT     # increasing
                else:
                    side = UP + RIGHT    # decreasing / flat
                lab_p = make_text(p["label"], font_size=24, lang=lang, color=ACCENT).next_to(d, side, buff=0.12)
                extras.append(lab_p)

        if head is not None:
            self.add(head)
        self.play(Create(axes), FadeIn(xl), FadeIn(yl), run_time=a * 0.3)
        self.play(Create(curve), run_time=a * 0.5)
        if extras:
            self.play(*[FadeIn(e) for e in extras], run_time=a * 0.2)
        self.finish()


class ArrayStepsBeat(_BeatBase):
    def construct(self):
        s, lang = self.spec, self.spec["lang"]
        st = s["step_seconds"]
        head = self.header()
        n = len(s["values"])
        side = min(0.95, SAFE_W / n)
        cells = VGroup()
        for i, v in enumerate(s["values"]):
            sq = Square(side_length=side, color=FG, stroke_width=3, fill_color=BG, fill_opacity=1)
            txt = Text(v, font=pick_font(v), font_size=30 * side / 0.95, color=FG)
            if txt.width > side * 0.84:  # 5-char values must stay inside the box
                txt.scale_to_fit_width(side * 0.84)
            cells.add(VGroup(sq, txt.move_to(sq)))
        cells.arrange(RIGHT, buff=0).move_to(UP * 0.6)
        idx = VGroup(*[Text(str(i), font="Noto Sans", font_size=18, color=MUTED).next_to(c, UP, buff=0.12)
                       for i, c in enumerate(cells)])
        colors = {nm: POINTER_COLORS[k % len(POINTER_COLORS)] for k, nm in enumerate(s["pointer_names"])}

        def pointer_mobs(step):
            mobs = {}
            stacked: dict[int, int] = {}
            for nm, i in step["pointers"].items():
                level = stacked.get(i, 0)
                stacked[i] = level + 1
                base = cells[i].get_bottom() + DOWN * (0.1 + 0.55 * level)
                arr = Arrow(base + DOWN * 0.45, base, buff=0, color=colors[nm],
                            stroke_width=5, max_tip_length_to_length_ratio=0.35)
                lab = Text(nm, font="Noto Sans", font_size=22, color=colors[nm]).next_to(arr, LEFT, buff=0.08)
                mobs[nm] = VGroup(arr, lab)
            return mobs

        def cell_anims(step):
            anims = []
            for i, c in enumerate(cells):
                sq, txt = c
                if i in step["found"]:
                    fill, op, tcol = GOOD, 0.85, BG
                elif i in step["highlight"]:
                    fill, op, tcol = ACCENT, 0.85, BG
                else:
                    fill, op, tcol = BG, 1.0, FG
                o = 0.25 if i in step["dim"] else 1.0
                anims.append(sq.animate.set_fill(fill, opacity=op).set_stroke(opacity=o))
                anims.append(txt.animate.set_color(tcol).set_opacity(o))
            return anims

        def cap_mob(c):
            return make_text(c, font_size=30, lang=lang, color=FG, max_units=56).move_to(DOWN * 2.6) if c else None

        intro = [FadeIn(cells), FadeIn(idx)]
        if head is not None:
            intro.append(FadeIn(head))
        self.play(*intro, run_time=0.6)
        cur_ptrs: dict = {}
        cap = None
        for step in s["steps"]:
            new_ptrs = pointer_mobs(step)
            anims = cell_anims(step)
            for nm, mob in new_ptrs.items():
                if nm in cur_ptrs:
                    anims.append(cur_ptrs[nm].animate.move_to(mob))
                else:
                    anims.append(FadeIn(mob, shift=UP * 0.2))
            for nm, mob in cur_ptrs.items():
                if nm not in new_ptrs:
                    anims.append(FadeOut(mob))
            new_cap = cap_mob(step["caption"])
            if cap is not None and new_cap is not None:
                anims.append(FadeTransform(cap, new_cap))
            elif cap is not None:
                anims.append(FadeOut(cap))
            elif new_cap is not None:
                anims.append(FadeIn(new_cap))
            self.play(*anims, run_time=st * 0.5)
            self.wait(st * 0.5)
            # keep the moved mobjects (not the target copies) as current
            cur_ptrs = {nm: (cur_ptrs[nm] if nm in cur_ptrs else new_ptrs[nm]) for nm in new_ptrs}
            cap = new_cap
        self.finish()


SCENES = {"title": TitleBeat, "equation": EquationBeat, "graph": GraphBeat,
          "array_steps": ArrayStepsBeat, "bullets": BulletsBeat}


class AutoBeat(_BeatBase):
    """Dispatch on spec['type'] so the CLI can always render 'AutoBeat'."""

    def construct(self):
        cls = SCENES[self.spec["type"]]
        cls.construct(self)


# --------------------------------------------------------------------------
# JSON Schemas for llama-server constrained output ("json_schema" field).
# Kept deliberately small; validate_spec() is the real gate.
# --------------------------------------------------------------------------
_COMMON = {
    "lang": {"type": "string", "description": "BCP-47 code of the on-screen text, e.g. hi, kn, ta, zh"},
    "hold": {"type": "number", "minimum": 0, "maximum": 120},
}
_NUM_OR_CONST = {"anyOf": [{"type": "number"}, {"type": "string", "maxLength": 24}]}
BEAT_SCHEMAS = {
    "title": {"type": "object", "required": ["type", "title"], "additionalProperties": False,
              "properties": {"type": {"const": "title"}, "title": {"type": "string", "maxLength": 90},
                             "subtitle": {"type": "string", "maxLength": 140}, **_COMMON}},
    "bullets": {"type": "object", "required": ["type", "bullets"], "additionalProperties": False,
                "properties": {"type": {"const": "bullets"}, "title": {"type": "string", "maxLength": 90},
                               "bullets": {"type": "array", "minItems": 1, "maxItems": 6,
                                           "items": {"type": "string", "maxLength": 140}}, **_COMMON}},
    "equation": {"type": "object", "required": ["type", "steps"], "additionalProperties": False,
                 "properties": {"type": {"const": "equation"}, "title": {"type": "string", "maxLength": 90},
                                "step_seconds": {"type": "number", "minimum": 0.6, "maximum": 6},
                                "layout": {"enum": ["stack", "replace"]},
                                "steps": {"type": "array", "minItems": 1, "maxItems": 6, "items": {
                                    "type": "object", "required": ["math"], "additionalProperties": False,
                                    "properties": {"math": {"type": "string", "maxLength": 200,
                                                            "description": "Typst math without $, e.g. dif/(dif x) x^2 = 2x"},
                                                   "caption": {"type": "string", "maxLength": 120}}}},
                                **_COMMON}},
    "graph": {"type": "object", "required": ["type", "expr", "x_range"], "additionalProperties": False,
              "properties": {"type": {"const": "graph"}, "title": {"type": "string", "maxLength": 90},
                             "expr": {"type": "string", "maxLength": 160,
                                      "description": "function of x using + - * / ^ ( ) and sin cos tan exp log sqrt abs pi e"},
                             "x_range": {"type": "array", "items": _NUM_OR_CONST, "minItems": 2, "maxItems": 2,
                                         "description": "[min, max]; numbers or constants like \"-2*pi\""},
                             "y_range": {"type": "array", "items": _NUM_OR_CONST, "minItems": 2, "maxItems": 2},
                             "label": {"type": "string", "maxLength": 40},
                             "x_label": {"type": "string", "maxLength": 12},
                             "y_label": {"type": "string", "maxLength": 12},
                             "points": {"type": "array", "maxItems": 5, "items": {
                                 "type": "object", "required": ["x"], "additionalProperties": False,
                                 "properties": {"x": _NUM_OR_CONST, "label": {"type": "string", "maxLength": 24}}}},
                             **_COMMON}},
    "array_steps": {"type": "object", "required": ["type", "values", "steps"], "additionalProperties": False,
                    "properties": {"type": {"const": "array_steps"}, "title": {"type": "string", "maxLength": 90},
                                   "step_seconds": {"type": "number", "minimum": 0.6, "maximum": 6},
                                   "values": {"type": "array", "minItems": 2, "maxItems": 16,
                                              "description": "the array data shown in the boxes, e.g. [2, 5, 8, 12]",
                                              "items": {"anyOf": [{"type": "integer", "minimum": -9999, "maximum": 99999},
                                                                  {"type": "string", "minLength": 1, "maxLength": 5}]}},
                                   "steps": {"type": "array", "minItems": 1, "maxItems": 12, "items": {
                                       "type": "object", "additionalProperties": False,
                                       "properties": {
                                           "pointers": {"type": "object", "maxProperties": 4,
                                                        "description": "pointer name -> index, e.g. {\"lo\": 0, \"mid\": 4, \"hi\": 9}",
                                                        "additionalProperties": {"type": "integer", "minimum": 0}},
                                           "highlight": {"type": "array", "items": {"type": "integer", "minimum": 0}},
                                           "dim": {"type": "array", "items": {"type": "integer", "minimum": 0}},
                                           "found": {"type": "array", "items": {"type": "integer", "minimum": 0}},
                                           "caption": {"type": "string", "maxLength": 120}}}},
                                   **_COMMON}},
}
BEAT_SCHEMA_ANY = {"oneOf": list(BEAT_SCHEMAS.values())}

if __name__ == "__main__":  # dump schemas: python templates.py > beat_schemas.json
    print(json.dumps(BEAT_SCHEMAS, ensure_ascii=False, indent=1))
