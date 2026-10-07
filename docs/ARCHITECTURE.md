# Architecture

```
 phone / laptop browser ──Wi-Fi──►  Raspberry Pi 5 (8 GB, offline)
                                    ┌────────────────────────────────────────────────────────┐
  "Explain Ohm's law with a quiz"   │ FastAPI web app + single worker job queue               │
                                    │                                                          │
                                    │ 1. language id (script + py3langid, ms)                  │
                                    │ 2. MASTER ROUTER  Clef-flash 9B Q4_0  /v1/systemone      │
                                    │      one forward pass → P(yes) per gateway, subject,     │
                                    │      level, video engine          [model swap: router]   │
                                    │ 3. TEXT GATEWAY   Qwen3.5-4B Q4_0, JSON-schema decoding  │
                                    │      plan → KNOWLEDGE GATEWAY (Kiwix ZIM search, RRF,    │
                                    │             BM25) + PhET simulation match (no model)     │
                                    │      notes (+ concept links) · flashcards · quiz · code ·│
                                    │      podcast script · video scene plan                   │
                                    │                                   [model swap: generator]│
                                    │ 4. unload generator (free RAM)                            │
                                    │ 5. SPEECH GATEWAY  Supertonic 3 (31 langs, 10 voices)    │
                                    │                    or sherpa-onnx MMS (Indic languages)  │
                                    │ 6. VIDEO GATEWAY   Manim templates | HyperFrames templates│
                                    │                    + FFmpeg (narration, subtitles)       │
                                    │ 7. CONCEPT MAP (Graphviz) + HANDOUT (Typst PDF) +        │
                                    │    lesson.json + notes.md + Anki                         │
                                    └────────────────────────────────────────────────────────┘
                                      1 TB USB HDD: models/, zim/, jobs/, cache/
```

## Two levels of routing

| Level | Who decides | What is decided |
|---|---|---|
| 1. Master router | **Clef-flash** decision model | which gateways run (`notes`, `flashcards`, `quiz`, `podcast`, `video`, `code`), subject, level, preferred video engine, in-scope |
| 2. Gateway | gateway code (deterministic rules) | **Speech**: configured plugin (e.g. Fish Audio server) → Supertonic 3 → MMS / Piper / sherpa by language. **Video**: Manim vs HyperFrames by the router's engine choice + availability; template per beat chosen by the planner; rejected beats repaired once by the LLM, then degraded to a bullet slide, then the other engine. **Knowledge**: which ZIMs by learner language (+ English) and subject; topical archives only for their subject; a per-language article budget. **Simulations**: PhET sims whose titles the lesson's terms cover (learner-language version when installed). **Concept map**: Graphviz whenever the notes carry concept links. **Handout**: Typst PDF whenever notes/quiz/flashcards exist. |

Only the selected gateways execute; only the specialists they need are loaded. Every lesson
records `specialists.available` vs `specialists.activated` and all timings.

## Memory plan (8 GB)

* Exactly one llama-server process at a time (`runtime/model_manager.py`): router (≈ 6 GB peak)
  → unloaded → generator (≈ 3 GB) → unloaded before TTS + rendering.
* Speech ONNX sessions are created per lesson and closed afterwards.
* Render subprocesses (Manim, HyperFrames/Chromium, FFmpeg) run with CPU-time limits and no network.

## Code map

| Path | Role |
|---|---|
| `gatemoe/config.py`, `gatemoe/config/*.yaml` | defaults (Pi), language registry, `${ref}` interpolation, user override file |
| `gatemoe/runtime/` | llama-server subprocesses, one-model manager, event log, system sampler |
| `gatemoe/router/clef.py` | routing schema, `/v1/systemone` call, decision parsing, memo cache, `all`/`manual` modes |
| `gatemoe/langid.py` | script + py3langid language detection |
| `gatemoe/llm/` | OpenAI-compatible JSON-schema client (non-thinking), schema validator |
| `gatemoe/gateways/text.py`, `schemas.py` | prompts + schemas for plan, notes, flashcards, quiz, podcast, code, video plan |
| `gatemoe/knowledge/` | ZIM catalogue, full-text search, reciprocal-rank fusion, HTML→text (incl. LibreTexts JSON pages), passages, BM25; `phet.py` simulation matching |
| `gatemoe/gateways/conceptmap.py` | concept links → escaped DOT → sandboxed `dot` (cairo SVG + PNG) |
| `gatemoe/gateways/speech/` | Supertonic 3, MMS, Piper, sherpa-onnx engines, config plugins (HTTP/command), podcast + narration |
| `gatemoe/gateways/video/` | Manim templates + batch child process, HyperFrames templates + fallback renderer, sandbox, FFmpeg assembly, subtitles |
| `gatemoe/gateways/handout.py` | Typst PDF handout (notes, glossary, cut-out flashcards, quiz + answer key) |
| `gatemoe/pipeline.py` | the lesson pipeline (stages above) |
| `gatemoe/jobs.py`, `gatemoe/server/` | job queue, REST + SSE API, web UI |
| `gatemoe/cli.py` | `serve`, `lesson`, `route`, `catalog`, `doctor`, `swapbench` |
| `scripts/` | Pi install, offline asset download, log analysis |

## Safety

* LLM output is **data**, never code: templates read JSON slot values; function graphs use an AST
  whitelist; Typst maths passes a denylist (no `#`, `$`, LaTeX) and a precompile; HTML templates and
  the Typst handout insert text only; generated Python examples are shown, not run.
* Manim renders in one child process per lesson under `prlimit` (CPU seconds, address space,
  file size, no core dumps), niced, with a minimal environment and a process-group kill on timeout;
  HyperFrames/Chromium runs under the same CPU cap and timeout.
* The web API serves only files inside the job's own folder; job ids are validated; SVG/HTML lesson files carry a
  sandbox CSP. Offline archive pages (`/zim/<zim>/<path>`, e.g. PhET sims) are served under
  `Content-Security-Policy: sandbox allow-scripts; connect-src 'none'` - an opaque origin with no access to the API,
  storage or network (verified in Chromium).
* Requests must name this machine (IP literal, localhost, `*.local`, its hostname or `server.allowed_hosts`): blocks
  DNS rebinding from web pages. At most `server.max_pending_jobs` lessons wait at once.
* One resident model per machine across GateMoE processes (file lock); models are unloaded on exit/shutdown and a
  port another server already answers on is refused.
* The app never needs the network; llama-server and render subprocesses get proxy variables removed.
