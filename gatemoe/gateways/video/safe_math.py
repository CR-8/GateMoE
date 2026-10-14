"""Safe maths for video beats, shared by both renderers (no Manim import, so the HyperFrames
engine can use it in the server process): a whitelisted ``ast`` evaluator for graph expressions
and Typst math checks/compilation. Nothing from an LLM ever reaches eval/exec/compile."""
from __future__ import annotations

import ast
import math
import os
import re

import numpy as np
import typst


class SpecError(ValueError):
    """Invalid beat spec. The message is meant to be fed back to the LLM."""


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
                typst.compile(path, format="svg", root=d, ignore_system_fonts=True)
                return m
            except typst.TypstError as e:
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



def typst_svg(m: str, color: str = "#202124", size_pt: float = 30) -> str:
    """Render checked Typst math (``typst_math_compiles`` first) to a standalone SVG string,
    tightly cropped, in the given colour - for the HyperFrames math template."""
    import tempfile

    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise SpecError("bad colour")
    with tempfile.TemporaryDirectory(prefix="gm_typst_") as d:
        path = os.path.join(d, "m.typ")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f'#set page(width: auto, height: auto, margin: 2pt, fill: none)\n'
                    f'#set text(size: {float(size_pt):.1f}pt, fill: rgb("{color}"))\n$ ' + m + ' $\n')
        out = typst.compile(path, format="svg", root=d, ignore_system_fonts=True)
    return out.decode("utf-8") if isinstance(out, bytes) else str(out)


def curve_points(expr: str, x_min: float, x_max: float, samples: int = 240) -> dict:
    """Sample a whitelisted expression for an SVG plot: nice axes + polyline segments
    (gaps at asymptotes / undefined points), in data coordinates."""
    tree = parse_expr(expr)
    ax0, ax1, xstep = nice_axis(float(x_min), float(x_max))
    xs = np.linspace(float(ax0), float(ax1), samples)
    ys = eval_expr(tree, xs)
    fin = np.isfinite(ys)
    if not fin.any():
        raise SpecError("expr is undefined everywhere on x_range")
    lo, hi = (float(v) for v in np.percentile(ys[fin], [3, 97]))
    if hi - lo < 1e-9:
        lo, hi = lo - 1, hi + 1
    pad = 0.05 * (hi - lo)
    y0, y1 = lo - pad, hi + pad
    if lo >= 0 and (y0 < 0 or lo < 0.5 * (hi - lo)):
        y0 = 0.0
    if hi <= 0 and (y1 > 0 or -hi < 0.5 * (hi - lo)):
        y1 = 0.0
    ay0, ay1, ystep = nice_axis(y0, y1, 6)
    segs, cur = [], []
    for xi, yi, ok in zip(xs, ys, fin & (ys >= ay0) & (ys <= ay1)):
        if ok:
            cur.append([round(float(xi), 5), round(float(yi), 5)])
        elif cur:
            segs.append(cur)
            cur = []
    if cur:
        segs.append(cur)
    return {"x": [ax0, ax1, xstep], "y": [ay0, ay1, ystep], "segments": [s for s in segs if len(s) >= 2]}
