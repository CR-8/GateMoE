#!/usr/bin/env python3
"""Paper 1 experiment runner: a resumable, week-long measurement campaign on one 8 GB cloud VM.

    run.py run [--days 7] [--phases p2_router,p4_e2e] [--limit N]   # the campaign (systemd runs this)
    run.py status                                                    # progress, ETA, disk, last errors
    run.py analyze                                                   # compute metrics -> <out>/analysis
    run.py bundle                                                    # analyze + pack results for hand-back

Phases (in order; each is a list of items, every item's result is one JSON file, so a crash or
a reboot loses at most the item in progress):

  p0_env       machine provenance, disk read speed, llama-bench of the generator
  p1_swap      model load/unload ("swap") cost: cold / warm / after-the-other-model, 8 repeats
  p2_router    Clef-flash decisions for every labelled request (+ 20 repeats for determinism)
  p10_retrieval  local knowledge cache: archive catalogue, rescan time, search latency cold / warm
  p11_kdd      knowledge updates (KDD): snapshot layer (coverage gaps, newer Kiwix snapshots, add archives)
               and delta layer (newest arXiv papers -> novelty filter -> delta ZIM; freshness, pollution)
  p3_ablation  routing-schema variants on a 75-request subset (compact-only, verbose, one question per call)
  p8_serving   many clients, one node: a burst of 8 requests through the real web API and job queue
  p9_knowledge grounded vs ungrounded notes + quiz (local knowledge on / off) for blind human rating
  p12_fresh    lessons on this month's research with the KDD delta on / off
  p4_e2e       the headline: each request as ALL gateways / CLEF-routed / ORACLE (gold) gateways
  p6_genbench  generator: llama-server json_schema vs GateMoE's compact grammar on stored lesson plans
  p5_repeat    ALL + CLEF again for 10 requests (run-to-run variance)
  p7_quant     a 3-bit Clef-flash quant on the ablation subset (only if the disk has room)

Everything lands in $PAPER1_DIR (default <data_dir>/paper1). See docs/PAPER1_EXPERIMENT.md.
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import hashlib
import importlib.util
import json
import os
import random
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from gatemoe.config import load_config  # noqa: E402
from gatemoe.router.clef import GATEWAYS, build_questions, build_state, parse_answers  # noqa: E402
from gatemoe.runtime.events import EventLog  # noqa: E402
from gatemoe.runtime.http import post_json  # noqa: E402
from gatemoe.runtime.model_manager import ModelManager, cpu_has_amx  # noqa: E402
from gatemoe.runtime.sysinfo import meminfo_mb  # noqa: E402

REQUESTS_FILE = HERE / "requests.jsonl"
ARMS = ("all", "clef", "oracle")
MEDIA_SUFFIXES = {".mp4", ".mp3", ".wav", ".webm", ".ogg", ".m4a", ".png", ".jpg", ".jpeg", ".gif", ".mov"}
KEEP_MEDIA_PER_ARM_SLICE = 2           # lessons per (arm, slice) whose audio/video is kept for figures
REPEAT_RIDS = ["en-01", "en-05", "en-07", "en-17", "en-52", "hi-01", "hi-10", "hi-37", "kn-01", "kn-10"]
GENBENCH_RIDS = ["en-01", "en-07", "en-17", "en-30", "en-40", "en-52", "hi-01", "hi-10", "hi-37",
                 "kn-01", "kn-10", "kn-37"]
GENBENCH_TASKS = "notes,quiz,podcast,video"
SERVING_RIDS = ["en-01", "hi-01", "en-05", "kn-01", "en-07", "hinglish-03", "en-19", "hi-10"]
KNOWLEDGE_RIDS = ["en-01", "en-06", "en-12", "en-19", "en-26", "en-29", "en-34", "en-37", "en-44", "en-49",
                  "en-58", "en-64", "hi-02", "hi-09", "hi-13", "hi-15", "hi-22", "hi-26", "hi-30", "hi-33"]
QUANT_CANDIDATES = ["Q3_K_M", "IQ3_M", "Q3_K_L"]
QUANT_URL = "https://huggingface.co/bartowski/Cloudflare_clef-flash-GGUF/resolve/main/Cloudflare_clef-flash-{q}.gguf"

# Long, descriptive wording (research/clefbench/route.py) for the "verbose" schema variant
VERBOSE_GATEWAYS = {
    "notes": "Should the system produce written study notes for this learner?",
    "flashcards": "Should the system produce flashcards for memorising facts or vocabulary?",
    "quiz": "Should the system produce a quiz or practice questions?",
    "podcast": "Should the system produce an audio podcast the learner can listen to?",
    "video": "Should the system produce an animated explainer video?",
    "code": "Should the system produce runnable code examples or coding exercises?",
}


# ------------------------------------------------------------------------------------------ utils
def cfg_load():
    return load_config()


def out_root(cfg) -> Path:
    return Path(os.environ.get("PAPER1_DIR") or (cfg.path("paths.data_dir") / "paper1"))


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def load_requests() -> dict[str, dict]:
    rows = [json.loads(line) for line in REQUESTS_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {r["id"]: r for r in rows}


def log(out: Path, msg: str) -> None:
    line = f"{now()} {msg}"
    print(line, flush=True)
    (out / "logs").mkdir(parents=True, exist_ok=True)
    with (out / "logs" / "runner.log").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def proc_stat() -> list[int]:
    try:
        with open("/proc/stat") as fh:
            return [int(x) for x in fh.readline().split()[1:]]
    except OSError:
        return []


def cpu_fractions(a: list[int], b: list[int]) -> dict:
    """iowait / steal share of CPU time between two /proc/stat samples (steal = hypervisor took the CPU)."""
    if not a or not b:
        return {}
    d = [y - x for x, y in zip(a, b)]
    total = sum(d[:8]) or 1
    return {"iowait_frac": round(d[4] / total, 4), "steal_frac": round(d[7] / total, 4) if len(d) > 7 else None,
            "busy_frac": round(1 - (d[3] + d[4]) / total, 4)}


def evict(paths) -> bool:
    """Drop these files from the page cache (no root needed): POSIX_FADV_DONTNEED on clean pages."""
    ok = True
    for p in paths:
        try:
            fd = os.open(p, os.O_RDONLY)
            try:
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
            finally:
                os.close(fd)
        except (OSError, AttributeError):
            ok = False
    return ok


_libc = None


def residency_mb(path) -> float | None:
    """MB of the file currently in the page cache (fincore, else mincore through ctypes)."""
    path = str(path)
    if shutil.which("fincore"):
        try:
            res = subprocess.run(["fincore", "--bytes", "--noheadings", "--output", "RES", path],
                                 capture_output=True, text=True, timeout=60)
            if res.returncode == 0 and res.stdout.strip():
                return round(int(res.stdout.split()[0]) / 2**20, 1)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    global _libc
    try:
        if _libc is None:
            _libc = ctypes.CDLL(None, use_errno=True)
            _libc.mmap.restype = ctypes.c_void_p
            _libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_long]
            _libc.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
            _libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        size = os.path.getsize(path)
        if size == 0:
            return 0.0
        fd = os.open(path, os.O_RDONLY)
        try:
            addr = _libc.mmap(None, size, 1, 1, fd, 0)          # PROT_READ, MAP_SHARED
            if addr in (None, ctypes.c_void_p(-1).value):
                return None
            try:
                ps = os.sysconf("SC_PAGE_SIZE")
                n = (size + ps - 1) // ps
                vec = (ctypes.c_ubyte * n)()
                if _libc.mincore(addr, size, vec) != 0:
                    return None
                return round(sum(b & 1 for b in bytes(vec)) * ps / 2**20, 1)
            finally:
                _libc.munmap(addr, size)
        finally:
            os.close(fd)
    except Exception:
        return None


def model_files(cfg) -> list[Path]:
    return [cfg.model_path("router"), cfg.model_path("generator")]


def kill_stray_servers(cfg) -> None:
    exe = str(cfg.path("paths.llama_server"))
    subprocess.run(["pkill", "-f", re.escape(exe)], capture_output=True)
    time.sleep(1)


def disk_free_gb(path: Path) -> float:
    p = path
    while not p.exists():
        p = p.parent
    return round(shutil.disk_usage(p).free / 2**30, 2)


def cgroup_dir() -> Path | None:
    """This process's cgroup v2 folder (the systemd unit confines the campaign to a memory/CPU budget)."""
    try:
        for line in Path("/proc/self/cgroup").read_text().splitlines():
            if line.startswith("0::"):
                d = Path("/sys/fs/cgroup") / line[3:].lstrip("/")
                return d if d.exists() else None
    except OSError:
        pass
    return None


def _cg_read(name: str) -> str | None:
    d = cgroup_dir()
    try:
        return (d / name).read_text().strip() if d else None
    except OSError:
        return None


def cgroup_info() -> dict:
    mx = _cg_read("memory.max")
    ev = dict(line.split() for line in (_cg_read("memory.events") or "").splitlines() if " " in line)
    cur = _cg_read("memory.current")
    try:
        cpus = sorted(os.sched_getaffinity(0))
    except AttributeError:
        cpus = None
    return {"path": str(cgroup_dir()), "memory_max_mb": round(int(mx) / 2**20) if mx and mx.isdigit() else mx,
            "swap_max": _cg_read("memory.swap.max"),
            "memory_current_mb": round(int(cur) / 2**20) if cur and cur.isdigit() else None,
            "oom_kills": int(ev.get("oom_kill", 0)), "cpus": cpus}


class CgroupPeak:
    """Peak memory.current of the campaign's cgroup while a block runs (RSS + page cache inside the budget)."""

    def __init__(self, interval: float = 1.0):
        self.interval, self.peak, self._stop = interval, 0, None

    def __enter__(self):
        import threading

        self._stop = threading.Event()

        def run():
            while not self._stop.is_set():
                v = _cg_read("memory.current")
                if v and v.isdigit():
                    self.peak = max(self.peak, int(v))
                self._stop.wait(self.interval)

        self.oom0 = cgroup_info()["oom_kills"]
        self._t = threading.Thread(target=run, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join(timeout=5)
        self.result = {"cgroup_peak_mb": round(self.peak / 2**20) if self.peak else None,
                       "cgroup_oom_kills": cgroup_info()["oom_kills"] - self.oom0}


def mem_snapshot() -> dict:
    m = meminfo_mb()
    cur = _cg_read("memory.current")
    return {"mem_available_mb": round(m.get("MemAvailable", 0)), "cached_mb": round(m.get("Cached", 0)),
            "swap_used_mb": round(m.get("SwapTotal", 0) - m.get("SwapFree", 0)),
            "cgroup_current_mb": round(int(cur) / 2**20) if cur and cur.isdigit() else None}


def cpu_model() -> str:
    try:
        for line in open("/proc/cpuinfo"):
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return ""


# ------------------------------------------------------------------------------------- phases
class Phase:
    name = ""
    batch_size = 1
    item_timeout_s = 3600
    batch_overhead_s = 1800           # model load etc. on top of the per-item timeout
    max_attempts = 2
    min_left_h = 0.0                  # do not START an item when less than this is left before the deadline

    def __init__(self, cfg, out: Path, reqs: dict[str, dict], limit: int | None = None):
        self.cfg, self.out, self.reqs, self.limit = cfg, out, reqs, limit
        self.dir = out / self.name

    def items(self) -> list[dict]:
        raise NotImplementedError

    def all_items(self) -> list[dict]:
        its = self.items()
        return its[: self.limit] if self.limit else its

    def result_path(self, uid: str) -> Path:
        return self.dir / "items" / f"{uid}.json"

    def done(self, uid: str) -> bool:
        return self.result_path(uid).exists()

    def enabled(self) -> tuple[bool, str]:
        return True, ""

    def prepare(self) -> bool:
        return True

    def finish(self) -> None:
        pass

    def work(self, items: list[dict]) -> None:
        raise NotImplementedError


class EnvPhase(Phase):
    name = "p0_env"
    item_timeout_s = 3 * 3600

    def items(self):
        return [{"uid": "env", "group": "env"}]

    def work(self, items):
        cfg = self.cfg
        env: dict = {"time": now(), "hostname": socket.gethostname(), "uname": " ".join(os.uname()),
                     "cpu_model": cpu_model(), "nproc": os.cpu_count(), "amx": cpu_has_amx(),
                     "threads": cfg["hardware.threads"], "python": sys.version.split()[0],
                     "meminfo_mb": {k: round(v) for k, v in meminfo_mb().items()
                                    if k in ("MemTotal", "MemAvailable", "SwapTotal")},
                     "disk_free_gb": disk_free_gb(cfg.path("paths.data_dir")), "cgroup": cgroup_info()}
        try:
            env["os_release"] = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines()
                                     if "=" in line)
        except OSError:
            pass
        for key, url in (("machine_type", "instance/machine-type"), ("zone", "instance/zone"),
                         ("cpu_platform", "instance/cpu-platform")):
            try:   # Google Cloud metadata server (no proxy)
                req = urllib.request.Request(f"http://169.254.169.254/computeMetadata/v1/{url}",
                                             headers={"Metadata-Flavor": "Google"})
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                env["gcp_" + key] = opener.open(req, timeout=2).read().decode().rsplit("/", 1)[-1]
            except Exception:
                pass
        for name, path in (("gatemoe_rev", ROOT), ("llama_cpp_rev", cfg.path("paths.llama_server").parents[2])):
            try:
                env[name] = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True,
                                           text=True, timeout=10).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            env["lscpu"] = subprocess.run(["lscpu"], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            pass
        env["config"] = cfg.tree
        # model files: size, sha256, raw sequential read speed from a cold page cache
        env["models"] = {}
        for role, path in (("router", cfg.model_path("router")), ("generator", cfg.model_path("generator"))):
            rec: dict = {"file": path.name}
            if path.exists():
                rec["size_mb"] = round(path.stat().st_size / 2**20)
                evict([path])
                h = hashlib.sha256()
                t = time.perf_counter()
                with path.open("rb") as fh:
                    while chunk := fh.read(16 << 20):
                        h.update(chunk)
                secs = time.perf_counter() - t
                rec.update(sha256=h.hexdigest(), cold_read_s=round(secs, 2),
                           cold_read_mb_s=round(rec["size_mb"] / secs, 1) if secs else None)
                evict([path])
            env["models"][role] = rec
        write_json(self.dir / "env.json", env)
        # gatemoe doctor
        try:
            res = subprocess.run([sys.executable, "-m", "gatemoe", "doctor"], capture_output=True, text=True,
                                 timeout=600, cwd=str(ROOT))
            (self.dir / "doctor.txt").write_text(res.stdout + res.stderr, encoding="utf-8")
        except (OSError, subprocess.SubprocessError) as exc:
            (self.dir / "doctor.txt").write_text(f"doctor failed: {exc}", encoding="utf-8")
        # llama-bench: raw prefill / decode speed of the generator on this CPU (and Clef prefill if it runs)
        bench = cfg.path("paths.llama_server").with_name("llama-bench")
        out = {}
        if bench.exists():
            for role, args in (("generator", ["-p", "512", "-n", "128"]), ("router", ["-p", "1024", "-n", "0"])):
                argv = [str(bench), "-m", str(cfg.model_path(role)), "-t", str(cfg["hardware.threads"]),
                        "-r", "3", "-o", "json"] + args + (["-nr", "1"] if cpu_has_amx() else [])
                try:
                    res = subprocess.run(argv, capture_output=True, text=True, timeout=5400)
                    out[role] = {"argv": argv, "rc": res.returncode,
                                 "json": json.loads(res.stdout) if res.returncode == 0 and res.stdout.strip() else None,
                                 "stderr_tail": res.stderr[-2000:]}
                except Exception as exc:
                    out[role] = {"argv": argv, "error": str(exc)[:500]}
        else:
            out["error"] = f"llama-bench not found next to {cfg.path('paths.llama_server')}"
        write_json(self.dir / "llama_bench.json", out)
        write_json(self.result_path("env"), {"ok": True, "time": now()})


class SwapPhase(Phase):
    name = "p1_swap"
    item_timeout_s = 1800
    reps = 8
    SEQUENCE = [("router", "cold"), ("router", "warm"), ("generator", "cold"), ("generator", "warm"),
                ("router", "after_generator")]

    def items(self):
        return [{"uid": f"rep-{i:02d}", "group": "swap", "rep": i} for i in range(1, self.reps + 1)]

    def work(self, items):
        cfg = self.cfg
        for it in items:
            if self.done(it["uid"]):
                continue
            mm, ev, rows = ModelManager(cfg), EventLog(None), []
            files = {"router": cfg.model_path("router"), "generator": cfg.model_path("generator")}
            for role, cond in self.SEQUENCE:
                if cond == "cold":
                    evict(files.values())
                before = {"resident_mb": residency_mb(files[role]), **mem_snapshot()}
                s0, t = proc_stat(), time.perf_counter()
                with CgroupPeak(0.5) as cgp:
                    mm.acquire(role, ev)
                load_s = time.perf_counter() - t
                s1 = proc_stat()
                mm.unload(ev)
                un = [e for e in ev.events if e["kind"] == "model_unload"][-1]
                size_mb = round(files[role].stat().st_size / 2**20)
                rows.append({"role": role, "condition": cond, "load_s": round(load_s, 3), "size_mb": size_mb,
                             "mb_per_s": round(size_mb / load_s, 1) if load_s else None,
                             "unload_s": un.get("seconds"), "peak_rss_mb": un.get("peak_rss_mb"),
                             "before": before, **cpu_fractions(s0, s1), **cgp.result})
                print(json.dumps(rows[-1]), flush=True)
            write_json(self.result_path(it["uid"]), {"uid": it["uid"], "rep": it["rep"], "time": now(),
                                                     "cpu_model": cpu_model(), "rows": rows})


def variant_questions(cfg, variant: str) -> dict:
    base = build_questions(cfg)
    if variant == "prod":
        return base
    if variant == "gw_only":
        return {k: v for k, v in base.items() if k in GATEWAYS or k == "in_scope"}
    if variant == "verbose":
        q = dict(base)
        for g in GATEWAYS:
            q[g] = {**base[g], "instructions": VERBOSE_GATEWAYS[g]}
        q["subject"] = {**base["subject"], "instructions": "Which subject area is the learner asking about?"}
        q["level"] = {**base["level"], "instructions": "What level should the material be pitched at?"}
        q["video_engine"] = {**base["video_engine"],
                             "instructions": "If a video is made, which renderer fits the topic best?"}
        return q
    raise ValueError(variant)


class RouterPhase(Phase):
    """Router-only decisions. One model load per batch; one result file per request."""
    name = "p2_router"
    batch_size = 25
    item_timeout_s = 900
    max_attempts = 3
    repeat_n = 20

    def items(self):
        ids = list(self.reqs)
        its = [{"uid": rid, "rid": rid, "group": "main", "variant": "prod"} for rid in ids]
        rep = sorted(r for r in ids if self.reqs[r]["ablation"])[: self.repeat_n]
        its += [{"uid": f"{rid}__rep2", "rid": rid, "group": "rep2", "variant": "prod"} for rid in rep]
        return its

    def work(self, items):
        from gatemoe.langid import detect_language

        cfg = self.cfg
        model_file = items[0].get("model_file")
        if model_file:
            cfg.tree["models"]["router"]["file"] = model_file
        variant = items[0]["variant"]
        mm, ev = ModelManager(cfg), EventLog(None)
        evict(model_files(cfg))
        t = time.perf_counter()
        server = mm.acquire("router", ev)
        load_s = round(time.perf_counter() - t, 3)
        base_q = build_questions(cfg)
        timeout = float(cfg["models.router.request_timeout_s"])
        n_done = 0
        try:
            for k, it in enumerate(items):
                if self.done(it["uid"]):
                    continue
                req = self.reqs[it["rid"]]
                lang_name = cfg.language(req["lang"])["name"]
                state = build_state(req["text"], lang_name, int(cfg.get("router.max_state_chars", 600)))
                s0 = proc_stat()
                calls = []
                if variant == "single":
                    answers: dict = {}
                    for g in GATEWAYS:
                        t = time.perf_counter()
                        resp = post_json(server.base_url + "/v1/systemone", {"state": state, "questions": {g: base_q[g]}},
                                         timeout=timeout)
                        calls.append({"question": g, "latency_s": round(time.perf_counter() - t, 3),
                                      "input_tokens": (resp.get("usage") or {}).get("input_tokens")})
                        answers.update(resp.get("answers") or {})
                else:
                    q = variant_questions(cfg, variant)
                    t = time.perf_counter()
                    resp = post_json(server.base_url + "/v1/systemone", {"state": state, "questions": q}, timeout=timeout)
                    calls.append({"latency_s": round(time.perf_counter() - t, 3),
                                  "input_tokens": (resp.get("usage") or {}).get("input_tokens")})
                    answers = resp.get("answers") or {}
                dec = parse_answers(answers, cfg)
                lid = detect_language(req["text"], list(cfg.languages))
                write_json(self.result_path(it["uid"]), {
                    "uid": it["uid"], "rid": it["rid"], "variant": variant, "group": it["group"],
                    "router_file": cfg["models.router.file"], "time": now(), "call_index": n_done,
                    "batch_load_s": load_s if n_done == 0 else None,
                    "latency_s": round(sum(c["latency_s"] for c in calls), 3),
                    "input_tokens": sum(c["input_tokens"] or 0 for c in calls) or None, "calls": calls,
                    "gateways": dec.gateways, "selected": dec.selected, "subject": dec.subject,
                    "subject_probs": dec.subject_probs, "level": dec.level, "video_engine": dec.video_engine,
                    "video_engine_probs": dec.video_engine_probs, "in_scope": dec.in_scope,
                    "langid": lid, "answers": answers, **cpu_fractions(s0, proc_stat())})
                n_done += 1
                print(f"{it['uid']}: {dec.selected} in {calls[-1]['latency_s']}s", flush=True)
        finally:
            mm.unload(ev)
            un = [e for e in ev.events if e["kind"] == "model_unload"]
            write_json(self.dir / "batches" / f"{items[0]['uid']}-{int(time.time())}.json",
                       {"variant": variant, "router_file": cfg["models.router.file"], "load_s": load_s,
                        "n": n_done, "unload": un[-1] if un else None, "cpu_model": cpu_model()})


class AblationPhase(RouterPhase):
    name = "p3_ablation"
    VARIANTS = ("gw_only", "verbose", "single")

    def items(self):
        ids = sorted(r for r in self.reqs if self.reqs[r]["ablation"])
        return [{"uid": f"{v}__{rid}", "rid": rid, "group": v, "variant": v} for v in self.VARIANTS for rid in ids]


class QuantPhase(RouterPhase):
    name = "p7_quant"
    min_left_h = 3.0

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.state_path = self.dir / "quant.json"

    def model_file(self) -> str | None:
        return (read_json(self.state_path, {}) or {}).get("file")

    def items(self):
        f = self.model_file() or "(pending download)"
        ids = sorted(r for r in self.reqs if self.reqs[r]["ablation"])
        return [{"uid": f"quant__{rid}", "rid": rid, "group": "quant", "variant": "prod", "model_file": f}
                for rid in ids]

    def prepare(self) -> bool:
        st = read_json(self.state_path, {}) or {}
        models = self.cfg.path("paths.models_dir")
        if st.get("file") and (models / st["file"]).exists():
            return True
        if st.get("gave_up"):
            return False
        for q in QUANT_CANDIDATES:
            if disk_free_gb(models) < 9.0:
                write_json(self.state_path, {"gave_up": True, "reason": f"only {disk_free_gb(models)} GB free"})
                return False
            name = f"Cloudflare_clef-flash-{q}.gguf"
            dst = models / name
            res = subprocess.run(["curl", "-fL", "--retry", "3", "-C", "-", "-o", str(dst) + ".part", QUANT_URL.format(q=q)],
                                 capture_output=True, text=True)
            if res.returncode == 0:
                os.replace(str(dst) + ".part", dst)
                write_json(self.state_path, {"file": name, "quant": q, "size_mb": round(dst.stat().st_size / 2**20)})
                return True
            Path(str(dst) + ".part").unlink(missing_ok=True)
        write_json(self.state_path, {"gave_up": True, "reason": "no candidate quant could be downloaded"})
        return False

    def finish(self) -> None:
        f = self.model_file()
        pending = [it for it in self.all_items() if not self.done(it["uid"])]
        if f and not pending:
            (self.cfg.path("paths.models_dir") / f).unlink(missing_ok=True)   # give the disk back


def interleaved_e2e_order(reqs: dict[str, dict], seed: int = 7) -> list[str]:
    """Round-robin over slices (each shuffled) so a campaign cut short is still balanced."""
    rng = random.Random(seed)
    by: dict[str, list[str]] = {}
    for rid, r in reqs.items():
        if r["e2e"]:
            by.setdefault(r["slice"], []).append(rid)
    for v in by.values():
        rng.shuffle(v)
    order: list[str] = []
    slices = ["en", "hi", "kn", "hinglish", "en", "oos"]   # English twice: it is the largest slice
    while any(by.values()):
        for s in slices:
            if by.get(s):
                order.append(by[s].pop(0))
    return order


class E2EPhase(Phase):
    name = "p4_e2e"
    item_timeout_s = 4 * 3600
    batch_overhead_s = 600
    min_left_h = 6.0                  # leave room for p6_genbench at the end of the week

    def items(self):
        its = []
        for rid in interleaved_e2e_order(self.reqs):
            arms = list(ARMS) if self.reqs[rid]["in_scope"] else ["all", "clef"]
            random.Random(f"arms-{rid}").shuffle(arms)
            its += [{"uid": f"{rid}__{a}", "rid": rid, "arm": a, "rep": 1, "group": "e2e"} for a in arms]
        return its

    def work(self, items):
        for it in items:
            if not self.done(it["uid"]):
                run_lesson(self, it)


class RepeatPhase(E2EPhase):
    name = "p5_repeat"
    min_left_h = 1.5

    def items(self):
        its = []
        for rid in REPEAT_RIDS:
            for a in ("clef", "all"):
                its.append({"uid": f"{rid}__{a}__r2", "rid": rid, "arm": a, "rep": 2, "group": "repeat"})
        return its


def run_lesson(phase: Phase, it: dict) -> None:
    from gatemoe.pipeline import LessonPipeline

    cfg, req = phase.cfg, phase.reqs[it["rid"]]
    lessons = phase.dir / "lessons"
    out = lessons / it["uid"]
    if out.exists():                    # leftovers of an interrupted attempt: keep only its event log
        failed = phase.dir / "failed"
        failed.mkdir(parents=True, exist_ok=True)
        if (out / "events.jsonl").exists():
            shutil.move(str(out / "events.jsonl"), failed / f"{it['uid']}-{int(time.time())}.events.jsonl")
        shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    # gateways differ, nothing else: ALL / ORACLE take subject, level and video engine from the stored
    # Clef decision of p2 (the CLEF arm decides again - its decision should match, a determinism check)
    p2 = read_json(phase.out / "p2_router" / "items" / f"{it['rid']}.json")
    overrides = {k: p2.get(k) for k in ("subject", "level", "video_engine")} if p2 else \
        ({"subject": req["subject"][0]} if req.get("slice") == "fresh" else None)
    mode = {"all": "all", "clef": "clef", "oracle": "manual", "kb_on": "manual", "kb_off": "manual",
            "delta_on": "manual", "delta_off": "manual"}[it["arm"]]
    gateways = req["gold"] if it["arm"] in ("oracle", "delta_on", "delta_off") else \
        (["notes", "quiz"] if it["arm"].startswith("kb_") else None)
    options = {"language": req["lang"], "mode": mode, "gateways": gateways,
               "route_overrides": overrides if it["arm"] != "clef" else None}
    if it["arm"].startswith("kb_"):     # grounded vs ungrounded: the offline knowledge cache on / off
        cfg.tree["knowledge"]["enabled"] = it["arm"] == "kb_on"
    if it["arm"].startswith("delta_"):  # this month's research: KDD delta layer on / off
        cfg.tree["knowledge"]["delta"] = it["arm"] == "delta_on"
    files = model_files(cfg)
    evict(files)                        # every lesson starts with cold model files (equal for all arms)
    before = {"resident_mb": {f.name: residency_mb(f) for f in files}, **mem_snapshot()}
    pipe = LessonPipeline(cfg)
    ev = EventLog(out / "events.jsonl")
    s0, t0, started = proc_stat(), time.perf_counter(), now()
    lesson: dict = {}
    cgp = CgroupPeak()
    try:
        with cgp:
            lesson = pipe.run(req["text"], out, ev, options)
    finally:
        pipe.models.unload(ev)
        ev.close()
    wall = time.perf_counter() - t0
    cpu = cpu_fractions(s0, proc_stat())
    evs = ev.events
    llm = {}
    for e in (e for e in evs if e["kind"] == "llm_call"):
        d = llm.setdefault(e.get("task"), {"calls": 0, "seconds": 0.0, "prompt_n": 0, "cache_n": 0, "predicted_n": 0,
                                           "gen_tps": [], "finish": []})
        d["calls"] += 1
        d["seconds"] = round(d["seconds"] + (e.get("seconds") or 0), 3)
        for k in ("prompt_n", "cache_n", "predicted_n"):
            d[k] += e.get(k) or 0
        d["gen_tps"].append(e.get("gen_tps"))
        d["finish"].append(e.get("finish"))
    sys_ev = [e for e in evs if e["kind"] == "sys"]
    delivered = {g: bool(lesson.get(g)) and g not in (lesson.get("errors") or {}) for g in GATEWAYS}
    media = {"podcast_audio": bool(lesson.get("podcast_audio")), "video_render": bool(lesson.get("video_render"))}
    # keep audio/video of the first few lessons per (arm, slice) for figures; strip the rest (30 GB disk)
    kept = sum(1 for p in (phase.dir / "items").glob("*.json")
               if (r := read_json(p)) and r.get("media_kept") and r.get("arm") == it["arm"]
               and r.get("slice") == req["slice"])
    keep = kept < KEEP_MEDIA_PER_ARM_SLICE
    manifest = []
    for f in sorted(out.rglob("*")):
        if f.is_file() and f.suffix.lower() in MEDIA_SUFFIXES:
            manifest.append({"path": str(f.relative_to(out)), "bytes": f.stat().st_size})
            if not keep and f.stat().st_size > 64 * 1024:
                f.unlink()
    result = {
        "uid": it["uid"], "rid": it["rid"], "arm": it["arm"], "rep": it["rep"], "slice": req["slice"],
        "lang": req["lang"], "gold": req["gold"], "in_scope_gold": req["in_scope"], "started": started,
        "finished": now(), "wall_outer_s": round(wall, 3), "cpu_model": cpu_model(), **cpu,
        "before": before, "overrides": options["route_overrides"], "route": lesson.get("route"),
        "selected": (lesson.get("route") or {}).get("selected"), "out_of_scope": bool(lesson.get("out_of_scope")),
        "metrics": lesson.get("metrics"), "errors": lesson.get("errors"), "delivered": delivered, "media": media,
        "video_engine": lesson.get("video_engine"), "language": lesson.get("language"),
        "model_loads": [{k: e.get(k) for k in ("role", "seconds", "size_mb", "t")} for e in evs if e["kind"] == "model_load"],
        "model_unloads": [{k: e.get(k) for k in ("role", "seconds", "peak_rss_mb", "t")}
                          for e in evs if e["kind"] == "model_unload"],
        "llm": llm,
        "sys": {"min_mem_available_mb": min([e.get("mem_available_mb") or 1e9 for e in sys_ev] or [None]),
                "max_swap_used_mb": max([e.get("swap_used_mb") or 0 for e in sys_ev] or [0]),
                "max_model_rss_mb": max([e.get("model_rss_mb") or 0 for e in sys_ev] or [0]),
                "samples": len(sys_ev)},
        "stage_seconds": (lesson.get("metrics") or {}).get("stage_seconds"),
        "knowledge_enabled": bool(cfg["knowledge.enabled"]),
        "delta_enabled": bool(cfg.get("knowledge.delta", True)),
        "sources": [{k: s.get(k) for k in ("title", "zim", "lang")} for s in lesson.get("sources") or []],
        "media_manifest": manifest, "media_kept": keep, **getattr(cgp, "result", {}),
        "counts": {"flashcards": len((lesson.get("flashcards") or {}).get("cards") or []),
                   "quiz": len((lesson.get("quiz") or {}).get("questions") or []),
                   "podcast_turns": len((lesson.get("podcast") or {}).get("turns") or []),
                   "video_beats": len((lesson.get("video") or {}).get("beats") or []),
                   "sources": len(lesson.get("sources") or [])},
    }
    write_json(phase.result_path(it["uid"]), result)
    print(f"{it['uid']}: {round(wall)}s selected={result['selected']} errors={list((lesson.get('errors') or {}))}",
          flush=True)


class KnowledgePhase(E2EPhase):
    """Grounded vs ungrounded: notes + quiz with the local offline knowledge on / off (for blind rating)."""
    name = "p9_knowledge"
    item_timeout_s = 2 * 3600
    min_left_h = 8.0

    def items(self):
        its = []
        for rid in KNOWLEDGE_RIDS:
            arms = ["kb_on", "kb_off"]
            random.Random(f"kb-{rid}").shuffle(arms)
            its += [{"uid": f"{rid}__{a}", "rid": rid, "arm": a, "rep": 1, "group": "kb"} for a in arms]
        return its


class RetrievalPhase(Phase):
    """Local knowledge cache: archive catalogue, refresh (rescan) time, search latency cold vs warm."""
    name = "p10_retrieval"
    item_timeout_s = 3 * 3600

    def items(self):
        return [{"uid": "retrieval", "group": "retrieval"}]

    def work(self, items):
        from gatemoe.knowledge import KnowledgeBase

        cfg = self.cfg
        zim_dir = cfg.path("paths.zim_dir")
        zims = sorted(zim_dir.glob("*.zim")) if zim_dir.exists() else []
        evict(zims)
        t = time.perf_counter()
        kb = KnowledgeBase(cfg)
        cat = kb.catalog()
        cold_scan = time.perf_counter() - t
        t = time.perf_counter()
        KnowledgeBase(cfg).catalog()
        warm_scan = time.perf_counter() - t
        archives = [{**{k: v for k, v in c.items() if isinstance(v, (str, int, float, bool))},
                     "size_mb": round((zim_dir / c["file"]).stat().st_size / 2**20, 1)} for c in cat]
        rows = []
        for rid, r in self.reqs.items():
            if not r["in_scope"]:
                continue
            subject = r["subject"][0]
            queries = [(r["text"], r["lang"])] + ([(r["text"], "en")] if r["lang"] != "en" else [])
            for cond in ("cold", "warm"):
                if cond == "cold":
                    evict(zims)
                ev = EventLog(None)
                t = time.perf_counter()
                try:
                    ps = kb.search(queries, learner_lang=r["lang"], subject=subject, events=ev)
                    err = None
                except Exception as exc:
                    ps, err = [], f"{type(exc).__name__}: {exc}"[:300]
                k = [e for e in ev.events if e["kind"] == "kb_search"]
                rows.append({"rid": rid, "slice": r["slice"], "lang": r["lang"], "condition": cond,
                             "subject": subject, "latency_s": round(time.perf_counter() - t, 4), "passages": len(ps),
                             "learner_passages": sum(1 for p in ps if p["lang"] == r["lang"]),
                             "passage_langs": sorted({p["lang"] for p in ps}), "zims": sorted({p["zim"] for p in ps}),
                             "articles": k[-1].get("articles") if k else None,
                             "search_s": k[-1].get("search_s") if k else None, "error": err})
        write_json(self.result_path("retrieval"), {
            "time": now(), "archives": archives, "n_archives": len(archives),
            "total_archive_mb": round(sum(a["size_mb"] for a in archives), 1),
            "catalog_scan_cold_s": round(cold_scan, 3), "catalog_scan_warm_s": round(warm_scan, 3),
            "rows": rows})


class KDDPhase(Phase):
    """Knowledge updates (KDD), both layers:
    snapshot layer - mine coverage gaps from p10, check Kiwix for newer snapshots, add up to two
                     recommended archives that fit on the disk, re-measure coverage;
    delta layer    - ingest the newest arXiv papers + injected copies of snapshot text (negatives),
                     novelty-filter, build the delta ZIM, measure freshness recall and pollution of
                     old queries. Everything after this phase runs with the updated knowledge."""
    name = "p11_kdd"
    item_timeout_s = 6 * 3600
    reserve_gb = 6.0                  # keep this much disk free for results
    max_archive_gb = 2.5
    max_archives = 2
    negatives = 30
    fresh_lessons = 6

    def items(self):
        return [{"uid": "kdd", "group": "kdd"}]

    def enabled(self):
        ok = (self.out / "p10_retrieval" / "items" / "retrieval.json").exists()
        return ok, "" if ok else "needs p10_retrieval"

    def _search_all(self, kb, cold: bool = True) -> list[dict]:
        rows = []
        zim_dir = self.cfg.path("paths.zim_dir")
        for rid, r in self.reqs.items():
            if not r["in_scope"]:
                continue
            if cold:
                evict(sorted(zim_dir.glob("*.zim")))
            queries = [(r["text"], r["lang"])] + ([(r["text"], "en")] if r["lang"] != "en" else [])
            t = time.perf_counter()
            try:
                ps = kb.search(queries, learner_lang=r["lang"], subject=r["subject"][0])
            except Exception:
                ps = []
            rows.append({"rid": rid, "lang": r["lang"], "subject": r["subject"][0], "condition": "cold" if cold else "warm",
                         "latency_s": round(time.perf_counter() - t, 4), "passages": len(ps),
                         "learner_passages": sum(1 for p in ps if p["lang"] == r["lang"]),
                         "zims": sorted({p["zim"] for p in ps}),
                         "delta_passages": sum(1 for p in ps if p["zim"].startswith("gatemoe_delta")),
                         "titles": [p["title"] for p in ps]})
        return rows

    def work(self, items):
        from gatemoe.knowledge import KnowledgeBase
        from gatemoe.knowledge import kdd
        from gatemoe.knowledge.delta import DeltaKDD, fetch_arxiv, read_inbox

        cfg = self.cfg
        zim_dir = cfg.path("paths.zim_dir")
        r10 = read_json(self.out / "p10_retrieval" / "items" / "retrieval.json")
        before_rows = [x for x in r10["rows"] if x["condition"] == "cold" and not x.get("error")]
        to_rec = lambda rows: [{"lang": x["lang"], "subject": x["subject"], "passages": x["passages"],
                                "learner_passages": x.get("learner_passages", 0), "terms": []} for x in rows]
        mined_before = kdd.mine(to_rec(before_rows))
        recs = kdd.recommend(mined_before, zim_dir)
        result: dict = {"time": now(), "mined_before": mined_before, "recommendations": recs,
                        "catalog_before": sorted(p.name for p in zim_dir.glob("*.zim"))}
        # ---- snapshot layer: newer snapshots? add recommended archives that fit -------------------
        try:
            result["snapshot_check"] = kdd.check_updates(zim_dir)
        except Exception as exc:
            result["snapshot_check_error"] = f"{type(exc).__name__}: {exc}"[:300]
        installed, skipped = [], []
        for rec in recs:
            if len([i for i in installed if i.get("ok")]) >= self.max_archives:
                break
            try:
                snap = kdd.latest_snapshot(rec["archive"])
            except Exception as exc:
                skipped.append({"archive": rec["archive"], "reason": f"listing: {exc}"[:200]})
                continue
            if not snap:
                skipped.append({"archive": rec["archive"], "reason": "not on download.kiwix.org"})
                continue
            size_gb = (snap.get("size_mb") or 0) / 1024
            free = disk_free_gb(zim_dir)
            if not snap.get("size_mb") or size_gb > self.max_archive_gb or free - size_gb < self.reserve_gb:
                skipped.append({"archive": snap["file"], "reason": f"size {size_gb:.2f} GB, free {free} GB",
                                "size_mb": snap.get("size_mb")})
                continue
            installed.append({**kdd.install_snapshot(zim_dir, snap, min_free_gb=self.reserve_gb),
                              "demand": rec["demand"], "cells": rec["cells"]})
        result["installed"] = installed
        result["skipped"] = skipped
        t = time.perf_counter()
        kb = KnowledgeBase(cfg)
        kb.catalog()
        result["rescan_s"] = round(time.perf_counter() - t, 3)    # time until a new archive is searchable
        if any(i.get("ok") for i in installed):
            after_rows = self._search_all(kb)
            result["after_rows"] = after_rows
            result["mined_after"] = kdd.mine(to_rec(after_rows))
        # ---- delta layer: new research between snapshots -------------------------------------------
        delta = DeltaKDD(cfg, kb, log=print)
        t = time.perf_counter()
        papers = fetch_arxiv(list(cfg.get("kdd.arxiv_categories") or []), int(cfg.get("kdd.arxiv_per_category", 25)))
        result["arxiv_fetch_s"] = round(time.perf_counter() - t, 1)
        inbox = read_inbox(cfg.path("kdd.inbox_dir")) if cfg.get("kdd.inbox_dir") else []
        # negatives: verbatim copies of snapshot passages - the novelty filter must reject them
        negs = []
        cfg.tree["knowledge"]["delta"] = False
        for rid in sorted(r for r in self.reqs if self.reqs[r]["in_scope"] and self.reqs[r]["lang"] == "en"):
            if len(negs) >= self.negatives:
                break
            r = self.reqs[rid]
            try:
                ps = kb.search([(r["text"], "en")], learner_lang="en", subject=r["subject"][0])
            except Exception:
                ps = []
            for p in ps[:1]:
                if len(p["text"].split()) >= 60:
                    negs.append({"id": f"control-{rid}", "source": "control:snapshot-copy", "title": p["title"],
                                 "text": p["text"], "lang": "en", "license": "snapshot text (control)"})
        cfg.tree["knowledge"]["delta"] = True
        demand = {f"{c['lang']}/{c['subject']}": c["weak_share"] for c in mined_before["cells"]}
        rep = delta.ingest(papers + inbox + negs, demand=demand)
        result["ingest"] = {k: v for k, v in rep.items() if k != "decisions"}
        result["decisions"] = rep["decisions"]
        result["controls"] = len(negs)
        result["papers"] = len(papers)
        result["inbox_docs"] = len(inbox)
        # control documents must not stay in the delta: drop them from the store before building
        for lg in delta.langs():
            delta._write_store(lg, [d for d in delta.store(lg) if not str(d.get("source", "")).startswith("control:")])
        result["build"] = delta.build()
        kb = KnowledgeBase(cfg)
        kb.catalog()
        # freshness: can a learner reach this month's papers? query = paper title
        accepted = {x["id"]: x for x in rep["decisions"] if x["decision"] == "accepted"
                    and str(x.get("source", "")).startswith("arxiv")}
        store = {d["id"]: d for lg in delta.langs() for d in delta.store(lg)}
        fresh = []
        for aid, x in accepted.items():
            d = store.get(aid)
            if not d:
                continue
            row = {"id": aid, "title": d["title"], "subject": d["subject"]}
            for cond in ("delta_off", "delta_on"):
                cfg.tree["knowledge"]["delta"] = cond == "delta_on"
                t = time.perf_counter()
                try:
                    ps = kb.search([(d["title"], d["lang"])], learner_lang=d["lang"], subject=d["subject"])
                except Exception:
                    ps = []
                row[f"{cond}_hit"] = any(p["zim"].startswith("gatemoe_delta") and p["title"] == d["title"] for p in ps)
                row[f"{cond}_latency_s"] = round(time.perf_counter() - t, 4)
            fresh.append(row)
        cfg.tree["knowledge"]["delta"] = True
        result["freshness"] = fresh
        # pollution: do old (curriculum) queries now pull in delta passages?
        result["pollution_rows"] = self._search_all(kb, cold=False)
        # requests for p12_fresh_lessons: the best-scored new paper per category
        best: dict[str, dict] = {}
        for d in sorted(store.values(), key=lambda d: -d.get("score", 0)):
            if str(d.get("source", "")).startswith("arxiv") and d.get("category") not in best:
                best[d.get("category")] = d
        result["fresh_requests"] = [
            {"id": f"fresh-{i + 1:02d}", "slice": "fresh", "lang": "en", "gold": ["notes"], "explicit": True,
             "subject": [d["subject"]], "in_scope": True, "pair": None, "e2e": False, "ablation": False,
             "paper": d["id"], "paper_title": d["title"],
             "text": f"Explain the recent research \"{d['title']}\" in simple terms, with short notes."}
            for i, d in enumerate(list(best.values())[: self.fresh_lessons])]
        write_json(self.result_path("kdd"), result)


class FreshLessonsPhase(E2EPhase):
    """Lessons about this month's research with the KDD delta on / off (notes only)."""
    name = "p12_fresh"
    item_timeout_s = 2 * 3600
    min_left_h = 10.0

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        kdd_res = read_json(self.out / "p11_kdd" / "items" / "kdd.json") or {}
        self.reqs = dict(self.reqs)
        for r in kdd_res.get("fresh_requests") or []:
            self.reqs[r["id"]] = r

    def enabled(self):
        ok = any(r.startswith("fresh-") for r in self.reqs)
        return ok, "" if ok else "no fresh requests from p11_kdd"

    def items(self):
        its = []
        for rid in sorted(r for r in self.reqs if r.startswith("fresh-")):
            arms = ["delta_on", "delta_off"]
            random.Random(f"fresh-{rid}").shuffle(arms)
            its += [{"uid": f"{rid}__{a}", "rid": rid, "arm": a, "rep": 1, "group": "fresh"} for a in arms]
        return its


class ServingPhase(Phase):
    """Many connected clients, one node: a burst of requests through the real web API and job queue."""
    name = "p8_serving"
    item_timeout_s = 10 * 3600
    min_left_h = 12.0

    def items(self):
        return [{"uid": "burst-8", "group": "serving", "rids": SERVING_RIDS}]

    def work(self, items):
        import secrets

        import yaml

        spec = importlib.util.spec_from_file_location("loadgen", HERE / "loadgen.py")
        lg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lg)
        cfg = self.cfg
        for it in items:
            if self.done(it["uid"]):
                continue
            work = self.dir / "server"
            shutil.rmtree(work, ignore_errors=True)
            work.mkdir(parents=True)
            tree = json.loads(json.dumps(cfg.tree))
            tree["paths"]["jobs_dir"] = str(work / "jobs")
            tree["paths"]["cache_dir"] = str(work / "cache")
            tree["paths"]["db"] = str(work / "gatemoe.db")      # never the node's own database
            tree["router"]["memo_cache"] = False
            tree["server"]["max_pending_jobs"] = 50
            with socket.socket() as sk:
                sk.bind(("127.0.0.1", 0))
                port = sk.getsockname()[1]
            conf = work / "serve.yaml"
            conf.write_text(yaml.safe_dump(tree, allow_unicode=True), encoding="utf-8")
            password = secrets.token_hex(8)
            env = dict(os.environ, GATEMOE_CONFIG=str(conf), GATEMOE_PASSWORD=password, GATEMOE_USER="gatemoe")
            log_fh = (work / "serve.log").open("ab")
            srv = subprocess.Popen([sys.executable, "-m", "gatemoe", "serve", "--host", "127.0.0.1", "--port", str(port)],
                                   env=env, cwd=str(ROOT), stdout=log_fh, stderr=subprocess.STDOUT, start_new_session=True)
            evict(model_files(cfg))
            try:
                reqs = [self.reqs[r] for r in it["rids"]]
                res = lg.run([f"http://127.0.0.1:{port}"], reqs, user="gatemoe", password=password, mode="clef",
                             arrival="burst", timeout_h=self.item_timeout_s / 3600 - 0.5)
            finally:
                try:
                    os.killpg(srv.pid, signal.SIGTERM)
                    srv.wait(timeout=60)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    os.killpg(srv.pid, signal.SIGKILL)
                log_fh.close()
            # keep lesson.json + events of each job, drop audio/video (disk)
            for f in (work / "jobs").rglob("*"):
                if f.is_file() and f.suffix.lower() in MEDIA_SUFFIXES and f.stat().st_size > 64 * 1024:
                    f.unlink()
            res.update(uid=it["uid"], time=now(), cpu_model=cpu_model(), threads=cfg["hardware.threads"])
            write_json(self.result_path(it["uid"]), res)



class GenbenchPhase(Phase):
    name = "p6_genbench"
    item_timeout_s = 2 * 3600
    min_left_h = 0.75

    def lesson_path(self, rid: str) -> Path:
        return self.out / "p4_e2e" / "lessons" / f"{rid}__all" / "lesson.json"

    def items(self):
        its = []
        for rid in GENBENCH_RIDS:
            vs = ["baseline", "compact"]
            random.Random(f"gb-{rid}").shuffle(vs)
            its += [{"uid": f"{rid}__{v}", "rid": rid, "variant": v, "group": "genbench"} for v in vs]
        return its

    def enabled(self):
        have = [r for r in GENBENCH_RIDS if self.lesson_path(r).exists()]
        return (bool(have), "" if have else "no ALL-arm lessons from p4_e2e yet")

    def all_items(self):
        its = [it for it in super().all_items() if self.lesson_path(it["rid"]).exists()]
        return its

    def work(self, items):
        spec = importlib.util.spec_from_file_location("genbench", ROOT / "research" / "genbench" / "genbench.py")
        gb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gb)
        from gatemoe.knowledge import KnowledgeBase

        cfg = self.cfg
        table = gb.variants(None, int(cfg["hardware.threads"]))
        kb = KnowledgeBase(cfg)
        for it in items:
            if self.done(it["uid"]):
                continue
            lesson = json.loads(self.lesson_path(it["rid"]).read_text(encoding="utf-8"))
            evict(model_files(cfg))
            s0 = proc_stat()
            rec = gb.run_variant(cfg, it["variant"], table[it["variant"]], lesson, GENBENCH_TASKS.split(","), kb,
                                 cfg.path("paths.cache_dir") / "logs")
            rec.update(uid=it["uid"], rid=it["rid"], slice=self.reqs[it["rid"]]["slice"], time=now(),
                       cpu_model=cpu_model(), **cpu_fractions(s0, proc_stat()))
            write_json(self.result_path(it["uid"]), rec)


PHASES = [EnvPhase, SwapPhase, RouterPhase, RetrievalPhase, KDDPhase, AblationPhase, ServingPhase, KnowledgePhase,
          FreshLessonsPhase, E2EPhase, GenbenchPhase, RepeatPhase, QuantPhase]


# ------------------------------------------------------------------------------- orchestrator
class Stop(Exception):
    pass


_STOP = False


def _on_term(signum, frame):
    global _STOP
    _STOP = True


def run_batch(phase: Phase, batch: list[dict], out: Path) -> None:
    attempts_p = out / "state" / "attempts.json"
    attempts = read_json(attempts_p, {}) or {}
    items_file = out / "state" / "current_items.json"
    write_json(items_file, batch)
    write_json(out / "state" / "heartbeat.json", {"phase": phase.name, "uids": [b["uid"] for b in batch],
                                                  "started": now(), "ts": time.time()})
    logf = (out / "logs" / f"{phase.name}.log").open("a", encoding="utf-8")
    logf.write(f"\n==== {now()} batch {[b['uid'] for b in batch]}\n")
    logf.flush()
    timeout = phase.item_timeout_s * len(batch) + phase.batch_overhead_s
    t = time.time()
    proc = subprocess.Popen([sys.executable, "-u", str(Path(__file__).resolve()), "_work", phase.name,
                             "--items", str(items_file)] + (["--limit", str(phase.limit)] if phase.limit else []),
                            stdout=logf, stderr=subprocess.STDOUT, start_new_session=True, cwd=str(ROOT))
    reason = None
    while True:
        try:
            rc = proc.wait(timeout=5)
            break
        except subprocess.TimeoutExpired:
            if _STOP or time.time() - t > timeout:
                reason = "stopped" if _STOP else f"timeout after {timeout}s"
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                    proc.wait(timeout=60)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                rc = proc.wait()
                break
    logf.close()
    if _STOP:                          # systemd signals the whole unit: the worker may have died first
        reason = "stopped"
    missing = [b for b in batch if not phase.done(b["uid"])]
    if missing:
        kill_stray_servers(phase.cfg)   # a crashed worker can orphan a llama-server (own session)
    if reason == "stopped":
        log(out, f"{phase.name}: stopped during {[b['uid'] for b in missing]} (not counted as a failure)")
        raise Stop()
    for b in missing:
        attempts[b["uid"]] = attempts.get(b["uid"], 0) + 1
        with (out / "state" / "failures.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"time": now(), "phase": phase.name, "uid": b["uid"], "rc": rc, "reason": reason,
                                 "attempt": attempts[b["uid"]]}) + "\n")
    write_json(attempts_p, attempts)
    done_n = len(batch) - len(missing)
    log(out, f"{phase.name}: batch of {len(batch)} -> {done_n} done, {len(missing)} failed, "
             f"{round(time.time() - t)}s, rc={rc}{', ' + reason if reason else ''}")


def cmd_run(args) -> int:
    signal.signal(signal.SIGTERM, _on_term)
    cfg = cfg_load()
    out = out_root(cfg)
    (out / "state").mkdir(parents=True, exist_ok=True)
    reqs = load_requests()
    st = read_json(out / "state" / "campaign.json")
    if not st or args.reset_deadline:
        st = {"started": time.time(), "started_iso": now(), "days": args.days}
        write_json(out / "state" / "campaign.json", st)
    deadline = st["started"] + float(st.get("days", args.days)) * 86400
    log(out, f"campaign: deadline {dt.datetime.fromtimestamp(deadline).isoformat(timespec='minutes')} "
             f"(data {out}, config {cfg.source})")
    if subprocess.run(["systemctl", "is-active", "--quiet", "gatemoe"], capture_output=True).returncode == 0:
        log(out, "WARNING: the gatemoe web service is running - stop it (sudo systemctl stop gatemoe) so it "
                 "cannot load models during the measurements")
    if cfg.get("router.memo_cache"):
        log(out, "WARNING: router.memo_cache is on - CLEF arm router costs would be 0 for repeated requests")
    kill_stray_servers(cfg)
    wanted = set(args.phases.split(",")) if args.phases else None
    attempts_p = out / "state" / "attempts.json"
    try:
        for cls in PHASES:
            phase = cls(cfg, out, reqs, args.limit)
            if wanted and phase.name not in wanted:
                continue
            ok, why = phase.enabled()
            if not ok:
                log(out, f"{phase.name}: skipped ({why})")
                continue
            prepared = False
            while not _STOP:
                attempts = read_json(attempts_p, {}) or {}
                pending = [it for it in phase.all_items()
                           if not phase.done(it["uid"]) and attempts.get(it["uid"], 0) < phase.max_attempts]
                if not pending:
                    break
                left_h = (deadline - time.time()) / 3600
                if left_h < phase.min_left_h:
                    log(out, f"{phase.name}: {len(pending)} items left but only {left_h:.1f} h before the deadline "
                             f"(phase needs >= {phase.min_left_h} h) - moving on")
                    break
                if not prepared:
                    if not phase.prepare():
                        log(out, f"{phase.name}: preparation failed - skipped")
                        break
                    prepared = True
                    pending = [dict(it, **({"model_file": phase.model_file()} if hasattr(phase, "model_file") else {}))
                               for it in pending]
                free = disk_free_gb(out)
                if free < 2.0:
                    log(out, f"disk nearly full ({free} GB free) - pausing 15 min; free space to continue")
                    time.sleep(900)
                    continue
                grp = pending[0]["group"]
                batch = [it for it in pending if it["group"] == grp][: phase.batch_size]
                run_batch(phase, batch, out)
            phase.finish()
            if _STOP:
                raise Stop()
    except Stop:
        log(out, "campaign stopped (will resume where it left off)")
        return 0
    log(out, "campaign: all phases finished or out of time. Next: run.py bundle")
    write_json(out / "state" / "finished.json", {"time": now()})
    return 0


def cmd_work(args) -> int:
    cfg = cfg_load()
    out = out_root(cfg)
    reqs = load_requests()
    cls = {c.name: c for c in PHASES}[args.phase]
    phase = cls(cfg, out, reqs, args.limit)
    items = read_json(Path(args.items))
    phase.work(items)
    return 0


def cmd_status(args) -> int:
    cfg = cfg_load()
    out = out_root(cfg)
    reqs = load_requests()
    attempts = read_json(out / "state" / "attempts.json", {}) or {}
    st = read_json(out / "state" / "campaign.json")
    print(f"results: {out}")
    if st:
        deadline = st["started"] + float(st.get("days", 7)) * 86400
        print(f"started {st['started_iso']}, {(time.time() - st['started']) / 3600:.1f} h ago; "
              f"deadline in {(deadline - time.time()) / 3600:.1f} h")
    if (out / "state" / "finished.json").exists():
        print("campaign FINISHED - run: paper1 bundle")
    print(f"disk free {disk_free_gb(out)} GB; {json.dumps(mem_snapshot())}")
    hb = read_json(out / "state" / "heartbeat.json")
    if hb:
        print(f"current: {hb['phase']} {hb['uids'][:3]}{'...' if len(hb['uids']) > 3 else ''} "
              f"for {(time.time() - hb['ts']) / 60:.0f} min")
    print(f"\n{'phase':<13}{'done':>6}{'failed':>8}{'pending':>9}{'mean item':>11}{'ETA h':>8}")
    for cls in PHASES:
        ph = cls(cfg, out, reqs, None)
        its = ph.items()
        done = [it for it in its if ph.done(it["uid"])]
        failed = [it for it in its if not ph.done(it["uid"]) and attempts.get(it["uid"], 0) >= ph.max_attempts]
        pend = len(its) - len(done) - len(failed)
        durs = []
        for it in done:
            r = read_json(ph.result_path(it["uid"]), {}) or {}
            if "wall_outer_s" in r:
                durs.append(r["wall_outer_s"])
            elif "latency_s" in r:
                durs.append(r["latency_s"])
            elif "total_s" in r:
                durs.append(r["total_s"])
            elif "summary" in r:
                durs.append(r["summary"].get("makespan_s") or 0)
        mean = sum(durs) / len(durs) if durs else None
        eta = f"{pend * mean / 3600:.1f}" if mean and pend else "-"
        print(f"{ph.name:<13}{len(done):>6}{len(failed):>8}{pend:>9}{(f'{mean:.0f}s' if mean else '-'):>11}{eta:>8}")
    fails = (out / "state" / "failures.jsonl")
    if fails.exists():
        lines = fails.read_text().splitlines()[-5:]
        print("\nlast failures:")
        for line in lines:
            print("  " + line)
    lg = out / "logs" / "runner.log"
    if lg.exists():
        print("\nrunner.log (last 8 lines):")
        for line in lg.read_text().splitlines()[-8:]:
            print("  " + line)
    return 0


def cmd_analyze(args) -> int:
    spec = importlib.util.spec_from_file_location("p1analyze", HERE / "analyze.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cfg = cfg_load()
    return mod.main(["--results", str(out_root(cfg))] + (["--price-per-hour", str(args.price)] if args.price else []))


def cmd_bundle(args) -> int:
    import tarfile

    cfg = cfg_load()
    out = out_root(cfg)
    cmd_analyze(args)
    dest = Path(args.dest) if args.dest else out.parent.parent / "paper1_bundles"
    dest.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    host = socket.gethostname()

    def full_filter(ti: tarfile.TarInfo):
        p = Path(ti.name)
        if ti.isfile() and p.suffix.lower() in MEDIA_SUFFIXES:
            return None
        if ti.isfile() and p.suffix == ".log" and ti.size > 20 * 2**20:
            return None
        return ti

    def lite_filter(ti: tarfile.TarInfo):
        parts = Path(ti.name).parts
        if "lessons" in parts or "failed" in parts or ti.name.endswith(".log"):
            return None
        return full_filter(ti)

    made = []
    for kind, flt in (("full", full_filter), ("lite", lite_filter)):
        path = dest / f"paper1_results_{host}_{stamp}_{kind}.tar.xz"
        with tarfile.open(path, "w:xz") as tf:
            tf.add(out, arcname="paper1", filter=flt)
            for extra in (cfg.path("paths.cache_dir") / "logs",):
                if extra.exists() and kind == "full":
                    tf.add(extra, arcname="paper1/llama_logs",
                           filter=lambda ti: ti if not ti.isfile() or ti.size < 20 * 2**20 else None)
        os.chmod(path, 0o644)
        made.append(path)
    media = dest / f"paper1_media_{host}_{stamp}.tar"
    with tarfile.open(media, "w") as tf:
        for d in sorted((out / "p4_e2e" / "lessons").glob("*")):
            for f in d.rglob("*"):
                if f.is_file() and f.suffix.lower() in MEDIA_SUFFIXES:
                    tf.add(f, arcname=f"media/{d.name}/{f.relative_to(d)}")
    os.chmod(media, 0o644)
    made.append(media)
    print("\nbundles:")
    for p in made:
        print(f"  {p}  ({p.stat().st_size / 2**20:.1f} MB)")
    print("\nSend me the *_full.tar.xz (or *_lite.tar.xz if the upload is too big).")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("run")
    s.add_argument("--days", type=float, default=7.0)
    s.add_argument("--phases", help="comma list, e.g. p0_env,p2_router")
    s.add_argument("--limit", type=int, help="at most N items per phase (smoke tests)")
    s.add_argument("--reset-deadline", action="store_true")
    s = sub.add_parser("_work")
    s.add_argument("phase")
    s.add_argument("--items", required=True)
    s.add_argument("--limit", type=int)
    sub.add_parser("status")
    s = sub.add_parser("analyze")
    s.add_argument("--price", type=float, help="VM USD per hour (cost per lesson)")
    s = sub.add_parser("bundle")
    s.add_argument("--price", type=float)
    s.add_argument("--dest")
    args = ap.parse_args(argv)
    return {"run": cmd_run, "_work": cmd_work, "status": cmd_status, "analyze": cmd_analyze,
            "bundle": cmd_bundle}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
