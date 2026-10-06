# Experiments: measuring theoretical vs realised savings on a Pi 5

GateMoE logs every stage, model load/unload, LLM call and system sample of every lesson to
`<jobs_dir>/<job>/events.jsonl`, and a per-lesson summary to `lesson.json → metrics`.
These are the raw data for the research questions below. Run everything on the Pi itself
(Pi-only, offline), with the active cooler fitted, and record the power with an inline
USB-C meter if you have one.

## Setup that stays fixed

* Raspberry Pi 5 8 GB, Raspberry Pi OS 64-bit, models + ZIMs on the 1 TB USB HDD (no NVMe).
* `hardware.threads: 4`, Clef-flash **Q4_0**, Qwen3.5-4B **Q4_0**, Supertonic 3 at 5 steps.
* A request set of 60–150 learner requests (technical/scientific, several languages, with a
  mix of explicit asks — "with a quiz" — and implicit ones). Keep it in a text file, one per line.
* Check for throttling: every job logs `max_temp_c` and `throttled_seen`; discard or flag
  throttled runs.

## RQ1 — What does the router cost on the Pi?

* Command: `gatemoe route "<request>" ...` (router only) and `gatemoe swapbench --roles router --repeats 5`.
* Vary: number of questions in the schema (edit `router.gateways`/`subjects`), Clef quant
  (Q4_0 vs IQ4_NL vs Q4_K_M), page cache cold (`sync; echo 3 | sudo tee /proc/sys/vm/drop_caches`) vs warm.
* Metrics: `route_decision.input_tokens`, `latency_s`, `model_load.seconds`, `model_unload.peak_rss_mb`.
* Expected shape: latency ∝ schema tokens (Clef keeps no KV cache); load time ∝ file size / disk bandwidth.

## RQ2 — Does conditional activation save time once swaps are paid?

* Run each request twice: `gatemoe lesson "<req>" --mode all` (baseline, every gateway) and
  `gatemoe lesson "<req>" --mode clef` (router decides).
* Analyse: `python scripts/analyze_jobs.py <jobs_dir> --csv runs.csv`
  * **theoretical saving** = cost of the gateways Clef skipped (per-gateway profile from the `all` runs)
  * **realised saving** = T(all) − T(clef) for the same request
  * **realisation ratio** = realised / theoretical (it can be negative when the router's own
    load + decision costs more than the gateways it skipped)
* This is the model-level analogue of "theoretical vs realised sparsity" in MoE research.

## RQ3 — Model swap cost by storage and quantisation

* `gatemoe swapbench --roles router,generator --repeats 5` with cold and warm page cache.
* Optional: copy the GGUFs to the SD card or a USB SSD and point `paths.models_dir` there.
* Report MB/s = file size / load time and the eviction effect (does loading the generator push the
  router out of the page cache? — compare the second router load time).

## RQ4 — INT4/INT8 effects on routing decisions

* Same request set, Clef-flash Q4_0 vs Q8_0 (Q8_0 is 9.7 GB → it will page from disk on 8 GB; that is
  itself a finding) — or run Q8_0/BF16 once on any bigger machine as the reference.
* Metrics: agreement of `selected`, max |Δp| per gateway, subject/level agreement.

## RQ5 — Where does the time go?

* From `lesson.json → metrics.stage_seconds`: router, generator load, plan, retrieval, each
  `gen_*`, TTS, render. Plot a stacked bar per lesson. Expect decoding (gen tokens ÷ tok/s) to
  dominate, then video rendering, then model loads.

## Wording to use in the report

* "hierarchical model routing / mixture-of-models", not "MoE"; "edge AI on a single-board computer",
  not "TinyML"; "Q4_0 4-bit block quantisation", not "INT4 pipeline".
* Never quote Cloudflare's 38.8 ms datacentre latency as a Pi number.
* Always name the baseline (`--mode all`) when reporting savings.
