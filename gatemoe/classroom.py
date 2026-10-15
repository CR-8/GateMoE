"""Classroom layer for institutes (teacher side), after the "Redesign of Google Classroom" case study:

* Sections - teachers group students into sections of their choice (periods, blocks, clubs,
  tutoring groups). One lesson is posted once to several sections; grading can be filtered by
  section. Deleted sections are archived (and can be restored), their students stay in the roster.
* Feedback saver + auto-grader - a reusable bank of feedback comments, each optionally carrying
  grade points (e.g. "No working shown: -5"). Applying saved feedback to a submission adjusts its
  grade automatically; the quiz itself is auto-marked from the generated answer key.

Storage: the node database (gatemoe/db.py). No accounts: a node is a trusted LAN appliance and
students are names on a roster.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from .db import Database

PALETTE = ["#1a73e8", "#188038", "#e37400", "#9334e6", "#d93025", "#007b83", "#b06000", "#5f6368"]
KINDS = {"period": "Class period", "block": "Block", "club": "Club", "tutoring": "Tutoring group", "group": "Other group"}
POINTS_PER_QUESTION = 2
LESSON_STATUSES = ("review", "published", "archived")


def _id() -> str:
    return uuid.uuid4().hex[:10]


class ClassroomError(ValueError):
    pass


def _name(s, n: int = 80, what: str = "name") -> str:
    s = " ".join(str(s or "").split())[:n]
    if not s:
        raise ClassroomError(f"{what} must not be empty")
    return s


def _points(v) -> float:
    try:
        p = float(v)
    except (TypeError, ValueError):
        raise ClassroomError("points must be a number") from None
    if not -100 <= p <= 100:
        raise ClassroomError("points must be between -100 and 100")
    return round(p, 2)


class Classroom:
    def __init__(self, db: Database, legacy_json: Path | None = None):
        self.db = db
        if legacy_json is not None:
            self._import_legacy(Path(legacy_json))

    # -- reading -------------------------------------------------------------------------------
    def snapshot(self) -> dict:
        """Everything the console shows, in one consistent read."""
        with self.db.lock:
            q = self.db.all
            members: dict[str, list[str]] = {}
            for r in q("SELECT section_id, student_id FROM section_students ORDER BY position"):
                members.setdefault(r["section_id"], []).append(r["student_id"])
            posted_to: dict[str, list[str]] = {}
            for r in q("SELECT assignment_id, section_id FROM assignment_sections ORDER BY position"):
                posted_to.setdefault(r["assignment_id"], []).append(r["section_id"])
            applied: dict[str, list[str]] = {}
            for r in q("SELECT submission_id, feedback_id FROM submission_feedback ORDER BY position"):
                applied.setdefault(r["submission_id"], []).append(r["feedback_id"])
            return {
                "students": q("SELECT id, name, created FROM students ORDER BY created, rowid"),
                "sections": [{**s, "archived": bool(s["archived"]), "student_ids": members.get(s["id"], [])}
                             for s in q("SELECT * FROM sections ORDER BY created, rowid")],
                "assignments": [{**a, "section_ids": posted_to.get(a["id"], [])}
                                for a in q("SELECT * FROM assignments ORDER BY posted, rowid")],
                "feedback": q("SELECT * FROM feedback ORDER BY created, rowid"),
                "submissions": [self._sub_out(s, applied.get(s["id"], []))
                                for s in q("SELECT * FROM submissions ORDER BY submitted, rowid")],
                "lesson_meta": {r["lesson_id"]: {"status": r["status"]}
                                for r in q("SELECT lesson_id, status FROM lesson_status")},
            }

    @staticmethod
    def _sub_out(s: dict, feedback_ids: list[str]) -> dict:
        return {**s, "answers": json.loads(s["answers"]), "correct": json.loads(s["correct"]),
                "feedback_ids": feedback_ids}

    def _section(self, sec_id: str) -> dict:
        s = self.db.one("SELECT * FROM sections WHERE id = ?", (sec_id,))
        if s is None:
            raise KeyError("no such section")
        ids = [r["student_id"] for r in self.db.all(
            "SELECT student_id FROM section_students WHERE section_id = ? ORDER BY position", (sec_id,))]
        return {**s, "archived": bool(s["archived"]), "student_ids": ids}

    def _assignment(self, aid: str) -> dict:
        a = self.db.one("SELECT * FROM assignments WHERE id = ?", (aid,))
        if a is None:
            raise KeyError("no such assignment")
        ids = [r["section_id"] for r in self.db.all(
            "SELECT section_id FROM assignment_sections WHERE assignment_id = ? ORDER BY position", (aid,))]
        return {**a, "section_ids": ids}

    def _submission(self, sub_id: str) -> dict:
        s = self.db.one("SELECT * FROM submissions WHERE id = ?", (sub_id,))
        if s is None:
            raise KeyError("no such submission")
        ids = [r["feedback_id"] for r in self.db.all(
            "SELECT feedback_id FROM submission_feedback WHERE submission_id = ? ORDER BY position", (sub_id,))]
        return self._sub_out(s, ids)

    # -- students ------------------------------------------------------------------------------
    def add_students(self, names: list[str]) -> list[dict]:
        out = []
        with self.db.tx() as c:
            for n in names:
                n = _name(n, 60, "student name")
                if c.execute("SELECT 1 FROM students WHERE name_key = ?", (n.lower(),)).fetchone():
                    continue
                st = {"id": _id(), "name": n, "created": time.time()}
                c.execute("INSERT INTO students(id, name, name_key, created) VALUES (?,?,?,?)",
                          (st["id"], n, n.lower(), st["created"]))
                out.append(st)
        return out

    def remove_student(self, sid: str) -> None:
        with self.db.tx() as c:
            if c.execute("DELETE FROM students WHERE id = ?", (sid,)).rowcount == 0:
                raise KeyError("no such student")

    # -- sections (case study feature 1) -------------------------------------------------------
    @staticmethod
    def _set_members(c, sec_id: str, student_ids: list[str]) -> None:
        known = {r[0] for r in c.execute("SELECT id FROM students")}
        c.execute("DELETE FROM section_students WHERE section_id = ?", (sec_id,))
        c.executemany("INSERT INTO section_students(section_id, student_id, position) VALUES (?,?,?)",
                      [(sec_id, x, i) for i, x in enumerate(x for x in dict.fromkeys(student_ids) if x in known)])

    def add_section(self, name: str, student_ids: list[str] | None = None, color: str | None = None,
                    kind: str = "period") -> dict:
        sec_id = _id()
        with self.db.tx() as c:
            n = c.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
            c.execute("INSERT INTO sections(id, name, kind, color, archived, created) VALUES (?,?,?,?,0,?)",
                      (sec_id, _name(name, 60, "section name"), kind if kind in KINDS else "group",
                       color if color in PALETTE else PALETTE[n % len(PALETTE)], time.time()))
            self._set_members(c, sec_id, student_ids or [])
        return self._section(sec_id)

    def update_section(self, sec_id: str, **changes) -> dict:
        with self.db.tx() as c:
            if not c.execute("SELECT 1 FROM sections WHERE id = ?", (sec_id,)).fetchone():
                raise KeyError("no such section")
            if changes.get("name") is not None:
                c.execute("UPDATE sections SET name = ? WHERE id = ?", (_name(changes["name"], 60, "section name"), sec_id))
            if changes.get("student_ids") is not None:
                self._set_members(c, sec_id, list(changes["student_ids"]))
            if changes.get("color") in PALETTE:
                c.execute("UPDATE sections SET color = ? WHERE id = ?", (changes["color"], sec_id))
            if changes.get("kind") in KINDS:
                c.execute("UPDATE sections SET kind = ? WHERE id = ?", (changes["kind"], sec_id))
            if changes.get("archived") is not None:      # "delete" archives; "see archived" restores
                arch = bool(changes["archived"])
                c.execute("UPDATE sections SET archived = ?, archived_at = ? WHERE id = ?",
                          (int(arch), time.time() if arch else None, sec_id))
        return self._section(sec_id)

    def delete_section(self, sec_id: str) -> None:
        """Permanent removal (only for archived sections); posts keep their other sections."""
        with self.db.tx() as c:
            row = c.execute("SELECT archived FROM sections WHERE id = ?", (sec_id,)).fetchone()
            if row is None:
                raise KeyError("no such section")
            if not row[0]:
                raise ClassroomError("archive the section first")
            c.execute("DELETE FROM sections WHERE id = ?", (sec_id,))

    # -- assignments: post a lesson once to several sections ------------------------------------
    def assign(self, lesson_id: str, section_ids: list[str], title: str = "", due: str | None = None,
               note: str = "") -> dict:
        aid = _id()
        with self.db.tx() as c:
            live = {r[0] for r in c.execute("SELECT id FROM sections WHERE archived = 0")}
            secs = [x for x in dict.fromkeys(section_ids) if x in live]
            if not secs:
                raise ClassroomError("choose at least one active section")
            c.execute("INSERT INTO assignments(id, lesson_id, title, due, note, posted) VALUES (?,?,?,?,?,?)",
                      (aid, lesson_id, " ".join(str(title).split())[:140], (due or None) and str(due)[:32],
                       " ".join(str(note).split())[:500], time.time()))
            c.executemany("INSERT INTO assignment_sections(assignment_id, section_id, position) VALUES (?,?,?)",
                          [(aid, s, i) for i, s in enumerate(secs)])
            self._status(c, lesson_id, "published")
        return self._assignment(aid)

    def unassign(self, assignment_id: str) -> None:
        with self.db.tx() as c:
            row = c.execute("SELECT lesson_id FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
            if row is None:
                raise KeyError("no such assignment")
            c.execute("DELETE FROM assignments WHERE id = ?", (assignment_id,))     # cascades to submissions
            if not c.execute("SELECT 1 FROM assignments WHERE lesson_id = ?", (row[0],)).fetchone():
                self._status(c, row[0], "review")

    @staticmethod
    def _status(c, lesson_id: str, status: str) -> None:
        c.execute("INSERT INTO lesson_status(lesson_id, status) VALUES (?, ?) "
                  "ON CONFLICT(lesson_id) DO UPDATE SET status = excluded.status", (lesson_id, status))

    def set_lesson_status(self, lesson_id: str, status: str) -> dict:
        if status not in LESSON_STATUSES:
            raise ClassroomError("status must be review, published or archived")
        with self.db.tx() as c:
            self._status(c, lesson_id, status)
        return {"status": status}

    # -- feedback bank (case study feature 2) ---------------------------------------------------
    def add_feedback(self, text: str, points: float = 0, tag: str = "") -> dict:
        fb = {"id": _id(), "text": _name(text, 240, "feedback"), "points": _points(points),
              "tag": " ".join(str(tag).split())[:30], "uses": 0, "created": time.time()}
        with self.db.tx() as c:
            c.execute("INSERT INTO feedback(id, text, points, tag, uses, created) VALUES (:id,:text,:points,:tag,:uses,:created)", fb)
        return fb

    def update_feedback(self, fb_id: str, **changes) -> dict:
        with self.db.tx() as c:
            if not c.execute("SELECT 1 FROM feedback WHERE id = ?", (fb_id,)).fetchone():
                raise KeyError("no such feedback")
            if changes.get("text") is not None:
                c.execute("UPDATE feedback SET text = ? WHERE id = ?", (_name(changes["text"], 240, "feedback"), fb_id))
            if changes.get("points") is not None:
                c.execute("UPDATE feedback SET points = ? WHERE id = ?", (_points(changes["points"]), fb_id))
            if changes.get("tag") is not None:
                c.execute("UPDATE feedback SET tag = ? WHERE id = ?", (" ".join(str(changes["tag"]).split())[:30], fb_id))
        return self.db.one("SELECT * FROM feedback WHERE id = ?", (fb_id,))

    def delete_feedback(self, fb_id: str) -> None:
        with self.db.tx() as c:
            subs = [r[0] for r in c.execute("SELECT submission_id FROM submission_feedback WHERE feedback_id = ?", (fb_id,))]
            if c.execute("DELETE FROM feedback WHERE id = ?", (fb_id,)).rowcount == 0:
                raise KeyError("no such feedback")
            for s in subs:                          # grades that used it are recomputed without it
                self._regrade(c, s)

    # -- submissions + auto-grading ---------------------------------------------------------------
    def submit(self, assignment_id: str, student_id: str, answers: list, quiz: dict | None) -> dict:
        with self.db.tx() as c:
            if not c.execute("SELECT 1 FROM assignments WHERE id = ?", (assignment_id,)).fetchone():
                raise KeyError("no such assignment")
            if not c.execute("SELECT 1 FROM students WHERE id = ?", (student_id,)).fetchone():
                raise KeyError("no such student")
            member = c.execute("SELECT 1 FROM assignment_sections a JOIN section_students s ON s.section_id = a.section_id "
                               "WHERE a.assignment_id = ? AND s.student_id = ?", (assignment_id, student_id)).fetchone()
            if not member:
                raise ClassroomError("this student is not in a section the lesson was posted to")
            questions = (quiz or {}).get("questions") or []
            if not questions:
                raise ClassroomError("this lesson has no quiz")
            ans = [int(x) if isinstance(x, (int, float)) and 0 <= int(x) <= 9 else None
                   for x in list(answers)[: len(questions)]]
            ans += [None] * (len(questions) - len(ans))
            correct = [q.get("answer_index") for q in questions]
            auto = sum(POINTS_PER_QUESTION for a_, k in zip(ans, correct) if a_ is not None and a_ == k)
            prev = c.execute("SELECT id FROM submissions WHERE assignment_id = ? AND student_id = ?",
                             (assignment_id, student_id)).fetchone()
            sub_id = prev[0] if prev else _id()
            if prev:          # a resubmission replaces the answers; applied feedback and comment stay
                c.execute("UPDATE submissions SET answers=?, correct=?, auto=?, max=?, submitted=? WHERE id=?",
                          (json.dumps(ans), json.dumps(correct), auto, POINTS_PER_QUESTION * len(questions),
                           time.time(), sub_id))
            else:
                c.execute("INSERT INTO submissions(id, assignment_id, student_id, answers, correct, auto, max, adjust, "
                          "grade, comment, submitted) VALUES (?,?,?,?,?,?,?,0,0,'',?)",
                          (sub_id, assignment_id, student_id, json.dumps(ans), json.dumps(correct), auto,
                           POINTS_PER_QUESTION * len(questions), time.time()))
            self._regrade(c, sub_id)
        return self._submission(sub_id)

    def grade(self, submission_id: str, feedback_ids: list[str] | None = None, comment: str | None = None) -> dict:
        """Apply saved feedback (each may carry points) and an optional personal comment."""
        with self.db.tx() as c:
            if not c.execute("SELECT 1 FROM submissions WHERE id = ?", (submission_id,)).fetchone():
                raise KeyError("no such submission")
            if feedback_ids is not None:
                bank = {r[0] for r in c.execute("SELECT id FROM feedback")}
                new = [x for x in dict.fromkeys(feedback_ids) if x in bank]
                old = {r[0] for r in c.execute("SELECT feedback_id FROM submission_feedback WHERE submission_id = ?",
                                               (submission_id,))}
                c.executemany("UPDATE feedback SET uses = uses + 1 WHERE id = ?", [(x,) for x in set(new) - old])
                c.execute("DELETE FROM submission_feedback WHERE submission_id = ?", (submission_id,))
                c.executemany("INSERT INTO submission_feedback(submission_id, feedback_id, position) VALUES (?,?,?)",
                              [(submission_id, x, i) for i, x in enumerate(new)])
            if comment is not None:
                c.execute("UPDATE submissions SET comment = ? WHERE id = ?", (str(comment)[:2000], submission_id))
            c.execute("UPDATE submissions SET graded = ? WHERE id = ?", (time.time(), submission_id))
            self._regrade(c, submission_id)
        return self._submission(submission_id)

    @staticmethod
    def _regrade(c, sub_id: str) -> None:
        """grade = auto-marked points + points of the applied feedback, kept within [0, max]."""
        adj = c.execute("SELECT COALESCE(SUM(f.points), 0) FROM submission_feedback sf JOIN feedback f ON f.id = sf.feedback_id "
                        "WHERE sf.submission_id = ?", (sub_id,)).fetchone()[0]
        c.execute("UPDATE submissions SET adjust = ?, grade = ROUND(MAX(0, MIN([max], auto + ?)), 2) WHERE id = ?",
                  (round(adj, 2), adj, sub_id))

    # -- one-time import of the earlier classroom.json -------------------------------------------
    def _import_legacy(self, path: Path) -> None:
        if not path.is_file():
            return
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        with self.db.tx() as c:
            lessons = {r[0] for r in c.execute("SELECT id FROM jobs")}
            for s in d.get("students") or []:
                c.execute("INSERT OR IGNORE INTO students(id, name, name_key, created) VALUES (?,?,?,?)",
                          (s["id"], s["name"], s["name"].lower(), s.get("created") or time.time()))
            for s in d.get("sections") or []:
                c.execute("INSERT OR IGNORE INTO sections(id, name, kind, color, archived, archived_at, created) "
                          "VALUES (?,?,?,?,?,?,?)", (s["id"], s["name"], s.get("kind") or "group", s.get("color") or PALETTE[0],
                                                     int(bool(s.get("archived"))), s.get("archived_at"), s.get("created") or time.time()))
                self._set_members(c, s["id"], s.get("student_ids") or [])
            for a in d.get("assignments") or []:
                if a.get("lesson_id") not in lessons:
                    continue
                c.execute("INSERT OR IGNORE INTO assignments(id, lesson_id, title, due, note, posted) VALUES (?,?,?,?,?,?)",
                          (a["id"], a["lesson_id"], a.get("title") or "", a.get("due"), a.get("note") or "", a.get("posted") or time.time()))
                c.executemany("INSERT OR IGNORE INTO assignment_sections(assignment_id, section_id, position) "
                              "SELECT ?, id, ? FROM sections WHERE id = ?", [(a["id"], i, s) for i, s in enumerate(a.get("section_ids") or [])])
            for f in d.get("feedback") or []:
                c.execute("INSERT OR IGNORE INTO feedback(id, text, points, tag, uses, created) VALUES (?,?,?,?,?,?)",
                          (f["id"], f["text"], f.get("points") or 0, f.get("tag") or "", f.get("uses") or 0, f.get("created") or time.time()))
            subs = {r[0] for r in c.execute("SELECT id FROM assignments")}
            studs = {r[0] for r in c.execute("SELECT id FROM students")}
            for s in d.get("submissions") or []:
                if s.get("assignment_id") not in subs or s.get("student_id") not in studs:
                    continue
                c.execute("INSERT OR IGNORE INTO submissions(id, assignment_id, student_id, answers, correct, auto, max, adjust, "
                          "grade, comment, submitted, graded) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                          (s["id"], s["assignment_id"], s["student_id"], json.dumps(s.get("answers") or []),
                           json.dumps(s.get("correct") or []), s.get("auto") or 0, s.get("max") or 0, s.get("adjust") or 0,
                           s.get("grade") or 0, s.get("comment") or "", s.get("submitted") or time.time(), s.get("graded")))
                c.executemany("INSERT OR IGNORE INTO submission_feedback(submission_id, feedback_id, position) "
                              "SELECT ?, id, ? FROM feedback WHERE id = ?", [(s["id"], i, f) for i, f in enumerate(s.get("feedback_ids") or [])])
            for lid, meta in (d.get("lesson_meta") or {}).items():
                if lid in lessons and (meta or {}).get("status") in LESSON_STATUSES:
                    self._status(c, lid, meta["status"])
        path.rename(path.with_name(path.name + ".imported"))
