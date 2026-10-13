# Paper 1: the one-week Google Cloud campaign

**Working title:** *GateMoE: router-orchestrated, offline learning infrastructure for educational
institutes - when does model-level conditional computation pay under an 8 GB memory budget?*

Target: an IEEE conference (education technology / edge computing / applied AI track), 6 pages,
IEEEtran. Everything below runs unattended on **one 8 GB Google Cloud VM** for about a week and ends
with one command that packs the results for the write-up.

---

## 1. What the paper claims and which measurement backs each claim

GateMoE is presented as **infrastructure, not an app**: one offline server ("node") per lab or
campus serves every student computer on the LAN; an institute scales out by adding nodes; every
behaviour is an admin-controlled parameter; answers are grounded in a local knowledge cache that
is kept current by a KDD pipeline.

| claim | backed by | phase |
|---|---|---|
| A router that runs only the needed gateways saves real time on an 8 GB machine - and we know how much of the theoretical saving survives model swapping | ALL vs CLEF vs ORACLE on 64 requests: realisation ratio, routing efficiency vs oracle, swap tax | p4_e2e |
| The router's decisions are good enough, in English, Hindi, Hinglish and Kannada | per-gateway F1, calibration, threshold sweep, scope detection, cross-lingual agreement on 201 labelled requests | p2_router |
| Every parameter is under the institute's control, with known effects | router threshold sweep, prompt-schema variants, 3-bit router quant, output grammar | p2, p3, p7, p6 |
| Many connected computers can use one node | burst of 8 requests through the real web API: queue wait, throughput, API responsiveness under full CPU load | p8_serving |
| Horizontal scaling | capacity model (nodes needed for N students at a P95 wait target, with vs without routing), validated on the measured burst; optional measured 1/2/4-node test | analysis, scale_test.sh |
| Local data cache gives grounded, more accurate results | grounded vs ungrounded notes + quiz, blind human rating; retrieval latency of the local archives | p9_knowledge, p10_retrieval |
| KDD keeps knowledge current between snapshots | novelty filter (new arXiv papers vs injected copies of snapshot text), freshness recall, pollution of old queries, lessons on this month's research with the delta on/off, snapshot refresh check, usage-driven gap mining | p11_kdd, p12_fresh |

Wording to keep (as in EXPERIMENTS.md): *hierarchical model routing / mixture-of-models*, not "MoE";
*edge AI on a single-board-class machine*, not "TinyML". The cloud VM is an **8 GB x86 stand-in for
the Raspberry Pi 5's memory budget** - never quote its timings as Pi timings.

## 2. The knowledge architecture (snapshot + KDD delta)

```
                        ┌─────────────────────────── node ───────────────────────────┐
 student PCs ──LAN──►   │ web API + job queue ─► router ─► gateways ─► knowledge gateway │
 (browser)              │                                            │                │
                        │       snapshot layer: Kiwix ZIMs (Wikipedia, LibreTexts, PhET) │
                        │       delta layer:    gatemoe_delta_<lang>_<YYYY-MM>.zim       │
                        └─────────────────────────────────▲──────────────────────────────┘
                                                          │ gatemoe kdd run (when online)
   arXiv (newest papers, CC0 metadata)  ─┐                │
   inbox: staff papers, lab manuals ─────┼─► selection → cleaning/dedup → subject+language
   lesson logs (usage)  ──► gap mining ──┘   → novelty vs snapshot → ranked by demand → delta ZIM
   Kiwix (newer snapshots) ─► gatemoe kdd snapshots --apply (download → verify → atomic swap)
```

Commands (code: `gatemoe/knowledge/kdd.py`, `gatemoe/knowledge/delta.py`):

| command | what it does |
|---|---|
| `gatemoe kdd gaps [jobs_dir]` | mines lesson logs: coverage per language x subject, weak cells, recommended archives |
| `gatemoe kdd snapshots [--apply] [--add X]` | newer Kiwix snapshots of installed archives; download, verify, swap in; add recommended ones |
| `gatemoe kdd run` | ingest arXiv + inbox -> novelty filter -> store -> build the delta ZIM |
| `gatemoe kdd prune` | after a snapshot refresh: drop delta documents the new snapshot now covers |
| `gatemoe kdd status` | store size, delta and snapshot archives |

No re-indexing anywhere: every ZIM (including the delta) carries its own full-text index, and the
knowledge gateway picks a new archive up on its next search. The novelty filter is lexical
(containment of a chunk's words in the best snapshot passage) - say so in the paper.

## 3. The campaign (what runs, in order)

Estimated for a 2-vCPU / 8 GB VM (e2-standard-2). `paper1 status` shows real ETAs.

| phase | what | items | est. time |
|---|---|---|---|
| p0_env | provenance (CPU, GCP machine type, revisions, model hashes), cold disk read, llama-bench | 1 | 20 min |
| p1_swap | load/unload cost: router & generator cold / warm / router after generator, 8 repeats | 8 | 20 min |
| p2_router | Clef-flash on all 201 labelled requests + 20 repeats in a fresh server (determinism) | 221 | 4 h |
| p10_retrieval | archive catalogue, rescan time, search latency cold vs warm for 175 requests | 1 | 20 min |
| p11_kdd | gap mining + snapshot check + (disk permitting) add 2 archives; arXiv (10 categories x 25) + 30 snapshot-copy controls -> novelty filter -> delta ZIM; freshness + pollution | 1 | 1 h |
| p3_ablation | schema variants on 75 requests: gateways-only, verbose wording, one question per call | 225 | 4 h |
| p8_serving | 8 requests at once through `gatemoe serve` (real job queue), API probed every 2 s | 1 | 3 h |
| p9_knowledge | notes + quiz with local knowledge on / off, 20 requests (12 EN, 8 HI) | 40 | 5 h |
| p12_fresh | notes on 6 of this month's papers, delta on / off | 12 | 1 h |
| **p4_e2e** | **64 requests x ALL / CLEF / ORACLE (OOS: ALL / CLEF), cold model cache before each** | 188 | **~85 h** |
| p6_genbench | json_schema vs compact grammar on 12 stored lesson plans (4 tasks) | 24 | 4.5 h |
| p5_repeat | ALL + CLEF again for 10 requests (run-to-run variance) | 20 | 10 h |
| p7_quant | 3-bit Clef-flash on the 75-request subset (needs ~9 GB free disk) | 75 | 2 h |

Total ≈ 5 days; the deadline (7 days from the first start) cuts p4 at 6 h before the end so that
p6 still runs. p4's request order interleaves languages, so a cut-short p4 stays balanced.

Design choices that matter for the paper:
* **Same decision, different gateways.** ALL and ORACLE arms take subject, level and video engine
  from the stored Clef decision (p2), so the three arms differ only in which gateways run.
* **Cold model files before every lesson** (`posix_fadvise DONTNEED`): equal starting conditions for
  all arms, and realistic for an 8 GB machine, where the router and the generator cannot both stay
  in the page cache.
* **Router memo cache off** (`paper1.yaml`), so every CLEF decision is paid for.
* **Arm order shuffled per request**; CPU steal and iowait are recorded for every item (cloud noise).
* Media (audio/video) is kept for 2 lessons per arm and language, stripped from the rest (30 GB disk).

## 4. The request set (`research/paper1/requests.jsonl`)

201 requests: English 70, Hindi 40, Hinglish 25 (Latin script), Kannada 40, out of scope 26
(errands, entertainment, and 2 "hard negatives": history and a language lesson). Built by
`research/paper1/build_requests.py`.

Labelling rules: gold = artefacts asked for explicitly ("make flashcards") or by a clear cue
("help me memorise" -> flashcards, "test myself / practise" -> quiz, "something to listen to" ->
podcast, "show me visually / animate" -> video, "Python program" -> code). "Explain / what is / how
does / teach me / notes" -> notes; a request that names only other artefacts does not get notes;
"explain X with a video" = notes + video. `explicit` = every gold artefact is named. `subject` lists
the acceptable router subjects. `pair` links translations of the same intent across languages.

**Before submission:** have a native speaker proof-read the Hindi and Kannada requests (they were
written for this study) and spot-check 20 gold labels with a second annotator (report agreement).

## 5. Metrics (computed by `research/paper1/analyze.py`)

* **Theoretical saving** TS = ALL-arm cost of the gateways the router skipped (same request).
* **Realised saving** RS = T(ALL) − T(CLEF). **Realisation ratio** RR = ΣRS / ΣTS (bootstrap CI).
* **Oracle saving** OS = T(ALL) − T(ORACLE); **routing efficiency** η = ΣRS / ΣOS.
* **Router overhead** = route stage (router load + decision) + router unload.
* **Swap tax** = (model load + unload seconds) / wall time.
* **Artefact recall** = |gold ∩ delivered| / |gold|; **waste** = |delivered − gold| / |delivered|.
* **Cost model** T̂(CLEF) = T(ALL) − TS + overhead; reported R² and MAPE; break-even per gateway.
* Router: per-gateway P/R/F1, macro/micro-F1, exact-set match, AUROC, Brier, ECE, best threshold,
  scope AUROC / false rejections, cross-lingual agreement, latency vs prompt tokens (ms/token).
* Serving: queue wait P50/P95, throughput (lessons/h), API latency P50/P95/P99 under load.
* Capacity: M/G/c simulation with the measured service times -> nodes needed for 50 ... 2000
  students/day at P95 wait ≤ 15 / 60 min, with vs without routing ("fleet reduction").
* KDD: papers accepted, controls rejected, novelty AUROC, freshness recall (delta off -> on),
  pollution (share of curriculum queries that pull in a delta passage), build and rescan times.
* Grounding: blind ratings (accuracy / completeness / clarity 1-5, factual errors, preference).

## 6. Runbook

**Before you start (recommended, 2 minutes, about $2/month):** grow the VM disk from 30 to 50 GB so the
3-bit router study and the snapshot-layer additions have room. Console -> Compute Engine -> Disks ->
your VM's disk -> Edit -> Size 50 -> Save, then reboot the VM (Ubuntu on GCE grows the partition
by itself). Or in Cloud Shell: `gcloud compute disks resize <disk> --size=50GB --zone=<zone>`.

1. **SSH into the VM** (Console -> VM instances -> SSH) and run:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/CR-8/GateMoE/paper1-cloud-experiment/research/paper1/setup_vm.sh | sudo bash
   ```
   On a fresh VM this installs everything first (~30-45 min, Hindi + Kannada included), then starts
   the campaign as the service `gatemoe-paper1`. Re-running is safe. Leave the VM on all week.
2. **Optional - your own material in the KDD delta:** copy PDFs / notes / lab manuals into
   `/opt/gatemoe/data/kdd/inbox/` within the first ~4 hours (before p11_kdd starts).
3. **Once a day:** `paper1 status` (progress, ETA, disk, last failures). `paper1 log` follows the log.
4. **After the campaign** (`paper1 status` says FINISHED, or after 7 days):
   ```bash
   paper1 bundle      # runs the analysis and writes /opt/gatemoe/paper1_bundles/*.tar.xz
   ```
   Download `paper1_results_*_full.tar.xz` (SSH window -> gear icon -> Download file, path
   `/opt/gatemoe/paper1_bundles/<file>`) and send it back. If it is too large to upload, send the
   `_lite` one. The `_media` tar has a few rendered videos/podcasts for figures.
5. **Optional - measured horizontal scaling** (≈ 5 h, $2-6), from Cloud Shell, after step 4:
   ```bash
   curl -fsSLO https://raw.githubusercontent.com/CR-8/GateMoE/paper1-cloud-experiment/research/paper1/scale_test.sh
   VM=<vm-name> ZONE=<zone> bash scale_test.sh up       # clones 3 nodes, runs 8 requests on 4 / 2 / 1 nodes
   VM=<vm-name> ZONE=<zone> bash scale_test.sh status
   VM=<vm-name> ZONE=<zone> bash scale_test.sh down     # deletes the clones - do not forget
   ```
   then `paper1 bundle` again on the VM.
6. **Optional - blind rating (grounding study):** after `paper1 analyze`, open
   `/opt/gatemoe/data/paper1/analysis/rating_sheet.html`; 3-5 raters fill `rating_sheet.csv`
   (one row per request and version). Save the merged sheet as `analysis/ratings_filled.csv` and
   re-run `paper1 analyze` (it un-blinds with `rating_key.json`). Raters should not see the key.
7. Afterwards: `sudo systemctl enable --now gatemoe` turns the web app back on.

Cost: an e2-standard-2 runs about $0.07/h -> ≈ $12 for the week plus disk; the scaling test adds
$2-6. Far inside the remaining credit. **Do not resize the VM's RAM** - 8 GB is the point.

Troubleshooting: `paper1 phase-log p4_e2e` shows the current phase's output; a failed item is
retried once (router items twice) and then skipped (listed under "last failures"); `paper1 restart`
restarts the runner, which resumes where it stopped.

## 7. Status and what remains

Done (this branch):
* `research/paper1/` - request set, campaign runner, analysis, load generator, VM setup, scaling scripts
* `gatemoe/knowledge/kdd.py` + `delta.py` - KDD knowledge updates (snapshot refresh, gap mining,
  delta layer); `gatemoe kdd ...` CLI; delta archives searched next to the snapshots
* pipeline: `route_overrides` for fair arms; model unload time is logged
* tests: 55 passing (campaign smoke test and the KDD loop run against the fake llama-server / small ZIMs)

To do, in order:
1. Run the campaign (section 6) and send back the bundle.
2. Proof-read Hindi/Kannada requests; second annotator on 20 labels.
3. Blind rating of p9 (optional but strengthens the "accurate results" claim).
4. Write-up (IEEEtran, 6 pages): I. Introduction; II. Related work (LLM routing / compound AI,
   offline education servers, edge LLM serving, KDD and knowledge freshness for RAG); III. Architecture
   (node, campus deployment, two-layer knowledge, admin parameters); IV. Setup (VM, models, request
   set, arms, metrics); V. Results (router quality and cost; swap tax and realisation ratio; serving
   and capacity; grounding; KDD); VI. Discussion and limitations (x86 stand-in for the Pi, single
   seed for generation, lexical novelty, small human rating); VII. Conclusion.
5. Pick the venue and match its template and page limit.
