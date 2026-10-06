export const meta = {
  name: 'offline-edu-router-consult',
  description: 'Research + benchmark the fully-offline Pi 5 learning-content router (Clef-flash, Kiwix, Manim/HyperFrames, Supertonic) and adversarially verify key claims',
  phases: [
    { title: 'Research', detail: '6 parallel research tracks + 1 CPU benchmark of Clef-flash' },
    { title: 'Verify', detail: 'adversarial cross-check of decision-critical claims + gaps' },
  ],
}

const SCRATCH = '/path/to/scratchpad'  // original run used the session scratchpad

const CTX = `Context (you are researching for a consultation with a final-year student; today is 2026-10-06 — your training data may be stale, so verify current facts on the web and cite URLs):
- Goal: a fully OFFLINE educational-content system. A learner types "I want to learn X". A MASTER ROUTER decides which GATEWAYS fire (study notes, flashcards, quiz, two-voice podcast, explainer video, code). Each gateway picks a specialist model. Research angle: hierarchical conditional computation on edge hardware; theoretical vs realised savings (latency, memory, model load/swap cost, energy, INT8/INT4 effects).
- Hardware: Raspberry Pi 5 8 GB (4x Cortex-A76 @ 2.4 GHz, active cooler) + 1 TB USB HDD. The user wants the Pi to work fully offline on its own. A separate RTX 4090 desktop exists and could sit on the same LAN (still offline, no cloud) — treat it as optional.
- Router (user's decision): Cloudflare Clef-flash (released 2026-10-01, Apache-2.0, 9B, post-trained from Qwen/Qwen3.5-9B; a "decision model" that returns probabilities over typed choices (noul/choice/score questions) in ONE forward pass; API = POST /v1/systemone, compatible with TypeSafe's Jev). GGUFs: ggml-org/Clef-Flash-GGUF (Clef-Flash-Q4_K_M.gguf, Q8_0, BF16, plus mmproj vision files) and bartowski/Cloudflare_clef-flash-GGUF. llama.cpp master (commit 5e03bdd, 2026-10-05) already contains tools/server/server-decision.cpp with systemone support. A local clone is at /home/user/ggml-org/llama.cpp.
- Video (user's plan): Manim plus "HyperFrames CLI" (HTML/CSS/JS compositions rendered to video), with code written by a small specialised code-generation model.
- TTS (user's plan): Supertonic (on-device ONNX TTS; user believes ~300 MB with multiple voices in one model) for a two-speaker dialogue podcast.
- Knowledge (user's plan): offline local index on the 1 TB HDD (Kiwix ZIM etc.). The user believes they cannot download all of Wikipedia.
Rules: label every number as VERIFIED (give source URL), MEASURED (by you), or ESTIMATE (give reasoning). Prefer primary sources (model cards, official docs, release notes, papers). Do NOT clone repositories or use GitHub API / GitHub MCP tools; reading public web pages via WebSearch/WebFetch and Hugging Face API/raw files via curl is fine. Any file you download goes in its own new directory under ${SCRATCH}; never execute downloaded code. Be factual and dense.`

const RESEARCH_SCHEMA = {
  type: 'object',
  properties: {
    topic: { type: 'string' },
    bottom_line: { type: 'string', description: '3-6 sentence answer to the research question' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          claim: { type: 'string' },
          status: { type: 'string', enum: ['VERIFIED', 'MEASURED', 'ESTIMATE', 'UNVERIFIED'] },
          source: { type: 'string' },
        },
        required: ['claim', 'status'],
      },
    },
    recommendation: { type: 'string', description: 'concrete recommended design/stack for this area' },
    risks: { type: 'array', items: { type: 'string' } },
    open_questions: { type: 'array', items: { type: 'string' } },
  },
  required: ['topic', 'bottom_line', 'findings', 'recommendation', 'risks'],
}

const BENCH_SCHEMA = {
  type: 'object',
  properties: {
    success: { type: 'boolean' },
    hardware: { type: 'string' },
    llama_cpp_commit: { type: 'string' },
    build_notes: { type: 'string' },
    model_file: { type: 'string' },
    model_size_gb: { type: 'number' },
    load_time_s: { type: 'number' },
    rss_gb: { type: 'number' },
    llama_bench: { type: 'string', description: 'raw llama-bench pp/tg results' },
    requests: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          label: { type: 'string' },
          prompt_tokens: { type: 'number' },
          latency_ms: { type: 'number' },
          cached: { type: 'boolean' },
          decisions: { type: 'string' },
        },
        required: ['label'],
      },
    },
    prefill_tps: { type: 'number' },
    prompt_layout_notes: { type: 'string', description: 'what part of the request is static and cacheable' },
    pi5_extrapolation: { type: 'string', description: 'estimated Pi 5 latency/RAM with arithmetic and sources' },
    issues: { type: 'string' },
  },
  required: ['success', 'hardware', 'pi5_extrapolation', 'issues'],
}

const CRITIC_SCHEMA = {
  type: 'object',
  properties: {
    contradictions: {
      type: 'array',
      items: { type: 'object', properties: { issue: { type: 'string' }, resolution: { type: 'string' } }, required: ['issue', 'resolution'] },
    },
    verified_claims: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          claim: { type: 'string' },
          verdict: { type: 'string', enum: ['CONFIRMED', 'CORRECTED', 'UNVERIFIABLE'] },
          correct_value: { type: 'string' },
          source: { type: 'string' },
        },
        required: ['claim', 'verdict'],
      },
    },
    gaps: { type: 'array', items: { type: 'string' } },
    overall_assessment: { type: 'string' },
  },
  required: ['contradictions', 'verified_claims', 'gaps', 'overall_assessment'],
}

const TRACKS = [
  {
    key: 'clef-on-pi',
    prompt: `${CTX}

RESEARCH QUESTION: Can Cloudflare Clef-flash realistically serve as the router ON the Raspberry Pi 5 (8 GB), fully offline — and how do we make it fast enough?
Investigate:
(a) Read the model's own code to learn the input layout: curl https://huggingface.co/Cloudflare/clef-flash/raw/main/joint_schema_model.py (also joint_head_config.json, config.json) into a fresh dir and READ it (do not run it). Determine token order (is the question schema placed before or after the state?), whether a static schema prefix could be prompt/KV-cached across requests, roughly how many tokens a typical schema of ~10 questions costs, and how the joint head works.
(b) Read the llama.cpp implementation locally (read-only): /home/user/ggml-org/llama.cpp/tools/server/server-decision.cpp, server-decision.h, tools/server/README.md (systemone section), tools/server/tests/unit/test_systemone.py. Report request format, whether prompt caching / prefix reuse applies, whether mmproj is needed for text-only, relevant flags.
(c) Qwen3.5-9B architecture (hybrid linear attention / Gated DeltaNet? layer mix) and implications for llama.cpp CPU/ARM speed and memory; exact GGUF file sizes per quant (use https://huggingface.co/api/models/<repo>/tree/main or ?blobs=true) for ggml-org/Clef-Flash-GGUF and bartowski/Cloudflare_clef-flash-GGUF — note ARM-friendly quants (Q4_0, IQ4_NL, Q3, Q2).
(d) Published Raspberry Pi 5 llama.cpp benchmarks for 7–9B models (prompt-processing t/s and generation t/s). Estimate Clef-flash per-decision latency on Pi 5 (i) cold full prompt, (ii) with cached static prefix; RAM headroom on 8 GB alongside the OS.
(e) Smaller decision-model alternatives (Laya, Kev 9B, DiffusionGemma Jev from Clef's Decision Index) — open-weight? size? Pi-viable? Also other small open routers (e.g., Arch-Router-1.5B) and their Pi viability.
(f) A practical recipe: llama-server flags for Pi 5 (threads, ctx size, mmap/mlock, no mmproj, cache settings) and a cascade design (tiny classifier first; Clef-flash only when unsure) plus distillation (use Clef labels to train a tiny Pi router).`,
  },
  {
    key: 'offline-knowledge',
    prompt: `${CTX}

RESEARCH QUESTION: Design the OFFLINE knowledge layer ("a local piece of the internet") on Raspberry Pi 5 + 1 TB USB HDD so the router/LLMs can search and ground content with no internet.
Investigate with current (2026) sources:
(a) Kiwix ZIM catalog — exact current sizes for: wikipedia_en_all_maxi, _nopic, _mini; Simple English Wikipedia; Wikibooks, Wikiversity, Wiktionary; Stack Exchange ZIMs (stackoverflow, math, physics, cs, electronics); DevDocs / Python / MDN docs ZIMs; LibreTexts; PhET; freeCodeCamp; Khan Academy / CK-12 / OpenStax if present; Project Gutenberg; TED or other video-based educational ZIMs. Use the Kiwix OPDS catalog (e.g. https://library.kiwix.org/catalog/v2/entries?lang=eng&q=wikipedia&count=50) or https://download.kiwix.org/zim/ directory listings to get sizes. Explicitly correct the belief that all of Wikipedia cannot be stored; produce a 1 TB storage budget table.
(b) Searching ZIM offline: kiwix-serve full-text search (Xapian index embedded in ZIM) and its HTTP endpoints (/search, /suggest, JSON/XML output options), python-libzim Searcher/SuggestionSearcher, performance on Pi/HDD; existing Kiwix MCP servers or LLM+Kiwix RAG projects (e.g., openzim-mcp or similar); extracting clean text from ZIM HTML.
(c) Semantic search feasible on a Pi: prebuilt indexes such as NeuML txtai-wikipedia (size, what it covers), pre-computed Wikipedia embedding datasets, binary/int8 quantised embeddings; small embedding models runnable on Pi (bge-small, all-MiniLM-L6, EmbeddingGemma, model2vec/static embeddings) with speeds; vector stores on ARM (sqlite-vec, usearch, faiss, LanceDB). Recommend a hybrid: Xapian/BM25 first stage → embedding rerank of top-k.
(d) Offline-education platforms that already run on a Pi: Internet-in-a-Box (IIAB), Kolibri (Learning Equality) — content, sizes, APIs — as reuse and prior art.
(e) Bigger "piece of the internet" options: FineWeb-Edu samples (sizes), Wikipedia plain-text dumps, arXiv abstracts; indexing cost on Pi vs on the 4090 machine then copying the index to the Pi.
(f) Hardware pitfalls: powering a USB HDD from Pi 5 (USB current limit ~600 mA unless 5 A PSU / usb_max_current_enable), spinning-disk random-read latency for search indexes, NVMe via Pi 5 PCIe for models+indexes vs HDD for bulk ZIM.
(g) Optional "sync when online" path: Crawl4AI / trafilatura / zimit / warc2zim to snapshot sites into local ZIM or index.
Deliver a concrete recommended stack, a 1 TB storage budget, a retrieval pipeline with expected Pi 5 latencies, and risks.`,
  },
  {
    key: 'video',
    prompt: `${CTX}

RESEARCH QUESTION: How should the VIDEO gateway produce short educational explainer videos fully offline using Manim and HyperFrames, with code written by a small local code model?
Investigate (2026-current sources):
(a) HyperFrames: identify exactly what "HyperFrames" / "HyperFrames CLI" is (possibly an open-source HTML-to-video framework by HeyGen — verify), license, install (npm?), dependencies (Node, headless Chrome/Chromium, FFmpeg), whether it runs on Linux ARM64 / Raspberry Pi 5, render speed, composition format (HTML + data attributes? GSAP?), docs or "skills" aimed at AI agents, determinism, audio support, how a small LLM can author compositions. Compare briefly with Remotion and Motion Canvas.
(b) Manim Community Edition on Raspberry Pi 5 (Raspberry Pi OS arm64, Bookworm/Trixie): install (Cairo/Pango/FFmpeg), LaTeX needs for MathTex (TeX Live size; using Text instead to avoid LaTeX), render speed at -ql/-qm, OpenGL renderer viability; manim-voiceover for syncing narration with an offline TTS.
(c) Evidence on LLMs writing Manim/HTML animations: Code2Video, TheoremExplainAgent, Manim-specific fine-tuned models/datasets on HF, success rates, failure modes; what small models (≤9B) achieve.
(d) Robust design for SMALL models: template library + JSON slot-filling (LLM emits a scene spec, deterministic renderer fills templates), grammar-constrained decoding (llama.cpp JSON schema/GBNF), render→error→repair loop with retry cap, scene caching. Estimate tokens (full Manim code vs JSON spec) and generation time on Pi 5 at ~5–10 tok/s.
(e) Assembly: FFmpeg on Pi 5 (Pi 5 lacks hardware H.264 encode — verify), software encode speed at 720p, muxing per-scene narration, subtitles from script; time budget for a 2–3 minute video on Pi vs on the 4090 box.
Deliver: which renderer for which content, pipeline steps, expected render times, risks (including security: sandboxing LLM-generated code).`,
  },
  {
    key: 'speech',
    prompt: `${CTX}

RESEARCH QUESTION: Choose offline speech models for Raspberry Pi 5 (8 GB): (1) two-speaker dialogue podcast + single narrator for videos; (2) optional speech-to-text input.
Investigate (2026-current, primary sources):
(a) Supertonic (Supertone Inc.): all versions (1/2/3?), parameter count, ONNX file sizes (is ~300 MB right?), built-in voices/voice styles count, languages, voice cloning?, license of code AND weights (OpenRAIL-M? restrictions), runtimes (onnxruntime Python/C++/JS/web), reported speed on Raspberry Pi / ARM CPUs (real-time factor), quality notes, text normalisation limits (numbers, maths, code).
(b) Two-speaker dialogue with a single multi-voice model: script JSON with turns → synthesize per turn with voice A/B → pauses → concat + loudness-normalise (FFmpeg) — time estimate for a 5-minute podcast on Pi 5.
(c) Alternatives runnable on Pi 5: Kokoro-82M (kokoro-onnx), Piper, KittenTTS, NeuTTS Air and other 2026 small TTS; true dialogue models (Dia, VibeVoice, MOSS-TTSD, Higgs Audio) — would they require the 4090?
(d) STT on Pi 5: Moonshine (sizes, ONNX), whisper.cpp tiny/base/small speed on Pi 5, sherpa-onnx, Vosk.
(e) Pronouncing technical content (equations, code) — mitigation via an LLM-written "speakable" script.
Deliver a recommendation table (size, license, Pi speed, voices) and risks.`,
  },
  {
    key: 'pi-llm',
    prompt: `${CTX}

RESEARCH QUESTION: Which local generative models should run the text and code specialists (notes/flashcards/quiz JSON, podcast script, Manim/HTML code) on a Raspberry Pi 5 8 GB, and what does an optional LAN RTX 4090 add while staying offline?
Investigate (Oct 2026; verify with model cards and published benchmarks):
(a) Small open models suitable for Pi 5 + llama.cpp: Qwen3.5 small sizes (which exist?), Qwen3 0.6B–4B, Qwen2.5-Coder / Qwen3-Coder small, Gemma 3/3n/4 small, Phi-4-mini, SmolLM3-3B, Liquid AI LFM2/LFM2.5 incl. MoE LFM2-8B-A1B (fits the MoE research theme), IBM Granite 4 tiny/micro (hybrid MoE), Llama 3.2 1B/3B, Ministral 3B. For each: params (active if MoE), Q4 GGUF size, published Pi 5 tok/s (prompt + generation), code-quality signals, license.
(b) RAM plan on 8 GB with Clef-flash (~5.5 GB at Q4) as router: can a generator co-reside? If not, quantify model swap/load times from USB HDD (~100–150 MB/s) vs NVMe via Pi 5 PCIe (Gen2 ~450 MB/s, Gen3 ~800–900 MB/s) vs Linux page cache; llama.cpp mmap behaviour. This is the user's "expert caching / realised vs theoretical sparsity" research angle — make it quantitative.
(c) Raspberry Pi AI HAT+ 2 (Hailo-10H with onboard RAM) or other Pi accelerators in 2026: supported LLMs/VLMs, tok/s, whether custom models (like Clef) can run there, price.
(d) Constrained JSON decoding in llama.cpp (JSON schema / GBNF) for flashcard/quiz reliability on small models.
(e) What the optional RTX 4090 on LAN adds (still offline): bigger coder models (e.g., Qwen3-Coder-30B-A3B), full Clef 27B, faster rendering; graceful fallback to Pi-only when the 4090 is off (another routing decision).
Deliver: recommended model per specialist for Pi-only mode and Pi+4090 mode, a RAM/swap plan, expected latencies, risks.`,
  },
  {
    key: 'novelty',
    prompt: `${CTX}

RESEARCH QUESTION: What is genuinely already done vs still open, so the student can claim an HONEST small contribution? Candidate contributions the user liked: (1) a fully offline, local-first, router-orchestrated learning-content system on a Pi 5; (2) a confidence-cascade router (tiny classifier on Pi → escalate to a decision model like Clef-flash only when unsure = early-exit applied to routing); (3) measuring realised vs theoretical savings when specialist models must be loaded/swapped under constrained memory (expert caching at model level).
Search literature/projects 2023–2026: HuggingGPT/TaskMatrix/compound AI systems; LLM routers & cascades (RouteLLM, Arch-Router, RouterBench, FrugalGPT, AutoMix, router distillation); decision models (Jev, Clef, SystemOne); on-device MoE / expert offloading (EdgeMoE, SwapMoE, MoE-Infinity, Fiddler, Pre-gated MoE, AdapMoE, HOBBIT, etc.) and any work measuring model-swap or expert-load costs on Raspberry Pi/ARM; early-exit on edge; offline education + LLM on Raspberry Pi (Internet-in-a-Box + LLM, Kolibri + AI, offline AI tutor projects, offline NotebookLM-style clones).
Deliver: (a) prior-art findings (name, year, what it shows, link) as findings; (b) for each of the 3 candidate contributions: overlap, what remains open, how to phrase the claim honestly; (c) 3–5 concrete, small, measurable research questions with a simple experiment design (independent variables, metrics, baselines) runnable on Pi 5 (+ optional 4090) in a few weeks; (d) wording pitfalls (MoE vs mixture-of-models, TinyML vs edge). Put (b)-(d) in recommendation.`,
  },
]

const BENCH_PROMPT = `${CTX}

TASK: MEASURE Clef-flash routing latency on CPU in this container as a proxy for the Raspberry Pi 5. You may run commands with Bash.
Steps:
1. llama.cpp source (trusted, user-approved for benchmarking) is at /home/user/ggml-org/llama.cpp (master 2026-10-05; contains tools/server/server-decision.cpp). Do NOT modify it. Read docs/build.md for current CPU build flags. Build out-of-tree: cmake -S /home/user/ggml-org/llama.cpp -B ${SCRATCH}/llama-build -DCMAKE_BUILD_TYPE=Release (disable curl/other optional deps if they fail), then build only the targets you need (llama-server and llama-bench; target names may differ in this version — check) with -j 4. Install missing build deps via apt/pip if needed.
2. Check disk (df -h). Download ONLY the text GGUF (no mmproj) into a new dir ${SCRATCH}/models: curl -L -C - -o ${SCRATCH}/models/Clef-Flash-Q4_K_M.gguf https://huggingface.co/ggml-org/Clef-Flash-GGUF/resolve/main/Clef-Flash-Q4_K_M.gguf . If the proxy blocks large files, read /root/.ccr/README.md for fixes. Optionally, if time allows, also test an ARM-friendly Q4_0 quant from bartowski/Cloudflare_clef-flash-GGUF.
3. Read tools/server/README.md (systemone section) and tools/server/tests/unit/test_systemone.py for the exact request format and server flags. Start llama-server on 127.0.0.1 with 4 threads (-t 4, mimicking the Pi 5's 4 cores), ctx ~4096, as a background process; record model load time.
4. Send a realistic educational routing request to /v1/systemone: state = a learner message (e.g. "I want to learn how binary search works, with a short quiz and something I can listen to on the bus"); ~10 questions: noul gateways (needs_notes, needs_flashcards, needs_quiz, needs_podcast, needs_video, needs_code), choice subject (cs, maths, physics, chemistry, biology, history, language, other), score difficulty (beginner, intermediate, advanced), choice video_style (manim_math, html_motion, none), choice voice_mode (single_narrator, two_speaker_dialogue). Then try 5 different learner requests (e.g., photosynthesis podcast; derivative of x^2 with animation; Python list comprehension practice; French greetings flashcards; causes of WW1 summary) and report whether decisions look sensible.
5. Measure per request: prompt tokens (from usage/timings), wall latency, cold vs warm (repeat the identical request; then a different request with the same questions to see prefix-cache effect), prefill tokens/s from server timings, llama-server RSS (ps -o rss), and run llama-bench -t 4 -p 512 -n 32 for a standard number. Determine which part of the encoded prompt is static (question schema) vs dynamic (state) and whether reordering/caching can help.
6. Record CPU model (lscpu), core count, ISA extensions (AVX2/AVX512/AMX).
7. Extrapolate to Raspberry Pi 5: search the web for published llama-bench pp512 numbers on Pi 5 (Cortex-A76) for a similar-size (7–9B) Q4 model; compute the ratio vs your measured pp512 here; estimate Pi 5 per-decision latency (cold and with cached prefix) and RAM fit on 8 GB. Show the arithmetic.
Time-box: if build or download fails after two honest attempts, stop and report exactly what failed. Kill the server when done. Report raw numbers.`

phase('Research')
const results = await parallel([
  ...TRACKS.map(t => () => agent(t.prompt, { label: `research:${t.key}`, phase: 'Research', schema: RESEARCH_SCHEMA }).then(r => ({ key: t.key, ...(r || {}) }))),
  () => agent(BENCH_PROMPT, { label: 'bench:clef-flash-cpu', phase: 'Research', schema: BENCH_SCHEMA, agentType: 'general-purpose' }).then(r => ({ key: 'bench', ...(r || {}) })),
])
const done = results.filter(Boolean)
const missing = ['clef-on-pi', 'offline-knowledge', 'video', 'speech', 'pi-llm', 'novelty', 'bench'].filter(k => !done.find(d => d.key === k && (d.bottom_line || d.hardware)))
if (missing.length) log(`Tracks that returned nothing: ${missing.join(', ')}`)

phase('Verify')
const critic = await agent(`${CTX}

You are the ADVERSARIAL REVIEWER for this consultation. Below are research reports (JSON) from parallel tracks, including a CPU benchmark of Clef-flash.
Tasks:
1. Find contradictions between reports; resolve each by checking sources yourself.
2. Pick the 10 most decision-critical claims — ones that would change the architecture if wrong (e.g., Clef-flash latency and RAM on Pi 5; llama.cpp systemone support; Supertonic license/size/voices/Pi speed; what HyperFrames is, its license and ARM/Pi support; Kiwix Wikipedia ZIM sizes; Raspberry Pi AI HAT+ 2 capabilities; Pi 5 USB HDD power limits; Pi 5 hardware video encoding; best small LLMs for Pi 5 and their tok/s). Independently re-verify each with fresh web searches / HF API. Mark CONFIRMED, CORRECTED (give correct value + source), or UNVERIFIABLE. Default to skepticism.
3. Completeness: list important gaps none of the reports covered (e.g., thermal throttling under sustained load, concurrent users, SD-card wear, sandboxing LLM-generated Manim/HTML code, Chromium memory on Pi, total end-to-end latency per lesson, evaluation methodology).
4. Overall assessment in 4-6 sentences: is a fully Pi-only offline system with Clef-flash as router realistic, and what is the single biggest risk?

REPORTS:
${JSON.stringify(done, null, 1)}`, { label: 'critic:adversarial-verify', phase: 'Verify', schema: CRITIC_SCHEMA, effort: 'high' })

return { reports: done, critic }
