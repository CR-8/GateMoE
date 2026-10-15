"""Background lesson jobs: single worker (one Pi, one big model at a time), persisted in the
node database (gatemoe/db.py). Lesson files (notes.md, audio, video, PDF) live in one folder per job."""
from __future__ import annotations

import json
import queue
import re
import threading
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import Config
from .db import Database, put_lesson
from .runtime.events import Cancelled, EventLog

_ID = re.compile(r"^[a-f0-9]{12}$")


class QueueFull(RuntimeError):
    pass


@dataclass
class Job:
    id: str
    request: str
    options: dict
    status: str = "queued"            # queued | running | done | failed | cancelled | interrupted
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    error: str | None = None
    stage: str | None = None

    def public(self) -> dict:
        return asdict(self)


class GenerationOff(RuntimeError):
    pass


def open_db(cfg: Config) -> Database:
    return Database(cfg.path("paths.db"))


class JobManager:
    def __init__(self, cfg: Config, pipeline_factory, db: Database | None = None, generate: bool = True):
        self.cfg = cfg
        self.root = cfg.path("paths.jobs_dir")
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = db or open_db(cfg)
        self.generate = generate          # False: a read-only console (e.g. next to a benchmark run)
        self._pipeline_factory = pipeline_factory
        self._pipeline = None
        self.jobs: dict[str, Job] = {}
        self._dirs: dict[str, str] = {}         # job id -> folder, for lessons registered in place
        self.logs: dict[str, EventLog] = {}
        self._queue: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._load_existing()
        self._worker = threading.Thread(target=self._loop, name="lesson-worker", daemon=True)
        self._worker.start()

    @property
    def pipeline(self):
        if self._pipeline is None:
            self._pipeline = self._pipeline_factory()
        return self._pipeline

    # -- persistence -------------------------------------------------------------------------
    def job_dir(self, job_id: str) -> Path:
        if not _ID.match(job_id):
            raise KeyError(job_id)
        d = self._dirs.get(job_id)
        return Path(d) if d else self.root / job_id

    def _save(self, job: Job) -> None:
        with self.db.tx() as c:
            c.execute("INSERT INTO jobs(id, request, options, status, created, started, finished, error, stage, dir, source) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status, "
                      "started=excluded.started, finished=excluded.finished, error=excluded.error, stage=excluded.stage",
                      (job.id, job.request, json.dumps(job.options, ensure_ascii=False), job.status, job.created,
                       job.started, job.finished, job.error, job.stage, self._dirs.get(job.id),
                       "import" if job.id in self._dirs else "node"))

    def _store_lesson(self, job_id: str, lesson: dict | None) -> None:
        if lesson is None:
            p = self.job_dir(job_id) / "lesson.json"
            if not p.exists():
                return
            lesson = json.loads(p.read_text(encoding="utf-8"))
        with self.db.tx() as c:
            put_lesson(c, job_id, lesson, self.job_dir(job_id))

    def _load_existing(self) -> None:
        self._import_legacy_folders()
        for job in self._adopt(self.db.all("SELECT * FROM jobs")):
            if job.status in ("queued", "running"):
                job.status, job.error = "interrupted", "server restarted while this job was pending"
                self._save(job)

    def _adopt(self, rows: list[dict]) -> list[Job]:
        out = []
        with self._lock:
            for r in rows:
                if r["id"] in self.jobs:
                    continue
                job = Job(id=r["id"], request=r["request"], options=json.loads(r["options"] or "{}"),
                          status=r["status"], created=r["created"], started=r["started"], finished=r["finished"],
                          error=r["error"], stage=None)
                if r["dir"]:
                    self._dirs[job.id] = r["dir"]
                self.jobs[job.id] = job
                out.append(job)
        return out

    def _sync(self) -> None:
        """Pick up finished lessons another process added (``gatemoe import`` while the server runs)."""
        ids = {r["id"] for r in self.db.all("SELECT id FROM jobs")} - set(self.jobs)
        if ids:
            marks = ",".join("?" * len(ids))
            self._adopt(self.db.all(f"SELECT * FROM jobs WHERE id IN ({marks}) AND status NOT IN ('queued', 'running')",
                                    tuple(ids)))

    def _import_legacy_folders(self) -> None:
        """Nodes before the database kept job.json (+ lesson.json) per folder: import them once."""
        known = {r["id"] for r in self.db.all("SELECT id FROM jobs")}
        for p in sorted(self.root.glob("*/job.json")):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
                job = Job(**{k: rec.get(k) for k in Job.__dataclass_fields__ if k in rec})
            except (OSError, TypeError, ValueError):
                continue
            if job.id in known or not _ID.match(job.id) or p.parent.name != job.id:
                continue
            job.options = job.options or {}
            self._save(job)
            if job.status == "done":
                try:
                    self._store_lesson(job.id, None)
                except (OSError, ValueError):
                    pass
            p.rename(p.with_name("job.json.imported"))

    def register_folder(self, folder: Path) -> Job | None:
        """Add a finished lesson folder (one with lesson.json, e.g. made by `gatemoe lesson` or a
        benchmark) to the board, in place: files are read from there, never copied or changed."""
        folder = Path(folder).resolve()
        lp = folder / "lesson.json"
        if not lp.is_file():
            return None
        if self.db.one("SELECT id FROM jobs WHERE dir = ?", (str(folder),)):
            return None
        lesson = json.loads(lp.read_text(encoding="utf-8"))
        created = float(lesson.get("created") or lp.stat().st_mtime)
        wall = float((lesson.get("metrics") or {}).get("wall_seconds") or 0)
        job = Job(id=uuid.uuid4().hex[:12], request=str(lesson.get("request") or folder.name)[:2000],
                  options={"language": (lesson.get("language") or {}).get("lang", "auto"),
                           "mode": (lesson.get("route") or {}).get("mode")},
                  status="done", created=created, started=created, finished=created + wall)
        with self._lock:
            self._dirs[job.id] = str(folder)
            self.jobs[job.id] = job
        self._save(job)
        self._store_lesson(job.id, lesson)
        return job

    # -- API ---------------------------------------------------------------------------------
    def submit(self, request: str, options: dict | None = None) -> Job:
        if not self.generate:
            raise GenerationOff("lesson generation is switched off on this server")
        request = (request or "").strip()
        if not request:
            raise ValueError("empty request")
        if len(request) > 2000:
            raise ValueError("request too long (max 2000 characters)")
        job = Job(id=uuid.uuid4().hex[:12], request=request, options=options or {})
        with self._lock:
            pending = sum(1 for j in self.jobs.values() if j.status in ("queued", "running"))
            if pending >= int(self.cfg.get("server.max_pending_jobs", 20)):
                raise QueueFull(f"{pending} lessons are already waiting - try again later")
            self.jobs[job.id] = job
            self.job_dir(job.id).mkdir(parents=True, exist_ok=True)      # the lesson's media files go here
            self.logs[job.id] = EventLog(self.job_dir(job.id) / "events.jsonl")
        self._save(job)
        self.logs[job.id].emit("queued", position=self._queue.qsize())
        self._queue.put(job.id)
        return job

    def get(self, job_id: str) -> Job:
        if job_id not in self.jobs:
            self._sync()
        return self.jobs[job_id]

    def list(self) -> list[Job]:
        self._sync()
        return sorted(self.jobs.values(), key=lambda j: j.created, reverse=True)

    def events(self, job_id: str) -> EventLog | None:
        return self.logs.get(job_id)

    def lesson(self, job_id: str) -> dict | None:
        row = self.db.one("SELECT doc FROM lessons WHERE job_id = ?", (job_id,))
        return json.loads(row["doc"]) if row else None

    def summaries(self) -> dict[str, dict]:
        """job id -> lesson board summary, for every finished lesson."""
        return {r["job_id"]: json.loads(r["summary"]) for r in self.db.all("SELECT job_id, summary FROM lessons")}

    def cancel(self, job_id: str) -> Job:
        job = self.jobs[job_id]
        with self._lock:                   # the worker flips queued -> running under the same lock
            was_queued = job.status == "queued"
            if was_queued:
                job.status, job.finished = "cancelled", time.time()
            log = self.logs.get(job_id)
            if job.status == "running" and log is not None:
                log.cancel_flag.set()
        if was_queued:
            self._save(job)
            self._finish_log(job)          # end the SSE stream of a job that never started
        return job

    def _finish_log(self, job: Job) -> None:
        log = self.logs.get(job.id)
        if log is None:
            return
        try:
            log.emit("job_end", status=job.status, error=job.error)
        finally:
            log.close()
            with self._lock:                # late subscribers replay events.jsonl from disk
                self.logs.pop(job.id, None)

    # -- worker ------------------------------------------------------------------------------
    def _loop(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                self._run_one(job_id)
            except Exception:              # the only worker must survive anything (e.g. a full disk)
                traceback.print_exc()

    def _run_one(self, job_id: str) -> None:
        with self._lock:
            job = self.jobs.get(job_id)
            if not job or job.status != "queued":
                return
            ev = self.logs[job_id]
            job.status, job.started = "running", time.time()
        try:
            self._save(job)
            ev.emit("job_start")
            lesson = self.pipeline.run(job.request, self.job_dir(job_id), ev, job.options)
            self._store_lesson(job_id, lesson if isinstance(lesson, dict) and lesson else None)
            job.status = "done"
        except Cancelled:
            job.status = "cancelled"
        except Exception as exc:
            job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"
            try:
                ev.emit("error", error=job.error, trace=traceback.format_exc(limit=8))
            except Exception:
                pass
        finally:
            # never leave a 6 GB model resident after a failure or cancel; each step best-effort
            try:
                self.pipeline.models.unload(ev)
            except Exception:
                pass
            job.finished = time.time()
            try:
                self._save(job)
            except Exception:
                traceback.print_exc()
            self._finish_log(job)
