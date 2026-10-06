"""Lesson pipeline: language -> ROUTER (Clef) -> GATEWAYS -> specialists, one big model at a time.

    request ─► langid ─► Clef-flash decides gateways ─► [generator loaded]
                                                         plan ─► offline ZIM retrieval
                                                         notes / flashcards / quiz / code /
                                                         podcast script / video plan
                                                       ─► [generator unloaded]
                                                         TTS (podcast + narration) ─► video render
                                                       ─► lesson.json + exports
Every stage and model swap is timed in events.jsonl.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .config import Config
from .gateways.handout import HandoutGateway
from .gateways.speech import SpeechGateway
from .gateways.text import TextGateway
from .gateways.video import VideoGateway
from .knowledge import KnowledgeBase
from .langid import detect_language
from .llm.client import Generator
from .registry import specialists
from .router.clef import ClefRouter
from .runtime.events import Cancelled, EventLog
from .runtime.model_manager import ModelManager
from .runtime.sysinfo import SysSampler

TEXT_ORDER = ["notes", "flashcards", "quiz", "code", "podcast", "video"]


class LessonPipeline:
    def __init__(self, cfg: Config, models: ModelManager | None = None):
        self.cfg = cfg
        self.models = models or ModelManager(cfg)
        self.router = ClefRouter(cfg, self.models)
        self.kb = KnowledgeBase(cfg)
        self.speech = SpeechGateway(cfg)
        self.video = VideoGateway(cfg)
        self.handout = HandoutGateway(cfg)

    def _pick_engine(self, wanted: str) -> str | None:
        engines = self.video.available_engines()
        if wanted in engines:
            return wanted
        return engines[0] if engines else None

    def run(self, request: str, out_dir: Path, events: EventLog, options: dict | None = None) -> dict:
        options = options or {}
        out_dir.mkdir(parents=True, exist_ok=True)
        lesson: dict = {"request": request, "created": time.time(), "errors": {}, "activated": []}
        activated: list[str] = lesson["activated"]
        try:
            with SysSampler(events, interval=float(self.cfg.get("server.sys_interval_s", 5)),
                            rss_pid_fn=self.models.current_pid):
                self._run(request, out_dir, events, options, lesson, activated)
        finally:
            lesson["metrics"] = events.summary()
            lesson["specialists"] = {"available": specialists(self.cfg, self),
                                     "activated": sorted(set(activated))}
            (out_dir / "lesson.json").write_text(json.dumps(lesson, ensure_ascii=False, indent=1),
                                                 encoding="utf-8")
        return lesson

    def _run(self, request: str, out_dir: Path, ev: EventLog, options: dict, lesson: dict,
             activated: list[str]) -> None:
        cfg = self.cfg
        # 1. language ---------------------------------------------------------------------
        with ev.stage("language") as st:
            forced = options.get("language")
            if forced and forced != "auto":
                lang = {"lang": forced, "confidence": 1.0, "method": "user"}
            else:
                lang = detect_language(request)
            code = lang["lang"]
            info = cfg.language(code)
            st.update(lang=code)
        lesson["language"] = {**lang, "name": info["name"], "native": info["native"]}

        # 2. master router -------------------------------------------------------------------
        with ev.stage("route") as st:
            decision = self.router.route(request, info["name"], ev, mode=options.get("mode"),
                                         manual=options.get("gateways"))
            st.update(selected=decision.selected, cached=decision.cached)
        lesson["route"] = decision.to_dict()
        if decision.mode == "clef":
            activated.append("router:clef-flash")
        in_min = float(cfg.get("router.in_scope_min", 0) or 0)
        if decision.mode == "clef" and decision.in_scope is not None and decision.in_scope < in_min:
            # conditional computation at its cheapest: no generator, TTS or video is loaded at all
            lesson["out_of_scope"] = True
            lesson["errors"]["scope"] = (f"This does not look like a request to learn a technical or scientific topic "
                                         f"(router P(in scope) = {decision.in_scope:.2f}). Rephrase it, or pick the "
                                         f"gateways manually.")
            return
        selected = [g for g in TEXT_ORDER if g in decision.selected]
        engine = self._pick_engine(decision.video_engine) if "video" in selected else None
        if "video" in selected and engine is None:
            lesson["errors"]["video"] = "no video engine available (install manim and/or hyperframes)"
            selected.remove("video")
        lesson["video_engine"] = engine

        # 3. generator: plan, retrieval, all text artefacts -----------------------------------
        server = self.models.acquire("generator", ev)
        activated.append("generator:" + cfg["models.generator.file"])
        gen = Generator(server.base_url, timeout=float(cfg["models.generator.request_timeout_s"]),
                        temperature=float(cfg["models.generator.temperature"]),
                        disable_thinking=bool(cfg["models.generator.disable_thinking"]), events=ev)
        tts_engine = self.speech.engine_for(code)
        tg = TextGateway(cfg, gen, code, decision, request, tts_engine=tts_engine)
        try:
            with ev.stage("plan"):
                plan = tg.plan()
        except Cancelled:
            raise
        except Exception as exc:
            lesson["errors"]["plan"] = f"{type(exc).__name__}: {exc}"
            plan = {"title": request[:100], "search_queries_en": [], "search_queries_native": [request[:80]],
                    "key_terms": [], "outline": []}
        lesson["plan"] = plan

        passages: list[dict] = []
        if cfg["knowledge.enabled"]:
            try:
                with ev.stage("retrieve") as st:
                    queries = [(q, "en") for q in plan.get("search_queries_en", [])]
                    queries += [(q, code) for q in plan.get("search_queries_native", [])]
                    if not queries:
                        queries = [(request, code)]
                    passages = self.kb.search(queries, learner_lang=code, subject=decision.subject, events=ev)
                    st.update(passages=len(passages), zims=sorted({p.get("zim", "") for p in passages}))
            except Cancelled:
                raise
            except Exception as exc:
                lesson["errors"]["retrieve"] = f"{type(exc).__name__}: {exc}"
            for z in sorted({p.get("zim", "") for p in passages if p.get("zim")}):
                activated.append("knowledge:" + z)
        lesson["sources"] = [{**{k: p.get(k) for k in ("title", "zim", "path", "lang", "score")},
                              "excerpt": (p.get("text") or "")[:400]} for p in passages]
        tg.set_sources(passages)

        for task in selected:
            ev.check_cancel()
            try:
                with ev.stage(f"gen_{task}"):
                    lesson[task] = tg.generate(task, video_engine=engine)
            except Cancelled:
                raise
            except Exception as exc:  # one failed gateway must not sink the lesson
                lesson["errors"][task] = f"{type(exc).__name__}: {exc}"

        # 3b. check the video plan against the chosen renderer while the generator can still fix it
        if "video" in lesson and engine:
            try:
                with ev.stage("check_video") as st:
                    bad = self.video.validate(lesson["video"], code, engine)
                    fixed = 0
                    for i, err in list(bad.items())[: int(cfg.get("video.max_repairs", 3))]:
                        ev.check_cancel()
                        try:
                            beat = tg.repair_beat(lesson["video"]["beats"][i], err, engine)
                        except Cancelled:
                            raise
                        except Exception:
                            continue
                        lesson["video"]["beats"][i] = beat
                        fixed += 1
                    st.update(invalid=len(bad), repaired=fixed)
            except Cancelled:
                raise
            except Exception as exc:  # validation is an optimisation; rendering still falls back per beat
                lesson["errors"]["check_video"] = f"{type(exc).__name__}: {exc}"

        # 4. free RAM before audio/video work --------------------------------------------------
        with ev.stage("unload_generator"):
            self.models.unload(ev)

        # 5. speech ----------------------------------------------------------------------------
        narration = None
        if ("podcast" in lesson or "video" in lesson) and not tts_engine:
            lesson["errors"]["speech"] = f"no offline TTS voice installed for '{code}' (text and captions only)"
        try:
            if "podcast" in lesson and tts_engine:
                try:
                    with ev.stage("tts_podcast"):
                        lesson["podcast_audio"] = self.speech.podcast(lesson["podcast"]["turns"], code, out_dir, ev)
                    activated.append("tts:" + tts_engine)
                except Cancelled:
                    raise
                except Exception as exc:
                    lesson["errors"]["tts_podcast"] = f"{type(exc).__name__}: {exc}"
            if "video" in lesson and tts_engine:
                try:
                    with ev.stage("tts_narration"):
                        narration = self.speech.narrate([b["narration"] for b in lesson["video"]["beats"]],
                                                        code, out_dir / "narration", ev)
                    activated.append("tts:" + tts_engine)
                except Cancelled:
                    raise
                except Exception as exc:  # the video still renders, silently, with subtitles
                    lesson["errors"]["tts_narration"] = f"{type(exc).__name__}: {exc}"
        finally:
            self.speech.close()

        # 6. video -----------------------------------------------------------------------------
        if "video" in lesson and engine:
            try:
                with ev.stage("render_video") as st:
                    lesson["video_render"] = self.video.render(lesson["video"], narration, code, engine,
                                                               out_dir, ev)
                    st.update(engine=engine)
                activated.append("video:" + engine)
                activated.append("assembler:ffmpeg")
            except Cancelled:
                raise
            except Exception as exc:
                lesson["errors"]["video_render"] = f"{type(exc).__name__}: {exc}"

        # 7. exports: Markdown, Anki TSV and a printable Typst handout (no LLM time) ------------
        with ev.stage("package"):
            write_exports(lesson, out_dir)
        if any(k in lesson for k in ("notes", "quiz", "flashcards")) and self.handout.available():
            try:
                with ev.stage("handout"):
                    lesson["handout"] = self.handout.render(lesson, code, out_dir)
                activated.append("handout:typst")
            except Exception as exc:
                lesson["errors"]["handout"] = f"{type(exc).__name__}: {exc}"


def write_exports(lesson: dict, out_dir: Path) -> None:
    """Plain-file exports next to lesson.json: Markdown notes and an Anki-importable TSV."""
    notes = lesson.get("notes")
    if notes:
        md = [f"# {notes.get('title', '')}", ""]
        for s in notes.get("sections", []):
            md += [f"## {s.get('heading', '')}", "", s.get("body", ""), ""]
        if notes.get("key_points"):
            md += ["## Key points", ""] + [f"- {k}" for k in notes["key_points"]] + [""]
        if notes.get("glossary"):
            md += ["## Glossary", ""] + [f"- **{g['term']}**: {g['definition']}" for g in notes["glossary"]]
        (out_dir / "notes.md").write_text("\n".join(md), encoding="utf-8")
    cards = (lesson.get("flashcards") or {}).get("cards")
    if cards:
        clean = lambda s: str(s).replace("\t", " ").replace("\n", "<br>")
        (out_dir / "flashcards_anki.tsv").write_text(
            "".join(f"{clean(c['front'])}\t{clean(c['back'])}\n" for c in cards), encoding="utf-8")
    code = lesson.get("code")
    if code and code.get("code"):
        (out_dir / "example.py").write_text(code["code"], encoding="utf-8")
