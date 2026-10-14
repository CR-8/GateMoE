"""Classroom layer (case study features): sections, posting once to several sections,
feedback bank with grade points, auto-graded quiz submissions - through the HTTP API."""
import json

from fastapi.testclient import TestClient

from gatemoe.jobs import JobManager
from gatemoe.server.app import create_app

QUIZ = {"questions": [{"question": "q1", "options": ["a", "b", "c", "d"], "answer_index": 1, "explanation": ""},
                      {"question": "q2", "options": ["a", "b", "c", "d"], "answer_index": 2, "explanation": ""},
                      {"question": "q3", "options": ["a", "b", "c", "d"], "answer_index": 0, "explanation": ""}]}


class DonePipeline:
    class _M:
        def unload(self, *a):
            pass

    models = _M()

    def run(self, request, out, ev, options):
        lesson = {"request": request, "plan": {"title": "Ohm's law"}, "notes": {"title": "Ohm's law", "key_points": ["V=IR"]},
                  "quiz": QUIZ, "route": {"selected": ["notes", "quiz"], "mode": "clef", "subject": "physics"},
                  "language": {"lang": "en", "name": "English"}, "metrics": {"wall_seconds": 12.5}}
        (out / "lesson.json").write_text(json.dumps(lesson))
        return lesson


def wait_done(client, job_id):
    import time
    for _ in range(100):
        if client.get(f"/api/lessons/{job_id}").json()["job"]["status"] == "done":
            return
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_sections_assign_feedback_autograde(cfg):
    client = TestClient(create_app(cfg, JobManager(cfg, DonePipeline)))
    job = client.post("/api/lessons", json={"request": "Explain Ohm's law with a quiz"}).json()["id"]
    wait_done(client, job)
    board = client.get("/api/lessons").json()[0]
    assert board["title"] == "Ohm's law" and board["counts"]["questions"] == 3 and board["selected"] == ["notes", "quiz"]

    studs = client.post("/api/students", json={"names": ["Asha", "Ravi", "Meera", "asha"]}).json()
    assert [s["name"] for s in studs] == ["Asha", "Ravi", "Meera"]          # case-insensitive dedupe
    ids = {s["name"]: s["id"] for s in studs}
    p1 = client.post("/api/sections", json={"name": "Period 1", "student_ids": [ids["Asha"], ids["Ravi"]]}).json()
    club = client.post("/api/sections", json={"name": "Robotics club", "student_ids": [ids["Meera"]], "kind": "club"}).json()

    # post once, to two sections
    a = client.post("/api/assignments", json={"lesson_id": job, "section_ids": [p1["id"], club["id"]]}).json()
    assert set(a["section_ids"]) == {p1["id"], club["id"]}
    assert client.get("/api/lessons").json()[0]["board"] == "published"

    # a student outside those sections cannot submit; one inside is auto-graded (2 points per question)
    other = client.post("/api/students", json={"names": ["Zoya"]}).json()[0]
    assert client.post("/api/submissions", json={"assignment_id": a["id"], "student_id": other["id"],
                                                  "answers": [1, 2, 0]}).status_code == 400
    # the student view never carries the answer key; it lists only the students of those sections
    take = client.get(f"/api/assignments/{a['id']}/take").json()
    assert take["title"] == "Ohm's law" and len(take["questions"]) == 3
    assert all("answer_index" not in q for q in take["questions"])
    assert {s["name"] for s in take["students"]} == {"Asha", "Ravi", "Meera"}
    sub = client.post("/api/submissions", json={"assignment_id": a["id"], "student_id": ids["Asha"],
                                                "answers": [1, 3, 0]}).json()
    assert (sub["auto"], sub["max"], sub["grade"]) == (4, 6, 4) and len(sub["explanations"]) == 3
    assert next(s for s in client.get(f"/api/assignments/{a['id']}/take").json()["students"]
                if s["name"] == "Asha")["submitted"]

    # feedback saver: reusable comment with points -> grade adjusts automatically, clamped to [0, max]
    fb = client.post("/api/feedback", json={"text": "No working shown", "points": -3}).json()
    bonus = client.post("/api/feedback", json={"text": "Great explanation", "points": 5}).json()
    g = client.patch(f"/api/submissions/{sub['id']}", json={"feedback_ids": [fb["id"]]}).json()
    assert (g["adjust"], g["grade"]) == (-3, 1)
    g = client.patch(f"/api/submissions/{sub['id']}", json={"feedback_ids": [bonus["id"]]}).json()
    assert g["grade"] == 6                                                     # 4 + 5 clamped to max 6
    client.patch(f"/api/feedback/{bonus['id']}", json={"points": 1})          # editing the bank regrades on next use
    state = client.get("/api/classroom").json()
    assert next(f for f in state["feedback"] if f["id"] == bonus["id"])["uses"] == 1

    # deleting a section archives it; archived sections cannot receive new posts, and can be restored
    client.patch(f"/api/sections/{club['id']}", json={"archived": True})
    assert client.post("/api/assignments", json={"lesson_id": job, "section_ids": [club["id"]]}).status_code == 400
    assert client.patch(f"/api/sections/{club['id']}", json={"archived": False}).json()["archived"] is False
    assert client.delete(f"/api/sections/{p1['id']}").status_code == 400      # only archived ones can be removed

    k = client.get("/api/knowledge").json()
    assert "snapshots" in k and "delta_status" in k
