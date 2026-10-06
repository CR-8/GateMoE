import json
import threading
import time

from fastapi.testclient import TestClient

from gatemoe.jobs import JobManager
from gatemoe.server.app import create_app


class FakeModels:
    def unload(self, ev=None):
        pass

    def status(self):
        return {"loaded": None}


class FakePipeline:
    def __init__(self):
        self.models = FakeModels()
        self.gate = threading.Event()

    def run(self, request, out_dir, ev, options):
        with ev.stage("route"):
            ev.emit("route_decision", selected=["notes"], gateways={"notes": 0.9})
        self.gate.wait(5)
        ev.check_cancel()
        (out_dir / "notes.md").write_text("# hi")
        lesson = {"request": request, "notes": {"title": "t", "sections": []}, "errors": {},
                  "metrics": ev.summary(), "specialists": {"available": [], "activated": ["router:clef-flash"]}}
        (out_dir / "lesson.json").write_text(json.dumps(lesson))
        return lesson


def wait_status(client, job_id, want, timeout=10):
    t = time.time()
    while time.time() - t < timeout:
        st = client.get(f"/api/lessons/{job_id}").json()["job"]["status"]
        if st in want:
            return st
        time.sleep(0.05)
    raise AssertionError(f"status never reached {want}")


def test_lesson_lifecycle_and_files(cfg):
    pipe = FakePipeline()
    jobs = JobManager(cfg, lambda: pipe)
    client = TestClient(create_app(cfg, jobs))
    assert "GateMoE" in client.get("/").text
    conf = client.get("/api/config").json()
    assert "kn" in conf["languages"] and "notes" in conf["gateways"]

    r = client.post("/api/lessons", json={"request": "Explain Ohm's law", "language": "auto"})
    assert r.status_code == 200
    job_id = r.json()["id"]
    pipe.gate.set()
    assert wait_status(client, job_id, {"done"}) == "done"
    body = client.get(f"/api/lessons/{job_id}").json()
    assert body["lesson"]["notes"]["title"] == "t"
    assert client.get(f"/api/lessons/{job_id}/files/notes.md").text == "# hi"
    assert client.get(f"/api/lessons/{job_id}/files/../job.json").status_code == 404
    assert client.get(f"/api/lessons/{job_id}/files/%2e%2e/%2e%2e/etc/passwd").status_code == 404
    assert client.get("/api/lessons/notanid/files/x").status_code == 404
    events = client.get(f"/api/lessons/{job_id}/events").text
    assert "route_decision" in events and "job_end" in events


def test_validation_and_cancel(cfg):
    pipe = FakePipeline()
    jobs = JobManager(cfg, lambda: pipe)
    client = TestClient(create_app(cfg, jobs))
    assert client.post("/api/lessons", json={"request": ""}).status_code == 422
    assert client.post("/api/lessons", json={"request": "x", "language": "zz"}).status_code == 400
    assert client.post("/api/lessons", json={"request": "x", "gateways": ["rm -rf"]}).status_code == 400
    job_id = client.post("/api/lessons", json={"request": "Explain diodes"}).json()["id"]
    wait_status(client, job_id, {"running"})
    client.post(f"/api/lessons/{job_id}/cancel")
    pipe.gate.set()
    assert wait_status(client, job_id, {"cancelled"}) == "cancelled"


def test_restart_marks_running_jobs_interrupted(cfg):
    pipe = FakePipeline()
    jobs = JobManager(cfg, lambda: pipe)
    job = jobs.submit("Explain capacitors")
    wait = time.time()
    while jobs.get(job.id).status != "running" and time.time() - wait < 5:
        time.sleep(0.05)
    again = JobManager(cfg, lambda: pipe)      # simulates a server restart mid-job
    assert again.get(job.id).status == "interrupted"
    pipe.gate.set()
