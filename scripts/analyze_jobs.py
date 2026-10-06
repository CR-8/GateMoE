#!/usr/bin/env python3
"""Turn GateMoE job logs into the research numbers: theoretical vs realised savings.

    python scripts/analyze_jobs.py /mnt/hdd/gatemoe/jobs --csv results.csv

* Runs with mode=all (every gateway) give the per-gateway cost profile.
* Runs with mode=clef give what the router actually selected and what it really cost,
  including router load + decision and every model swap.
* Theoretical saving  = cost of the gateways the router skipped (from the 'all' profile).
* Realised saving     = mean T(all) - T(clef) for comparable requests (same request text when
  both exist, otherwise the overall means).
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path

GATEWAYS = ("notes", "flashcards", "quiz", "podcast", "video", "code")
# gateway -> stages whose time belongs to it
GW_STAGES = {"notes": ["gen_notes"], "flashcards": ["gen_flashcards"], "quiz": ["gen_quiz"],
             "code": ["gen_code"], "podcast": ["gen_podcast", "tts_podcast"],
             "video": ["gen_video", "tts_narration", "render_video"]}


def load_runs(root: Path) -> list[dict]:
    runs = []
    for lesson_p in sorted(root.glob("*/lesson.json")):
        try:
            lesson = json.loads(lesson_p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        m = lesson.get("metrics") or {}
        st = m.get("stage_seconds") or {}
        route = lesson.get("route") or {}
        runs.append({
            "job": lesson_p.parent.name,
            "request": lesson.get("request", ""),
            "lang": (lesson.get("language") or {}).get("lang"),
            "mode": route.get("mode"),
            "selected": route.get("selected") or [],
            "router_s": st.get("route", 0.0),
            "router_tokens": route.get("input_tokens"),
            "router_cached": route.get("cached"),
            "wall_s": m.get("wall_seconds"),
            "model_loads": m.get("model_loads"),
            "model_load_s": m.get("model_load_seconds"),
            "peak_rss_mb": m.get("model_peak_rss_mb"),
            "gen_tokens": m.get("generated_tokens"),
            "prompt_tokens": m.get("prompt_tokens"),
            "cached_prompt_tokens": m.get("cached_prompt_tokens"),
            "max_temp_c": m.get("max_temp_c"),
            "throttled": m.get("throttled_seen"),
            "errors": ";".join(sorted((lesson.get("errors") or {}).keys())),
            **{f"gw_{g}_s": round(sum(st.get(s, 0.0) for s in GW_STAGES[g]), 3) for g in GATEWAYS},
        })
    return runs


def summarise(runs: list[dict]) -> dict:
    all_runs = [r for r in runs if r["mode"] == "all" and not r["errors"]]
    clef_runs = [r for r in runs if r["mode"] == "clef" and not r["errors"]]
    profile = {g: statistics.mean([r[f"gw_{g}_s"] for r in all_runs]) for g in GATEWAYS} if all_runs else {}
    out: dict = {"runs": len(runs), "all_runs": len(all_runs), "clef_runs": len(clef_runs),
                 "gateway_cost_profile_s": {g: round(v, 1) for g, v in profile.items()}}
    if not (all_runs and clef_runs):
        out["note"] = "need successful runs in both modes (gatemoe lesson ... --mode all / --mode clef)"
        return out
    theo, real = [], []
    by_req_all = defaultdict(list)
    for r in all_runs:
        by_req_all[r["request"]].append(r["wall_s"])
    for r in clef_runs:
        skipped = [g for g in GATEWAYS if g not in r["selected"]]
        theo.append(sum(profile[g] for g in skipped))
        base = statistics.mean(by_req_all[r["request"]]) if r["request"] in by_req_all else statistics.mean(
            x["wall_s"] for x in all_runs)
        real.append(base - r["wall_s"])
    t_mean, r_mean = statistics.mean(theo), statistics.mean(real)
    out.update({
        "mean_router_overhead_s": round(statistics.mean(r["router_s"] for r in clef_runs), 1),
        "mean_theoretical_saving_s": round(t_mean, 1),
        "mean_realised_saving_s": round(r_mean, 1),
        "realisation_ratio": round(r_mean / t_mean, 3) if t_mean else None,
        "mean_gateways_selected": round(statistics.mean(len(r["selected"]) for r in clef_runs), 2),
    })
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("jobs_dir", type=Path)
    ap.add_argument("--csv", type=Path)
    a = ap.parse_args()
    runs = load_runs(a.jobs_dir)
    if a.csv and runs:
        with a.csv.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(runs[0]))
            w.writeheader()
            for r in runs:
                w.writerow({**r, "selected": ",".join(r["selected"])})
    print(json.dumps(summarise(runs), indent=1))


if __name__ == "__main__":
    main()
