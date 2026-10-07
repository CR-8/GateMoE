# Extending GateMoE: more voices, renderers, knowledge and languages

GateMoE is built so that specialists can be added without touching the router: the router
decides *which gateway* runs; each gateway picks among whatever specialists are installed.
Every specialist you add shows up in `gatemoe doctor`, in `/api/system` and in each lesson's
`specialists.available` / `specialists.activated` lists (the conditional-computation numbers).

## 1. Add a voice engine (Fish Audio, Kokoro, XTTS, eSpeak, …) — config only

Put a `speech.plugins` list in your `gatemoe.yaml`. Two kinds exist (`gatemoe/gateways/speech/plugin_tts.py`):

### HTTP server (e.g. Fish Speech / Fish Audio API server)

```yaml
speech:
  plugins:
    - name: fish-s2
      type: http
      url: http://127.0.0.1:8080/v1/tts          # Fish Speech `tools/api_server.py`
      body: {text: "{text}", reference_id: "{voice}", format: wav}
      voices: {A: host_a, B: host_b}             # two saved reference voices -> two real hosts
      languages: ["*"]                           # or [en, hi, kn, ...]
      prefer: true                               # try before the built-in engines for these languages
      timeout_s: 900
```

`{text}`, `{voice}` and `{lang}` are replaced in every string of `body`. Any response audio
format FFmpeg can decode works (WAV, MP3, Opus …). OpenAI-style servers work the same way, e.g.
`body: {model: tts-1, input: "{text}", voice: "{voice}", response_format: wav}`.

### Command line

```yaml
speech:
  plugins:
    - name: espeak
      type: command
      argv: [espeak-ng, -v, "{lang}", -w, "{out_wav}", -f, "{text_file}"]
      languages: [en, hi]
      prefer: false        # only used when no built-in engine speaks the language
```

**Fish Audio on a Pi-only setup — the honest numbers.** Fish Audio's current open model, S2 Pro
(March 2026), has 4.56 B parameters (≈ 9 GB in BF16) and a custom licence; Fish Speech 1.5 is
CC BY-NC-SA and its authors recommend a 12 GB+ GPU. Neither fits the 8 GB Pi next to anything
else, and CPU inference of a 4.5 B autoregressive codec model would take far longer than the
audio itself. Use it through the HTTP plugin when a GPU machine is on the LAN (still offline),
and keep Supertonic/MMS/Piper as the Pi-only voices. Licences of plugged-in models are your
responsibility.

Built-in engines and their routing table live in `gatemoe/gateways/speech/podcast.py`
(`ENGINE_TABLE`); to add another ONNX engine permanently, add a class with
`synth(text, ...) -> (float32 wav, sample_rate)` and a row in that table.

## 2. Add a video template

* **HyperFrames (HTML + GSAP):** copy one of `gatemoe/gateways/video/hf_templates/*.html`.
  Read the payload with `GM.payload()`, insert text only with `GM.text()` (textContent), register
  the GSAP timeline with `window.__timelines["main"] = tl`, finish animating within ~3 s.
  Then add the template to `TEMPLATE_FILES` and a payload branch in `build_payload()`
  (`hyperframes.py`), and map a planner template to it in `hf_scene()` (`video/__init__.py`).
* **Manim:** add a Scene class + validation rules + JSON schema in `manim_templates.py`
  (`SCENES`, `validate_spec`, `BEAT_SCHEMAS`) and a mapping in `beat_to_spec()` (`manim_engine.py`).
  Never `eval` LLM text: follow the existing AST whitelist / Typst denylist pattern.
* Finally list the new planner template in `VIDEO_TEMPLATES` (`gateways/schemas.py`) with the
  engines that can render it — the planner's JSON schema is built from that table.

Other renderers suited to scientific content that could become gateways the same way:
Typst diagrams (`cetz` needs vendoring — Typst packages are fetched from the internet by default),
Graphviz concept maps (`dot`, tiny on a Pi), Matplotlib plots, and the PhET simulations that
Kiwix packages as ZIM files (link them from the notes).

## 3. Add offline knowledge

Drop any `.zim` file into `paths.zim_dir` (`gatemoe catalog` lists them). The knowledge gateway
picks archives by the ZIM's `Language` metadata (learner language first, then English) and boosts
names matching the router's subject (`SUBJECT_HINTS` in `gatemoe/knowledge/__init__.py`).
Good additions: `wikipedia_<lang>_all_nopic`, `libretexts.org_en_*`, `wikibooks_en_all`,
`physics.stackexchange.com_en_all`, `devdocs_en_python`, PhET. Fetch the latest of each with
`ZIMS="..." ./scripts/download_models.sh`.

## 4. Add a language

Add a line to `gatemoe/config/languages.yaml` (or under `languages:` in your own config):
`xx: {name: ..., native: ..., iso3: xxx, tts: ..., font: "Noto Sans ...", token_factor: 2.0}`.
`token_factor` scales every generation budget (Indic scripts need ~2x the tokens of English).
Give it a voice (built-in table or a plugin), a font that `fc-list` knows, and a ZIM in that language.

## 5. Swap the models

`models.router.file` / `models.generator.file` take any GGUF llama.cpp can run. The router
must be a decision model served at `/v1/systemone` (Clef, Clef-flash or a future one with the same
API); the generator any instruct model (set `disable_thinking: false` for models without a
thinking switch). Measure the change with `gatemoe swapbench` and the experiments in
[EXPERIMENTS.md](EXPERIMENTS.md).
