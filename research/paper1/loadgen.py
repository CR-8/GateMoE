#!/usr/bin/env python3
"""Multi-client load generator and least-loaded dispatcher for one or more GateMoE nodes.

Each GateMoE node is one machine with one lesson worker (one big model in RAM at a time). An
institute scales out by adding nodes; this script plays the "many student computers + a
dispatcher" side: it submits learner requests through each node's normal web API, sends each
one to the node with the shortest queue, probes the API's responsiveness while the nodes are
busy, and records queue wait, service time and makespan.

    # one node (the paper's serving phase does this automatically)
    python loadgen.py --nodes http://127.0.0.1:8000 --n 8 --out burst.json

    # horizontal scaling test: 4 nodes, 4 requests per node, same password on every node
    GATEMOE_PASSWORD=... python loadgen.py --nodes http://10.128.0.2:8000,http://10.128.0.3:8000,... \
        --per-node 4 --out scale_4.json

Requests come from requests.jsonl (the end-to-end set by default); every node must have the
models and archives installed (clone one VM from a machine image).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import random
import statistics
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
TERMINAL = {"done", "failed", "cancelled", "interrupted"}


class Node:
    def __init__(self, url: str, user: str, password: str):
        self.url = url.rstrip("/")
        self.headers = {"Content-Type": "application/json"}
        if password:
            tok = base64.b64encode(f"{user}:{password}".encode()).decode()
            self.headers["Authorization"] = f"Basic {tok}"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # LAN: never via a proxy
        self.submitted = 0

    def call(self, method: str, path: str, body: dict | None = None, timeout: float = 30) -> tuple[int, object, float]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=self.headers)
        t = time.perf_counter()
        try:
            with self.opener.open(req, timeout=timeout) as r:
                raw = r.read()
                status = r.status
        except urllib.error.HTTPError as exc:
            raw, status = exc.read(), exc.code
        except (OSError, TimeoutError) as exc:
            return -1, str(exc), time.perf_counter() - t
        dt_ = time.perf_counter() - t
        try:
            return status, json.loads(raw or b"null"), dt_
        except json.JSONDecodeError:
            return status, raw[:200].decode("utf-8", "replace"), dt_


def pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    i = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return round(xs[i], 3)


def load_requests(select: str, seed: int) -> list[dict]:
    rows = [json.loads(line) for line in (HERE / "requests.jsonl").read_text(encoding="utf-8").splitlines() if line]
    rows = [r for r in rows if r["in_scope"] and (select == "all" or r.get(select))]
    random.Random(seed).shuffle(rows)
    return rows


def run(node_urls: list[str], requests: list[dict], *, user: str = "gatemoe", password: str = "",
        mode: str = "clef", arrival: str = "burst", policy: str = "least_loaded", probe_s: float = 2.0,
        poll_s: float = 10.0, timeout_h: float = 24.0, seed: int = 1, log=print) -> dict:
    nodes = [Node(u, user, password) for u in node_urls]
    for n in nodes:                                    # wait until every node answers
        for _ in range(120):
            st, _, _ = n.call("GET", "/healthz", timeout=5)
            if st == 200:
                break
            time.sleep(2)
        else:
            raise RuntimeError(f"{n.url} is not answering /healthz")
    rng = random.Random(seed)
    t0 = time.time()
    probes: list[dict] = []
    stop = threading.Event()

    def prober():
        while not stop.is_set():
            for i, n in enumerate(nodes):
                st, body, lat = n.call("GET", "/api/system", timeout=30)
                probes.append({"t": round(time.time() - t0, 2), "node": i, "status": st, "latency_s": round(lat, 4),
                               "queue": body.get("queue") if isinstance(body, dict) else None,
                               "mem_available_mb": ((body or {}).get("sys") or {}).get("mem_available_mb")
                               if isinstance(body, dict) else None})
            stop.wait(probe_s)

    th = threading.Thread(target=prober, daemon=True)
    th.start()
    # arrival times (seconds after start)
    if arrival == "burst":
        at = [0.0] * len(requests)
    elif arrival.startswith("poisson:"):
        rate_per_s = float(arrival.split(":", 1)[1]) / 3600.0
        t, at = 0.0, []
        for _ in requests:
            t += rng.expovariate(rate_per_s)
            at.append(t)
    else:
        raise ValueError(arrival)
    jobs: list[dict] = []
    rr = 0
    for req, a in zip(requests, at):
        while time.time() - t0 < a:
            time.sleep(0.5)
        while True:
            if policy == "round_robin":
                order = [nodes[(rr + k) % len(nodes)] for k in range(len(nodes))]
                rr += 1
            else:                                          # least loaded: shortest queue right now
                qs = []
                for n in nodes:
                    st, body, _ = n.call("GET", "/api/system", timeout=30)
                    qs.append((body.get("queue", 99) if st == 200 and isinstance(body, dict) else 99, n.submitted, n))
                order = [x[2] for x in sorted(qs, key=lambda x: (x[0], x[1]))]
            body = {"request": req["text"], "language": req["lang"], "mode": mode}
            if mode == "manual":
                body["gateways"] = req["gold"]
            st, resp, lat = order[0].call("POST", "/api/lessons", body, timeout=60)
            if st == 200 and isinstance(resp, dict):
                order[0].submitted += 1
                jobs.append({"rid": req["id"], "node": nodes.index(order[0]), "job": resp["id"],
                             "submitted_t": round(time.time() - t0, 2), "submit_latency_s": round(lat, 4)})
                log(f"submitted {req['id']} -> node {jobs[-1]['node']} ({resp['id']})")
                break
            if st == 429:                                  # node queue full: wait and retry
                time.sleep(30)
                continue
            raise RuntimeError(f"submit failed on {order[0].url}: {st} {resp}")
    # wait for completion
    deadline = time.time() + timeout_h * 3600
    pending = {j["job"] for j in jobs}
    while pending and time.time() < deadline:
        for j in jobs:
            if j["job"] not in pending:
                continue
            st, body, _ = nodes[j["node"]].call("GET", f"/api/lessons/{j['job']}", timeout=60)
            if st != 200 or not isinstance(body, dict):
                continue
            job = body.get("job") or {}
            if job.get("status") in TERMINAL:
                pending.discard(j["job"])
                lesson = body.get("lesson") or {}
                m = lesson.get("metrics") or {}
                j.update(status=job["status"], error=job.get("error"), created=job.get("created"),
                         started=job.get("started"), finished=job.get("finished"),
                         done_t=round(time.time() - t0, 2),
                         wait_s=round(job["started"] - job["created"], 2) if job.get("started") else None,
                         service_s=round(job["finished"] - job["started"], 2) if job.get("started") else None,
                         wall_s=m.get("wall_seconds"), stage_seconds=m.get("stage_seconds"),
                         selected=(lesson.get("route") or {}).get("selected"),
                         model_load_s=m.get("model_load_seconds"))
                log(f"finished {j['rid']} on node {j['node']}: {job['status']} wait {j['wait_s']}s "
                    f"service {j['service_s']}s")
        time.sleep(poll_s)
    stop.set()
    th.join(timeout=probe_s + 35)
    done = [j for j in jobs if j.get("status") == "done"]
    makespan = max([j.get("done_t") or 0 for j in jobs] or [0])
    lat = [p["latency_s"] for p in probes if p["status"] == 200]
    summary = {
        "nodes": len(nodes), "requests": len(jobs), "done": len(done), "mode": mode, "arrival": arrival,
        "policy": policy, "makespan_s": makespan,
        "throughput_lessons_per_h": round(len(done) / (makespan / 3600), 3) if makespan else None,
        "wait_s": {"mean": round(statistics.mean([j["wait_s"] for j in done]), 1) if done else None,
                   "p50": pct([j["wait_s"] for j in done], .5), "p95": pct([j["wait_s"] for j in done], .95),
                   "max": max([j["wait_s"] for j in done] or [0])},
        "service_s": {"mean": round(statistics.mean([j["service_s"] for j in done]), 1) if done else None,
                      "p50": pct([j["service_s"] for j in done], .5), "p95": pct([j["service_s"] for j in done], .95)},
        "api_latency_s": {"n": len(lat), "p50": pct(lat, .5), "p95": pct(lat, .95), "p99": pct(lat, .99),
                          "max": max(lat or [0]), "errors": sum(1 for p in probes if p["status"] != 200)},
        "per_node_jobs": [sum(1 for j in jobs if j["node"] == i) for i in range(len(nodes))],
    }
    return {"summary": summary, "jobs": jobs, "probes": probes, "node_urls": node_urls}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--nodes", required=True, help="comma list of node URLs")
    ap.add_argument("--user", default=os.environ.get("GATEMOE_USER", "gatemoe"))
    ap.add_argument("--password", default=os.environ.get("GATEMOE_PASSWORD", ""))
    ap.add_argument("--n", type=int, help="number of requests")
    ap.add_argument("--per-node", type=int, help="requests per node (weak scaling); overrides --n")
    ap.add_argument("--select", default="e2e", help="e2e | ablation | all")
    ap.add_argument("--mode", default="clef", choices=["clef", "all", "manual"])
    ap.add_argument("--arrival", default="burst", help="burst | poisson:<requests per hour>")
    ap.add_argument("--policy", default="least_loaded", choices=["least_loaded", "round_robin"])
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--timeout-h", type=float, default=24)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    urls = [u.strip() for u in a.nodes.split(",") if u.strip()]
    reqs = load_requests(a.select, a.seed)
    n = a.per_node * len(urls) if a.per_node else (a.n or len(urls) * 4)
    res = run(urls, reqs[:n], user=a.user, password=a.password, mode=a.mode, arrival=a.arrival, policy=a.policy,
              timeout_h=a.timeout_h, seed=a.seed)
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res["summary"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
