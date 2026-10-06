"use strict";
// GateMoE UI: vanilla JS, no build step. All model output is inserted with textContent.

const $ = (s) => document.querySelector(s);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k != null) n.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return n;
};
const api = async (path, opts = {}) => {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) throw new Error((await r.text()) || r.statusText);
  return r.json();
};
const fmtS = (s) => (s == null ? "–" : s < 60 ? `${s.toFixed(1)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`);

let CONFIG = null, CURRENT = null, SOURCE = null, POLL = null, STAGES = {}, LIVE = { loads: [], llm: [] };

// ---------- setup ----------
async function init() {
  CONFIG = await api("/api/config");
  const sel = $("#language");
  for (const [code, l] of Object.entries(CONFIG.languages)) sel.append(el("option", { value: code, text: `${l.native} (${l.name})` }));
  $("#mode").value = CONFIG.router_mode || "clef";
  for (const g of CONFIG.gateways) {
    $("#gwboxes").append(el("label", {}, el("input", { type: "checkbox", value: g, checked: "" }), " ", g));
  }
  $("#mode").addEventListener("change", () => ($("#manual").hidden = $("#mode").value !== "manual"));
  $("#form").addEventListener("submit", submit);
  $("#cancel").addEventListener("click", async () => { if (CURRENT) await api(`/api/lessons/${CURRENT}/cancel`, { method: "POST" }); });
  refreshJobs(); refreshSys();
  setInterval(refreshJobs, 5000); setInterval(refreshSys, 5000);
  const m = location.hash.match(/^#lesson=([a-f0-9]{12})$/);
  if (m) openLesson(m[1]);
}

async function submit(e) {
  e.preventDefault();
  const body = { request: $("#request").value.trim(), language: $("#language").value, mode: $("#mode").value };
  if (!body.request) return;
  if (body.mode === "manual") body.gateways = [...document.querySelectorAll("#gwboxes input:checked")].map((i) => i.value);
  $("#go").disabled = true;
  try { const job = await api("/api/lessons", { method: "POST", body: JSON.stringify(body) }); openLesson(job.id); refreshJobs(); }
  catch (err) { alert(err.message); }
  finally { $("#go").disabled = false; }
}

async function refreshSys() {
  try {
    const s = await api("/api/system");
    const box = $("#sys"); box.replaceChildren();
    const loaded = s.models && s.models.loaded ? s.models.loaded : "none";
    box.append(el("span", { text: `model: ${loaded}` }), el("span", { text: `RAM free: ${(s.sys.mem_available_mb / 1024).toFixed(1)} GB` }));
    if (s.sys.temp_c != null) box.append(el("span", { text: `CPU ${s.sys.temp_c.toFixed(0)} °C` }));
    if (s.sys.throttled && s.sys.throttled !== "0x0") box.append(el("span", { class: "err", text: `throttled ${s.sys.throttled}` }));
    box.append(el("span", { text: `queue: ${s.queue}` }));
  } catch { /* server busy */ }
}

async function refreshJobs() {
  const jobs = await api("/api/lessons").catch(() => []);
  const ul = $("#jobs"); ul.replaceChildren();
  if (!jobs.length) ul.append(el("li", { class: "muted", text: "No lessons yet." }));
  for (const j of jobs.slice(0, 20)) {
    ul.append(el("li", { onclick: () => openLesson(j.id) },
      el("span", { class: "req", text: j.request }), el("span", { class: `badge ${j.status}`, text: j.status })));
  }
}

// ---------- lesson view ----------
async function openLesson(id) {
  CURRENT = id; location.hash = `lesson=${id}`;
  STAGES = {}; LIVE = { loads: [], llm: [] };
  if (SOURCE) SOURCE.close(); if (POLL) clearInterval(POLL);
  $("#lesson").hidden = false; $("#stages").replaceChildren(); $("#tabs").replaceChildren(); $("#tabbody").replaceChildren();
  $("#route").replaceChildren(document.createTextNode("Waiting for the router…")); $("#cc").textContent = "—";
  const data = await api(`/api/lessons/${id}`);
  showHeader(data.job, data.lesson);
  if (data.lesson) renderLesson(data.lesson);
  if (["queued", "running"].includes(data.job.status) || !data.lesson) follow(id);
  $("#lesson").scrollIntoView({ behavior: "smooth" });
}

function showHeader(job, lesson) {
  $("#l-title").textContent = (lesson && lesson.plan && lesson.plan.title) || "Lesson";
  $("#l-req").textContent = job.request;
  const b = $("#l-status"); b.className = `badge ${job.status}`; b.textContent = job.status + (job.error ? `: ${job.error}` : "");
  $("#cancel").hidden = !["queued", "running"].includes(job.status);
}

function follow(id) {
  SOURCE = new EventSource(`/api/lessons/${id}/events`);
  SOURCE.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.kind === "eof") { SOURCE.close(); finish(id); return; }
    onEvent(ev);
  };
  SOURCE.onerror = () => { SOURCE.close(); POLL = setInterval(async () => {
    const d = await api(`/api/lessons/${id}`).catch(() => null);
    if (d && !["queued", "running"].includes(d.job.status)) { clearInterval(POLL); finish(id); }
  }, 4000); };
}

async function finish(id) {
  if (id !== CURRENT) return;
  const d = await api(`/api/lessons/${id}`);
  showHeader(d.job, d.lesson);
  if (d.lesson) renderLesson(d.lesson);
  refreshJobs();
}

function onEvent(ev) {
  if (ev.kind === "stage_start") STAGES[ev.stage] = { state: "run", t: ev.t };
  if (ev.kind === "stage_end") STAGES[ev.stage] = { state: ev.ok ? "ok" : "err", secs: ev.seconds, error: ev.error };
  if (ev.kind === "route_decision") renderRoute({ mode: "clef", ...ev });
  if (ev.kind === "model_load" || ev.kind === "model_unload") LIVE.loads.push(ev);
  if (ev.kind === "llm_call") LIVE.llm.push(ev);
  if (ev.kind === "job_start") { $("#l-status").textContent = "running"; $("#l-status").className = "badge running"; }
  renderStages(); renderLiveCC();
}

function renderStages() {
  const ol = $("#stages"); ol.replaceChildren();
  for (const [name, s] of Object.entries(STAGES)) {
    const right = s.state === "run" ? "running…" : s.state === "ok" ? fmtS(s.secs) : `failed ${s.error || ""}`;
    ol.append(el("li", {}, el("span", { text: name.replace(/_/g, " ") }), el("span", { class: s.state, text: right })));
  }
}

function renderRoute(r) {
  const box = $("#route"); box.replaceChildren(); box.classList.remove("muted");
  const thr = CONFIG.threshold ?? 0.5;
  for (const g of CONFIG.gateways) {
    const p = (r.gateways && r.gateways[g]) || 0;
    const on = (r.selected || []).includes(g);
    const track = el("div", { class: "track" }, el("div", { class: "fill", style: `width:${(p * 100).toFixed(1)}%` }),
      el("div", { class: "thr", style: `left:${thr * 100}%` }));
    box.append(el("div", { class: `gw ${on ? "on" : ""}` }, el("span", { class: "name", text: `${on ? "▶ " : ""}${g}` }), track,
      el("span", { text: p.toFixed(2) })));
  }
  const dl = el("dl", { class: "kv" });
  const add = (k, v) => v != null && v !== "" && dl.append(el("dt", { text: k }), el("dd", { text: String(v) }));
  add("mode", r.mode); add("subject", r.subject); add("level", r.level); add("video engine", r.video_engine);
  add("in scope", r.in_scope != null ? r.in_scope.toFixed(2) : null);
  add("router tokens", r.input_tokens); add("router time", r.latency_s != null ? fmtS(r.latency_s) : null);
  if (r.cached) add("cache", "memo hit (identical request)");
  if (r.note) add("note", r.note);
  box.append(dl);
}

function renderLiveCC() {
  const loads = LIVE.loads.filter((e) => e.kind === "model_load");
  const gen = LIVE.llm.reduce((a, e) => a + (e.predicted_n || 0), 0);
  const box = $("#cc"); box.replaceChildren(); box.classList.remove("muted");
  const dl = el("dl", { class: "kv" });
  dl.append(el("dt", { text: "model loads" }), el("dd", { text: `${loads.length} (${fmtS(loads.reduce((a, e) => a + e.seconds, 0))})` }));
  dl.append(el("dt", { text: "LLM calls" }), el("dd", { text: `${LIVE.llm.length}, ${gen} tokens generated` }));
  box.append(dl);
}

function renderCC(lesson) {
  const box = $("#cc"); box.replaceChildren(); box.classList.remove("muted");
  const sp = lesson.specialists || { available: [], activated: [] };
  const avail = sp.available.filter((s) => s.available).length;
  box.append(el("div", {}, el("span", { class: "big", text: `${sp.activated.length}` }), ` of ${avail} installed specialists activated`));
  const m = lesson.metrics || {};
  const dl = el("dl", { class: "kv" });
  const add = (k, v) => v != null && dl.append(el("dt", { text: k }), el("dd", { text: String(v) }));
  add("total time", fmtS(m.wall_seconds)); add("model loads", `${m.model_loads} (${fmtS(m.model_load_seconds)})`);
  add("peak model RAM", m.model_peak_rss_mb ? `${(m.model_peak_rss_mb / 1024).toFixed(2)} GB` : null);
  add("prompt tokens", `${m.prompt_tokens} (${m.cached_prompt_tokens} from cache)`); add("generated tokens", m.generated_tokens);
  add("min free RAM", m.min_mem_available_mb != null ? `${(m.min_mem_available_mb / 1024).toFixed(2)} GB` : null);
  add("max CPU temp", m.max_temp_c != null ? `${m.max_temp_c.toFixed(0)} °C` : null);
  if (m.throttled_seen) add("throttling", "yes – timings affected");
  box.append(dl, el("div", { class: "chips" }, sp.activated.map((a) => el("span", { class: "chip", text: a }))));
}

// ---------- artefact tabs ----------
function renderLesson(lesson) {
  if (lesson.route) renderRoute(lesson.route);
  renderCC(lesson);
  if (lesson.metrics && lesson.metrics.stage_seconds) {
    for (const [k, v] of Object.entries(lesson.metrics.stage_seconds)) STAGES[k] = { state: "ok", secs: v };
    renderStages();
  }
  const tabs = [];
  if (lesson.notes) tabs.push(["Notes", () => notesView(lesson.notes)]);
  if (lesson.flashcards) tabs.push(["Flashcards", () => cardsView(lesson.flashcards.cards || [])]);
  if (lesson.quiz) tabs.push(["Quiz", () => quizView(lesson.quiz.questions || [])]);
  if (lesson.podcast) tabs.push(["Podcast", () => podcastView(lesson.podcast, lesson.podcast_audio)]);
  if (lesson.video) tabs.push(["Video", () => videoView(lesson.video, lesson.video_render)]);
  if (lesson.code) tabs.push(["Code", () => codeView(lesson.code)]);
  if (lesson.sources) tabs.push(["Sources", () => sourcesView(lesson.sources)]);
  tabs.push(["Downloads", () => downloadsView(lesson)]);
  if (lesson.errors && Object.keys(lesson.errors).length) tabs.push(["Issues", () => issuesView(lesson.errors)]);
  const nav = $("#tabs"); nav.replaceChildren();
  const show = (i) => {
    [...nav.children].forEach((b, j) => b.setAttribute("aria-selected", String(i === j)));
    $("#tabbody").replaceChildren(tabs[i][1]());
  };
  tabs.forEach(([name], i) => nav.append(el("button", { role: "tab", text: name, onclick: () => show(i) })));
  if (tabs.length) show(0);
}

function inline(text) { // **bold** only; everything else stays text
  const frag = document.createDocumentFragment();
  String(text).split(/(\*\*[^*]+\*\*)/g).forEach((part) => {
    if (/^\*\*[^*]+\*\*$/.test(part)) frag.append(el("strong", { text: part.slice(2, -2) }));
    else if (part) frag.append(document.createTextNode(part));
  });
  return frag;
}
function mdBlock(body) {
  const wrap = el("div"); let list = null;
  for (const raw of String(body).split("\n")) {
    const line = raw.trim();
    if (/^[-*•]\s+/.test(line)) { if (!list) { list = el("ul"); wrap.append(list); } list.append(el("li", {}, inline(line.replace(/^[-*•]\s+/, "")))); }
    else { list = null; if (line) wrap.append(el("p", {}, inline(line.replace(/^#+\s*/, "")))); }
  }
  return wrap;
}
const fileUrl = (name) => `/api/lessons/${CURRENT}/files/${encodeURIComponent(name).replace(/%2F/g, "/")}`;

function notesView(n) {
  const d = el("div", { class: "notes" }, el("h3", { text: n.title || "" }));
  for (const s of n.sections || []) d.append(el("h4", { text: s.heading }), mdBlock(s.body));
  if ((n.key_points || []).length) d.append(el("h4", { text: "Key points" }), el("ul", {}, n.key_points.map((k) => el("li", {}, inline(k)))));
  if ((n.glossary || []).length) {
    const dl = el("dl", { class: "kv" }); n.glossary.forEach((g) => dl.append(el("dt", { text: g.term }), el("dd", { text: g.definition })));
    d.append(el("h4", { text: "Glossary" }), dl);
  }
  d.append(el("p", {}, el("a", { href: fileUrl("notes.md"), download: "notes.md", text: "Download Markdown" })));
  return d;
}
function cardsView(cards) {
  const g = el("div", { class: "cards" });
  for (const c of cards) {
    const card = el("div", { class: "flash", tabindex: "0", role: "button" }, el("div", { class: "front", text: c.front }), el("div", { class: "back", text: c.back }));
    const flip = () => card.classList.toggle("flipped");
    card.addEventListener("click", flip); card.addEventListener("keydown", (e) => (e.key === "Enter" || e.key === " ") && flip());
    g.append(card);
  }
  return el("div", {}, el("p", { class: "muted", text: "Tap a card to flip it." }), g,
    el("p", {}, el("a", { href: fileUrl("flashcards_anki.tsv"), download: "flashcards.tsv", text: "Export for Anki (TSV)" })));
}
function quizView(qs) {
  const box = el("div"); let score = 0, answered = 0;
  const total = el("p", { class: "muted", text: `Score: 0 / ${qs.length}` });
  qs.forEach((q, qi) => {
    const exp = el("p", { class: "exp" }); const card = el("div", { class: "q" }, el("p", {}, el("b", { text: `${qi + 1}. ` }), q.question));
    const buttons = q.options.map((o, oi) => el("button", { class: "opt", type: "button", text: o, onclick: () => {
      if (card.dataset.done) return; card.dataset.done = "1"; answered++;
      buttons[q.answer_index].classList.add("right"); if (oi !== q.answer_index) buttons[oi].classList.add("wrong"); else score++;
      exp.textContent = q.explanation; total.textContent = `Score: ${score} / ${qs.length}` + (answered === qs.length ? " – done!" : "");
    } }));
    card.append(...buttons, exp); box.append(card);
  });
  box.append(total); return box;
}
function podcastView(p, audio) {
  const d = el("div", {}, el("h3", { text: p.title || "Podcast" }));
  if (audio && audio.audio) d.append(el("audio", { controls: "", preload: "metadata", src: fileUrl(audio.audio) }),
    el("p", { class: "muted", text: `${fmtS(audio.duration_s)} · voice engine: ${audio.engine || "?"}` }));
  else d.append(el("p", { class: "muted", text: "No audio (no offline voice for this language) – script only." }));
  for (const t of p.turns || []) d.append(el("p", { class: "turn" }, el("b", { text: t.speaker === "A" ? "Host A: " : "Host B: " }), t.text));
  return d;
}
function videoView(v, r) {
  const d = el("div", {}, el("h3", { text: v.title || "Video" }));
  if (r && r.video) {
    const vid = el("video", { controls: "", preload: "metadata", src: fileUrl(r.video) });
    if (r.subtitles) vid.append(el("track", { kind: "subtitles", default: "", src: fileUrl(r.subtitles) }));
    d.append(vid, el("p", { class: "muted", text: `${fmtS(r.duration_s)} · ${(r.beats || []).length} beats` }));
  } else d.append(el("p", { class: "muted", text: "Video not rendered – scene plan below." }));
  (v.beats || []).forEach((b, i) => d.append(el("p", { class: "turn" }, el("b", { text: `${i + 1}. [${b.template}] ${b.title} – ` }), b.narration)));
  return d;
}
function codeView(c) {
  return el("div", {}, el("h3", { text: c.title || "Code" }), mdBlock(c.explanation || ""), el("pre", { text: c.code || "" }),
    c.expected_output ? el("div", {}, el("h4", { text: "Expected output" }), el("pre", { text: c.expected_output })) : null,
    el("p", { class: "muted", text: "Generated code is not executed on the Pi. Read it before running it." }));
}
function sourcesView(src) {
  if (!src.length) return el("p", { class: "muted", text: "No offline sources were found; content relies on the model's own knowledge." });
  return el("div", {}, src.map((s, i) => el("div", { class: "src" }, el("b", { text: `[${i + 1}] ${s.title || ""}` }), ` · ${s.zim || ""}`, el("p", { class: "muted", text: s.excerpt || "" }))));
}
function downloadsView(l) {
  const items = [];
  if (l.handout) items.push(["handout.pdf", "Printable handout (PDF): notes, flashcards, quiz + answer key"]);
  if (l.notes) items.push(["notes.md", "Notes (Markdown)"]);
  if (l.flashcards) items.push(["flashcards_anki.tsv", "Flashcards for Anki (TSV)"]);
  if (l.podcast_audio) items.push([l.podcast_audio.audio, "Podcast audio"]);
  if (l.video_render) items.push([l.video_render.video, "Video (MP4)"], [l.video_render.srt, "Subtitles (SRT)"]);
  if (l.code) items.push(["example.py", "Python example"]);
  items.push(["lesson.json", "Everything as JSON (incl. routing + metrics)"]);
  return el("ul", {}, items.map(([f, label]) => el("li", {}, el("a", { href: fileUrl(f), download: f, text: label }))));
}
function issuesView(errs) {
  return el("div", {}, Object.entries(errs).map(([k, v]) => el("p", {}, el("b", { text: `${k}: ` }), el("span", { class: "err", text: v }))));
}

init();
