# GateMoE — an offline, router-orchestrated tutor on a Raspberry Pi 5

GateMoE turns one learner request — *"Explain Ohm's law with a quiz and a podcast"*, in English,
Hindi, Kannada, Tamil, Spanish, … — into a small lesson: study notes, flashcards, a quiz, a
two-host podcast, a narrated explainer video and a code example. It runs **entirely on one
Raspberry Pi 5 (8 GB)** with a 1 TB USB disk: no GPU, no cloud, no internet after setup.

The point of the project is *conditional computation at model level*: a master router decides
which gateways run, each gateway activates only the specialist it needs, and every stage, model
swap and token is measured so we can compare **theoretical** savings (work skipped) with
**realised** savings (wall time once model loading and swapping are paid).

```
request ─► language id ─► MASTER ROUTER (Cloudflare Clef-flash, one forward pass)
                              │ P(notes) P(flashcards) P(quiz) P(podcast) P(video) P(code)
                              │ subject · level · video engine · in-scope
            ┌─────────────────┼──────────────────────┬───────────────────────┐
     TEXT GATEWAY       KNOWLEDGE GATEWAY      SPEECH GATEWAY          VIDEO GATEWAY
   Qwen3.5-4B, JSON     Kiwix ZIM search        Supertonic 3 (31 langs)  Manim (maths) |
   schemas per task     (Wikipedia offline)     MMS / Piper (Indic)      HyperFrames (text)
                                                                          + FFmpeg
```

Only one big model is in RAM at a time (router ≈ 6 GB, generator ≈ 3 GB): the model manager
swaps them and logs each swap's cost.

## Quick start (Raspberry Pi)

```bash
git clone https://github.com/CR-8/GateMoE.git && cd GateMoE
DATA_DIR=/mnt/hdd/gatemoe ./scripts/install_pi.sh
DATA_DIR=/mnt/hdd/gatemoe ./scripts/download_models.sh
export GATEMOE_CONFIG=/mnt/hdd/gatemoe/gatemoe.yaml
/mnt/hdd/gatemoe/venv/bin/gatemoe doctor
/mnt/hdd/gatemoe/venv/bin/gatemoe serve          # open http://<pi>:8000 on your phone
```

Full instructions: [docs/PI_SETUP.md](docs/PI_SETUP.md).

## Commands

| Command | What it does |
|---|---|
| `gatemoe serve` | web app + job queue (phone-friendly UI with live routing/metrics) |
| `gatemoe lesson "<request>" [--language kn] [--mode clef\|all\|manual --gateways notes,quiz]` | one lesson from the terminal |
| `gatemoe route "<request>" ...` | router decision only |
| `gatemoe doctor` | checks models, voices, video engines, ZIMs, fonts, RAM |
| `gatemoe catalog` | lists offline knowledge archives |
| `gatemoe swapbench` | measures model load/unload (swap) cost |
| `python scripts/analyze_jobs.py <jobs_dir>` | theoretical vs realised savings across lessons |

`--mode all` runs every gateway — the baseline for the research comparison.

## Documentation

* [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — pipeline, two-level routing, memory plan, code map
* [docs/RESEARCH_FINDINGS.md](docs/RESEARCH_FINDINGS.md) — what was researched/measured before building (Clef-flash on CPU, Kiwix sizes, TTS, video, prior work) and the honest contribution
* [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md) — research questions and how to run them
* [docs/PI_SETUP.md](docs/PI_SETUP.md) — install, memory settings, troubleshooting
* [research/](research/) — the multi-agent research/verification workflows, raw reports and the Clef CPU benchmark

## What is (and is not) claimed

GateMoE is **hierarchical model routing** (a mixture-of-models / compound AI system) on an
edge single-board computer. It is not a Mixture-of-Experts network and not TinyML. NotebookLM-
style artefacts, LLM task decomposition and model routing all exist already; the contribution
is an end-to-end, fully offline implementation on an 8 GB Pi 5 and the measurement of when
conditional activation actually saves time there. See [docs/RESEARCH_FINDINGS.md](docs/RESEARCH_FINDINGS.md).

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest                      # model-free tests (a fake llama-server stands in for the models)
```

On an x86 dev machine with AMX, add `llama: {extra_args: ["-nr"]}` to your config (llama.cpp
weight-repack bug); never on the Pi.

## Licences

Code: MIT. Models and data keep their own licences — Clef-flash and Qwen3.5 (Apache-2.0),
Supertonic 3 weights (OpenRAIL-M: label audio as AI-generated), Meta MMS voices
(**CC-BY-NC-4.0**, non-commercial), Piper/piper-tts (GPL-3.0 runtime, per-voice cards),
GSAP (GreenSock standard no-charge licence), Wikipedia/Kiwix content (CC BY-SA).
