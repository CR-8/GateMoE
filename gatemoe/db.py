"""The node's local database: one SQLite file (``paths.db``, default ``<data_dir>/gatemoe.db``).

It holds everything the server knows: lesson jobs and the generated lessons (the full lesson
document plus a small summary for the board), the roster, sections, posts, the feedback bank
and quiz submissions. Generated media (audio, video, PDF, images) stay as files in the job
folder; the database records where that folder is.

One connection per process, guarded by a lock (FastAPI handlers and the lesson worker run in
threads); WAL journal so readers never block the writer. Older nodes kept ``job.json`` per job
folder and a ``classroom.json``: those are imported once, automatically (see ``migrate_files``).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS jobs (
  id        TEXT PRIMARY KEY,
  request   TEXT NOT NULL,
  options   TEXT NOT NULL DEFAULT '{}',
  status    TEXT NOT NULL,                  -- queued | running | done | failed | cancelled | interrupted
  created   REAL NOT NULL,
  started   REAL,
  finished  REAL,
  error     TEXT,
  stage     TEXT,
  dir       TEXT,                           -- folder with the lesson files; NULL = <jobs_dir>/<id>
  source    TEXT NOT NULL DEFAULT 'node'    -- node: generated here | import: registered from a folder
);
CREATE INDEX IF NOT EXISTS jobs_created ON jobs(created);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_dir ON jobs(dir) WHERE dir IS NOT NULL;

CREATE TABLE IF NOT EXISTS lessons (
  job_id    TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
  doc       TEXT NOT NULL,                  -- the lesson document (what lesson.json holds)
  summary   TEXT NOT NULL,                  -- small JSON for the lesson board
  updated   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS lesson_status (    -- the teacher's status: review | published | archived
  lesson_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
  status    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS students (
  id        TEXT PRIMARY KEY,
  name      TEXT NOT NULL,
  name_key  TEXT NOT NULL UNIQUE,           -- lower-case name: no duplicates on the roster
  created   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sections (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  kind        TEXT NOT NULL,
  color       TEXT NOT NULL,
  archived    INTEGER NOT NULL DEFAULT 0,
  archived_at REAL,
  created     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS section_students (
  section_id TEXT NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
  student_id TEXT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
  position   INTEGER NOT NULL,
  PRIMARY KEY (section_id, student_id)
);

CREATE TABLE IF NOT EXISTS assignments (
  id        TEXT PRIMARY KEY,
  lesson_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  title     TEXT NOT NULL DEFAULT '',
  due       TEXT,
  note      TEXT NOT NULL DEFAULT '',
  posted    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS assignment_sections (
  assignment_id TEXT NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
  section_id    TEXT NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
  position      INTEGER NOT NULL,
  PRIMARY KEY (assignment_id, section_id)
);

CREATE TABLE IF NOT EXISTS feedback (
  id      TEXT PRIMARY KEY,
  text    TEXT NOT NULL,
  points  REAL NOT NULL DEFAULT 0,
  tag     TEXT NOT NULL DEFAULT '',
  uses    INTEGER NOT NULL DEFAULT 0,
  created REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS submissions (
  id            TEXT PRIMARY KEY,
  assignment_id TEXT NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
  student_id    TEXT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
  answers       TEXT NOT NULL,              -- JSON list, one chosen option index (or null) per question
  correct       TEXT NOT NULL,              -- JSON list, the answer key at submission time
  auto          REAL NOT NULL,
  max           REAL NOT NULL,
  adjust        REAL NOT NULL DEFAULT 0,
  grade         REAL NOT NULL,
  comment       TEXT NOT NULL DEFAULT '',
  submitted     REAL NOT NULL,
  graded        REAL,
  UNIQUE (assignment_id, student_id)
);

CREATE TABLE IF NOT EXISTS submission_feedback (
  submission_id TEXT NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
  feedback_id   TEXT NOT NULL REFERENCES feedback(id) ON DELETE CASCADE,
  position      INTEGER NOT NULL,
  PRIMARY KEY (submission_id, feedback_id)
);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None, timeout=10)
        self.conn.row_factory = sqlite3.Row
        for pragma in ("journal_mode=WAL", "synchronous=NORMAL", "foreign_keys=ON", "busy_timeout=10000"):
            self.conn.execute(f"PRAGMA {pragma}")
        self.conn.executescript(SCHEMA)        # idempotent (IF NOT EXISTS)
        with self.tx() as c:
            row = c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None:
                c.execute("INSERT INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
            elif int(row["value"]) > SCHEMA_VERSION:
                raise RuntimeError(f"{self.path} was written by a newer GateMoE (schema {row['value']})")

    @contextmanager
    def tx(self):
        """One write transaction (BEGIN IMMEDIATE ... COMMIT), serialised with the process lock."""
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            self.conn.execute("COMMIT")

    def all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql: str, args: tuple = ()) -> dict | None:
        with self.lock:
            r = self.conn.execute(sql, args).fetchone()
        return dict(r) if r is not None else None

    def close(self) -> None:
        with self.lock:
            self.conn.close()


def summarize_lesson(lesson: dict, job_dir: Path | None = None) -> dict:
    """What the lesson board needs (the full document can be large)."""
    route = lesson.get("route") or {}
    plan, notes = lesson.get("plan") or {}, lesson.get("notes") or {}
    png = (lesson.get("concept_map") or {}).get("png")
    return {
        "title": notes.get("title") or plan.get("title") or (lesson.get("request") or "")[:80],
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
        "thumb": png if png and job_dir is not None and (job_dir / str(png)).is_file() else None,
    }


def put_lesson(c: sqlite3.Connection, job_id: str, lesson: dict, job_dir: Path | None) -> None:
    c.execute("INSERT INTO lessons(job_id, doc, summary, updated) VALUES (?, ?, ?, ?) "
              "ON CONFLICT(job_id) DO UPDATE SET doc=excluded.doc, summary=excluded.summary, updated=excluded.updated",
              (job_id, json.dumps(lesson, ensure_ascii=False), json.dumps(summarize_lesson(lesson, job_dir),
                                                                          ensure_ascii=False), time.time()))
