# Research findings (consultation, 6 October 2026)

This document records what was researched and measured before GateMoE was built: what it
found, what was corrected by an adversarial review, and how each finding shaped the design.
The raw agent reports are in [`research/results/01-consult-reports-and-critic.json`](../research/results/01-consult-reports-and-critic.json),
the orchestration scripts in [`research/workflows/`](../research/workflows/) and the Clef-flash CPU benchmark in
[`research/clefbench/`](../research/clefbench/).

Labels: **MEASURED** (we ran it), **VERIFIED** (primary source), **ESTIMATE** (reasoned, not measured on a Pi).

---

## 1. Project framing

* **What GateMoE is:** a *hierarchical model-routing* (mixture-of-models / compound AI) system. A master router
  (Clef-flash) decides which gateways run; each gateway picks a specialist (TTS engine, video renderer, ZIM collection …).
* **What it is not:** a Mixture-of-Experts in the strict sense (experts + gate trained jointly inside one network),
  and not TinyML (microcontroller-class ML). Correct wording: *edge AI on a single-board computer*, *model-level
  conditional computation*, *cascade* (not "early exit") when two models are chained.
* **Not novel on its own:** NotebookLM-style artefacts (notes, flashcards, quizzes, two-host podcasts, video overviews),
  LLM task decomposition (HuggingGPT, 2023), LLM routers (RouteLLM, Arch-Router, Clef/Jev), offline knowledge on a Pi
  (Internet-in-a-Box, Kolibri, Project NOMAD).
* **The honest contribution:** an end-to-end *characterisation* of a router-orchestrated, six-gateway learning-content
  pipeline running fully offline on **one 8 GB Raspberry Pi 5** — and the measured gap between *theoretical* savings
  (gateways skipped by the router) and *realised* savings once model loading/swapping, prefill and I/O are paid.
  See [EXPERIMENTS.md](EXPERIMENTS.md).

## 2. The router: Cloudflare Clef-flash

| Fact | Value | Label |
|---|---|---|
| Release | 1 Oct 2026, Apache-2.0, 9B, post-trained from Qwen3.5-9B; "decision model" (typed probabilities, one forward pass, no text) | VERIFIED |
| API | `POST /v1/systemone` (Jev/TypeSafe-compatible); `noul` / `choice` / `score` questions | VERIFIED |
| llama.cpp support | merged in master (`tools/server/server-decision.cpp`, commit 5e03bdd, 5 Oct 2026); no unit test for Clef yet | VERIFIED |
| GGUF sizes | ggml-org Q4_K_M 6.49 GB · Q8_0 9.66 GB · BF16 18.16 GB; bartowski **Q4_0 5.60 GB** · IQ4_NL 5.63 GB | VERIFIED |
| Prompt layout | `[system][STATE][SCHEMA][suffix]` — the schema comes **after** the request | VERIFIED (HF code + llama.cpp) |
| Caching | **none**: llama.cpp's `clef` graph has no memory module; identical repeat = same cost; `prompt_tokens_cached_total = 0` | VERIFIED + MEASURED |
| Prompt size | 10-question routing schema = **1,048 tokens**, 92 % of it schema; each default yes/no question ≈ 88 tokens | MEASURED |
| Latency, 4-core x86 (Q4_K_M) | **29 s** per decision, ≈ 36 tok/s prefill; repeat 29 s | MEASURED |
| RAM | Q4_K_M VmRSS **7.4 GiB**, Q4_0 **6.6 GiB** after a 1k-token decision; activations ≈ 1.15 MB per prompt token | MEASURED |
| **Pi 5 estimate (Q4_0, ~1k tokens)** | **≈ 1.3–2.3 min per decision**; a single question ≈ 15–25 s | ESTIMATE (Pi anchor: Qwen2.5-7B Q4_0_4_4 pp512 = 15.6 tok/s) |
| Cold load from USB HDD | 5.6 GB at 100–130 MB/s ≈ **43–65 s** (llama.cpp uses `MAP_POPULATE`) | ESTIMATE |
| Decision quality | subject 6/6 correct; gateway flags sensible 5/6 (missed "short summary" → notes) | MEASURED (Q4_K_M, 6 requests) |
| Out-of-scope detection | weak (CLINC150+OOS macro-F1 66.8 in Cloudflare's own table) | VERIFIED |

**Design consequences in GateMoE**

* Clef-flash is the *only* router (no cascade, by project decision). It is loaded, asked once, and unloaded.
* The schema is compact (bare option keys, `{"true":"yes","false":"no"}` criteria, one-line instructions).
* An **exact-match memo cache** returns the stored decision for an identical request (0 s) — not a cascade, just reuse.
* Default quant is **Q4_0** (fast 4×4 dot-product repack on Cortex-A76); Q4_K_M does not fit reliably in 8 GB.
* `-b`/`-ub` equal the context size: Clef needs the whole prompt in one micro-batch.

## 3. "Jev" and other routers

* **Jev** = TypeSafe AI, released 15 Sept 2026, **API-only** → incompatible with an offline Pi (not from Thinking Machines;
  Thinking Machines released *Inkling*, a 975B open-weights model, on 15 July 2026).
* Arch-Router-1.5B, RouteLLM, vLLM Semantic Router, tiny decision models (Laya/Kev) were reviewed as alternatives.

## 4. Offline knowledge (Kiwix ZIM)

* **All of English Wikipedia fits:** `wikipedia_en_all_maxi_2026-08` = **127.4 GB** (with images), `nopic` = 52.7 GB,
  `mini` = 14.4 GB (VERIFIED from download.kiwix.org Content-Length). Each ZIM embeds a Xapian full-text index — offline
  search with no indexing work on the Pi.
* A curated "education internet" (Wikipedia, Wikibooks, Wikiversity, LibreTexts, Stack Exchange STEM sites, DevDocs,
  Khan Academy via Kolibri, OpenStax, PhET, Gutenberg science) fits in ≈ 800 GB of the 1 TB disk.
* Retrieval design: per-ZIM full-text search → fetch top articles → clean text → ~200-word passages → BM25 rerank →
  3–5 passages packed into the generator prompt (≈ 0.3–0.8 s warm, 1.5–4 s cold on HDD, ESTIMATE).
* Pi 5 USB gives **600 mA** to peripherals unless a 5 A supply is detected (1.6 A): a 2.5" HDD needs a powered hub or
  its own supply (VERIFIED, raspberrypi.com docs).

## 5. Video

* **HyperFrames** = HeyGen's Apache-2.0 HTML+GSAP → MP4 framework (Node ≥ 22, headless Chrome, FFmpeg); auto low-memory
  mode at ≤ 8 GB; no published Pi benchmark (VERIFIED).
* **Manim 0.21** (Aug 2026) adds Typst/MathTypst — maths without a multi-GB LaTeX install (VERIFIED).
* **Small models cannot reliably write free-form Manim:** ManimTrainer (arXiv 2604.18364) vanilla render success —
  Coder-1.5B 27 %, Coder-3B 34 %, Qwen3-4B 29 %; Qwen3-Coder-30B-A3B 66 % → 89–94 % with render-in-the-loop + docs.
* **Design consequence:** the generator emits a *JSON scene plan*; pre-tested templates (Manim for maths/graphs/
  algorithms, HyperFrames for text/process/charts/code) render it. LLM text is never executed as code.
* Pi 5 has **no hardware H.264 encoder**; libx264 runs on the CPU (VERIFIED).
* Diffusion video models were rejected for teaching content (wrong text/equations, minutes per few seconds).

## 6. Speech

* **Supertonic 3**: 99M params, ≈ 398 MB ONNX (v2: 66M, ≈ 263 MB); **10 preset voices in one model** (F1–F5, M1–M5) →
  a two-host podcast just switches a style vector; 31 languages incl. Hindi (not Kannada/Tamil/Telugu/Bengali/Marathi/
  Malayalam/Chinese). MIT code + OpenRAIL-M weights (requires an "AI-generated" disclosure). **The GitHub repo was
  archived on 9 Sept 2026** — mirror the weights.
* Flow-matching steps matter: 2 steps → 32 % WER, **5 steps → 3.5 %** (MEASURED with an ASR check).
* All TTS engines mangle raw equations/code → the generator writes a separate *speakable* podcast script.
* Other offline options: Kokoro-82M, Piper, KittenTTS, Meta MMS (VITS, per language), Moonshine for STT.

## 7. Generator LLM on the Pi

* One shared **Qwen3.5-4B** (Q4_0 ≈ 2.58 GB, Apache-2.0, ≈ 3–4 tok/s decode on a Pi 5 — ESTIMATE) writes every text
  artefact through JSON-schema-constrained decoding; shared system+sources prefix → llama-server prompt cache reuses it.
* **Only one big model fits at a time** (Clef ≈ 6+ GB peak) → the model manager swaps them; every swap is measured.
* Raspberry Pi **AI HAT+ 2** (Hailo-10H) does not help: fixed small-model list, 2K context, cannot run Clef (VERIFIED).

## 8. Expected Pi-only lesson time (ESTIMATE)

| Stage | Time |
|---|---|
| Router load (HDD) + one decision | ≈ 1.5–3 min |
| Generator load + plan + retrieval | ≈ 1–2 min |
| Notes, flashcards, quiz, script, video plan (≈ 2–3k tokens at 3–4 tok/s) | ≈ 10–15 min |
| TTS (Supertonic, 5 steps) | ≈ 1–3 min |
| Video render + FFmpeg | ≈ 5–15 min |
| **Full package** | **≈ 25–45 min** — a background job, not a chat |

## 9. Gaps flagged by the adversarial review (and what the code does)

| Gap | How GateMoE handles it |
|---|---|
| Memory pressure / OOM | one-model-at-a-time manager; generator unloaded before TTS/render; `gatemoe doctor` warns on swap |
| Thermal throttling confounds timings | system sampler logs CPU temp + `vcgencmd get_throttled` into every job |
| Concurrency | single worker queue; jobs persisted; cancel; restart marks interrupted jobs |
| Executing LLM output | never: templates read JSON; render subprocesses get CPU/time limits and no network; generated code is shown, not run |
| Clef on ARM untested | `gatemoe route` and `gatemoe swapbench` exist to measure it on the real Pi first |
| Multilingual scope | language registry: Supertonic voices for its 31 languages, sherpa-onnx MMS voices for Indic languages, Noto fonts for on-screen text |
| Content-quality evaluation | open — see EXPERIMENTS.md |
