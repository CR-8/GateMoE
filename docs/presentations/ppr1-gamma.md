# EdgeMoE: An ASIC-Inspired Tiny Mixture-of-Experts Inference Platform for Ultra-Low-Power Edge Intelligence

**Project Progress Report I**

A compact router activates only the experts that an input needs, so a small edge device does only the work that request requires.

- **Student:** Pratyush Tiwari (2301221540034)
- **Project Guide:** Er. Aayush Singh
- **Project Coordinator:** Er. Ayodhya Prasad Sahu
- **Department:** Computer Science and Engineering (AI/ML & DS), Shri Ramswaroop Memorial College of Engineering and Management
- **Area:** Edge AI · Embedded Systems · ML Systems · Conditional Computation · Hardware-Aware AI
- **Type:** Software-based model, evaluated on embedded ARM hardware (Raspberry Pi 5)

---

# Aim and Research Gap

**Aim:** design and evaluate a Mixture-of-Experts inference system with input-adaptive, sparse expert activation on constrained edge CPUs. Then measure the latency and energy efficiency actually **realised at runtime**, against the architecture's **theoretical** sparsity.

**Why it matters**
- Edge devices (an 8 GB Raspberry Pi 5) cannot run every model for every request.
- Skipping work is only useful if the saving survives real overheads: model loading, cache misses, dispatch, prefill.

**Research gap (from the completed review paper)**
- MoE routing, edge MoE, quantization and hardware-aware execution are well studied on their own.
- Missing: *when* does expert sparsity become real latency and energy gains on edge CPUs?
- Theoretical sparsity and runtime-realised sparsity are rarely measured side by side.

**Honest contribution:** an end-to-end, fully offline system with measurements, not a claim that MoE on the edge is new.

---

# Objectives and Status

| # | Objective | Status (as reported in PPR-I) |
|---|---|---|
| 1 | Design a lightweight MoE architecture with a compact router and cache-resident experts | Complete |
| 2 | Implement input-adaptive top-k routing (reference configuration: 8 experts, top-2 active) | Complete |
| 3 | Apply INT8 quantization and structured/unstructured sparsity to reduce per-expert footprint | Complete |
| 4 | Develop a hardware-aware runtime optimising cache locality and expert dispatch on ARM CPUs | Under integration |
| 5 | Deploy and benchmark on Raspberry Pi / embedded ARM hardware | In progress |
| 6 | Compare dense, quantized, early-exit and MoE inference; quantify theoretical vs runtime-realised sparsity | In progress |

- **Overall progress at PPR-I:** about 40%
- **Hardware:** about 90% of the required hardware acquired; only minor peripherals remain

---

# System Architecture: the Working Prototype

The EdgeMoE principle is realised at **model level** (a mixture of models): one router decides which expert gateways run, and each gateway loads only the specialist it needs.

**Pipeline**
1. **Request** in any of 26 languages
2. **Language ID** (milliseconds)
3. **Router:** Clef-flash 9B decision model, 4-bit; one forward pass gives P(yes) for each expert gateway
4. **Active gateways only:** notes · flashcards · quiz · podcast · video · code
5. **Specialists:** Qwen3.5-4B generator · offline Wikipedia/LibreTexts (Kiwix) · Supertonic/MMS speech · Manim/HyperFrames video · Graphviz concept maps · PhET simulations · Typst PDF handout

**Edge constraints built in**
- Only **one** large model in RAM at a time: router about 6.6 GB, then generator about 3.3 GB
- Fully offline after setup; no GPU and no cloud API

**Example:** "Explain Ohm's law with a short quiz and a short video" activated 3 of 6 gateways and 10 specialists. Everything else stayed unloaded.

---

# Modules Developed

| Module | Status | Evidence |
|---|---|---|
| Literature review and gap analysis | Complete | Review paper ready for submission |
| System architecture and router design | Complete | Router → gateways → specialists, with a memory plan for 8 GB |
| Expert network and router | Implemented | Clef-flash routing; 6 gateways; 26 languages |
| Sparse dispatch pipeline | Under integration | Only selected experts run; every stage timed |
| Cache/memory-aware execution layer | Under integration | One resident model; prompt-prefix reuse across experts |
| Raspberry Pi deployment environment | Established | Pi install script ready; same stack verified on Google Cloud |
| Needle router evaluation | Evaluated | As reported in PPR-I |

**Engineering quality:** about 8,600 lines of Python · 51 automated tests · independent code review with its findings fixed · full technical documentation

---

# Results So Far: Theory vs Reality

Measured on a 4-core x86 CPU; Raspberry Pi 5 numbers come next.

| Measurement | Result |
|---|---|
| Router decision (one forward pass, about 900 tokens) | 33 s on 4 threads |
| Generator throughput (4B, 4-bit) | about 7 tokens/s |
| Compact-output decoding (our optimisation) | generation time **−42 %** (English), **−18 %** (Kannada) |
| Speculative decoding, 0.8B draft model | 71 % of drafts accepted, yet **slower**: 6.2 vs 7.0 tokens/s |
| Multilingual retrieval fix | Kannada lessons now cite Kannada sources (3 Kannada + 2 English passages) |

**Key insight, which is the project's research question in miniature:** 71 % acceptance looks like a speed-up (about 9 tokens/s on paper), but verifying a 5-token batch costs 2.2× a single step on this CPU. The realised result was a slowdown. Theoretical savings must be measured, not assumed.

---

# Deployment and Research Outputs

**Working, measurable system**
- One request produces notes, quiz, flashcards, a two-voice podcast, a narrated video, code, a concept map, interactive simulations and a printable handout
- Phone-friendly web interface showing the router's decision and which experts were activated vs available
- Every lesson logs stage timings, model swaps, tokens, RAM and CPU temperature: the raw data for the theory-vs-reality study

**Deployment**
- Raspberry Pi 5 install script for Pi-only, offline operation (Pi benchmarks next)
- Cloud installer verified on Google Cloud (8 vCPU) for demos and large experiments
- Login protection, safe model lifecycle, sandboxed simulations

**Papers**
- Review paper: complete, awaiting submission
- Research paper: in progress

---

# Difficulties and Next Steps

**Difficulties**
- Turning theoretical sparsity into real latency gains: model swaps and prefill eat into the savings
- Runtime dispatch overhead and memory tuning in 8 GB
- Router stability under quantization
- Router reliability: one observed miss, where a quiz explicitly requested in Kannada scored P = 0.30 and was skipped
- Reliable on-device energy measurement

**Next steps**
1. Benchmark on the Raspberry Pi 5: router latency, swap cost, tokens/s
2. Measure energy per lesson with a USB power meter
3. Dense (all experts) vs routed comparison over a fixed request set: theoretical vs realised savings
4. Quantization study: router accuracy vs speed (4-bit vs 8-bit)
5. Finish and submit the research paper

**Thank you. Questions?**
