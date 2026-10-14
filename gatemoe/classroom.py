"""Classroom layer for institutes (teacher side), after the "Redesign of Google Classroom" case study:

* Sections - teachers group students into sections of their choice (periods, blocks, clubs,
  tutoring groups). One lesson is posted once to several sections; grading can be filtered by
  section. Deleted sections are archived (and can be restored), their students stay in the roster.
* Feedback saver + auto-grader - a reusable bank of feedback comments, each optionally carrying
  grade points (e.g. "No working shown: -5"). Applying saved feedback to a submission adjusts its
  grade automatically; the quiz itself is auto-marked from the generated answer key.

Storage: one JSON file next to the jobs folder (atomic writes, a lock for the single server
process). No accounts: a node is a trusted LAN appliance; students are names on a roster.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

PALETTE = ["#1a73e8", "#188038", "#e37400", "#9334e6", "#d93025", "#007b83", "#b06000", "#5f6368"]
POINTS_PER_QUESTION = 2


def _id() -> str:
    return uuid.uuid4().hex[:10]


class ClassroomError(ValueError):
    pass


class Classroom:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.data = self._load()

    # -- storage -------------------------------------------------------------------------------
    def _empty(self) -> dict:
        return {"students": [], "sections": [], "assignments": [], "feedback": [], "submissions": [],
                "lesson_meta": {}}

    def _load(self) -> dict:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty()
        base = self._empty()
        base.update({k: v for k, v in d.items() if k in base})
        return base

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def snapshot(self) -> dict:
        with self.lock:
            return json.loads(json.dumps(self.data))

    @staticmethod
    def _find(items: list[dict], item_id: str, what: str) -> dict:
        for it in items:
            if it["id"] == item_id:
                return it
        raise KeyError(f"no such {what}")

    @staticmethod
    def _name(s, n: int = 80, what: str = "name") -> str:
        s = " ".join(str(s or "").split())[:n]
        if not s:
            raise ClassroomError(f"{what} must not be empty")
        return s

    # -- students ------------------------------------------------------------------------------
    def add_students(self, names: list[str]) -> list[dict]:
        out = []
        with self.lock:
            have = {s["name"].lower() for s in self.data["students"]}
            for n in names:
                n = self._name(n, 60, "student name")
                if n.lower() in have:
                    continue
                st = {"id": _id(), "name": n, "created": time.time()}
                self.data["students"].append(st)
                have.add(n.lower())
                out.append(st)
            self._save()
        return out

    def remove_student(self, sid: str) -> None:
        with self.lock:
            self._find(self.data["students"], sid, "student")
            self.data["students"] = [s for s in self.data["students"] if s["id"] != sid]
            for sec in self.data["sections"]:
                sec["student_ids"] = [x for x in sec["student_ids"] if x != sid]
            self._save()

    # -- sections (case study feature 1) -------------------------------------------------------
    def add_section(self, name: str, student_ids: list[str] | None = None, color: str | None = None,
                    kind: str = "period") -> dict:
        with self.lock:
            known = {s["id"] for s in self.data["students"]}
            sec = {"id": _id(), "name": self._name(name, 60, "section name"),
                   "kind": kind if kind in ("period", "block", "club", "tutoring", "group") else "group",
                   "color": color if color in PALETTE else PALETTE[len(self.data["sections"]) % len(PALETTE)],
                   "student_ids": [x for x in dict.fromkeys(student_ids or []) if x in known],
                   "archived": False, "created": time.time()}
            self.data["sections"].append(sec)
            self._save()
            return sec

    def update_section(self, sec_id: str, **changes) -> dict:
        with self.lock:
            sec = self._find(self.data["sections"], sec_id, "section")
            if "name" in changes and changes["name"] is not None:
                sec["name"] = self._name(changes["name"], 60, "section name")
            if changes.get("student_ids") is not None:
                known = {s["id"] for s in self.data["students"]}
                sec["student_ids"] = [x for x in dict.fromkeys(changes["student_ids"]) if x in known]
            if changes.get("color") in PALETTE:
                sec["color"] = changes["color"]
            if changes.get("archived") is not None:
                sec["archived"] = bool(changes["archived"])     # "delete" archives; "see archived" restores
                sec["archived_at"] = time.time() if sec["archived"] else None
            self._save()
            return sec

    def delete_section(self, sec_id: str) -> None:
        """Permanent removal (only for archived sections); assignments keep their other sections."""
        with self.lock:
            sec = self._find(self.data["sections"], sec_id, "section")
            if not sec["archived"]:
                raise ClassroomError("archive the section first")
            self.data["sections"] = [s for s in self.data["sections"] if s["id"] != sec_id]
            for a in self.data["assignments"]:
                a["section_ids"] = [x for x in a["section_ids"] if x != sec_id]
            self._save()

    # -- assignments: post a lesson once to several sections ------------------------------------
    def assign(self, lesson_id: str, section_ids: list[str], title: str = "", due: str | None = None,
               note: str = "") -> dict:
        with self.lock:
            live = {s["id"] for s in self.data["sections"] if not s["archived"]}
            secs = [x for x in dict.fromkeys(section_ids) if x in live]
            if not secs:
                raise ClassroomError("choose at least one active section")
            a = {"id": _id(), "lesson_id": lesson_id, "section_ids": secs, "title": " ".join(str(title).split())[:140],
                 "due": (due or None) and str(due)[:32], "note": " ".join(str(note).split())[:500],
                 "posted": time.time()}
            self.data["assignments"].append(a)
            self.data["lesson_meta"].setdefault(lesson_id, {})["status"] = "published"
            self._save()
            return a

    def unassign(self, assignment_id: str) -> None:
        with self.lock:
            a = self._find(self.data["assignments"], assignment_id, "assignment")
            self.data["assignments"] = [x for x in self.data["assignments"] if x["id"] != assignment_id]
            self.data["submissions"] = [s for s in self.data["submissions"] if s["assignment_id"] != assignment_id]
            if not any(x["lesson_id"] == a["lesson_id"] for x in self.data["assignments"]):
                self.data["lesson_meta"].setdefault(a["lesson_id"], {})["status"] = "review"
            self._save()

    def set_lesson_status(self, lesson_id: str, status: str) -> dict:
        if status not in ("review", "published", "archived"):
            raise ClassroomError("status must be review, published or archived")
        with self.lock:
            meta = self.data["lesson_meta"].setdefault(lesson_id, {})
            meta["status"] = status
            self._save()
            return meta

    # -- feedback bank (case study feature 2) ---------------------------------------------------
    def add_feedback(self, text: str, points: float = 0, tag: str = "") -> dict:
        with self.lock:
            fb = {"id": _id(), "text": self._name(text, 240, "feedback"), "points": self._points(points),
                  "tag": " ".join(str(tag).split())[:30], "uses": 0, "created": time.time()}
            self.data["feedback"].append(fb)
            self._save()
            return fb

    def update_feedback(self, fb_id: str, **changes) -> dict:
        with self.lock:
            fb = self._find(self.data["feedback"], fb_id, "feedback")
            if changes.get("text") is not None:
                fb["text"] = self._name(changes["text"], 240, "feedback")
            if changes.get("points") is not None:
                fb["points"] = self._points(changes["points"])
            if changes.get("tag") is not None:
                fb["tag"] = " ".join(str(changes["tag"]).split())[:30]
            self._save()
            return fb

    def delete_feedback(self, fb_id: str) -> None:
        with self.lock:
            self._find(self.data["feedback"], fb_id, "feedback")
            self.data["feedback"] = [f for f in self.data["feedback"] if f["id"] != fb_id]
            for s in self.data["submissions"]:
                s["feedback_ids"] = [x for x in s.get("feedback_ids", []) if x != fb_id]
                self._grade(s)
            self._save()

    @staticmethod
    def _points(v) -> float:
        try:
            p = float(v)
        except (TypeError, ValueError):
            raise ClassroomError("points must be a number") from None
        if not -100 <= p <= 100:
            raise ClassroomError("points must be between -100 and 100")
        return round(p, 2)

    # -- submissions + auto-grading ---------------------------------------------------------------
    def submit(self, assignment_id: str, student_id: str, answers: list, quiz: dict | None) -> dict:
        with self.lock:
            a = self._find(self.data["assignments"], assignment_id, "assignment")
            st = self._find(self.data["students"], student_id, "student")
            in_sections = {sid for sec in self.data["sections"] if sec["id"] in a["section_ids"]
                           for sid in sec["student_ids"]}
            if st["id"] not in in_sections:
                raise ClassroomError("this student is not in a section the lesson was posted to")
            questions = (quiz or {}).get("questions") or []
            if not questions:
                raise ClassroomError("this lesson has no quiz")
            ans = [int(x) if isinstance(x, (int, float)) and 0 <= int(x) <= 9 else None
                   for x in list(answers)[: len(questions)]]
            ans += [None] * (len(questions) - len(ans))
            correct = [q.get("answer_index") for q in questions]
            prev = next((s for s in self.data["submissions"]
                         if s["assignment_id"] == assignment_id and s["student_id"] == student_id), None)
            sub = prev or {"id": _id(), "assignment_id": assignment_id, "student_id": student_id,
                           "feedback_ids": [], "comment": ""}
            sub.update(answers=ans, correct=correct, submitted=time.time(),
                       auto=sum(POINTS_PER_QUESTION for a_, c in zip(ans, correct) if a_ is not None and a_ == c),
                       max=POINTS_PER_QUESTION * len(questions))
            self._grade(sub)
            if prev is None:
                self.data["submissions"].append(sub)
            self._save()
            return sub

    def grade(self, submission_id: str, feedback_ids: list[str] | None = None, comment: str | None = None) -> dict:
        """Apply saved feedback (each may carry points) and an optional personal comment."""
        with self.lock:
            sub = self._find(self.data["submissions"], submission_id, "submission")
            if feedback_ids is not None:
                bank = {f["id"]: f for f in self.data["feedback"]}
                new = [x for x in dict.fromkeys(feedback_ids) if x in bank]
                for x in set(new) - set(sub.get("feedback_ids", [])):
                    bank[x]["uses"] = bank[x].get("uses", 0) + 1
                sub["feedback_ids"] = new
            if comment is not None:
                sub["comment"] = str(comment)[:2000]
            sub["graded"] = time.time()
            self._grade(sub)
            self._save()
            return sub

    def _grade(self, sub: dict) -> None:
        bank = {f["id"]: f for f in self.data["feedback"]}
        adj = sum(bank[x]["points"] for x in sub.get("feedback_ids", []) if x in bank)
        sub["adjust"] = round(adj, 2)
        sub["grade"] = round(max(0.0, min(float(sub.get("max", 0)), sub.get("auto", 0) + adj)), 2)
