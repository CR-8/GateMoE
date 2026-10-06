"""Command line: ``gatemoe serve | lesson | route | doctor | catalog | swapbench``."""
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
            lang = detect_language(text)
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

    from .server.app import create_app

    app = create_app(cfg)
    uvicorn.run(app, host=args.host or cfg["server.host"], port=args.port or int(cfg["server.port"]),
                log_level="info")
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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gatemoe", description=__doc__)
    p.add_argument("--config", help="YAML file merged over the defaults (or set GATEMOE_CONFIG)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the web app")
    s.add_argument("--host")
    s.add_argument("--port", type=int)

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

    args = p.parse_args(argv)
    cfg = load_config(args.config)
    return {"serve": cmd_serve, "lesson": cmd_lesson, "route": cmd_route, "catalog": cmd_catalog,
            "doctor": cmd_doctor, "swapbench": cmd_swapbench}[args.cmd](args, cfg)


if __name__ == "__main__":
    sys.exit(main())
