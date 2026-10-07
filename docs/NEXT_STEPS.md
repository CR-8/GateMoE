# Next steps / handover

This file is the running plan for continuing GateMoE in a fresh session (another machine or
account). It is updated with every commit that changes the plan. Read it with
[ARCHITECTURE.md](ARCHITECTURE.md) and [RESEARCH_FINDINGS.md](RESEARCH_FINDINGS.md).

## State (7 Oct 2026)

Working end to end on x86 with the real models (Clef-flash router, Qwen3.5-4B generator):
router -> plan -> Kiwix retrieval -> notes / flashcards / quiz / code / podcast script / video plan
-> Supertonic or MMS speech -> Manim or HyperFrames video -> Typst handout, plus Graphviz concept
maps and PhET simulations served from ZIM files. Web UI (phone-friendly) and CLI. 47 model-free tests.

Measured on a 2-thread x86 container (not a Pi): English lesson ~19 min, Kannada ~26 min; the router
call is ~60 s (930 prompt tokens, no prefix cache possible); the generator runs at ~3.5 tok/s, so text
generation (notes 390 s, podcast 282 s, video plan 267 s) dominates.

## Set up a development session

```bash
git clone https://github.com/CR-8/GateMoE.git && cd GateMoE
python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
pytest                                   # no models needed (fake llama-server)
sudo apt-get install -y graphviz ffmpeg fonts-noto-core   # concept maps, audio/video, fonts
```

For real-model runs: build llama.cpp (master, with Clef support), then
`DATA_DIR=... ./scripts/download_models.sh` (or only the GGUFs + a few ZIMs, e.g.
`ZIMS="wikipedia_en_physics_nopic phet_en_all" SKIP_SPEECH=1`), write a small config like:

```yaml
paths: {data_dir: /path/to/data, llama_server: /path/to/llama.cpp/build/bin/llama-server}
llama: {extra_args: ["-nr"], ready_timeout_s: 3600}   # -nr only on x86 with AMX, never on the Pi
hardware: {threads: 4}
```

and run `GATEMOE_CONFIG=that.yaml gatemoe lesson "Explain Ohm's law with a quiz" --language en`.

## Open work, in priority order

### A. Review findings

All findings of review run 1 are fixed (commits "Fix review findings ..." and "Fix media review
findings ...") except: podcast TTS checks for cancel only between lessons stages, not between turns
(about 1 min on the Pi). The verification half of the review was not re-run (heavy).

### B. Optimisation work (deep, measured)

1. **Generator speed.** Done on x86 (see `research/genbench/README.md`): compact-JSON grammar (one optional
   space after ':' and ',', no newlines) is now the default (-42 % English, -18 % Kannada generation time); draft-model speculation with
   Qwen3.5-0.8B accepted 71 % of tokens but ran slower (6.2 vs 7.0 t/s) and n-gram lookup accepted 7 %.
   Next: repeat `genbench --variants baseline,compact,draft-0.8b-n2,draft-0.8b-n4` on the Pi 5
   (memory-bound decode may change the speculation result), and with more requests per task.
2. **Grammar cost.** Measured: the custom grammar samples at the same tok/s as json_schema.
3. **Router prompt size vs quality.** The schema is 92 % of Clef's prompt and cannot be cached.
   Build a small labelled request set (EN + Indic), then ablate question wording/count and quantisation
   (Q4_0 / IQ4_NL / Q3_K) for latency vs decision F1.
4. **Retrieval quality.** Done: per-language budget, rank fusion, LibreTexts JSON pages, subject-aware
   archives (see RESEARCH_FINDINGS section 10). Next: a small labelled relevance set (EN + Indic).
5. **CPU overlap.** TTS and Manim are CPU-bound; measure whether starting podcast TTS while the
   generator writes the video plan helps or hurts on 4 cores.
6. **Pi validation.** Everything above is measured on x86 so far; repeat the key numbers on a Pi 5.

### C. Research write-up

`scripts/analyze_jobs.py` already produces theoretical vs realised savings; run `--mode all` vs
`--mode clef` on the same request set and fill the tables in [EXPERIMENTS.md](EXPERIMENTS.md).
Known router behaviour to report: Clef-flash missed an explicitly requested quiz in a Kannada
request that also asked for a video (P(quiz) = 0.30).
