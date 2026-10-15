"""The node database: jobs, lessons and the classroom live in one SQLite file; older file-based
nodes are imported once; finished lesson folders can be registered in place; generation can be
switched off for a browse-and-grade console."""
import json
import sqlite3

from fastapi.testclient import TestClient

from gatemoe.cli import main as cli_main
from gatemoe.jobs import JobManager
from gatemoe.server.app import create_app

LESSON = {"request": "Explain Ohm's law", "created": 1760000000.0, "language": {"lang": "en", "name": "English"},
          "route": {"mode": "clef", "selected": ["notes", "quiz"]}, "notes": {"title": "Ohm's law", "key_points": ["V=IR"]},
          "quiz": {"questions": [{"question": "q", "options": ["a", "b"], "answer_index": 1}]},
          "metrics": {"wall_seconds": 30.0}}


class NoPipeline:
    class _M:
        def unload(self, *a):
            pass

        def status(self):
            return {"loaded": None}

    models = _M()

    def run(self, *a, **k):
        raise AssertionError("must not run")


def test_legacy_files_are_imported_once(cfg):
    jobs_dir = cfg.path("paths.jobs_dir")
    old = jobs_dir / "0123456789ab"
    old.mkdir(parents=True)
    (old / "job.json").write_text(json.dumps({"id": "0123456789ab", "request": "Explain Ohm's law", "options": {},
                                              "status": "done", "created": 1760000000.0, "started": 1760000001.0,
                                              "finished": 1760000031.0, "error": None, "stage": None}))
    (old / "lesson.json").write_text(json.dumps(LESSON))
    (jobs_dir.parent / "classroom.json").write_text(json.dumps({
        "students": [{"id": "s1", "name": "Asha", "created": 1.0}],
        "sections": [{"id": "c1", "name": "Period 1", "kind": "period", "color": "#1a73e8", "student_ids": ["s1"],
                      "archived": False, "created": 1.0}],
        "assignments": [{"id": "a1", "lesson_id": "0123456789ab", "section_ids": ["c1"], "title": "", "posted": 2.0}],
        "feedback": [{"id": "f1", "text": "Good", "points": 1, "uses": 1, "created": 1.0}],
        "submissions": [{"id": "x1", "assignment_id": "a1", "student_id": "s1", "answers": [1], "correct": [1],
                         "auto": 2, "max": 2, "adjust": 1, "grade": 2, "feedback_ids": ["f1"], "comment": "", "submitted": 3.0}],
        "lesson_meta": {"0123456789ab": {"status": "published"}}}))

    client = TestClient(create_app(cfg, JobManager(cfg, NoPipeline)))
    board = client.get("/api/lessons").json()
    assert [(b["id"], b["title"], b["board"]) for b in board] == [("0123456789ab", "Ohm's law", "published")]
    state = client.get("/api/classroom").json()
    assert state["sections"][0]["student_ids"] == ["s1"] and state["submissions"][0]["feedback_ids"] == ["f1"]
    assert (old / "job.json.imported").exists() and (jobs_dir.parent / "classroom.json.imported").exists()

    # everything now lives in the database: a restart reads it back without the files
    again = TestClient(create_app(cfg, JobManager(cfg, NoPipeline)))
    assert again.get("/api/lessons/0123456789ab").json()["lesson"]["notes"]["title"] == "Ohm's law"
    with sqlite3.connect(cfg.path("paths.db")) as con:
        assert con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 1


def test_import_registers_folders_in_place(cfg, tmp_path, capsys):
    run = tmp_path / "benchmark" / "lessons" / "en-01"
    run.mkdir(parents=True)
    (run / "lesson.json").write_text(json.dumps(LESSON))
    (run / "notes.md").write_text("# Ohm's law")
    conf = tmp_path / "node.yaml"
    conf.write_text(json.dumps({"paths": {"data_dir": str(cfg.path("paths.data_dir"))}}))
    assert cli_main(["--config", str(conf), "import", str(tmp_path / "benchmark")]) == 0
    assert cli_main(["--config", str(conf), "import", str(tmp_path / "benchmark")]) == 0    # idempotent
    assert "0 lesson(s) added, 1 skipped or already" in capsys.readouterr().out

    client = TestClient(create_app(cfg, JobManager(cfg, NoPipeline)))
    (lesson,) = client.get("/api/lessons").json()
    assert lesson["status"] == "done" and lesson["counts"]["questions"] == 1
    assert client.get(f"/api/lessons/{lesson['id']}/files/notes.md").text == "# Ohm's law"   # served from the folder
    assert not list(cfg.path("paths.jobs_dir").glob(f"{lesson['id']}*"))                       # nothing copied


def test_generation_can_be_switched_off(cfg):
    client = TestClient(create_app(cfg, JobManager(cfg, NoPipeline, generate=False)))
    assert client.get("/api/config").json()["generate"] is False
    r = client.post("/api/lessons", json={"request": "Explain diodes"})
    assert r.status_code == 503 and "switched off" in r.json()["detail"]
    conf = client.get("/api/config").json()["classroom"]
    assert conf["points_per_question"] == 2 and "period" in conf["kinds"] and len(conf["palette"]) == 8
