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
                                    │      plan → KNOWLEDGE GATEWAY (Kiwix ZIM search + BM25)  │
                                    │      notes · flashcards · quiz · code ·                  │
                                    │      podcast script · video scene plan                   │
                                    │                                   [model swap: generator]│
                                    │ 4. unload generator (free RAM)                            │
                                    │ 5. SPEECH GATEWAY  Supertonic 3 (31 langs, 10 voices)    │
                                    │                    or sherpa-onnx MMS (Indic languages)  │
                                    │ 6. VIDEO GATEWAY   Manim templates | HyperFrames templates│
                                    │                    + FFmpeg (narration, subtitles)       │
                                    │ 7. lesson.json + notes.md + Anki TSV + mp3 + mp4         │
                                    └────────────────────────────────────────────────────────┘
                                      1 TB USB HDD: models/, zim/, jobs/, cache/
```

## Two levels of routing

| Level | Who decides | What is decided |
|---|---|---|
| 1. Master router | **Clef-flash** decision model | which gateways run (`notes`, `flashcards`, `quiz`, `podcast`, `video`, `code`), subject, level, preferred video engine, in-scope |
| 2. Gateway | gateway code (deterministic rules) | **Speech**: Supertonic vs MMS voice by language. **Video**: Manim vs HyperFrames by the router's engine choice + availability; template per beat chosen by the planner. **Knowledge**: which ZIMs by learner language (+ English). |

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
| `gatemoe/knowledge/` | ZIM catalogue, full-text search, HTML→text, passages, BM25 |
| `gatemoe/gateways/speech/` | Supertonic 3 ONNX engine, sherpa-onnx fallback, podcast assembly |
| `gatemoe/gateways/video/` | Manim + HyperFrames templates, sandboxed rendering, FFmpeg assembly, subtitles |
| `gatemoe/pipeline.py` | the lesson pipeline (stages above) |
| `gatemoe/jobs.py`, `gatemoe/server/` | job queue, REST + SSE API, web UI |
| `gatemoe/cli.py` | `serve`, `lesson`, `route`, `catalog`, `doctor`, `swapbench` |
| `scripts/` | Pi install, offline asset download, log analysis |

## Safety

* LLM output is **data**, never code: templates read JSON slot values; function graphs use an AST
  whitelist; HTML templates escape every string; generated Python examples are shown, not run.
* The web API serves only files inside the job's own folder; job ids are validated.
* The app never needs the network; llama-server and render subprocesses get proxy variables removed.
