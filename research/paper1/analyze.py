#!/usr/bin/env python3
"""Paper 1 analysis: turn the campaign's raw results into the paper's metrics, tables and figures.

    python research/paper1/analyze.py --results /opt/gatemoe/data/paper1 [--price-per-hour 0.067]

Writes <results>/analysis/: summary.json (every headline number), tables/*.csv, figures/*.png
(if matplotlib is installed), REPORT.md, and the blind rating sheet for the grounding study.
Works on partial results: every section reports what it has.

Metric definitions (also in docs/PAPER1_EXPERIMENT.md):
  theoretical saving  TS = sum of the ALL-arm cost of the gateways the router skipped (same request)
  realised saving     RS = T(ALL) - T(CLEF)
  realisation ratio   RR = RS / TS           (aggregate: sum RS / sum TS)
  oracle saving       OS = T(ALL) - T(ORACLE)
  routing efficiency  eta = RS / OS          (share of the perfect-routing saving the real router gets)
  router overhead     O  = route stage (router load + decision) + router unload
  swap tax            (model load + unload seconds) / wall time
  artefact recall     |gold & delivered| / |gold|;  waste = |delivered - gold| / |delivered|
"""
from __future__ import annotations

import argparse
import csv
import heapq
import html
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path

GATEWAYS = ("notes", "flashcards", "quiz", "podcast", "video", "code")
GW_STAGES = {"notes": ["gen_notes"], "flashcards": ["gen_flashcards"], "quiz": ["gen_quiz"], "code": ["gen_code"],
             "podcast": ["gen_podcast", "tts_podcast"],
             "video": ["gen_video", "check_video", "tts_narration", "render_video"]}
STAGE_GROUPS = {"router": ["route"], "plan+retrieval": ["language", "plan", "retrieve", "simulations"],
                "text generation": ["gen_notes", "gen_flashcards", "gen_quiz", "gen_code", "gen_podcast", "gen_video",
                                    "check_video"],
                "speech": ["tts_podcast", "tts_narration"], "video render": ["render_video"],
                "packaging": ["unload_generator", "concept_map", "package", "handout"]}
HERE = Path(__file__).resolve().parent


# ----------------------------------------------------------------------------------- helpers
def rj(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def items(results: Path, phase: str) -> list[dict]:
    return [r for p in sorted((results / phase / "items").glob("*.json")) if (r := rj(p)) is not None]


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 4) if xs else None


def median(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 4) if xs else None


def sd(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.stdev(xs), 4) if len(xs) > 1 else None


def pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 4)


def boot_ci(values, stat=statistics.mean, n=2000, seed=0):
    vals = [v for v in values if v is not None]
    if len(vals) < 3:
        return None
    rng = random.Random(seed)
    sims = sorted(stat([rng.choice(vals) for _ in vals]) for _ in range(n))
    return [round(sims[int(0.025 * n)], 4), round(sims[int(0.975 * n)], 4)]


def boot_ratio_ci(num, den, n=2000, seed=0):
    pairs = [(a, b) for a, b in zip(num, den) if a is not None and b is not None]
    if len(pairs) < 3:
        return None
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        s = [rng.choice(pairs) for _ in pairs]
        d = sum(b for _, b in s)
        if d:
            out.append(sum(a for a, _ in s) / d)
    out.sort()
    return [round(out[int(0.025 * len(out))], 4), round(out[int(0.975 * len(out)) - 1], 4)] if out else None


def auroc(scores, labels):
    """Probability a random positive outscores a random negative (ties count half)."""
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    if not pos or not neg:
        return None
    wins = 0.0
    for p in pos:
        for q in neg:
            wins += 1.0 if p > q else (0.5 if p == q else 0.0)
    return round(wins / (len(pos) * len(neg)), 4)


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return round(p, 4), round(r, 4), round(f, 4)


def wilcoxon(x, y):
    try:
        from scipy.stats import wilcoxon as w
        pairs = [(a, b) for a, b in zip(x, y) if a is not None and b is not None]
        if len(pairs) < 6:
            return None
        res = w([a for a, _ in pairs], [b for _, b in pairs])
        return {"statistic": float(res.statistic), "p": float(res.pvalue), "n": len(pairs)}
    except Exception:
        return None


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
                        for k, v in r.items()})


def linfit(xs, ys):
    pts = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pts) < 3:
        return None
    mx = statistics.mean(x for x, _ in pts)
    my = statistics.mean(y for _, y in pts)
    sxx = sum((x - mx) ** 2 for x, _ in pts)
    if not sxx:
        return None
    b = sum((x - mx) * (y - my) for x, y in pts) / sxx
    a = my - b * mx
    ss_res = sum((y - (a + b * x)) ** 2 for x, y in pts)
    ss_tot = sum((y - my) ** 2 for _, y in pts) or 1e-12
    return {"intercept": round(a, 4), "slope": round(b, 6), "r2": round(1 - ss_res / ss_tot, 4), "n": len(pts)}


class Figs:
    def __init__(self, root: Path):
        self.root = root
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            self.plt = plt
            root.mkdir(parents=True, exist_ok=True)
        except Exception:
            self.plt = None

    def save(self, fig, name):
        fig.tight_layout()
        fig.savefig(self.root / f"{name}.png", dpi=180)
        self.plt.close(fig)


# ------------------------------------------------------------------------------------- sections
def sec_env(res: Path) -> dict:
    env = rj(res / "p0_env" / "env.json", {}) or {}
    bench = rj(res / "p0_env" / "llama_bench.json", {}) or {}
    out = {k: env.get(k) for k in ("hostname", "cpu_model", "nproc", "threads", "amx", "gcp_machine_type", "gcp_zone",
                                   "gcp_cpu_platform", "gatemoe_rev", "llama_cpp_rev", "disk_free_gb")}
    out["mem_total_mb"] = (env.get("meminfo_mb") or {}).get("MemTotal")
    out["models"] = env.get("models")
    tps = {}
    for role, b in bench.items():
        if isinstance(b, dict) and isinstance(b.get("json"), list):
            for row in b["json"]:
                kind = "prefill" if row.get("n_prompt") else "decode"
                tps[f"{role}_{kind}_tok_s"] = round(row.get("avg_ts", 0), 2)
    out["llama_bench"] = tps
    return out


def sec_swap(res: Path, figs: Figs) -> dict:
    rows = [dict(r, rep=it["rep"]) for it in items(res, "p1_swap") for r in it.get("rows", [])]
    write_csv(res / "analysis" / "tables" / "swap_rows.csv", rows)
    out = {}
    for role in ("router", "generator"):
        for cond in ("cold", "warm", "after_generator"):
            rs = [r for r in rows if r["role"] == role and r["condition"] == cond]
            if not rs:
                continue
            out[f"{role}/{cond}"] = {"n": len(rs), "load_s_mean": mean([r["load_s"] for r in rs]),
                                     "load_s_sd": sd([r["load_s"] for r in rs]),
                                     "mb_per_s": mean([r["mb_per_s"] for r in rs]),
                                     "unload_s_mean": mean([r.get("unload_s") for r in rs]),
                                     "peak_rss_mb": mean([r.get("peak_rss_mb") for r in rs]),
                                     "resident_before_mb": mean([(r.get("before") or {}).get("resident_mb") for r in rs]),
                                     "size_mb": rs[0].get("size_mb")}
    if figs.plt and out:
        fig, ax = figs.plt.subplots(figsize=(5, 3))
        keys = list(out)
        ax.bar(range(len(keys)), [out[k]["load_s_mean"] for k in keys],
               yerr=[out[k]["load_s_sd"] or 0 for k in keys], capsize=3)
        ax.set_xticks(range(len(keys)), [k.replace("/", "\n") for k in keys], fontsize=7)
        ax.set_ylabel("load time (s)")
        ax.set_title("Model swap cost (8 GB VM)")
        figs.save(fig, "swap_load_times")
    return out


def router_scores(rows: list[dict], reqs: dict, threshold: float = 0.5) -> dict:
    ins = [r for r in rows if reqs[r["rid"]]["in_scope"]]
    per = {}
    tot = [0, 0, 0]
    f1s = []
    for g in GATEWAYS:
        tp = sum(1 for r in ins if r["gateways"].get(g, 0) >= threshold and g in reqs[r["rid"]]["gold"])
        fp = sum(1 for r in ins if r["gateways"].get(g, 0) >= threshold and g not in reqs[r["rid"]]["gold"])
        fn = sum(1 for r in ins if r["gateways"].get(g, 0) < threshold and g in reqs[r["rid"]]["gold"])
        p, rc, f = prf(tp, fp, fn)
        ys = [g in reqs[r["rid"]]["gold"] for r in ins]
        ps = [r["gateways"].get(g, 0) for r in ins]
        per[g] = {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": rc, "f1": f, "support": sum(ys),
                  "auroc": auroc(ps, ys),
                  "brier": round(statistics.mean((p_ - y) ** 2 for p_, y in zip(ps, ys)), 4) if ps else None}
        tot = [tot[0] + tp, tot[1] + fp, tot[2] + fn]
        f1s.append(f)
    sel = lambda r: {g for g in GATEWAYS if r["gateways"].get(g, 0) >= threshold} or {"notes"}
    exact = [sel(r) == set(reqs[r["rid"]]["gold"]) for r in ins]
    jac = [len(sel(r) & set(reqs[r["rid"]]["gold"])) / len(sel(r) | set(reqs[r["rid"]]["gold"])) for r in ins]
    return {"n": len(ins), "per_gateway": per, "macro_f1": round(statistics.mean(f1s), 4) if ins else None,
            "micro_f1": prf(*tot)[2], "exact_match": round(statistics.mean(exact), 4) if exact else None,
            "mean_jaccard": round(statistics.mean(jac), 4) if jac else None}


def ece(rows, reqs, bins=10):
    pairs = [(r["gateways"].get(g, 0), g in reqs[r["rid"]]["gold"]) for r in rows if reqs[r["rid"]]["in_scope"]
             for g in GATEWAYS]
    out, e = [], 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = [(p, y) for p, y in pairs if lo <= p < hi or (b == bins - 1 and p == 1.0)]
        if sel:
            conf = statistics.mean(p for p, _ in sel)
            acc = statistics.mean(1.0 if y else 0.0 for _, y in sel)
            e += len(sel) / len(pairs) * abs(conf - acc)
            out.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": len(sel), "confidence": round(conf, 4),
                        "accuracy": round(acc, 4)})
    return (round(e, 4) if pairs else None), out


def sec_router(res: Path, reqs: dict, figs: Figs, cfg_threshold=0.5, scope_min=0.2) -> tuple[dict, list[dict]]:
    allr = items(res, "p2_router")
    main = [r for r in allr if r.get("group") == "main"]
    if not main:
        return {}, []
    out: dict = {"n_requests": len(main)}
    out["overall"] = router_scores(main, reqs, cfg_threshold)
    out["by_slice"] = {s: router_scores([r for r in main if reqs[r["rid"]]["slice"] == s], reqs, cfg_threshold)
                       for s in ("en", "hi", "hinglish", "kn")}
    out["explicit"] = router_scores([r for r in main if reqs[r["rid"]]["explicit"]], reqs, cfg_threshold)
    out["implicit"] = router_scores([r for r in main if not reqs[r["rid"]]["explicit"]], reqs, cfg_threshold)
    sweep = []
    for k in range(1, 20):
        t = k / 20
        s = router_scores(main, reqs, t)
        sweep.append({"threshold": t, "macro_f1": s["macro_f1"], "micro_f1": s["micro_f1"],
                      "exact_match": s["exact_match"]})
    out["threshold_sweep"] = sweep
    best = max(sweep, key=lambda x: x["macro_f1"] or 0)
    out["best_threshold"] = best
    per_best = {}
    for g in GATEWAYS:
        cand = []
        for k in range(1, 20):
            t = k / 20
            ins = [r for r in main if reqs[r["rid"]]["in_scope"]]
            tp = sum(1 for r in ins if r["gateways"].get(g, 0) >= t and g in reqs[r["rid"]]["gold"])
            fp = sum(1 for r in ins if r["gateways"].get(g, 0) >= t and g not in reqs[r["rid"]]["gold"])
            fn = sum(1 for r in ins if r["gateways"].get(g, 0) < t and g in reqs[r["rid"]]["gold"])
            cand.append((prf(tp, fp, fn)[2], t))
        f, t = max(cand)
        per_best[g] = {"threshold": t, "f1": f}
    out["per_gateway_best_threshold"] = per_best
    e, bins = ece(main, reqs)
    out["ece"] = e
    out["reliability"] = bins
    # subject
    subj = defaultdict(list)
    for r in main:
        q = reqs[r["rid"]]
        if q["in_scope"]:
            subj[q["slice"]].append(r["subject"] in q["subject"])
    out["subject_accuracy"] = {s: round(statistics.mean(v), 4) for s, v in subj.items()}
    out["subject_accuracy"]["all"] = round(statistics.mean([x for v in subj.values() for x in v]), 4) if subj else None
    # in scope / out of scope
    sc = [(r.get("in_scope"), reqs[r["rid"]]["in_scope"], reqs[r["rid"]].get("hard_negative")) for r in main
          if r.get("in_scope") is not None]
    if sc:
        out["scope"] = {"auroc": auroc([s for s, _, _ in sc], [y for _, y, _ in sc]), "threshold": scope_min,
                        "oos_rejected": round(statistics.mean([s < scope_min for s, y, _ in sc if not y]), 4)
                        if any(not y for _, y, _ in sc) else None,
                        "hard_negatives_rejected": round(statistics.mean([s < scope_min for s, y, h in sc if h]), 4)
                        if any(h for _, _, h in sc) else None,
                        "in_scope_falsely_rejected": round(statistics.mean([s < scope_min for s, y, _ in sc if y]), 4),
                        "in_scope_p_by_slice": {sl: median([r.get("in_scope") for r in main
                                                           if reqs[r["rid"]]["slice"] == sl])
                                                for sl in ("en", "hi", "hinglish", "kn", "oos")}}
    # cross-lingual consistency over concept groups
    groups = defaultdict(list)
    for r in main:
        if reqs[r["rid"]].get("pair"):
            groups[reqs[r["rid"]]["pair"]].append(r)
    agree, jacs, dps = [], [], []
    for g, rs in groups.items():
        for i in range(len(rs)):
            for j in range(i + 1, len(rs)):
                a, b = set(rs[i]["selected"]), set(rs[j]["selected"])
                agree.append(a == b)
                jacs.append(len(a & b) / len(a | b) if a | b else 1.0)
                dps.append(max(abs(rs[i]["gateways"].get(k, 0) - rs[j]["gateways"].get(k, 0)) for k in GATEWAYS))
    out["cross_lingual"] = {"groups": len(groups), "pairs": len(agree),
                            "exact_agreement": round(statistics.mean(agree), 4) if agree else None,
                            "mean_jaccard": round(statistics.mean(jacs), 4) if jacs else None,
                            "median_max_abs_dp": median(dps)}
    # language identification (the router itself is told the dataset language)
    lid = defaultdict(list)
    for r in main:
        q = reqs[r["rid"]]
        lid[q["slice"]].append((r.get("langid") or {}).get("lang") == q["lang"])
    out["langid_accuracy"] = {s: round(statistics.mean(v), 4) for s, v in lid.items()}
    # latency
    lat = {}
    for s in ("en", "hi", "hinglish", "kn", "oos"):
        rs = [r for r in main if reqs[r["rid"]]["slice"] == s]
        if rs:
            lat[s] = {"latency_s_mean": mean([r["latency_s"] for r in rs]), "latency_s_p95": pct([r["latency_s"] for r in rs], .95),
                      "input_tokens_mean": mean([r["input_tokens"] for r in rs])}
    out["latency"] = lat
    out["latency_all"] = {"mean": mean([r["latency_s"] for r in main]), "median": median([r["latency_s"] for r in main]),
                          "p95": pct([r["latency_s"] for r in main], .95),
                          "prefill_tok_s": mean([r["input_tokens"] / r["latency_s"] for r in main
                                                 if r.get("input_tokens") and r.get("latency_s")])}
    out["steal_frac_mean"] = mean([r.get("steal_frac") for r in main])
    # determinism: repeat calls in a fresh server
    reps = {r["rid"]: r for r in allr if r.get("group") == "rep2"}
    mp = {r["rid"]: r for r in main}
    d = [max(abs(reps[k]["gateways"].get(g, 0) - mp[k]["gateways"].get(g, 0)) for g in GATEWAYS)
         for k in reps if k in mp]
    out["determinism"] = {"n": len(d), "max_abs_dp": max(d) if d else None,
                          "same_selection": round(statistics.mean([reps[k]["selected"] == mp[k]["selected"]
                                                                   for k in reps if k in mp]), 4) if d else None}
    batches = [b for p in sorted((res / "p2_router" / "batches").glob("*.json")) if (b := rj(p))]
    out["router_load_s"] = {"mean": mean([b.get("load_s") for b in batches]), "n": len(batches),
                            "peak_rss_mb": mean([(b.get("unload") or {}).get("peak_rss_mb") for b in batches])}
    rows = []
    for r in main:
        q = reqs[r["rid"]]
        rows.append({"rid": r["rid"], "slice": q["slice"], "explicit": q["explicit"], "gold": ",".join(q["gold"]),
                     "selected": ",".join(r["selected"]), **{f"p_{g}": r["gateways"].get(g) for g in GATEWAYS},
                     "in_scope_p": r.get("in_scope"), "subject": r["subject"], "subject_ok": r["subject"] in q["subject"],
                     "latency_s": r["latency_s"], "input_tokens": r["input_tokens"],
                     "langid": (r.get("langid") or {}).get("lang")})
    write_csv(res / "analysis" / "tables" / "router_decisions.csv", rows)
    if figs.plt:
        fig, ax = figs.plt.subplots(figsize=(3.4, 3.2))
        ax.plot([0, 1], [0, 1], "--", color="grey", lw=1)
        ax.plot([b["confidence"] for b in bins], [b["accuracy"] for b in bins], "o-")
        ax.set_xlabel("router P(yes)")
        ax.set_ylabel("observed frequency")
        ax.set_title(f"Calibration (ECE {e})")
        figs.save(fig, "router_reliability")
        fig, ax = figs.plt.subplots(figsize=(3.4, 3.0))
        ax.plot([s["threshold"] for s in sweep], [s["macro_f1"] for s in sweep], label="macro-F1")
        ax.plot([s["threshold"] for s in sweep], [s["exact_match"] for s in sweep], label="exact set")
        ax.axvline(cfg_threshold, color="grey", ls="--", lw=1)
        ax.set_xlabel("threshold")
        ax.legend(fontsize=7)
        figs.save(fig, "router_threshold_sweep")
    return out, main


def sec_ablation(res: Path, reqs: dict, main: list[dict], figs: Figs) -> dict:
    ab = items(res, "p3_ablation")
    variants = {"prod": [r for r in main if reqs[r["rid"]]["ablation"]]}
    for r in ab:
        variants.setdefault(r["variant"], []).append(r)
    prod_by = {r["rid"]: r for r in variants["prod"]}
    out = {}
    for v, rs in variants.items():
        if not rs:
            continue
        sc = router_scores(rs, reqs)
        same = [r["selected"] == prod_by[r["rid"]]["selected"] for r in rs if r["rid"] in prod_by]
        scope = [(r.get("in_scope"), reqs[r["rid"]]["in_scope"]) for r in rs if r.get("in_scope") is not None]
        out[v] = {"n": len(rs), "input_tokens_mean": mean([r["input_tokens"] for r in rs]),
                  "latency_s_mean": mean([r["latency_s"] for r in rs]), "macro_f1": sc["macro_f1"],
                  "exact_match": sc["exact_match"], "agreement_with_prod": round(statistics.mean(same), 4) if same else None,
                  "scope_auroc": auroc([s for s, _ in scope], [y for _, y in scope]) if scope else None}
    # latency vs prompt tokens over every single router call measured (p2 + p3)
    xs, ys = [], []
    for r in main + ab:
        for c in r.get("calls") or []:
            if c.get("input_tokens") and c.get("latency_s"):
                xs.append(c["input_tokens"])
                ys.append(c["latency_s"])
    fit = linfit(xs, ys)
    out["latency_vs_tokens"] = fit
    if fit:
        out["latency_vs_tokens"]["ms_per_token"] = round(fit["slope"] * 1000, 2)
    if figs.plt and xs:
        fig, ax = figs.plt.subplots(figsize=(3.4, 3.0))
        ax.scatter(xs, ys, s=6, alpha=.5)
        if fit:
            lo, hi = min(xs), max(xs)
            ax.plot([lo, hi], [fit["intercept"] + fit["slope"] * lo, fit["intercept"] + fit["slope"] * hi], "r-", lw=1)
        ax.set_xlabel("router prompt tokens")
        ax.set_ylabel("decision latency (s)")
        ax.set_title(f"R² = {fit['r2'] if fit else '-'}")
        figs.save(fig, "router_latency_vs_tokens")
    return out


def sec_quant(res: Path, reqs: dict, main: list[dict]) -> dict:
    q = items(res, "p7_quant")
    if not q:
        return {}
    prod = {r["rid"]: r for r in main}
    common = [r for r in q if r["rid"] in prod]
    dps = [max(abs(r["gateways"].get(g, 0) - prod[r["rid"]]["gateways"].get(g, 0)) for g in GATEWAYS) for r in common]
    batches = [b for p in sorted((res / "p7_quant" / "batches").glob("*.json")) if (b := rj(p))]
    st = rj(res / "p7_quant" / "quant.json", {}) or {}
    return {"quant": st.get("quant"), "file": st.get("file"), "size_mb": st.get("size_mb"), "n": len(common),
            "selection_agreement": round(statistics.mean([r["selected"] == prod[r["rid"]]["selected"] for r in common]), 4),
            "median_max_abs_dp": median(dps), "max_abs_dp": max(dps) if dps else None,
            "macro_f1_quant": router_scores(common, reqs)["macro_f1"],
            "macro_f1_q4_0_same_items": router_scores([prod[r["rid"]] for r in common], reqs)["macro_f1"],
            "latency_s_quant": mean([r["latency_s"] for r in common]),
            "latency_s_q4_0": mean([prod[r["rid"]]["latency_s"] for r in common]),
            "load_s": mean([b.get("load_s") for b in batches]),
            "peak_rss_mb": mean([(b.get("unload") or {}).get("peak_rss_mb") for b in batches])}


def sec_retrieval(res: Path) -> dict:
    r = rj(res / "p10_retrieval" / "items" / "retrieval.json")
    if not r:
        return {}
    rows = r["rows"]
    write_csv(res / "analysis" / "tables" / "retrieval.csv", rows)
    out = {k: r.get(k) for k in ("n_archives", "total_archive_mb", "catalog_scan_cold_s", "catalog_scan_warm_s")}
    out["archives"] = [{k: a.get(k) for k in ("name", "lang", "kind", "size_mb", "file")} for a in r.get("archives", [])]
    for lang in ("en", "hi", "kn"):
        for cond in ("cold", "warm"):
            rs = [x for x in rows if x["lang"] == lang and x["condition"] == cond and not x.get("error")]
            if rs:
                out[f"{lang}/{cond}"] = {"n": len(rs), "latency_s_median": median([x["latency_s"] for x in rs]),
                                         "latency_s_p95": pct([x["latency_s"] for x in rs], .95),
                                         "passages_mean": mean([x["passages"] for x in rs]),
                                         "with_learner_language_passage": round(statistics.mean(
                                             [lang in x["passage_langs"] for x in rs]), 4),
                                         "no_passage": round(statistics.mean([x["passages"] == 0 for x in rs]), 4)}
    return out


def sec_kdd(res: Path) -> dict:
    r = rj(res / "p11_kdd" / "items" / "kdd.json")
    if not r:
        return {}
    out: dict = {}
    # snapshot layer
    cm = lambda m: {f"{c['lang']}/{c['subject']}": c for c in (m or {}).get("cells", [])}
    b, a = cm(r.get("mined_before")), cm(r.get("mined_after"))
    cells = []
    for k, c in b.items():
        cells.append({"cell": k, "n": c["n"], "weak_before": c["weak_share"], "weak_after": (a.get(k) or {}).get("weak_share"),
                      "own_language_before": c["learner_language_share"],
                      "own_language_after": (a.get(k) or {}).get("learner_language_share"),
                      "passages_before": c["passages_mean"], "passages_after": (a.get(k) or {}).get("passages_mean")})
    write_csv(res / "analysis" / "tables" / "kdd_snapshot_cells.csv", cells)
    wsum = lambda key: (round(sum((c[key] or 0) * c["n"] for c in cells) / sum(c["n"] for c in cells), 4)
                        if cells and all(c[key] is not None for c in cells) else None)
    out["snapshot_layer"] = {
        "requests": sum(c["n"] for c in cells), "weak_cells_before": len((r.get("mined_before") or {}).get("weak_cells", [])),
        "weak_share_before": wsum("weak_before"), "weak_share_after": wsum("weak_after"),
        "own_language_share_before": wsum("own_language_before"), "own_language_share_after": wsum("own_language_after"),
        "recommendations": (r.get("recommendations") or [])[:8], "installed": r.get("installed"),
        "skipped": r.get("skipped"), "rescan_s": r.get("rescan_s"),
        "newer_snapshots_available": sum(1 for x in r.get("snapshot_check") or [] if x.get("update_available")),
        "snapshot_check": r.get("snapshot_check"), "snapshot_check_error": r.get("snapshot_check_error")}
    # delta layer
    dec = r.get("decisions") or []
    pos = [x for x in dec if str(x.get("source", "")).startswith("arxiv")]
    neg = [x for x in dec if str(x.get("source", "")).startswith("control:")]
    nov = [x["novelty"] for x in pos if x.get("novelty") is not None]
    nov_neg = [x["novelty"] for x in neg if x.get("novelty") is not None]
    write_csv(res / "analysis" / "tables" / "kdd_decisions.csv",
              [{k: v for k, v in x.items() if k != "best_snapshot_match"} for x in dec])
    fresh = r.get("freshness") or []
    pol = [x for x in r.get("pollution_rows") or []]
    out["delta_layer"] = {
        "papers_fetched": r.get("papers"), "inbox_docs": r.get("inbox_docs"), "controls": r.get("controls"),
        "arxiv_fetch_s": r.get("arxiv_fetch_s"), "ingest": r.get("ingest"),
        "papers_accepted_share": round(statistics.mean([x["decision"] == "accepted" for x in pos]), 4) if pos else None,
        "controls_rejected_share": round(statistics.mean([x["decision"] == "rejected" for x in neg]), 4) if neg else None,
        "novelty_papers_median": median(nov), "novelty_controls_median": median(nov_neg),
        "novelty_auroc": auroc(nov + nov_neg, [True] * len(nov) + [False] * len(nov_neg)) if nov and nov_neg else None,
        "novelty_s_mean": mean([x.get("novelty_s") for x in dec]),
        "subjects_accepted": dict(Counter(x.get("subject") for x in pos if x["decision"] == "accepted")),
        "build": r.get("build"),
        "freshness_recall_delta_off": round(statistics.mean([x["delta_off_hit"] for x in fresh]), 4) if fresh else None,
        "freshness_recall_delta_on": round(statistics.mean([x["delta_on_hit"] for x in fresh]), 4) if fresh else None,
        "search_latency_delta_off_s": median([x["delta_off_latency_s"] for x in fresh]),
        "search_latency_delta_on_s": median([x["delta_on_latency_s"] for x in fresh]),
        "curriculum_queries": len(pol),
        "curriculum_queries_with_delta_passage": round(statistics.mean([x.get("delta_passages", 0) > 0 for x in pol]), 4)
        if pol else None,
        "delta_share_of_curriculum_passages": round(sum(x.get("delta_passages", 0) for x in pol) /
                                                    max(1, sum(x["passages"] for x in pol)), 4) if pol else None}
    # lessons about this month's research, delta on vs off
    fl = items(res, "p12_fresh")
    if fl:
        by = defaultdict(dict)
        for x in fl:
            by[x["rid"]][x["arm"]] = x
        share = lambda x: (sum(1 for s in x.get("sources") or [] if str(s.get("zim", "")).startswith("gatemoe_delta")) /
                           max(1, len(x.get("sources") or [])))
        pairs = [v for v in by.values() if "delta_on" in v and "delta_off" in v]
        out["fresh_lessons"] = {"pairs": len(pairs),
                                "delta_source_share_on": mean([share(v["delta_on"]) for v in pairs]),
                                "delta_source_share_off": mean([share(v["delta_off"]) for v in pairs]),
                                "T_on_mean_s": mean([v["delta_on"].get("wall_outer_s") for v in pairs]),
                                "T_off_mean_s": mean([v["delta_off"].get("wall_outer_s") for v in pairs])}
    return out


def lesson_T(r: dict) -> float | None:
    return r.get("wall_outer_s") or (r.get("metrics") or {}).get("wall_seconds")


def gw_cost(r: dict, g: str) -> float:
    st = r.get("stage_seconds") or {}
    return sum(st.get(s, 0.0) for s in GW_STAGES[g])


def delivered_set(r: dict) -> set:
    return {g for g, ok in (r.get("delivered") or {}).items() if ok}


def router_overhead(r: dict) -> float:
    st = r.get("stage_seconds") or {}
    un = sum(u.get("seconds") or 0 for u in r.get("model_unloads") or [] if u.get("role") == "router")
    return st.get("route", 0.0) + un


def swap_seconds(r: dict) -> float:
    return sum(x.get("seconds") or 0 for x in r.get("model_loads") or []) + \
        sum(x.get("seconds") or 0 for x in r.get("model_unloads") or [])


def sec_e2e(res: Path, reqs: dict, figs: Figs, price: float) -> tuple[dict, dict]:
    rs = [r for r in items(res, "p4_e2e") if r.get("rep", 1) == 1]
    by = defaultdict(dict)
    for r in rs:
        by[r["rid"]][r["arm"]] = r
    rows = []
    for rid, arms in by.items():
        q = reqs[rid]
        a, c, o = arms.get("all"), arms.get("clef"), arms.get("oracle")
        row = {"rid": rid, "slice": q["slice"], "lang": q["lang"], "in_scope": q["in_scope"], "gold": q["gold"],
               "gold_n": len(q["gold"]), "explicit": q["explicit"]}
        for name, r in (("all", a), ("clef", c), ("oracle", o)):
            if r:
                row[f"T_{name}"] = round(lesson_T(r), 2)
                row[f"swap_{name}"] = round(swap_seconds(r), 2)
                row[f"errors_{name}"] = ",".join(sorted((r.get("errors") or {}).keys()))
                dv = delivered_set(r)
                gold = set(q["gold"])
                row[f"recall_{name}"] = round(len(gold & dv) / len(gold), 4) if gold else None
                row[f"waste_{name}"] = round(len(dv - gold) / len(dv), 4) if dv else 0.0
                row[f"gen_tokens_{name}"] = (r.get("metrics") or {}).get("generated_tokens")
                row[f"peak_rss_{name}"] = (r.get("metrics") or {}).get("model_peak_rss_mb")
                row[f"min_mem_avail_{name}"] = (r.get("sys") or {}).get("min_mem_available_mb")
                row[f"max_swap_used_{name}"] = (r.get("sys") or {}).get("max_swap_used_mb")
                row[f"steal_{name}"] = r.get("steal_frac")
        if c:
            row["clef_selected"] = c.get("selected")
            row["clef_out_of_scope"] = c.get("out_of_scope")
            row["router_overhead_s"] = round(router_overhead(c), 2)
            sel = set(c.get("selected") or [])
            gold = set(q["gold"])
            row["sel_recall"] = round(len(sel & gold) / len(gold), 4) if gold else None
            row["sel_precision"] = round(len(sel & gold) / len(sel), 4) if sel and gold else None
        if a:
            for g in GATEWAYS:
                row[f"c_{g}"] = round(gw_cost(a, g), 2)
            row["c_shared"] = round(lesson_T(a) - sum(gw_cost(a, g) for g in GATEWAYS), 2)
        if a and c:
            if c.get("out_of_scope"):
                ts = lesson_T(a)                             # early exit: the whole lesson is skipped
            else:
                ts = sum(gw_cost(a, g) for g in GATEWAYS if g not in set(c.get("selected") or []))
            row["TS"] = round(ts, 2)
            row["RS"] = round(lesson_T(a) - lesson_T(c), 2)
            row["RR"] = round(row["RS"] / ts, 4) if ts > 1 else None
            row["T_clef_pred"] = round(lesson_T(a) - ts + router_overhead(c), 2)
            row["skipped_video"] = "video" not in set(c.get("selected") or []) and not c.get("out_of_scope")
        if a and o:
            row["OS"] = round(lesson_T(a) - lesson_T(o), 2)
        if a and c and o and row.get("OS"):
            row["eta"] = round(row["RS"] / row["OS"], 4) if row["OS"] > 1 else None
        rows.append(row)
    write_csv(res / "analysis" / "tables" / "e2e_per_request.csv", rows)
    if not rows:
        return {}, {}
    out: dict = {"requests": len(rows), "lessons": len(rs)}

    def agg(sub: list[dict]) -> dict:
        d: dict = {"n": len(sub)}
        for arm in ("all", "clef", "oracle"):
            T = [r.get(f"T_{arm}") for r in sub if r.get(f"T_{arm}") is not None]
            if T:
                d[f"T_{arm}_mean_s"] = mean(T)
                d[f"T_{arm}_ci"] = boot_ci(T)
                d[f"swap_tax_{arm}"] = round(sum(r.get(f"swap_{arm}") or 0 for r in sub if r.get(f"T_{arm}")) / sum(T), 4)
                rec = [r.get(f"recall_{arm}") for r in sub if r.get(f"recall_{arm}") is not None]
                d[f"artefact_recall_{arm}"] = mean(rec)
                d[f"waste_{arm}"] = mean([r.get(f"waste_{arm}") for r in sub if r.get(f"T_{arm}") is not None])
                if price:
                    d[f"usd_per_lesson_{arm}"] = round(statistics.mean(T) / 3600 * price, 4)
        paired = [r for r in sub if r.get("TS") is not None]
        if paired:
            d["RS_mean_s"] = mean([r["RS"] for r in paired])
            d["RS_ci"] = boot_ci([r["RS"] for r in paired])
            d["TS_mean_s"] = mean([r["TS"] for r in paired])
            d["RR_aggregate"] = round(sum(r["RS"] for r in paired) / sum(r["TS"] for r in paired), 4) \
                if sum(r["TS"] for r in paired) else None
            d["RR_aggregate_ci"] = boot_ratio_ci([r["RS"] for r in paired], [r["TS"] for r in paired])
            d["RR_median"] = median([r["RR"] for r in paired])
            d["routing_lost_time_share"] = round(statistics.mean([r["RS"] < 0 for r in paired]), 4)
            d["router_overhead_mean_s"] = mean([r.get("router_overhead_s") for r in paired])
            d["speedup_all_over_clef"] = round(sum(r["T_all"] for r in paired) / sum(r["T_clef"] for r in paired), 4)
        po = [r for r in sub if r.get("OS") is not None and r.get("RS") is not None]
        if po:
            d["OS_mean_s"] = mean([r["OS"] for r in po])
            d["eta_aggregate"] = round(sum(r["RS"] for r in po) / sum(r["OS"] for r in po), 4) \
                if sum(r["OS"] for r in po) else None
            d["eta_aggregate_ci"] = boot_ratio_ci([r["RS"] for r in po], [r["OS"] for r in po])
        return d

    ins = [r for r in rows if r["in_scope"]]
    out["overall_in_scope"] = agg(ins)
    out["by_slice"] = {s: agg([r for r in ins if r["slice"] == s]) for s in ("en", "hi", "hinglish", "kn")}
    out["by_gold_size"] = {k: agg([r for r in ins if (r["gold_n"] if r["gold_n"] < 3 else 3) == k])
                           for k in (1, 2, 3)}
    out["by_gold_size"]["3+"] = out["by_gold_size"].pop(3)
    oos = [r for r in rows if not r["in_scope"]]
    if oos:
        out["out_of_scope"] = {"n": len(oos), "T_all_mean_s": mean([r.get("T_all") for r in oos]),
                               "T_clef_mean_s": mean([r.get("T_clef") for r in oos]),
                               "early_exit_rate": round(statistics.mean([bool(r.get("clef_out_of_scope")) for r in oos]), 4)}
    paired = [r for r in ins if r.get("TS") is not None]
    out["tests"] = {"all_vs_clef": wilcoxon([r.get("T_all") for r in paired], [r.get("T_clef") for r in paired]),
                    "clef_vs_oracle": wilcoxon([r.get("T_clef") for r in ins if r.get("T_oracle")],
                                               [r.get("T_oracle") for r in ins if r.get("T_oracle")])}
    out["cost_model"] = linfit([r.get("T_clef_pred") for r in paired], [r.get("T_clef") for r in paired])
    if paired:
        out["cost_model_mape"] = round(statistics.mean(abs(r["T_clef_pred"] - r["T_clef"]) / r["T_clef"]
                                                       for r in paired if r["T_clef"]), 4)
        out["selection_recall_mean"] = mean([r.get("sel_recall") for r in paired])
        out["selection_precision_mean"] = mean([r.get("sel_precision") for r in paired])
        sv = [r["RS"] for r in paired if r.get("skipped_video")]
        nv = [r["RS"] for r in paired if not r.get("skipped_video")]
        out["RS_when_video_skipped_mean_s"] = mean(sv)
        out["RS_when_video_kept_mean_s"] = mean(nv)
    # per-gateway cost profile (ALL arm) by language - inputs of the break-even model
    prof = {}
    for lang in ("en", "hi", "kn"):
        sub = [r for r in ins if r["lang"] == lang and r.get("c_notes") is not None]
        if sub:
            prof[lang] = {g: mean([r[f"c_{g}"] for r in sub]) for g in GATEWAYS}
            prof[lang]["shared"] = mean([r["c_shared"] for r in sub])
    out["gateway_cost_profile_s"] = prof
    ov = out["overall_in_scope"].get("router_overhead_mean_s")
    if ov and prof:
        out["break_even"] = {lang: {"router_overhead_s": ov,
                                    "gateways_whose_skip_alone_pays": sorted(g for g in GATEWAYS if p[g] > ov),
                                    "cheapest_gateway_s": min(p[g] for g in GATEWAYS)}
                             for lang, p in prof.items()}
    # stage breakdown per arm
    stages = {}
    for arm in ("all", "clef", "oracle"):
        sub = [by[r["rid"]][arm] for r in ins if arm in by[r["rid"]]]
        if sub:
            stages[arm] = {grp: mean([sum((x.get("stage_seconds") or {}).get(s, 0.0) for s in ss) for x in sub])
                           for grp, ss in STAGE_GROUPS.items()}
            stages[arm]["model swaps (outside stages)"] = mean([
                max(0.0, swap_seconds(x) - sum((x.get("stage_seconds") or {}).get(s, 0.0) for s in ("route",)))
                for x in sub])
    out["stage_breakdown_mean_s"] = stages
    # language token inflation on parallel HI / KN items (ALL arm)
    pairs = defaultdict(dict)
    for r in ins:
        if reqs[r["rid"]].get("pair") and r.get("gen_tokens_all"):
            pairs[reqs[r["rid"]]["pair"]][r["slice"]] = r
    kn_hi = [p["kn"]["gen_tokens_all"] / p["hi"]["gen_tokens_all"] for p in pairs.values() if "kn" in p and "hi" in p]
    tkn_hi = [p["kn"]["T_all"] / p["hi"]["T_all"] for p in pairs.values() if "kn" in p and "hi" in p]
    out["kannada_vs_hindi"] = {"pairs": len(kn_hi), "token_ratio_median": median(kn_hi), "time_ratio_median": median(tkn_hi)}
    out["memory"] = {"max_swap_used_mb": max([r.get(f"max_swap_used_{a}") or 0 for r in rows for a in ARMS3] or [0]),
                     "min_mem_available_mb": min([r.get(f"min_mem_avail_{a}") for r in rows for a in ARMS3
                                                  if r.get(f"min_mem_avail_{a}") is not None] or [None]),
                     "max_peak_rss_mb": max([r.get(f"peak_rss_{a}") or 0 for r in rows for a in ARMS3] or [0])}
    out["steal_frac_mean"] = mean([r.get(f"steal_{a}") for r in rows for a in ARMS3])
    out["error_rate"] = {a: round(statistics.mean([bool(r.get(f"errors_{a}")) for r in rows if r.get(f"T_{a}") is not None]), 4)
                         for a in ARMS3 if any(r.get(f"T_{a}") is not None for r in rows)}
    # figures
    if figs.plt:
        if paired:
            fig, ax = figs.plt.subplots(figsize=(3.4, 3.2))
            ax.scatter([r["TS"] / 60 for r in paired], [r["RS"] / 60 for r in paired], s=10)
            m = max(r["TS"] for r in paired) / 60
            ax.plot([0, m], [0, m], "--", color="grey", lw=1)
            ax.axhline(0, color="black", lw=.5)
            ax.set_xlabel("theoretical saving (min)")
            ax.set_ylabel("realised saving (min)")
            ax.set_title(f"RR = {out['overall_in_scope'].get('RR_aggregate')}")
            figs.save(fig, "e2e_theoretical_vs_realised")
        sl = [s for s in ("en", "hi", "hinglish", "kn") if out["by_slice"][s].get("n")]
        fig, ax = figs.plt.subplots(figsize=(4.2, 3))
        w = 0.27
        for k, arm in enumerate(("all", "clef", "oracle")):
            ax.bar([i + (k - 1) * w for i in range(len(sl))],
                   [(out["by_slice"][s].get(f"T_{arm}_mean_s") or 0) / 60 for s in sl], w, label=arm.upper())
        ax.set_xticks(range(len(sl)), sl)
        ax.set_ylabel("mean lesson time (min)")
        ax.legend(fontsize=7)
        figs.save(fig, "e2e_time_by_arm_and_language")
        if stages:
            fig, ax = figs.plt.subplots(figsize=(4.2, 3))
            arms = list(stages)
            bottom = [0.0] * len(arms)
            for grp in list(STAGE_GROUPS) + ["model swaps (outside stages)"]:
                vals = [(stages[a].get(grp) or 0) / 60 for a in arms]
                ax.bar([a.upper() for a in arms], vals, bottom=bottom, label=grp)
                bottom = [b + v for b, v in zip(bottom, vals)]
            ax.set_ylabel("mean minutes per lesson")
            ax.legend(fontsize=6)
            figs.save(fig, "e2e_stage_breakdown")
    service = {arm: [r[f"T_{arm}"] for r in rows if r.get(f"T_{arm}") is not None] for arm in ARMS3}
    return out, service


ARMS3 = ("all", "clef", "oracle")


def sec_repeat(res: Path) -> dict:
    r2 = items(res, "p5_repeat")
    r1 = {(r["rid"], r["arm"]): r for r in items(res, "p4_e2e")}
    diffs = []
    for r in r2:
        a = r1.get((r["rid"], r["arm"]))
        if a and lesson_T(a) and lesson_T(r):
            diffs.append({"rid": r["rid"], "arm": r["arm"], "T1": lesson_T(a), "T2": lesson_T(r),
                          "rel_diff": abs(lesson_T(a) - lesson_T(r)) / ((lesson_T(a) + lesson_T(r)) / 2),
                          "same_selection": a.get("selected") == r.get("selected")})
    if not diffs:
        return {}
    write_csv(res / "analysis" / "tables" / "repeatability.csv", diffs)
    return {"n": len(diffs), "median_rel_diff": median([d["rel_diff"] for d in diffs]),
            "max_rel_diff": round(max(d["rel_diff"] for d in diffs), 4),
            "same_selection": round(statistics.mean([d["same_selection"] for d in diffs]), 4)}


def sec_genbench(res: Path, reqs: dict) -> dict:
    g = items(res, "p6_genbench")
    by = defaultdict(dict)
    for r in g:
        by[r["rid"]][r["variant"]] = r
    rows = []
    for rid, v in by.items():
        if "baseline" in v and "compact" in v:
            b, c = v["baseline"], v["compact"]
            tb = sum(t["wall_s"] for t in b.get("tasks", {}).values())
            tc = sum(t["wall_s"] for t in c.get("tasks", {}).values())
            nb = sum(t["generated"] for t in b.get("tasks", {}).values())
            nc = sum(t["generated"] for t in c.get("tasks", {}).values())
            eb = sum(1 for t in b.get("tasks", {}).values() if t.get("error"))
            ec = sum(1 for t in c.get("tasks", {}).values() if t.get("error"))
            rows.append({"rid": rid, "slice": reqs[rid]["slice"], "T_baseline": round(tb, 1), "T_compact": round(tc, 1),
                         "time_saving": round(1 - tc / tb, 4) if tb else None, "tok_baseline": nb, "tok_compact": nc,
                         "token_saving": round(1 - nc / nb, 4) if nb else None, "errors_baseline": eb,
                         "errors_compact": ec,
                         "gen_tps_baseline": mean([x for t in b["tasks"].values() for x in t.get("gen_tps") or []]),
                         "gen_tps_compact": mean([x for t in c["tasks"].values() for x in t.get("gen_tps") or []])})
    write_csv(res / "analysis" / "tables" / "genbench.csv", rows)
    if not rows:
        return {}
    out = {"n_lessons": len(rows), "time_saving_aggregate": round(1 - sum(r["T_compact"] for r in rows) /
                                                                    sum(r["T_baseline"] for r in rows), 4),
           "token_saving_aggregate": round(1 - sum(r["tok_compact"] for r in rows) / sum(r["tok_baseline"] for r in rows), 4),
           "time_saving_ci": boot_ci([r["time_saving"] for r in rows]),
           "errors": {"baseline": sum(r["errors_baseline"] for r in rows), "compact": sum(r["errors_compact"] for r in rows)},
           "tests": wilcoxon([r["T_baseline"] for r in rows], [r["T_compact"] for r in rows])}
    for s in ("en", "hi", "kn"):
        sub = [r for r in rows if r["slice"] == s]
        if sub:
            out[f"time_saving_{s}"] = round(1 - sum(r["T_compact"] for r in sub) / sum(r["T_baseline"] for r in sub), 4)
    return out


def simulate(service: list[float], arrivals_per_day: float, nodes: int, window_h: float = 8.0, days: int = 30,
             seed: int = 0) -> dict:
    """M/G/c, FCFS central queue (least-loaded dispatch), Poisson arrivals within a daily lab window."""
    rng = random.Random(seed)
    waits, done_late = [], 0
    for _ in range(days):
        n = max(0, int(round(rng.gauss(arrivals_per_day, math.sqrt(max(arrivals_per_day, 1))))))
        arr = sorted(rng.uniform(0, window_h * 3600) for _ in range(n))
        free = [0.0] * nodes
        heapq.heapify(free)
        for a in arr:
            f = heapq.heappop(free)
            start = max(a, f)
            waits.append(start - a)
            fin = start + rng.choice(service)
            if fin > window_h * 3600 + 3600:
                done_late += 1
            heapq.heappush(free, fin)
    tot = max(1, len(waits))
    util = arrivals_per_day * statistics.mean(service) / (nodes * window_h * 3600)
    return {"nodes": nodes, "utilisation": round(util, 3), "wait_mean_min": round(statistics.mean(waits) / 60, 2) if waits else 0,
            "wait_p95_min": round(pct(waits, .95) / 60, 2) if waits else 0,
            "finished_over_1h_after_window": round(done_late / tot, 4)}


def sec_capacity(service: dict, res: Path, figs: Figs, serving: dict) -> dict:
    out: dict = {}
    scenarios = [50, 100, 200, 500, 1000, 2000]          # students using the lab on a day, one request each
    targets = (15, 60)                                   # minutes of queue wait (95th percentile)
    for arm in ("clef", "all", "oracle"):
        s = [x for x in service.get(arm, []) if x]
        if len(s) < 5:
            continue
        per = {"service_mean_min": round(statistics.mean(s) / 60, 2),
               "lessons_per_node_hour": round(3600 / statistics.mean(s), 3),
               "lessons_per_node_8h_day": round(8 * 3600 / statistics.mean(s), 1)}
        need = {}
        curves = {}
        for st in scenarios:
            rows = []
            for c in range(1, 400):
                sim = simulate(s, st, c)
                rows.append(sim)
                if sim["utilisation"] < 0.95 and sim["wait_p95_min"] <= min(targets):
                    break
            curves[st] = rows
            need[st] = {f"nodes_for_p95_wait_le_{t}min": next((r["nodes"] for r in rows if r["wait_p95_min"] <= t
                                                                and r["utilisation"] < 1), None) for t in targets}
        per["nodes_needed"] = need
        out[arm] = per
        if figs.plt and arm == "clef":
            fig, ax = figs.plt.subplots(figsize=(3.6, 3))
            for st, rows in curves.items():
                ax.plot([r["nodes"] for r in rows], [r["wait_p95_min"] for r in rows], label=f"{st} students/day")
            ax.set_yscale("symlog")
            ax.set_xscale("log")
            ax.set_xlabel("nodes (8 GB, 2 vCPU)")
            ax.set_ylabel("P95 queue wait (min)")
            ax.legend(fontsize=6)
            figs.save(fig, "capacity_clef")
    if "clef" in out and "all" in out:
        out["fleet_reduction_by_routing"] = {
            st: {t: (1 - out["clef"]["nodes_needed"][st][t] / out["all"]["nodes_needed"][st][t])
                 if out["clef"]["nodes_needed"][st][t] and out["all"]["nodes_needed"][st][t] else None
                 for t in out["clef"]["nodes_needed"][st]} for st in scenarios}
    # validation against the measured single-node burst: predicted finish = cumulative service times
    jobs = sorted([j for j in (serving.get("jobs") or []) if j.get("service_s")], key=lambda j: j.get("started") or 0)
    if jobs:
        t0 = min(j["created"] for j in jobs)
        pred, acc = [], 0.0
        for j in jobs:
            acc += j["service_s"]
            pred.append({"rid": j["rid"], "measured_finish_s": round(j["finished"] - t0, 1),
                         "queue_model_finish_s": round(acc, 1)})
        out["burst_validation"] = {"jobs": pred, "max_abs_error_s": round(max(abs(p["measured_finish_s"] -
                                                                                   p["queue_model_finish_s"]) for p in pred), 1)}
    # optional measured horizontal scaling (loadgen runs with several nodes, copied into <results>/scale/)
    scale = []
    for p in sorted((res / "scale").glob("*.json")) if (res / "scale").exists() else []:
        d = rj(p)
        if d and d.get("summary"):
            scale.append({"file": p.name, **{k: d["summary"].get(k) for k in ("nodes", "requests", "done", "makespan_s",
                                                                               "throughput_lessons_per_h")},
                          "wait_p95_s": (d["summary"].get("wait_s") or {}).get("p95"),
                          "api_p95_s": (d["summary"].get("api_latency_s") or {}).get("p95")})
    if scale:
        one = next((x["throughput_lessons_per_h"] for x in scale if x["nodes"] == 1), None)
        for x in scale:
            if one and x["throughput_lessons_per_h"]:
                x["scaling_efficiency"] = round(x["throughput_lessons_per_h"] / (one * x["nodes"]), 4)
        out["measured_scaling"] = scale
    return out


def sec_serving(res: Path) -> dict:
    s = items(res, "p8_serving")
    if not s:
        return {}
    d = s[0]
    return {"summary": d.get("summary"), "jobs": [{k: j.get(k) for k in ("rid", "status", "wait_s", "service_s",
                                                                        "selected", "created", "started", "finished")}
                                                  for j in d.get("jobs", [])]}


def sec_knowledge(res: Path, reqs: dict) -> dict:
    k = items(res, "p9_knowledge")
    by = defaultdict(dict)
    for r in k:
        by[r["rid"]][r["arm"]] = r
    pairs = {rid: v for rid, v in by.items() if "kb_on" in v and "kb_off" in v}
    if not pairs:
        return {}
    out = {"n_pairs": len(pairs),
           "T_kb_on_mean_s": mean([v["kb_on"]["wall_outer_s"] for v in pairs.values()]),
           "T_kb_off_mean_s": mean([v["kb_off"]["wall_outer_s"] for v in pairs.values()]),
           "prompt_tokens_kb_on": mean([(v["kb_on"].get("metrics") or {}).get("prompt_tokens") for v in pairs.values()]),
           "prompt_tokens_kb_off": mean([(v["kb_off"].get("metrics") or {}).get("prompt_tokens") for v in pairs.values()]),
           "sources_mean_kb_on": mean([(v["kb_on"].get("counts") or {}).get("sources") for v in pairs.values()]),
           "retrieve_stage_s_mean": mean([(v["kb_on"].get("stage_seconds") or {}).get("retrieve") for v in pairs.values()])}
    # blind A/B rating sheet for human raters (notes + quiz of both conditions, order randomised per request)
    rng = random.Random(42)
    key, cards = {}, []
    for rid in sorted(pairs):
        arms = ["kb_on", "kb_off"]
        rng.shuffle(arms)
        key[rid] = {"A": arms[0], "B": arms[1]}
        sides = []
        for label, arm in zip("AB", arms):
            lp = res / "p9_knowledge" / "lessons" / f"{rid}__{arm}" / "lesson.json"
            lesson = rj(lp, {}) or {}
            sides.append((label, lesson))
        cards.append((rid, reqs[rid], sides))
    (res / "analysis").mkdir(parents=True, exist_ok=True)
    (res / "analysis" / "rating_key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
    h = ["<!doctype html><meta charset=utf-8><title>GateMoE blind rating sheet</title>",
         "<style>body{font-family:system-ui,sans-serif;max-width:1100px;margin:auto;padding:16px}"
         ".pair{display:grid;grid-template-columns:1fr 1fr;gap:16px;border-top:2px solid #333;padding:12px 0}"
         ".side{background:#f6f6f6;padding:10px;border-radius:6px;font-size:14px}h3{margin:4px 0}"
         "@media(max-width:700px){.pair{grid-template-columns:1fr}}</style>",
         "<h1>Blind rating sheet</h1><p>For each request, read version A and version B (notes + quiz). "
         "Score each 1-5 for <b>factual accuracy</b>, <b>completeness</b> and <b>clarity</b>, count factual errors, "
         "and say which version you prefer. Record your scores in rating_sheet.csv. Do not try to guess how they were "
         "made.</p>"]
    csv_rows = []
    for rid, q, sides in cards:
        h.append(f"<div class=pair><div style='grid-column:1/-1'><h2>{html.escape(rid)}: "
                 f"{html.escape(q['text'])}</h2></div>")
        for label, lesson in sides:
            notes = lesson.get("notes") or {}
            quiz = lesson.get("quiz") or {}
            body = [f"<h3>Version {label}</h3><b>{html.escape(str(notes.get('title', '')))}</b>"]
            for s in notes.get("sections", []):
                body.append(f"<h4>{html.escape(str(s.get('heading', '')))}</h4><p>"
                            f"{html.escape(str(s.get('body', ''))).replace(chr(10), '<br>')}</p>")
            if notes.get("key_points"):
                body.append("<ul>" + "".join(f"<li>{html.escape(str(k))}</li>" for k in notes["key_points"]) + "</ul>")
            for i, qq in enumerate(quiz.get("questions", []), 1):
                opts = qq.get("options", [])
                ans = qq.get("answer_index")
                body.append(f"<p><b>Q{i}.</b> {html.escape(str(qq.get('question', '')))}<br>" +
                            "<br>".join(("✔ " if j == ans else "• ") + html.escape(str(o)) for j, o in enumerate(opts)) +
                            "</p>")
            h.append("<div class=side>" + "".join(body) + "</div>")
            csv_rows.append({"rater": "", "request_id": rid, "version": label, "accuracy_1to5": "",
                             "completeness_1to5": "", "clarity_1to5": "", "factual_errors": "", "preferred(A/B)": "",
                             "comment": ""})
        h.append("</div>")
    (res / "analysis" / "rating_sheet.html").write_text("\n".join(h), encoding="utf-8")
    write_csv(res / "analysis" / "rating_sheet.csv", csv_rows)
    # if filled ratings are present (analysis/ratings_filled.csv), un-blind and score them
    filled = res / "analysis" / "ratings_filled.csv"
    if filled.exists():
        rows = list(csv.DictReader(filled.open(encoding="utf-8")))
        sc = defaultdict(lambda: defaultdict(list))
        for r in rows:
            arm = key.get(r["request_id"], {}).get(r["version"])
            for m in ("accuracy_1to5", "completeness_1to5", "clarity_1to5", "factual_errors"):
                try:
                    sc[arm][m].append(float(r[m]))
                except (KeyError, ValueError):
                    pass
        out["ratings"] = {arm: {m: mean(v) for m, v in d.items()} for arm, d in sc.items()}
    return out


def report(summary: dict) -> str:
    g = lambda d, *ks: (lambda v: v)(_get(d, ks))
    e2e = summary.get("e2e") or {}
    ov = e2e.get("overall_in_scope") or {}
    ro = summary.get("router") or {}
    lines = ["# GateMoE paper-1 results (auto-generated)", "",
             f"Machine: {(summary.get('env') or {}).get('gcp_machine_type') or '?'} · "
             f"{(summary.get('env') or {}).get('cpu_model') or '?'} · {(summary.get('env') or {}).get('nproc')} vCPU · "
             f"{(summary.get('env') or {}).get('mem_total_mb')} MB RAM", "",
             "## Headline (end-to-end, in-scope requests)", "",
             "| metric | value |", "|---|---|"]
    for k, label in (("n", "requests with results"), ("T_all_mean_s", "mean T(ALL) s"), ("T_clef_mean_s", "mean T(CLEF) s"),
                     ("T_oracle_mean_s", "mean T(ORACLE) s"), ("RR_aggregate", "realisation ratio RR"),
                     ("RR_aggregate_ci", "RR 95% CI"), ("eta_aggregate", "routing efficiency η vs oracle"),
                     ("router_overhead_mean_s", "router overhead s"), ("swap_tax_clef", "swap tax (CLEF)"),
                     ("artefact_recall_clef", "artefact recall (CLEF)"), ("waste_all", "waste (ALL)"),
                     ("routing_lost_time_share", "share of requests where routing lost time")):
        lines.append(f"| {label} | {ov.get(k)} |")
    lines += ["", "## Router (Clef-flash)", "",
              f"macro-F1 {_get(ro, ('overall', 'macro_f1'))}, exact-set match {_get(ro, ('overall', 'exact_match'))}, "
              f"ECE {ro.get('ece')}, best threshold {_get(ro, ('best_threshold', 'threshold'))} "
              f"(macro-F1 {_get(ro, ('best_threshold', 'macro_f1'))}), scope AUROC {_get(ro, ('scope', 'auroc'))}, "
              f"cross-lingual agreement {_get(ro, ('cross_lingual', 'exact_agreement'))}, "
              f"mean latency {_get(ro, ('latency_all', 'mean'))} s", ""]
    for s, v in (ro.get("by_slice") or {}).items():
        lines.append(f"* {s}: macro-F1 {v.get('macro_f1')}, exact {v.get('exact_match')}, n={v.get('n')}")
    lines += ["", "Full numbers: summary.json; tables/: per-request CSVs; figures/: PNGs.", ""]
    return "\n".join(lines)


def _get(d, ks):
    for k in ks:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", required=True, type=Path)
    ap.add_argument("--price-per-hour", type=float, default=0.067,
                    help="VM on-demand USD/h for cost per lesson (default: e2-standard-2, us-central1, approximate)")
    ap.add_argument("--requests", type=Path, default=HERE / "requests.jsonl")
    a = ap.parse_args(argv)
    res = a.results
    reqs = {r["id"]: r for r in (json.loads(line) for line in a.requests.read_text(encoding="utf-8").splitlines() if line)}
    env_cfg = (rj(res / "p0_env" / "env.json", {}) or {}).get("config") or {}
    thr = float(_get(env_cfg, ("router", "threshold")) or 0.5)
    scope_min = float(_get(env_cfg, ("router", "in_scope_min")) or 0.2)
    figs = Figs(res / "analysis" / "figures")
    summary: dict = {"generated": __import__("datetime").datetime.now().isoformat(timespec="seconds")}
    summary["env"] = sec_env(res)
    summary["swap"] = sec_swap(res, figs)
    summary["router"], main_rows = sec_router(res, reqs, figs, thr, scope_min)
    summary["ablation"] = sec_ablation(res, reqs, main_rows, figs) if main_rows else {}
    summary["quant"] = sec_quant(res, reqs, main_rows) if main_rows else {}
    summary["retrieval"] = sec_retrieval(res)
    summary["kdd"] = sec_kdd(res)
    summary["e2e"], service = sec_e2e(res, reqs, figs, a.price_per_hour)
    summary["repeatability"] = sec_repeat(res)
    summary["genbench"] = sec_genbench(res, reqs)
    summary["serving"] = sec_serving(res)
    summary["capacity"] = sec_capacity(service, res, figs, (items(res, "p8_serving") or [{}])[0])
    summary["knowledge"] = sec_knowledge(res, reqs)
    out = res / "analysis"
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (out / "REPORT.md").write_text(report(summary), encoding="utf-8")
    print(f"analysis written to {out}")
    print(report(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
