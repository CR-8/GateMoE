"""Background lesson jobs: single worker (one Pi, one big model at a time), persisted to disk."""
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
from .runtime.events import Cancelled, EventLog

_ID = re.compile(r"^[a-f0-9]{12}$")


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


class JobManager:
    def __init__(self, cfg: Config, pipeline_factory):
        self.cfg = cfg
        self.root = cfg.path("paths.jobs_dir")
        self.root.mkdir(parents=True, exist_ok=True)
        self._pipeline_factory = pipeline_factory
        self._pipeline = None
        self.jobs: dict[str, Job] = {}
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
        return self.root / job_id

    def _save(self, job: Job) -> None:
        d = self.job_dir(job.id)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "job.json.tmp"
        tmp.write_text(json.dumps(job.public(), ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(d / "job.json")

    def _load_existing(self) -> None:
        for p in sorted(self.root.glob("*/job.json")):
            try:
                job = Job(**json.loads(p.read_text(encoding="utf-8")))
            except (OSError, TypeError, json.JSONDecodeError):
                continue
            if job.status in ("queued", "running"):
                job.status, job.error = "interrupted", "server restarted while this job was pending"
                self._save(job)
            self.jobs[job.id] = job

    # -- API ---------------------------------------------------------------------------------
    def submit(self, request: str, options: dict | None = None) -> Job:
        request = (request or "").strip()
        if not request:
            raise ValueError("empty request")
        if len(request) > 2000:
            raise ValueError("request too long (max 2000 characters)")
        job = Job(id=uuid.uuid4().hex[:12], request=request, options=options or {})
        with self._lock:
            self.jobs[job.id] = job
            self.logs[job.id] = EventLog(self.job_dir(job.id) / "events.jsonl")
        self._save(job)
        self.logs[job.id].emit("queued", position=self._queue.qsize())
        self._queue.put(job.id)
        return job

    def get(self, job_id: str) -> Job:
        return self.jobs[job_id]

    def list(self) -> list[Job]:
        return sorted(self.jobs.values(), key=lambda j: j.created, reverse=True)

    def events(self, job_id: str) -> EventLog | None:
        return self.logs.get(job_id)

    def lesson(self, job_id: str) -> dict | None:
        p = self.job_dir(job_id) / "lesson.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        return None

    def cancel(self, job_id: str) -> Job:
        job = self.jobs[job_id]
        if job.status == "queued":
            job.status, job.finished = "cancelled", time.time()
            self._save(job)
        elif job.status == "running" and job_id in self.logs:
            self.logs[job_id].cancel_flag.set()
        return job

    # -- worker ------------------------------------------------------------------------------
    def _loop(self) -> None:
        while True:
            job_id = self._queue.get()
            job = self.jobs.get(job_id)
            if not job or job.status != "queued":
                continue
            ev = self.logs[job_id]
            job.status, job.started = "running", time.time()
            self._save(job)
            ev.emit("job_start")
            try:
                self.pipeline.run(job.request, self.job_dir(job_id), ev, job.options)
                job.status = "done"
            except Cancelled:
                job.status = "cancelled"
            except Exception as exc:
                job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"
                ev.emit("error", error=job.error, trace=traceback.format_exc(limit=8))
            finally:
                # never leave a 6 GB model resident after a failure or cancel
                try:
                    self.pipeline.models.unload(ev)
                except Exception:
                    pass
                job.finished = time.time()
                self._save(job)
                ev.emit("job_end", status=job.status, error=job.error)
                ev.close()
