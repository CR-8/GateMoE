"""TEXT GATEWAY: one shared generator writes every text artefact via a task-specific schema.

All tasks share an identical system message (role + rules + SOURCES), so llama-server's
prompt cache processes the retrieved sources once and reuses them for every later task.
"""
from __future__ import annotations

from ..config import Config
from ..llm.client import Generator
from ..router.clef import RouteDecision
from . import schemas
from .schemas import VIDEO_TEMPLATES, templates_for

SYSTEM_TMPL = """You are GateMoE, an offline tutor for technical and scientific subjects.
Learner language: {lang_name} ({native}). Write ALL learner-facing text in {lang_name}; keep standard scientific symbols, units and code identifiers as they are.
Subject: {subject}. Level: {level}.
Use the SOURCES below when they are relevant and correct; if they do not cover something, rely on well-established textbook knowledge and never invent citations, data or quotes.
Be accurate, concrete and concise. Output only JSON that matches the requested schema.

SOURCES:
{sources}"""

TASKS = {
    "plan": """Learner request: {request}
Plan a short lesson. Give a clear title, 1-3 English search queries for an offline encyclopedia, \
0-3 search queries in {lang_name} (empty if {lang_name} is English), 3-8 key terms and a 3-6 point outline.""",
    "notes": """Learner request: {request}
Write study notes: 3-6 sections with a heading and a body (plain text with simple Markdown such as **bold** and - lists; formulas inline like F = m·a), \
then 3-6 key points and a glossary of 2-8 terms.""",
    "flashcards": """Learner request: {request}
Write exactly {n} flashcards. Front: one precise question or term. Back: a short, correct answer (one or two sentences).""",
    "quiz": """Learner request: {request}
Write exactly {n} multiple-choice questions, each with exactly 4 options, one correct answer (answer_index 0-3, vary its position) \
and a one-sentence explanation. Test understanding, not trivia.""",
    "podcast": """Learner request: {request}
Write a two-host educational podcast script of about {n} turns. Host A is the curious learner, host B the expert; alternate A and B, starting with A.
The text will be read aloud by a speech synthesiser, so: write numbers, symbols, units and formulas the way they are spoken \
(e.g. "F equals m times a", "nine point eight metres per second squared"); no code, no Markdown, no lists, no URLs, no emojis; \
keep each turn to one to three short sentences.{spoken_rules}""",
    "code": """Learner request: {request}
Write ONE short, self-contained, runnable Python 3 example (standard library only, no input(), no files, no network) that demonstrates the topic, \
with an explanation and the exact expected output. Code comments may be in {lang_name}.""",
    "video": """Learner request: {request}
Plan a short explainer video of {n_min}-{n} beats. Each beat uses ONE template and fills only that template's slots:
{templates}
Rules: first beat 'title', last beat a recap ('bullets'{summary_hint}). On-screen text must be short (lines <= 8 words). \
Equations use Typst math syntax (e.g. "f(x) = x^2", "(d)/(d x) x^2 = 2x", "integral_0^1 x dif x = 1/2", "a^2 + b^2 = c^2"). \
Expressions for graphs use Python syntax in x (e.g. "x**2 - 1", "sin(x)"). \
Narration: 1-3 spoken sentences per beat in {lang_name}, written exactly as it should be spoken (no symbols or code).{spoken_rules}""",
}


def pack_sources(passages: list[dict], max_chars: int) -> str:
    if not passages:
        return "(no offline sources found for this request)"
    parts, used = [], 0
    for i, p in enumerate(passages, 1):
        chunk = f"[{i}] {p.get('title', '')} ({p.get('zim', '')}):\n{p.get('text', '').strip()}"
        if used + len(chunk) > max_chars:
            chunk = chunk[: max(0, max_chars - used)]
        if not chunk:
            break
        parts.append(chunk)
        used += len(chunk)
    return "\n\n".join(parts)


class TextGateway:
    def __init__(self, cfg: Config, gen: Generator, lang: str, decision: RouteDecision, request: str,
                 tts_engine: str | None = None):
        self.cfg = cfg
        self.tts_engine = tts_engine
        self.gen = gen
        self.lang = lang
        self.info = cfg.language(lang)
        self.decision = decision
        self.request = request.strip()
        self.sources = "(none)"

    def set_sources(self, passages: list[dict]) -> None:
        self.sources = pack_sources(passages, int(self.cfg["knowledge.max_source_chars"]))

    def _messages(self, task_text: str) -> list[dict]:
        system = SYSTEM_TMPL.format(lang_name=self.info["name"], native=self.info["native"],
                                    subject=self.decision.subject.replace("_", " "),
                                    level=self.decision.level, sources=self.sources)
        return [{"role": "system", "content": system}, {"role": "user", "content": task_text}]

    def spoken_rules(self) -> str:
        """Extra rules for text that a TTS engine will read (MMS/Piper drop digits and Latin words)."""
        if self.lang == "en" or not self.tts_engine or self.tts_engine.startswith("supertonic"):
            return ""
        return (f" Because the {self.info['name']} voice cannot read digits or Latin letters, write every number as "
                f"{self.info['name']} words and transliterate technical terms and abbreviations into {self.info['name']} script.")

    def _fmt(self, key: str, **extra) -> str:
        return TASKS[key].format(request=self.request, lang_name=self.info["name"], **extra)

    def plan(self) -> dict:
        return self.gen.chat_json(self._messages(self._fmt("plan")), schemas.plan_schema(),
                                  name="plan", max_tokens=int(self.cfg["generation.max_tokens.plan"]))

    def generate(self, task: str, video_engine: str | None = None) -> dict:
        mt = int(self.cfg[f"generation.max_tokens.{task}"])
        if task == "notes":
            return self.gen.chat_json(self._messages(self._fmt("notes")), schemas.notes_schema(),
                                      name="notes", max_tokens=mt)
        if task == "flashcards":
            n = int(self.cfg["generation.flashcards"])
            return self.gen.chat_json(self._messages(self._fmt("flashcards", n=n)),
                                      schemas.flashcards_schema(n), name="flashcards", max_tokens=mt)
        if task == "quiz":
            n = int(self.cfg["generation.quiz_questions"])
            return self.gen.chat_json(self._messages(self._fmt("quiz", n=n)), schemas.quiz_schema(n),
                                      name="quiz", max_tokens=mt)
        if task == "podcast":
            n = int(self.cfg["generation.podcast_turns"])
            return self.gen.chat_json(self._messages(self._fmt("podcast", n=n, spoken_rules=self.spoken_rules())),
                                      schemas.podcast_schema(n),
                                      name="podcast", max_tokens=mt)
        if task == "code":
            return self.gen.chat_json(self._messages(self._fmt("code")), schemas.code_schema(),
                                      name="code", max_tokens=mt)
        if task == "video":
            engine = video_engine or self.decision.video_engine
            n = int(self.cfg["generation.video_beats"])
            listing = "\n".join(f"- {t}: {VIDEO_TEMPLATES[t]['slots']}" for t in templates_for(engine))
            hint = " or 'definition'" if engine == "hyperframes" else ""
            text = self._fmt("video", n=n, n_min=min(3, n), templates=listing, summary_hint=hint,
                             spoken_rules=self.spoken_rules())
            return self.gen.chat_json(self._messages(text), schemas.video_schema(engine, n),
                                      name="video", max_tokens=mt)
        raise ValueError(f"unknown text task {task!r}")
