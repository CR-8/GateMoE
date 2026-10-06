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
        tg = TextGateway(cfg, gen, code, decision, request)
        with ev.stage("plan"):
            plan = tg.plan()
        lesson["plan"] = plan

        passages: list[dict] = []
        if cfg["knowledge.enabled"]:
            with ev.stage("retrieve") as st:
                queries = [(q, "en") for q in plan.get("search_queries_en", [])]
                queries += [(q, code) for q in plan.get("search_queries_native", [])]
                if not queries:
                    queries = [(request, code)]
                passages = self.kb.search(queries, learner_lang=code, subject=decision.subject, events=ev)
                st.update(passages=len(passages), zims=sorted({p.get("zim", "") for p in passages}))
            for z in sorted({p.get("zim", "") for p in passages if p.get("zim")}):
                activated.append("knowledge:" + z)
        lesson["sources"] = [{k: p.get(k) for k in ("title", "zim", "path", "lang", "score")} | {"excerpt": (p.get("text") or "")[:400]}
                             for p in passages]
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

        # 4. free RAM before audio/video work --------------------------------------------------
        with ev.stage("unload_generator"):
            self.models.unload(ev)

        # 5. speech ----------------------------------------------------------------------------
        tts_engine = self.speech.engine_for(code)
        narration = None
        try:
            if "podcast" in lesson and tts_engine:
                with ev.stage("tts_podcast"):
                    lesson["podcast_audio"] = self.speech.podcast(lesson["podcast"]["turns"], code, out_dir, ev)
                activated.append("tts:" + tts_engine)
            if "video" in lesson and tts_engine:
                with ev.stage("tts_narration"):
                    narration = self.speech.narrate([b["narration"] for b in lesson["video"]["beats"]],
                                                    code, out_dir / "narration", ev)
                activated.append("tts:" + tts_engine)
        except Cancelled:
            raise
        except Exception as exc:
            lesson["errors"]["speech"] = f"{type(exc).__name__}: {exc}"
        finally:
            self.speech.close()
        if ("podcast" in lesson or "video" in lesson) and not tts_engine:
            lesson["errors"]["speech"] = f"no offline TTS voice installed for '{code}' (text and captions only)"

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

        # 7. exports ---------------------------------------------------------------------------
        with ev.stage("package"):
            write_exports(lesson, out_dir)


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
