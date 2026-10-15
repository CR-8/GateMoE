"""Command line: ``gatemoe serve | import | lesson | route | doctor | catalog | swapbench | kdd``."""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

from .config import load_config


def _print_event(ev: dict) -> None:
    kind = ev.get("kind")
    t = ev.get("t", 0)
    if kind == "stage_start":
        print(f"[{t:8.1f}s] ▶ {ev['stage']}", flush=True)
    elif kind == "stage_end":
        status = "✓" if ev.get("ok") else "✗"
        extra = {k: v for k, v in ev.items() if k not in ("t", "ts", "kind", "stage", "ok", "seconds")}
        print(f"[{t:8.1f}s] {status} {ev['stage']} ({ev.get('seconds', 0):.1f}s) {json.dumps(extra, ensure_ascii=False) if extra else ''}", flush=True)
    elif kind in ("model_load", "model_unload", "route_decision", "route_cache_hit"):
        data = {k: v for k, v in ev.items() if k not in ("t", "ts", "kind")}
        print(f"[{t:8.1f}s]   {kind}: {json.dumps(data, ensure_ascii=False)}", flush=True)
    elif kind == "llm_call":
        print(f"[{t:8.1f}s]   llm {ev.get('task')}: prompt {ev.get('prompt_n')} (cached {ev.get('cache_n')}), "
              f"gen {ev.get('predicted_n')} tok @ {ev.get('gen_tps')} tok/s, {ev.get('seconds')}s", flush=True)


def cmd_lesson(args, cfg) -> int:
    from .pipeline import LessonPipeline
    from .runtime.events import EventLog

    cfg.ensure_dirs()
    out = Path(args.out) if args.out else cfg.path("paths.jobs_dir") / ("cli-" + uuid.uuid4().hex[:8])
    out.mkdir(parents=True, exist_ok=True)
    ev = EventLog(out / "events.jsonl")
    q = ev.subscribe()
    import threading

    def printer():
        while True:
            e = q.get()
            if e.get("kind") == "eof":
                return
            _print_event(e)

    th = threading.Thread(target=printer, daemon=True)
    th.start()
    pipe = LessonPipeline(cfg)
    try:
        lesson = pipe.run(args.request, out, ev, {"language": args.language, "mode": args.mode,
                                                  "gateways": args.gateways.split(",") if args.gateways else None})
    finally:
        pipe.models.unload(ev)
        ev.close()
        th.join(timeout=2)
    print(f"\nLesson written to {out}/lesson.json")
    print("Selected gateways:", lesson.get("route", {}).get("selected"))
    if lesson.get("errors"):
        print("Errors:", json.dumps(lesson["errors"], ensure_ascii=False, indent=1))
    print("Metrics:", json.dumps(lesson.get("metrics"), indent=1))
    return 0


def cmd_route(args, cfg) -> int:
    from .langid import detect_language
    from .router.clef import ClefRouter
    from .runtime.events import EventLog
    from .runtime.model_manager import ModelManager

    cfg.ensure_dirs()
    models = ModelManager(cfg)
    router = ClefRouter(cfg, models)
    ev = EventLog(None)
    try:
        for text in args.requests:
            lang = detect_language(text, list(cfg.languages))
            dec = router.route(text, cfg.language(lang["lang"])["name"], ev)
            print(json.dumps({"request": text, "language": lang, **dec.to_dict()}, ensure_ascii=False, indent=1))
    finally:
        models.unload(ev)
    for e in ev.events:
        if e["kind"] == "model_load":
            print(f"(router load: {e['seconds']}s)", file=sys.stderr)
    return 0


def cmd_serve(args, cfg) -> int:
    import uvicorn

    from .jobs import JobManager
    from .server.app import create_app

    generate = bool(cfg.get("server.generate", True)) and not args.no_generate
    jobs = None
    if not generate:      # a console beside a benchmark or on a second machine: never loads a model
        from .pipeline import LessonPipeline     # built only to report what is installed

        jobs = JobManager(cfg, lambda: LessonPipeline(cfg), generate=False)
    app = create_app(cfg, jobs)
    uvicorn.run(app, host=args.host or cfg["server.host"], port=args.port or int(cfg["server.port"]),
                log_level="info", timeout_graceful_shutdown=5)   # open SSE streams must not block shutdown
    return 0


def cmd_import(args, cfg) -> int:
    """Register finished lesson folders (any folder holding a lesson.json) on the lesson board."""
    from .jobs import JobManager

    cfg.ensure_dirs()
    jobs = JobManager(cfg, lambda: None, generate=False)
    added = skipped = 0
    for root in args.dirs:
        root = Path(root)
        found = [root] if (root / "lesson.json").is_file() else sorted(p.parent for p in root.rglob("lesson.json"))
        for folder in found:
            try:
                job = jobs.register_folder(folder)
            except (OSError, ValueError) as exc:     # e.g. a lesson.json still being written: next run
                print(f"skipped {folder}: {exc}")
                skipped += 1
                continue
            if job:
                added += 1
                print(f"added   {job.id}  {folder}")
            else:
                skipped += 1
    print(f"{added} lesson(s) added, {skipped} skipped or already on the board; database: {cfg.path('paths.db')}")
    return 0


def cmd_catalog(args, cfg) -> int:
    from .knowledge import KnowledgeBase

    for z in KnowledgeBase(cfg).catalog():
        print(json.dumps(z, ensure_ascii=False))
    return 0


def cmd_doctor(args, cfg) -> int:
    from .doctor import run_doctor

    return run_doctor(cfg)


def cmd_swapbench(args, cfg) -> int:
    """Research tool: measure cold/warm load + unload of each model (model-level 'expert swap' cost)."""
    from .runtime.events import EventLog
    from .runtime.model_manager import ModelManager

    cfg.ensure_dirs()
    models = ModelManager(cfg)
    ev = EventLog(None)
    rows = []
    for rep in range(args.repeats):
        for role in args.roles.split(","):
            t = time.perf_counter()
            models.acquire(role, ev)
            load = time.perf_counter() - t
            models.unload(ev)
            unload_ev = [e for e in ev.events if e["kind"] == "model_unload"][-1]
            rows.append({"rep": rep, "role": role, "load_s": round(load, 3),
                         "peak_rss_mb": unload_ev.get("peak_rss_mb")})
            print(json.dumps(rows[-1]), flush=True)
    return 0


def cmd_kdd(args, cfg) -> int:
    """Knowledge discovery for knowledge updates: snapshot layer (Kiwix) + delta layer (new knowledge)."""
    from .knowledge import kdd

    cfg.ensure_dirs()
    if args.action == "gaps":
        return kdd.cmd_gaps(cfg, args.dirs, args.json)
    if args.action == "snapshots":
        return kdd.cmd_update(cfg, apply=args.apply, add=args.add)
    if args.action == "fields":
        return kdd.cmd_fields(cfg, count=args.count, max_gb=args.max_gb, lang=args.lang, apply=args.apply)
    from .knowledge.delta import DeltaKDD, fetch_arxiv, read_inbox

    d = DeltaKDD(cfg)
    if args.action in ("ingest", "run"):
        docs = []
        if not args.no_arxiv:
            docs += fetch_arxiv(args.categories.split(",") if args.categories else list(cfg["kdd.arxiv_categories"]),
                                int(args.per_category or cfg["kdd.arxiv_per_category"]))
        inbox = Path(args.inbox) if args.inbox else cfg.path("kdd.inbox_dir")
        docs += read_inbox(inbox)
        demand = {}
        if args.dirs or cfg.path("paths.jobs_dir").exists():    # usage-driven priority: weak language/subject cells
            mined = kdd.mine(kdd.records_from_lessons([Path(x) for x in args.dirs] or [cfg.path("paths.jobs_dir")]))
            demand = {f"{c['lang']}/{c['subject']}": c["weak_share"] for c in mined["cells"]}
        rep = d.ingest(docs, demand=demand)
        print(json.dumps({k: v for k, v in rep.items() if k != "decisions"}, indent=1, default=str))
        if args.action == "run":
            print(json.dumps(d.build(), indent=1))
        return 0
    if args.action == "build":
        print(json.dumps(d.build(), indent=1))
    elif args.action == "prune":
        print(json.dumps(d.prune(), indent=1))
        print(json.dumps(d.build(), indent=1))
    else:
        print(json.dumps(d.status(), indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gatemoe", description=__doc__)
    p.add_argument("--config", help="YAML file merged over the defaults (or set GATEMOE_CONFIG)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the web app")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.add_argument("--no-generate", action="store_true",
                   help="browse, post and grade only: never load a model (e.g. while a benchmark runs)")

    s = sub.add_parser("import", help="add finished lesson folders (with lesson.json) to the lesson board, in place")
    s.add_argument("dirs", nargs="+", help="lesson folders, or folders to search for lesson.json")

    s = sub.add_parser("lesson", help="generate one lesson from the command line")
    s.add_argument("request")
    s.add_argument("--language", default="auto")
    s.add_argument("--mode", choices=["clef", "all", "manual"])
    s.add_argument("--gateways", help="comma list for --mode manual, e.g. notes,quiz")
    s.add_argument("--out", help="output folder (default: jobs_dir/cli-xxxx)")

    s = sub.add_parser("route", help="run only the Clef router on one or more requests")
    s.add_argument("requests", nargs="+")

    sub.add_parser("catalog", help="list the offline knowledge (ZIM) collections found")
    sub.add_parser("doctor", help="check binaries, models, voices, fonts and RAM")

    s = sub.add_parser("swapbench", help="measure model load/unload (swap) cost")
    s.add_argument("--roles", default="router,generator")
    s.add_argument("--repeats", type=int, default=3)

    s = sub.add_parser("kdd", help="knowledge updates: gaps, snapshots, fields (subject archive pack), ingest/build/prune the delta, status")
    s.add_argument("action", choices=["gaps", "snapshots", "fields", "ingest", "build", "run", "prune", "status"])
    s.add_argument("dirs", nargs="*", help="gaps/ingest: folders with lesson.json files (default: jobs_dir)")
    s.add_argument("--json", action="store_true", help="gaps: machine-readable output")
    s.add_argument("--apply", action="store_true", help="snapshots/fields: download, verify and install")
    s.add_argument("--count", type=int, default=50, help="fields: how many subject archives to add (default 50)")
    s.add_argument("--max-gb", type=float, default=60.0, help="fields: disk budget for the new archives (default 60)")
    s.add_argument("--lang", default="en", help="fields: language of the archives (default en)")
    s.add_argument("--add", nargs="*", help="snapshots: archive prefixes to add, e.g. libretexts.org_en_bio")
    s.add_argument("--no-arxiv", action="store_true", help="ingest/run: inbox only")
    s.add_argument("--categories", help="ingest/run: arXiv categories, e.g. cs.RO,eess.SY")
    s.add_argument("--per-category", type=int)
    s.add_argument("--inbox", help="ingest/run: folder of .pdf/.html/.md/.txt (default kdd.inbox_dir)")

    args = p.parse_args(argv)
    cfg = load_config(args.config)
    return {"serve": cmd_serve, "import": cmd_import, "lesson": cmd_lesson, "route": cmd_route, "catalog": cmd_catalog,
            "doctor": cmd_doctor, "swapbench": cmd_swapbench,
            "kdd": cmd_kdd}[args.cmd](args, cfg)


if __name__ == "__main__":
    sys.exit(main())
