"""Web app served from the Pi: phone/laptop browser on the same Wi-Fi opens http://<pi>:8000/."""
from __future__ import annotations

import asyncio
import ipaddress
import json
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

from ..config import Config, load_config
from ..jobs import JobManager, QueueFull
from ..knowledge import KnowledgeBase
from ..router.clef import GATEWAYS
from ..runtime import sysinfo

STATIC = Path(__file__).parent / "static"
# Offline archive content (PhET sims, Wikipedia articles) is third-party HTML/JS: it runs in a sandbox
# with an opaque origin (no access to this app's API, cookies or storage) and may not open connections.
ZIM_HEADERS = {
    "Content-Security-Policy": "sandbox allow-scripts; default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:; "
                               "connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "public, max-age=86400",
}


class LessonIn(BaseModel):
    request: str = Field(..., min_length=1, max_length=2000)
    language: str = "auto"                       # 'auto' or a code from languages.yaml
    mode: str | None = None                      # clef | all | manual (None = config default)
    gateways: list[str] | None = Field(None, max_length=len(GATEWAYS))   # for mode=manual


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

    @app.middleware("http")
    async def check_host(request, call_next):
        # A web page on the internet can point its own domain at the Pi (DNS rebinding) and then
        # drive this API from a learner's browser; requests by name must name this machine.
        if not host_allowed(request.headers.get("host", ""), extra_hosts):
            return PlainTextResponse("host not allowed (add it to server.allowed_hosts)", status_code=403)
        return await call_next(request)
    kb = KnowledgeBase(cfg)

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

    @app.get("/api/lessons")
    def list_lessons() -> list[dict]:
        return [j.public() for j in jobs.list()[:100]]

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
