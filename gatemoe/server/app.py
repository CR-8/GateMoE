"""Web app served from the Pi: phone/laptop browser on the same Wi-Fi opens http://<pi>:8000/."""
from __future__ import annotations

import asyncio
import base64
import hmac
import ipaddress
import json
import mimetypes
import queue
import socket
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from urllib.parse import quote

from fastapi.responses import (FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..classroom import Classroom, ClassroomError
from ..config import Config, load_config
from ..jobs import JobManager, QueueFull
from ..knowledge import KnowledgeBase
from ..router.clef import GATEWAYS
from ..runtime import sysinfo

STATIC = Path(__file__).parent / "static"          # built from web/ (npm run build); committed, nodes need no Node.js
# older Pythons map .mjs to nothing; module workers (pdf.js) are refused without a JavaScript MIME type
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("font/woff2", ".woff2")
# Offline archive content (PhET sims, Wikipedia articles) is third-party HTML/JS: it runs in a sandbox
# with an opaque origin (no access to this app's API, cookies or storage) and may not open connections.
ZIM_HEADERS = {
    "Content-Security-Policy": "sandbox allow-scripts; default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:; "
                               "connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "public, max-age=86400",
}


class StudentsIn(BaseModel):
    names: list[str] = Field(..., min_length=1, max_length=200)


class SectionIn(BaseModel):
    name: str | None = Field(None, max_length=60)
    student_ids: list[str] | None = Field(None, max_length=500)
    color: str | None = None
    kind: str | None = None
    archived: bool | None = None


class AssignIn(BaseModel):
    lesson_id: str = Field(..., max_length=12)
    section_ids: list[str] = Field(..., min_length=1, max_length=50)
    title: str = Field("", max_length=140)
    due: str | None = Field(None, max_length=32)
    note: str = Field("", max_length=500)


class FeedbackIn(BaseModel):
    text: str | None = Field(None, max_length=240)
    points: float | None = None
    tag: str | None = Field(None, max_length=30)


class SubmissionIn(BaseModel):
    assignment_id: str = Field(..., max_length=12)
    student_id: str = Field(..., max_length=12)
    answers: list[int | None] = Field(..., max_length=50)


class GradeIn(BaseModel):
    feedback_ids: list[str] | None = Field(None, max_length=50)
    comment: str | None = Field(None, max_length=2000)


class LessonMetaIn(BaseModel):
    status: str


def lesson_summary(job: dict, lesson: dict | None, job_dir: Path) -> dict:
    """What the lesson board shows for one job (lesson.json can be large; this is small)."""
    out = {**job}
    if not lesson:
        return out
    route = lesson.get("route") or {}
    plan, notes = lesson.get("plan") or {}, lesson.get("notes") or {}
    out.update({
        "title": notes.get("title") or plan.get("title") or job.get("request", "")[:80],
        "summary": (notes.get("key_points") or [""])[0] if notes.get("key_points") else "",
        "language": lesson.get("language"), "selected": route.get("selected") or [],
        "route_mode": route.get("mode"), "subject": route.get("subject"), "level": route.get("level"),
        "has": {k: bool(lesson.get(k)) for k in ("notes", "flashcards", "quiz", "podcast", "video", "code",
                                                  "podcast_audio", "video_render", "handout", "concept_map",
                                                  "simulations")},
        "counts": {"cards": len((lesson.get("flashcards") or {}).get("cards") or []),
                   "questions": len((lesson.get("quiz") or {}).get("questions") or []),
                   "sources": len(lesson.get("sources") or []),
                   "sims": len(lesson.get("simulations") or [])},
        "wall_s": (lesson.get("metrics") or {}).get("wall_seconds"),
        "errors": sorted((lesson.get("errors") or {}).keys()),
        "out_of_scope": bool(lesson.get("out_of_scope")),
        "thumb": ((lesson.get("concept_map") or {}).get("png")
                  if (job_dir / str((lesson.get("concept_map") or {}).get("png") or "_")).exists() else None),
    })
    return out


class LessonIn(BaseModel):
    request: str = Field(..., min_length=1, max_length=2000)
    language: str = "auto"                       # 'auto' or a code from languages.yaml
    mode: str | None = None                      # clef | all | manual (None = config default)
    gateways: list[str] | None = Field(None, max_length=len(GATEWAYS))   # for mode=manual


def basic_auth_ok(header: str, user: str, password: str) -> bool:
    if not header.lower().startswith("basic "):
        return False
    try:
        given_user, _, given_pw = base64.b64decode(header[6:].strip()).decode("utf-8").partition(":")
    except (ValueError, UnicodeDecodeError):
        return False
    ok_user = hmac.compare_digest(given_user.encode(), user.encode())
    ok_pw = hmac.compare_digest(given_pw.encode(), password.encode())
    return ok_user and ok_pw


def host_allowed(host_header: str, extra: list[str]) -> bool:
    """Accept IP literals, localhost, *.local, this machine's name and configured names."""
    h = (host_header or "").strip().lower()
    if h.startswith("["):                                   # [::1]:8000
        h = h[1:].split("]", 1)[0]
    elif h.count(":") == 1:
        h = h.split(":", 1)[0]
    h = h.rstrip(".")
    if not h:
        return False
    if "*" in extra or h in extra or h == "localhost" or h.endswith(".local"):
        return True
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return h == socket.gethostname().lower()


def create_app(cfg: Config | None = None, jobs: JobManager | None = None) -> FastAPI:
    cfg = cfg or load_config()
    cfg.ensure_dirs()
    if jobs is None:
        from ..pipeline import LessonPipeline
        jobs = JobManager(cfg, lambda: LessonPipeline(cfg))
    @asynccontextmanager
    async def lifespan(_app):
        yield
        # shutdown (Ctrl+C / SIGTERM): stop the running lesson and never leave a model resident
        for job in jobs.list():
            if job.status == "running":
                jobs.cancel(job.id)
        if jobs._pipeline is not None:
            jobs._pipeline.models.unload()

    app = FastAPI(title="GateMoE", docs_url="/api/docs", lifespan=lifespan)
    app.state.cfg, app.state.jobs = cfg, jobs
    extra_hosts = [str(h).lower() for h in cfg.get("server.allowed_hosts", []) or []]

    auth_user = str(cfg.get("server.auth.user") or "gatemoe")
    auth_password = str(cfg.get("server.auth.password") or "")

    @app.middleware("http")
    async def check_auth(request, call_next):
        # Optional login for servers reachable from the internet (cloud VMs): HTTP Basic, so the
        # browser asks once and then sends it with every request, including sandboxed sim assets.
        if auth_password and request.url.path != "/healthz" and not basic_auth_ok(
                request.headers.get("authorization", ""), auth_user, auth_password):
            return PlainTextResponse("login required", status_code=401,
                                     headers={"WWW-Authenticate": 'Basic realm="GateMoE", charset="UTF-8"'})
        return await call_next(request)

    @app.middleware("http")
    async def check_host(request, call_next):
        # A web page on the internet can point its own domain at the Pi (DNS rebinding) and then
        # drive this API from a learner's browser; requests by name must name this machine.
        if not host_allowed(request.headers.get("host", ""), extra_hosts):
            return PlainTextResponse("host not allowed (add it to server.allowed_hosts)", status_code=403)
        return await call_next(request)
    kb = KnowledgeBase(cfg)
    classroom = Classroom(cfg.path("paths.jobs_dir").parent / "classroom.json")
    app.state.classroom = classroom

    @app.get("/healthz")
    def healthz() -> dict:
        """Liveness for containers / load balancers (no login, no model work)."""
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))

    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    @app.get("/api/config")
    def config_view() -> dict:
        return {
            "languages": {k: {"name": v["name"], "native": v["native"], "tts": v.get("tts")}
                          for k, v in cfg.languages.items()},
            "gateways": list(GATEWAYS),
            "router_mode": cfg["router.mode"],
            "threshold": cfg["router.threshold"],
            "models": {r: cfg[f"models.{r}.file"] for r in ("router", "generator")},
        }

    @app.get("/api/system")
    def system() -> dict:
        pipe = jobs._pipeline
        from ..registry import specialists
        return {"sys": sysinfo.snapshot(),
                "models": pipe.models.status() if pipe else {"loaded": None},
                "specialists": specialists(cfg, pipe) if pipe else specialists(cfg),
                "queue": sum(1 for j in jobs.list() if j.status in ("queued", "running"))}

    @app.post("/api/lessons")
    def create_lesson(body: LessonIn) -> dict:
        if body.language != "auto" and body.language not in cfg.languages:
            raise HTTPException(400, f"unknown language {body.language!r}")
        if body.mode not in (None, "clef", "all", "manual"):
            raise HTTPException(400, "mode must be clef, all or manual")
        if body.gateways and any(g not in GATEWAYS for g in body.gateways):
            raise HTTPException(400, f"gateways must be among {list(GATEWAYS)}")
        try:
            job = jobs.submit(body.request, {"language": body.language, "mode": body.mode,
                                             "gateways": sorted(set(body.gateways)) if body.gateways else None})
        except QueueFull as exc:
            raise HTTPException(429, str(exc))
        except ValueError as exc:            # e.g. a whitespace-only request
            raise HTTPException(400, str(exc))
        return job.public()

    summaries: dict[str, tuple[float, dict]] = {}

    @app.get("/api/lessons")
    def list_lessons() -> list[dict]:
        out = []
        meta = classroom.snapshot()["lesson_meta"]
        for j in jobs.list()[:200]:
            pub = j.public()
            d = jobs.job_dir(j.id)
            p = d / "lesson.json"
            try:
                mt = p.stat().st_mtime
            except OSError:
                mt = None
            if mt is not None and (j.id not in summaries or summaries[j.id][0] != mt):
                summaries[j.id] = (mt, lesson_summary({}, jobs.lesson(j.id), d))
            summ = summaries[j.id][1] if mt is not None and j.id in summaries else {}
            if j.status == "running":                     # live stage for the board's "Generating" column
                log = jobs.events(j.id)
                pub["stage"] = log.current_stage if log else None
            out.append({**summ, **pub, "board": (meta.get(j.id) or {}).get("status")})
        return out

    def _job(job_id: str):
        try:
            return jobs.get(job_id)
        except KeyError:
            raise HTTPException(404, "no such lesson")

    @app.get("/api/lessons/{job_id}")
    def get_lesson(job_id: str) -> dict:
        job = _job(job_id)
        log = jobs.events(job_id)
        return {"job": job.public(), "stage": log.current_stage if log else None,
                "lesson": jobs.lesson(job_id)}

    @app.patch("/api/lessons/{job_id}/meta")
    def lesson_meta(job_id: str, body: LessonMetaIn) -> dict:
        _job(job_id)
        return _cls(lambda: classroom.set_lesson_status(job_id, body.status))

    # ---- classroom (sections, posting, feedback bank, quiz submissions) -------------------------
    def _cls(fn):
        try:
            return fn()
        except KeyError as exc:
            raise HTTPException(404, str(exc).strip("'"))
        except ClassroomError as exc:
            raise HTTPException(400, str(exc))

    @app.get("/api/classroom")
    def classroom_state() -> dict:
        return classroom.snapshot()

    @app.post("/api/students")
    def add_students(body: StudentsIn) -> list[dict]:
        return _cls(lambda: classroom.add_students(body.names))

    @app.delete("/api/students/{sid}")
    def remove_student(sid: str) -> dict:
        _cls(lambda: classroom.remove_student(sid))
        return {"ok": True}

    @app.post("/api/sections")
    def add_section(body: SectionIn) -> dict:
        return _cls(lambda: classroom.add_section(body.name or "", body.student_ids, body.color, body.kind or "period"))

    @app.patch("/api/sections/{sec_id}")
    def update_section(sec_id: str, body: SectionIn) -> dict:
        return _cls(lambda: classroom.update_section(sec_id, **body.model_dump()))

    @app.delete("/api/sections/{sec_id}")
    def delete_section(sec_id: str) -> dict:
        _cls(lambda: classroom.delete_section(sec_id))
        return {"ok": True}

    @app.post("/api/assignments")
    def assign(body: AssignIn) -> dict:
        _job(body.lesson_id)
        return _cls(lambda: classroom.assign(body.lesson_id, body.section_ids, body.title, body.due, body.note))

    @app.delete("/api/assignments/{aid}")
    def unassign(aid: str) -> dict:
        _cls(lambda: classroom.unassign(aid))
        return {"ok": True}

    @app.post("/api/feedback")
    def add_feedback(body: FeedbackIn) -> dict:
        return _cls(lambda: classroom.add_feedback(body.text or "", body.points or 0, body.tag or ""))

    @app.patch("/api/feedback/{fid}")
    def update_feedback(fid: str, body: FeedbackIn) -> dict:
        return _cls(lambda: classroom.update_feedback(fid, **body.model_dump()))

    @app.delete("/api/feedback/{fid}")
    def delete_feedback(fid: str) -> dict:
        _cls(lambda: classroom.delete_feedback(fid))
        return {"ok": True}

    @app.post("/api/submissions")
    def submit(body: SubmissionIn) -> dict:
        snap = classroom.snapshot()
        a = next((x for x in snap["assignments"] if x["id"] == body.assignment_id), None)
        if a is None:
            raise HTTPException(404, "no such assignment")
        lesson = jobs.lesson(a["lesson_id"]) or {}
        sub = _cls(lambda: classroom.submit(body.assignment_id, body.student_id, body.answers, lesson.get("quiz")))
        return {**sub, "explanations": [q.get("explanation") or "" for q in (lesson.get("quiz") or {}).get("questions") or []]}

    @app.get("/api/assignments/{aid}/take")
    def take(aid: str) -> dict:
        """The student view of a posted lesson: quiz without the answer key, the roster of the
        sections it was posted to, and the lesson files to study from."""
        snap = classroom.snapshot()
        a = next((x for x in snap["assignments"] if x["id"] == aid), None)
        if a is None:
            raise HTTPException(404, "no such assignment")
        lesson = jobs.lesson(a["lesson_id"]) or {}
        secs = [s for s in snap["sections"] if s["id"] in a["section_ids"]]
        member = {x for s in secs for x in s["student_ids"]}
        done = {s["student_id"] for s in snap["submissions"] if s["assignment_id"] == aid}
        files = {"notes": "notes.md" if lesson.get("notes") else None,
                 "handout": (lesson.get("handout") or {}).get("pdf"),
                 "podcast": (lesson.get("podcast_audio") or {}).get("audio"),
                 "video": (lesson.get("video_render") or {}).get("video"),
                 "subtitles": (lesson.get("video_render") or {}).get("subtitles")}
        return {"assignment": a, "lesson_id": a["lesson_id"],
                "title": a["title"] or (lesson.get("notes") or {}).get("title") or lesson.get("request", "")[:100],
                "language": lesson.get("language"),
                "sections": [{k: s[k] for k in ("id", "name", "color")} for s in secs],
                "students": [{"id": s["id"], "name": s["name"], "submitted": s["id"] in done}
                             for s in snap["students"] if s["id"] in member],
                "questions": [{"question": q.get("question", ""), "options": q.get("options") or []}
                              for q in (lesson.get("quiz") or {}).get("questions") or []],
                "key_points": (lesson.get("notes") or {}).get("key_points") or [],
                "files": {k: v for k, v in files.items() if v}}

    @app.patch("/api/submissions/{sub_id}")
    def grade(sub_id: str, body: GradeIn) -> dict:
        return _cls(lambda: classroom.grade(sub_id, body.feedback_ids, body.comment))

    @app.get("/api/knowledge")
    def knowledge() -> dict:
        """Knowledge layers: Kiwix snapshot archives and the KDD delta (new knowledge between snapshots)."""
        from ..knowledge import is_delta
        from ..knowledge.delta import DeltaKDD
        cat = [{k: z.get(k) for k in ("name", "file", "title", "lang", "kind", "date", "size_mb", "articles",
                                      "fulltext", "error")} for z in kb.catalog()]
        try:
            delta = DeltaKDD(cfg, kb).status()
        except Exception as exc:   # never break the UI over a status read
            delta = {"error": str(exc)[:200]}
        return {"snapshots": [z for z in cat if not is_delta(z.get("name") or "")],
                "delta": [z for z in cat if is_delta(z.get("name") or "")], "delta_status": delta}

    @app.post("/api/lessons/{job_id}/cancel")
    def cancel(job_id: str) -> dict:
        _job(job_id)
        return jobs.cancel(job_id).public()

    @app.get("/api/lessons/{job_id}/events")
    async def events(job_id: str) -> StreamingResponse:
        _job(job_id)
        log = jobs.events(job_id)

        async def replay_file():
            p = jobs.job_dir(job_id) / "events.jsonl"
            if p.exists():
                for line in p.read_text(encoding="utf-8").splitlines():
                    yield f"data: {line}\n\n"
            yield 'data: {"kind": "eof"}\n\n'

        async def live():
            q = log.subscribe()
            try:
                while True:
                    try:
                        ev = q.get_nowait()
                    except queue.Empty:
                        await asyncio.sleep(0.5)
                        yield ": keep-alive\n\n"
                        continue
                    yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"
                    if ev.get("kind") == "eof":
                        break
            finally:
                log.unsubscribe(q)

        gen = live() if log is not None else replay_file()
        return StreamingResponse(gen, media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/lessons/{job_id}/files/{name:path}")
    def lesson_file(job_id: str, name: str) -> FileResponse:
        _job(job_id)
        root = jobs.job_dir(job_id).resolve()
        target = (root / name).resolve()
        if root not in target.parents or not target.is_file():
            raise HTTPException(404, "no such file")
        headers = {"X-Content-Type-Options": "nosniff"}
        if target.suffix.lower() in (".svg", ".html", ".htm", ".xml"):   # never script in this origin
            headers["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:"
        return FileResponse(target, headers=headers)

    @app.get("/zim/{zim}/{path:path}")
    def zim_entry(zim: str, path: str) -> Response:
        """Serve one entry of an installed ZIM (simulations, offline articles) like kiwix-serve."""
        z = kb.by_name(zim)
        if z is None:
            raise HTTPException(404, "no such archive")
        try:
            if not path:
                main = kb._archive(z["file"]).main_entry
                path = main.get_redirect_entry().path if main.is_redirect else main.path
            content, mime, final = kb.read_entry(z, path)
        except (KeyError, RuntimeError):
            raise HTTPException(404, "no such entry")
        if final != path:   # redirect so that relative links inside the page resolve correctly
            return RedirectResponse(f"/zim/{quote(zim)}/{quote(final)}", status_code=302, headers=ZIM_HEADERS)
        return Response(content, media_type=mime or "application/octet-stream", headers=ZIM_HEADERS)

    return app
