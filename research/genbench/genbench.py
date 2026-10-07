#!/usr/bin/env python3
"""Generator-throughput benchmark: replay GateMoE's real text-gateway requests against
llama-server variants (speculative decoding, n-gram lookup, threads, ...).

The requests are exactly what the pipeline sends: the shared system prompt with SOURCES
retrieved from the installed ZIMs for a stored lesson plan, the task prompt and its JSON
schema, the configured temperature and seed. Each variant gets a fresh server; per task we
record wall time, generated tokens, tok/s, prefill, draft acceptance, and the output (so
quality/equality can be compared across variants).

    GATEMOE_CONFIG=my.yaml python research/genbench/genbench.py \
        --lesson path/to/jobs/<id>/lesson.json --tasks notes,quiz,video \
        --variants baseline,ngram-simple,draft-0.8b --draft-model models/Qwen3.5-0.8B-Q4_K_M.gguf \
        --out research/genbench/results_x86.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from gatemoe.config import load_config  # noqa: E402
from gatemoe.gateways.text import TextGateway  # noqa: E402
from gatemoe.knowledge import KnowledgeBase  # noqa: E402
from gatemoe.llm.client import Generator  # noqa: E402
from gatemoe.router.clef import RouteDecision  # noqa: E402
from gatemoe.runtime.events import EventLog  # noqa: E402
from gatemoe.runtime.llama_server import LlamaServer  # noqa: E402
from gatemoe.runtime.model_manager import ModelManager  # noqa: E402


def variants(draft_model: str | None, threads: int) -> dict[str, tuple[list[str], dict]]:
    """name -> (extra llama-server args, Generator options)"""
    v = {
        "baseline": ([], {}),
        "compact": ([], {"compact_json": True}),                  # GBNF without layout whitespace
        "ngram-simple": (["--spec-type", "ngram-simple"], {}),
        "ngram-map-k": (["--spec-type", "ngram-map-k"], {}),
        "ngram-mod": (["--spec-type", "ngram-mod"], {}),
        "ngram-cache": (["--spec-type", "ngram-cache"], {}),
    }
    if draft_model:
        for n in (2, 4, 6):
            v[f"draft-0.8b-n{n}"] = (["--spec-type", "draft-simple", "-md", draft_model, "--spec-draft-n-max", str(n),
                                      "-td", str(threads)], {})
    return v


def build_gateway(cfg, lesson: dict, gen: Generator, kb: KnowledgeBase) -> tuple[TextGateway, list[dict]]:
    route = lesson.get("route") or {}
    dec = RouteDecision(mode="clef", selected=route.get("selected", []), subject=route.get("subject", "physics"),
                        level=route.get("level", "beginner"), video_engine=route.get("video_engine", "hyperframes"))
    code = lesson["language"]["lang"]
    tg = TextGateway(cfg, gen, code, dec, lesson["request"], tts_engine="supertonic-3")
    plan = lesson["plan"]
    queries = [(q, "en") for q in plan.get("search_queries_en", [])] + \
              [(q, code) for q in plan.get("search_queries_native", [])]
    passages = kb.search(queries or [(lesson["request"], code)], learner_lang=code, subject=dec.subject)
    tg.set_sources(passages)
    return tg, passages


def run_variant(cfg, name: str, spec: tuple[list[str], dict], lesson: dict, tasks: list[str], kb,
                log_dir: Path) -> dict:
    extra, gen_opts = spec
    mm = ModelManager(cfg)
    argv = mm.argv("generator") + extra
    srv = LlamaServer(name="generator", argv=argv, host=cfg["llama.host"], port=int(cfg["models.generator.port"]),
                      log_path=log_dir / f"genbench_{name}.log", ready_timeout_s=float(cfg["llama.ready_timeout_s"]))
    t0 = time.perf_counter()
    try:
        load_s = srv.start()
    except Exception as exc:
        return {"variant": name, "args": extra, "error": f"start: {exc}"[:600]}
    rec = {"variant": name, "args": extra, "generator_options": gen_opts, "load_s": round(load_s, 2), "tasks": {}}
    try:
        ev = EventLog(None)
        gen = Generator(srv.base_url, timeout=float(cfg["models.generator.request_timeout_s"]),
                        temperature=float(cfg["models.generator.temperature"]),
                        disable_thinking=bool(cfg["models.generator.disable_thinking"]), events=ev, **gen_opts)
        tg, passages = build_gateway(cfg, lesson, gen, kb)
        rec["passages"] = [p["title"] for p in passages]
        for task in tasks:
            n0 = len(ev.events)
            t = time.perf_counter()
            try:
                out = tg.generate(task, video_engine=lesson.get("video_engine") or "hyperframes")
                err = None
            except Exception as exc:
                out, err = None, f"{type(exc).__name__}: {exc}"[:400]
            calls = [e for e in ev.events[n0:] if e["kind"] == "llm_call"]
            gen_n = sum(c.get("predicted_n") or 0 for c in calls)
            # how many of the last call's tokens were layout whitespace: compare with the same
            # object serialised compactly, counted by the server's own tokenizer
            ws = None
            if out is not None and calls:
                try:
                    from gatemoe.runtime.http import post_json
                    compact = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
                    ntok = len(post_json(srv.base_url + "/tokenize", {"content": compact}).get("tokens", []))
                    raw = gen.last_content
                    ws = {"last_call_tokens": calls[-1].get("predicted_n"), "compact_tokens": ntok,
                          "raw_chars": len(raw), "compact_chars": len(compact),
                          "newlines": raw.count("\n")}
                except Exception as exc:
                    ws = {"error": str(exc)[:200]}
            rec["tasks"][task] = {
                "wall_s": round(time.perf_counter() - t, 2), "calls": len(calls), "generated": gen_n,
                "prompt_n": sum(c.get("prompt_n") or 0 for c in calls),
                "cached": sum(c.get("cache_n") or 0 for c in calls),
                "gen_tps": [c.get("gen_tps") for c in calls], "prompt_tps": [c.get("prompt_tps") for c in calls],
                "draft_n": sum(c.get("draft_n") or 0 for c in calls),
                "draft_accepted": sum(c.get("draft_accepted") or 0 for c in calls),
                "finish": [c.get("finish") for c in calls], "error": err, "output": out,
                "predicted": [c.get("predicted_n") for c in calls], "whitespace": ws,
            }
            print(f"  {name:>16} {task:<10} {rec['tasks'][task]['wall_s']:7.1f}s gen={gen_n:5d} "
                  f"tps={rec['tasks'][task]['gen_tps']} draft={rec['tasks'][task]['draft_accepted']}/"
                  f"{rec['tasks'][task]['draft_n']} err={err}", flush=True)
    finally:
        stats = srv.stop()
        rec["peak_rss_mb"] = stats.get("peak_rss_mb")
        rec["total_s"] = round(time.perf_counter() - t0, 2)
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lesson", required=True, help="a lesson.json with plan, route and language")
    ap.add_argument("--tasks", default="notes,quiz,video")
    ap.add_argument("--variants", default="baseline,ngram-simple")
    ap.add_argument("--draft-model", default=None)
    ap.add_argument("--max-tokens-scale", type=float, default=1.0, help="scale generation budgets (quick runs)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    cfg = load_config()
    if args.max_tokens_scale != 1.0:
        for k, v in list(cfg["generation.max_tokens"].items()):
            cfg.tree["generation"]["max_tokens"][k] = int(v * args.max_tokens_scale)
    lesson = json.loads(Path(args.lesson).read_text(encoding="utf-8"))
    table = variants(args.draft_model, int(cfg["hardware.threads"]))
    kb = KnowledgeBase(cfg)
    log_dir = cfg.path("paths.cache_dir") / "logs"
    out_path = Path(args.out)
    results = json.loads(out_path.read_text()) if out_path.exists() else {"runs": []}
    results.update({"lesson": args.lesson, "tasks": args.tasks, "threads": cfg["hardware.threads"],
                    "generator": cfg["models.generator.file"]})
    for name in args.variants.split(","):
        print(f"== {name}", flush=True)
        rec = run_variant(cfg, name, table[name], lesson, args.tasks.split(","), kb, log_dir)
        rec["time"] = time.strftime("%Y-%m-%d %H:%M:%S")
        results["runs"].append(rec)
        out_path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        if rec.get("error"):
            print("  ERROR", rec["error"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
