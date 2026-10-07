# Generator throughput (genbench)

`genbench.py` replays the text gateway's real requests (system prompt with SOURCES retrieved for a
stored lesson plan, task prompt, JSON schema, temperature 0.4, seed 7) against llama-server
variants and records wall time, generated tokens, tok/s, draft acceptance and the outputs.

Machine for the results below: x86 Xeon @ 2.1 GHz, 4 threads, 16 GB, Qwen3.5-4B Q4_K_M,
llama.cpp 5e03bdd (`-nr`). **Not a Pi 5** - repeat on the Pi before quoting Pi numbers.

## 1. Speculative decoding does not pay off on this CPU (7 Oct 2026)

`results/x86-4t_en_speculative-quick.json` (quiz task, reduced budget):

| variant | tok/s | draft accepted | quiz wall |
|---|---|---|---|
| baseline | 6.9-7.1 | - | 164 s |
| `--spec-type ngram-simple` | 6.8 | 27 / 384 (7 %) | 168 s |
| `draft-simple`, Qwen3.5-0.8B, n_max 4 | 6.1-6.3 | 714 / 999 (71 %, mean 3.9 tokens/round) | 191 s |

Why the well-predicting draft model still loses (llama-bench, 4 threads, no repack):

| component | speed | time |
|---|---|---|
| 4B decode, 1 token | 7.2 t/s | 0.140 s |
| 4B verify a 5-token batch | pp5 16.4 t/s | 0.304 s (2.2x one token) |
| 0.8B decode (draft) | 32.5 t/s | 4 drafts 0.123 s |

One round costs ~0.43 s for ~3.9 tokens: at best ~9 t/s on paper, 6.3 t/s measured. Batched
verification is far from free on this CPU, the 0.8B model is only 4.5x faster than the 4B, and the
hybrid (Gated DeltaNet + attention) `qwen35` architecture adds recurrent-state bookkeeping on
rejected drafts. On the memory-bound Pi the batch/decode ratio may differ: re-measure there
(`--variants baseline,draft-0.8b-n2,draft-0.8b-n4`).

## 2. JSON layout whitespace costs 26-37 % of generated tokens

llama-server's `json_schema` grammar allows up to two newlines and 20 spaces between JSON tokens,
and Qwen3.5 pretty-prints. `results/x86-4t_en_baseline-vs-compact.json` (English Ohm's-law lesson):

| task | baseline (json_schema) | compact GBNF (`gatemoe/llm/gbnf.py`) | change |
|---|---|---|---|
| notes | 311 s, 2 calls (first hit the 1100-token cap), 927 tokens in the final call | 127 s, 1 call, 777 tokens | -59 % |
| quiz | 93 s, 613 tokens (433 when re-serialised compactly) | 56 s, 384 tokens | -40 % |
| video plan | 84 s, 551 tokens (346 compact) | 53 s, 335 tokens | -37 % |
| total | 488 s | 236 s | **-52 %** |

Throughput is unchanged (7.0 t/s): the custom grammar is not slower to sample; all savings are
tokens not generated plus the avoided truncation retry. Output shape is comparable (notes: 4
sections, 10 concept links; quiz: 5 questions; video: 6 beats vs 5).

Kannada (`results/x86-4t_kn_baseline-vs-compact.json`, same machine): whitespace is a smaller share
because Kannada text needs ~2.3x the tokens of English, but the saving holds:

| task | baseline | compact | whitespace share of baseline tokens |
|---|---|---|---|
| notes | 369 s | 341 s (compact wrote ~40 % longer sections) | 15 % |
| quiz | 211 s | 159 s | 13 % |
| podcast | 135 s | 118 s | 19 % |
| total | 714 s | 617 s | **-14 %** |

**Decision:** `models.generator.compact_json: true` is the default. One sample per task and
language (temperature 0.4, seed 7): re-run with more requests before quoting the exact numbers.

## 3. Correction: "no whitespace at all" derails the model; allow ": " and ", "

The section 2 runs used a grammar with **no** whitespace. Checking the plan request (three seeds per
mode, English lesson) showed that forbidding every space pushes Qwen3.5 into unusual token sequences:

| mode | clean plans | tokens |
|---|---|---|
| `json_schema` (llama-server) | 3 / 3 | 93-122 |
| GBNF, no whitespace | **1 / 3** (`[":[0]","[1]","[2]"]`, `"> Ohm's law ..."`) | 55-72 |
| GBNF, optional single space after `:` and `,` | 3 / 3 | 73-81 |

The default is therefore the spaced form (`Generator(json_spaces=True)`): newlines and indentation -
most of the waste - stay banned. Section 2's numbers are for the no-space grammar; the spaced re-run
is in section 4. Lesson: one sample per task hid a failure mode; check several seeds.

The same check exposed a prompt/schema contradiction for non-English learners (prompt "0-2 native
queries", schema "at least 1"): the model tried to write an empty list and the grammar forced a junk
string. The plan prompt now states the same count as the schema for each language.
