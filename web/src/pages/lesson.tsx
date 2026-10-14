import * as React from "react";
import { toast } from "sonner";
import {
  AlertTriangleIcon, ArrowLeftIcon, BookOpenIcon, CheckIcon, CircleIcon, CodeIcon, CopyIcon, DownloadIcon, ExternalLinkIcon,
  FileTextIcon, FlaskConicalIcon, GaugeIcon, HeadphonesIcon, LayersIcon, LibraryIcon, ListChecksIcon, LoaderCircleIcon,
  MaximizeIcon, RotateCcwIcon, SendIcon, VideoIcon, XIcon,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AudioPlayer } from "@/components/ui/audio-player";
import { Empty, Inline, PageHeader, RichText } from "@/components/page";
import { LessonMenu } from "@/components/lesson-card";
import { RouteBars } from "@/components/route-bars";
import { useDialogs } from "@/components/dialogs";
import { api } from "@/lib/api";
import { columnOf, sectionsOf } from "@/lib/board";
import { STAGE_LABELS, ago, dateShort, duration } from "@/lib/format";
import { go, useStore } from "@/lib/store";
import type { Lesson, LessonDetail, LiveEvent } from "@/lib/types";
import { cn } from "@/lib/utils";

// pdf.js is large: load the viewer only when a handout tab is opened
const PdfViewer = React.lazy(() => import("@/components/ui/pdf-viewer").then((m) => ({ default: m.PdfViewer })));

/* ------------------------------------------------------------------------------------------- */
/* live progress (Server-Sent Events from the job's event log)                                  */

type StageState = { state: "run" | "ok" | "err"; secs?: number; error?: string };
interface Live { stages: Record<string, StageState>; route: LiveEvent | null; loads: number; llm: number; tokens: number }
const LIVE0: Live = { stages: {}, route: null, loads: 0, llm: 0, tokens: 0 };

function useLive(id: string, active: boolean, onEnd: () => void) {
  const [live, setLive] = React.useState<Live>(LIVE0);
  const end = React.useRef(onEnd);
  end.current = onEnd;
  React.useEffect(() => {
    if (!active) return;
    setLive(LIVE0);
    const es = new EventSource(api.events(id));
    es.onmessage = (m) => {
      let ev: LiveEvent;
      try { ev = JSON.parse(m.data); } catch { return; }
      if (ev.kind === "eof") { es.close(); end.current(); return; }
      setLive((cur) => {
        const next = { ...cur, stages: { ...cur.stages } };
        if (ev.kind === "stage_start" && ev.stage) next.stages[ev.stage] = { state: "run" };
        if (ev.kind === "stage_end" && ev.stage) next.stages[ev.stage] = { state: ev.ok ? "ok" : "err", secs: ev.seconds, error: ev.error };
        if (ev.kind === "route_decision") next.route = ev;
        if (ev.kind === "model_load") next.loads += 1;
        if (ev.kind === "llm_call") { next.llm += 1; next.tokens += Number(ev.predicted_n || 0); }
        return next;
      });
    };
    es.onerror = () => { /* the browser reconnects; polling covers the rest */ };
    return () => es.close();
  }, [id, active]);
  return live;
}

function StageList({ stages }: { stages: Record<string, StageState> }) {
  const entries = Object.entries(stages);
  if (!entries.length) return <p className="text-body-sm text-slate">Waiting for the first stage…</p>;
  return (
    <ol className="flex flex-col">
      {entries.map(([name, s]) => (
        <li key={name} className="flex items-center gap-3 border-b border-mist py-2.5 text-body-sm last:border-0">
          {s.state === "run" ? <LoaderCircleIcon className="size-4 animate-spin text-google-blue" />
            : s.state === "ok" ? <CheckIcon className="size-4 text-classroom-green" /> : <XIcon className="size-4 text-danger" />}
          <span className={cn(s.state === "run" ? "font-medium text-charcoal-ink" : "text-graphite")}>{STAGE_LABELS[name] || name.replace(/_/g, " ")}</span>
          <span className={cn("ml-auto truncate text-caption tabular-nums", s.state === "err" ? "text-danger" : "text-slate")}>
            {s.state === "run" ? "running…" : s.state === "ok" ? duration(s.secs) : s.error || "failed"}
          </span>
        </li>
      ))}
    </ol>
  );
}

function Progress({ detail, live }: { detail: LessonDetail; live: Live }) {
  const { config, lessons } = useStore();
  const job = detail.job;
  const ahead = lessons.filter((l) => l.status === "queued" && l.created < job.created).length
    + lessons.filter((l) => l.status === "running").length;
  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_380px]">
      <Card>
        <CardHeader>
          <CardTitle>{job.status === "queued" ? "Waiting in the queue" : "Generating"}</CardTitle>
          <CardDescription>
            {job.status === "queued" ? `${ahead} job${ahead === 1 ? "" : "s"} ahead. One lesson is generated at a time so a single large model fits in RAM.`
              : `Started ${ago(job.started)}. Stages stream in as they finish.`}
          </CardDescription>
        </CardHeader>
        <CardContent><StageList stages={live.stages} /></CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="text-subheading">Router decision</CardTitle>
          <CardDescription>Clef-flash, P(yes) per gateway</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-5">
          {live.route ? <RouteBars probs={live.route.gateways as Record<string, number>} selected={live.route.selected as string[]} threshold={config?.threshold ?? 0.5} />
            : <p className="text-body-sm text-slate">{job.options?.mode === "manual" ? "Manual mode: the router is skipped." : "Not routed yet."}</p>}
          <div className="grid grid-cols-3 gap-2 text-center">
            {[["model loads", live.loads], ["LLM calls", live.llm], ["tokens", live.tokens]].map(([k, v]) => (
              <div key={k as string} className="rounded-2xl bg-canvas-white p-3">
                <div className="font-display text-heading-sm tabular-nums">{v}</div>
                <div className="text-caption text-slate">{k}</div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------------------------------- */
/* artefact tabs                                                                                */

function DownloadLink({ id, file, children }: { id: string; file: string; children: React.ReactNode }) {
  return <Button variant="outline" size="sm" asChild><a href={api.file(id, file)} download={file.split("/").pop()}><DownloadIcon /> {children}</a></Button>;
}

function NotesTab({ id, lesson }: { id: string; lesson: Lesson }) {
  const n = lesson.notes!;
  const cmap = lesson.concept_map;
  return (
    <div className="grid items-start gap-6 xl:grid-cols-[1fr_360px]">
      <Card>
        <CardContent className="flex flex-col gap-6 p-6 sm:p-32">
          {n.sections.map((s, i) => (
            <section key={i} className="flex flex-col gap-2">
              <h3 className="font-display text-heading-sm text-charcoal-ink">{s.heading}</h3>
              <RichText text={s.body} />
            </section>
          ))}
          <div><DownloadLink id={id} file="notes.md">Markdown</DownloadLink></div>
        </CardContent>
      </Card>
      <div className="flex flex-col gap-6">
        {!!n.key_points?.length && (
          <div className="rounded-3xl bg-green-tint p-6">
            <div className="mb-3 font-display text-subheading text-charcoal-ink">Key points</div>
            <ul className="flex flex-col gap-2.5">
              {n.key_points.map((k, i) => (
                <li key={i} className="flex gap-3 text-body-sm text-graphite">
                  <CheckIcon className="mt-0.5 size-4 shrink-0 text-classroom-green" /><span><Inline text={k} /></span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {cmap?.svg && (
          <Card className="overflow-hidden">
            <a href={api.file(id, cmap.svg)} target="_blank" rel="noopener" className="block bg-white p-4 hover:bg-canvas-white">
              <img src={api.file(id, cmap.svg)} alt="Concept map of the lesson" className="mx-auto max-h-[320px] w-full object-contain" loading="lazy" />
            </a>
            <div className="flex items-center justify-between border-t border-mist px-5 py-3 text-caption text-slate">
              Concept map · {cmap.nodes} ideas, {cmap.edges} links
              <a className="text-google-blue hover:underline" href={api.file(id, cmap.svg)} download>SVG</a>
            </div>
          </Card>
        )}
        {!!n.glossary?.length && (
          <Card>
            <CardContent>
              <div className="mb-3 font-display text-subheading">Glossary</div>
              <dl className="flex flex-col gap-3">
                {n.glossary.map((g) => (
                  <div key={g.term}>
                    <dt className="text-body-sm font-medium text-charcoal-ink">{g.term}</dt>
                    <dd className="text-body-sm text-slate">{g.definition}</dd>
                  </div>
                ))}
              </dl>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}

function FlashcardsTab({ id, lesson }: { id: string; lesson: Lesson }) {
  const cards = lesson.flashcards?.cards || [];
  const [flipped, setFlipped] = React.useState<Set<number>>(new Set());
  const toggle = (i: number) => setFlipped((s) => { const n = new Set(s); if (n.has(i)) n.delete(i); else n.add(i); return n; });
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-body-sm text-slate">Tap a card to flip it. {flipped.size}/{cards.length} revealed.</p>
        <div className="flex gap-2">
          {flipped.size > 0 && <Button variant="secondary" size="sm" onClick={() => setFlipped(new Set())}><RotateCcwIcon /> Reset</Button>}
          <DownloadLink id={id} file="flashcards_anki.tsv">Anki (TSV)</DownloadLink>
        </div>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {cards.map((c, i) => (
          <button key={i} type="button" className="flip h-[192px] text-left" data-flipped={flipped.has(i)} onClick={() => toggle(i)}
            aria-label={flipped.has(i) ? `Answer: ${c.back}` : `Card ${i + 1}: ${c.front}`}>
            <div className="flip-inner relative size-full">
              <div className="flip-face absolute inset-0 flex flex-col justify-between rounded-3xl border border-mist bg-white p-5">
                <span className="text-caption text-slate">Card {i + 1}</span>
                <span className="font-display text-subheading leading-snug text-charcoal-ink">{c.front}</span>
                <span className="text-caption text-google-blue">Show answer</span>
              </div>
              <div className="flip-face flip-back absolute inset-0 flex flex-col justify-center overflow-y-auto rounded-3xl bg-panel-tint p-5 text-body text-charcoal-ink">
                {c.back}
              </div>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

const LETTERS = "ABCDEFGH";
function QuizTab({ lesson }: { lesson: Lesson }) {
  const qs = lesson.quiz?.questions || [];
  const [picked, setPicked] = React.useState<Record<number, number>>({});
  const [key, setKey] = React.useState(false);
  const score = qs.filter((q, i) => picked[i] === q.answer_index).length;
  const answered = Object.keys(picked).length;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-body-sm text-slate">
          Try it as a student would. {answered > 0 && <span className="text-charcoal-ink">Score {score} / {qs.length}{answered === qs.length ? " · done" : ""}</span>}
        </p>
        <div className="flex gap-2">
          {answered > 0 && <Button variant="secondary" size="sm" onClick={() => setPicked({})}><RotateCcwIcon /> Reset</Button>}
          <Button variant={key ? "dark" : "secondary"} size="sm" onClick={() => setKey(!key)}><ListChecksIcon /> Answer key</Button>
        </div>
      </div>
      {qs.map((q, i) => {
        const done = i in picked || key;
        return (
          <Card key={i}>
            <CardContent className="flex flex-col gap-4">
              <div className="flex gap-3">
                <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-panel-tint text-caption font-medium text-google-blue-deep">{i + 1}</span>
                <p className="pt-0.5 text-body text-charcoal-ink">{q.question}</p>
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                {q.options.map((o, oi) => {
                  const right = done && oi === q.answer_index;
                  const wrong = picked[i] === oi && oi !== q.answer_index;
                  return (
                    <button key={oi} type="button" disabled={i in picked}
                      onClick={() => setPicked({ ...picked, [i]: oi })}
                      className={cn("flex items-center gap-3 rounded-2xl border px-4 py-3 text-left text-body-sm transition-colors",
                        right ? "border-classroom-green bg-green-tint text-charcoal-ink" : wrong ? "border-danger bg-red-tint" : "border-mist hover:bg-canvas-white",
                        i in picked && !right && !wrong && "opacity-60")}>
                      <span className={cn("flex size-6 shrink-0 items-center justify-center rounded-full border text-caption",
                        right ? "border-classroom-green bg-classroom-green text-white" : wrong ? "border-danger bg-danger text-white" : "border-mist text-slate")}>{LETTERS[oi]}</span>
                      {o}
                    </button>
                  );
                })}
              </div>
              {done && q.explanation && <p className="rounded-2xl bg-canvas-white px-4 py-3 text-body-sm text-graphite">{q.explanation}</p>}
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}

function PodcastTab({ id, lesson }: { id: string; lesson: Lesson }) {
  const p = lesson.podcast!;
  const audio = lesson.podcast_audio;
  const [t, setT] = React.useState(0);
  const timing = audio?.turns || [];
  const current = timing.findIndex((x) => x.start_s != null && x.end_s != null && t >= x.start_s && t < x.end_s);
  return (
    <div className="grid items-start gap-6 xl:grid-cols-[420px_1fr]">
      <div className="flex flex-col gap-3">
        {audio?.audio ? (
          <AudioPlayer src={api.file(id, audio.audio)} title={p.title || "Podcast"} onTime={setT}
            subtitle={`${duration(audio.duration_s)} · two voices · ${audio.engine || "offline TTS"}`} />
        ) : (
          <div className="rounded-3xl bg-yellow-tint p-5 text-body-sm text-graphite">
            No audio: there is no offline voice for this language on this node, so only the script is shown.
            {lesson.errors?.podcast_audio && <div className="mt-2 text-caption text-slate">{lesson.errors.podcast_audio}</div>}
          </div>
        )}
        <p className="px-1 text-caption text-slate">Two hosts explain the topic in conversation. Recorded on this node, no internet used.</p>
      </div>
      <Card>
        <CardContent className="flex flex-col gap-4">
          {p.turns.map((turn, i) => {
            const a = turn.speaker === "A";
            return (
              <div key={i} className={cn("flex gap-3 rounded-2xl p-2 transition-colors", current === i && "bg-panel-tint")}>
                <span className={cn("flex size-32 shrink-0 items-center justify-center rounded-full text-caption font-medium",
                  a ? "bg-purple-tint text-[#9334e6]" : "bg-green-tint text-classroom-green")}>{a ? "A" : "B"}</span>
                <div>
                  <div className="text-caption text-slate">Host {a ? "A" : "B"}</div>
                  <p className="text-body text-graphite">{turn.text}</p>
                </div>
              </div>
            );
          })}
        </CardContent>
      </Card>
    </div>
  );
}

function VideoTab({ id, lesson }: { id: string; lesson: Lesson }) {
  const v = lesson.video!;
  const r = lesson.video_render;
  return (
    <div className="grid items-start gap-6 xl:grid-cols-[1fr_380px]">
      <div className="flex flex-col gap-3">
        {r?.video ? (
          <div className="overflow-hidden rounded-3xl border border-mist bg-black">
            <video controls preload="metadata" className="aspect-video w-full" src={api.file(id, r.video)}>
              {r.subtitles && <track kind="subtitles" default src={api.file(id, r.subtitles)} label={lesson.language?.name || "Subtitles"} srcLang={lesson.language?.lang} />}
            </video>
          </div>
        ) : (
          <div className="flex aspect-video flex-col items-center justify-center gap-2 rounded-3xl border border-dashed border-mist bg-white p-6 text-center">
            <VideoIcon className="size-32 text-slate" />
            <div className="font-display text-subheading">Video not rendered</div>
            <p className="max-w-md text-body-sm text-slate">{lesson.errors?.video_render || lesson.errors?.video || "No video engine is installed on this node. The scene plan is on the right."}</p>
          </div>
        )}
        {r?.video && (
          <div className="flex flex-wrap items-center gap-2 text-caption text-slate">
            <Badge variant="gray">{r.engine === "hyperframes" ? "HyperFrames" : r.engine === "manim" ? "Manim" : r.engine}</Badge>
            {duration(r.duration_s)} · rendered in {duration(r.render_s)} · subtitles included
            <span className="ml-auto flex gap-2">
              <DownloadLink id={id} file={r.video}>MP4</DownloadLink>
              {r.srt && <DownloadLink id={id} file={r.srt}>SRT</DownloadLink>}
            </span>
          </div>
        )}
      </div>
      <Card>
        <CardHeader><CardTitle className="text-subheading">Scene plan</CardTitle><CardDescription>{v.beats.length} scenes, each from a tested template</CardDescription></CardHeader>
        <CardContent>
          <ol className="flex flex-col gap-4">
            {v.beats.map((b, i) => (
              <li key={i} className="flex gap-3">
                <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-[#f1f3f4] text-caption text-graphite">{i + 1}</span>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2 text-body-sm font-medium text-charcoal-ink">
                    {b.title}<Badge variant="outline">{b.template.replace(/_/g, " ")}</Badge>
                    {!!r?.beats?.[i]?.errors?.length && <Badge variant="yellow">fallback</Badge>}
                  </div>
                  {b.narration && <p className="mt-1 text-body-sm text-slate">{b.narration}</p>}
                </div>
              </li>
            ))}
          </ol>
        </CardContent>
      </Card>
    </div>
  );
}

function CodeTab({ id, lesson }: { id: string; lesson: Lesson }) {
  const c = lesson.code!;
  const copy = async () => { try { await navigator.clipboard.writeText(c.code); toast("Code copied"); } catch { toast.error("Copy is blocked here: select the code instead"); } };
  return (
    <div className="grid items-start gap-6 xl:grid-cols-[1fr_380px]">
      <div className="flex flex-col overflow-hidden rounded-3xl border border-mist bg-white">
        <div className="flex items-center justify-between border-b border-mist px-5 py-2.5">
          <span className="text-caption text-slate">example.py · Python</span>
          <div className="flex gap-1">
            <Button variant="ghost" size="sm" onClick={copy}><CopyIcon /> Copy</Button>
            <Button variant="ghost" size="sm" asChild><a href={api.file(id, "example.py")} download><DownloadIcon /> .py</a></Button>
          </div>
        </div>
        <pre className="overflow-x-auto bg-[#f8f9fa] p-5 font-mono text-[13px] leading-relaxed text-charcoal-ink scrollbar-thin"><code>{c.code}</code></pre>
        {c.expected_output && (
          <div className="border-t border-mist">
            <div className="px-5 pt-3 text-caption text-slate">Expected output</div>
            <pre className="overflow-x-auto px-5 pt-1 pb-4 font-mono text-[13px] text-graphite">{c.expected_output}</pre>
          </div>
        )}
      </div>
      <Card>
        <CardHeader><CardTitle className="text-subheading">{c.title || "How it works"}</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-4">
          <RichText text={c.explanation || ""} className="text-body-sm" />
          <p className="rounded-2xl bg-yellow-tint px-4 py-3 text-caption text-graphite">Generated code is never executed on the node. Read it before running it.</p>
        </CardContent>
      </Card>
    </div>
  );
}

function SimulationsTab({ lesson }: { lesson: Lesson }) {
  const sims = lesson.simulations || [];
  const [open, setOpen] = React.useState(sims[0]?.id);
  const cur = sims.find((s) => s.id === open) || sims[0];
  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {sims.map((s) => (
          <button key={s.id} onClick={() => setOpen(s.id)}
            className={cn("flex items-center gap-4 rounded-3xl border bg-white p-3 text-left transition-colors", cur?.id === s.id ? "border-google-blue" : "border-mist hover:bg-canvas-white")}>
            {s.thumbnail ? <img src={api.zim(s.zim, s.thumbnail)} alt="" className="h-[64px] w-[96px] shrink-0 rounded-2xl object-cover" loading="lazy" />
              : <div className="flex h-[64px] w-[96px] shrink-0 items-center justify-center rounded-2xl bg-canvas-white"><FlaskConicalIcon className="size-6 text-slate" /></div>}
            <div className="min-w-0">
              <div className="truncate text-body-sm font-medium text-charcoal-ink">{s.title}</div>
              <div className="truncate text-caption text-slate">{(s.categories || []).slice(0, 2).join(" · ") || s.zim}</div>
            </div>
          </button>
        ))}
      </div>
      {cur && (
        <div className="overflow-hidden rounded-3xl border border-mist bg-white">
          <div className="flex items-center justify-between gap-2 border-b border-mist px-5 py-2.5">
            <span className="truncate text-body-sm font-medium">{cur.title}</span>
            <Button variant="ghost" size="sm" asChild><a href={api.zim(cur.zim, cur.path)} target="_blank" rel="noopener"><MaximizeIcon /> Full screen</a></Button>
          </div>
          <iframe key={cur.id} src={api.zim(cur.zim, cur.path)} sandbox="allow-scripts" allow="fullscreen" title={cur.title} className="aspect-[16/10] w-full bg-white" />
        </div>
      )}
      <p className="text-caption text-slate">PhET Interactive Simulations, University of Colorado Boulder (phet.colorado.edu), CC BY 4.0. Served from the offline library on this node, in a sandbox.</p>
    </div>
  );
}

function SourcesTab({ lesson }: { lesson: Lesson }) {
  const src = lesson.sources || [];
  if (!src.length) return <Empty icon={LibraryIcon} title="No offline sources">Nothing relevant was found in the installed knowledge archives, so the content relies on the model's own knowledge. Check facts before posting.</Empty>;
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      {src.map((s, i) => (
        <Card key={i}>
          <CardContent className="flex flex-col gap-2">
            <div className="flex items-start gap-3">
              <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-panel-tint text-caption text-google-blue-deep">{i + 1}</span>
              <div className="min-w-0 flex-1">
                <div className="text-body-sm font-medium text-charcoal-ink">{s.title}</div>
                <div className="mt-0.5 flex flex-wrap gap-1.5">
                  <Badge variant={s.zim?.startsWith("gatemoe_delta") ? "purple" : "gray"}>{s.zim?.startsWith("gatemoe_delta") ? "KDD delta" : s.zim}</Badge>
                  {s.lang && <Badge variant="outline">{s.lang}</Badge>}
                </div>
              </div>
              {s.zim && s.path && (
                <Button variant="ghost" size="icon-sm" asChild><a href={api.zim(s.zim, s.path)} target="_blank" rel="noopener" aria-label="Read offline"><ExternalLinkIcon /></a></Button>
              )}
            </div>
            {s.excerpt && <p className="line-clamp-4 text-body-sm text-slate">{s.excerpt}</p>}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function Kv({ rows }: { rows: [string, React.ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-body-sm">
      {rows.filter(([, v]) => v != null && v !== "").map(([k, v]) => (
        <React.Fragment key={k}><dt className="text-slate">{k}</dt><dd className="text-right text-charcoal-ink tabular-nums">{v}</dd></React.Fragment>
      ))}
    </dl>
  );
}

function RoutingTab({ id, lesson }: { id: string; lesson: Lesson }) {
  const { config } = useStore();
  const r = lesson.route;
  const m = lesson.metrics;
  const sp = lesson.specialists;
  const stages = Object.entries(m?.stage_seconds || {});
  const maxStage = Math.max(1, ...stages.map(([, v]) => v));
  const files: [string, string][] = [];
  if (lesson.handout) files.push([lesson.handout.pdf, "Handout (PDF)"]);
  if (lesson.notes) files.push(["notes.md", "Notes (Markdown)"]);
  if (lesson.flashcards) files.push(["flashcards_anki.tsv", "Flashcards (Anki TSV)"]);
  if (lesson.podcast_audio) files.push([lesson.podcast_audio.audio, "Podcast audio"]);
  if (lesson.video_render) files.push([lesson.video_render.video, "Video (MP4)"]);
  files.push(["lesson.json", "Everything as JSON, incl. routing and metrics"]);
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader><CardTitle className="text-subheading">Routing</CardTitle><CardDescription>{r?.mode === "manual" ? "Gateways picked by hand" : r?.mode === "all" ? "Every gateway (router bypassed)" : "Clef-flash decision model"}</CardDescription></CardHeader>
        <CardContent className="flex flex-col gap-6">
          {r?.mode === "clef" && <RouteBars probs={r.gateways} selected={r.selected} threshold={config?.threshold ?? 0.5} />}
          <Kv rows={[["Subject", r?.subject], ["Level", r?.level], ["Video engine", r?.video_engine],
            ["In scope", r?.in_scope != null ? r.in_scope.toFixed(2) : null], ["Router input tokens", r?.input_tokens],
            ["Router time", r?.latency_s != null ? duration(r.latency_s) : null], ["Router cache", r?.cached ? "hit (identical request)" : null]]} />
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle className="text-subheading">Conditional computation</CardTitle>
          <CardDescription>{sp ? `${sp.activated.length} of ${sp.available.filter((s) => s.available).length} installed specialists activated` : "Resources used"}</CardDescription></CardHeader>
        <CardContent className="flex flex-col gap-6">
          <Kv rows={[["Total time", duration(m?.wall_seconds)], ["Model loads", m ? `${m.model_loads} (${duration(m.model_load_seconds)})` : null],
            ["Peak model RAM", m?.model_peak_rss_mb ? `${(m.model_peak_rss_mb / 1024).toFixed(2)} GB` : null],
            ["LLM calls", m?.llm_calls], ["Prompt tokens", m ? `${m.prompt_tokens}${m.cached_prompt_tokens ? ` (${m.cached_prompt_tokens} cached)` : ""}` : null],
            ["Generated tokens", m?.generated_tokens], ["Lowest free RAM", m?.min_mem_available_mb != null ? `${(m.min_mem_available_mb / 1024).toFixed(2)} GB` : null],
            ["Max CPU temperature", m?.max_temp_c != null ? `${m.max_temp_c.toFixed(0)} °C` : null], ["Throttling", m?.throttled_seen ? "yes: timings affected" : null]]} />
          {sp && <div className="flex flex-wrap gap-1.5">{sp.activated.map((a) => <Badge key={a} variant="outline">{a}</Badge>)}</div>}
        </CardContent>
      </Card>
      {stages.length > 0 && (
        <Card>
          <CardHeader><CardTitle className="text-subheading">Where the time went</CardTitle></CardHeader>
          <CardContent className="flex flex-col gap-2">
            {stages.map(([k, v]) => (
              <div key={k} className="grid grid-cols-[150px_1fr_84px] items-center gap-3 text-body-sm">
                <span className="truncate text-graphite">{STAGE_LABELS[k] || k}</span>
                <div className="h-2 rounded-full bg-[#f1f3f4]"><div className="h-full rounded-full bg-google-blue" style={{ width: `${Math.max(1, (100 * v) / maxStage)}%` }} /></div>
                <span className="text-right text-caption whitespace-nowrap text-slate tabular-nums">{duration(v)}</span>
              </div>
            ))}
          </CardContent>
        </Card>
      )}
      <Card>
        <CardHeader><CardTitle className="text-subheading">Files</CardTitle></CardHeader>
        <CardContent className="flex flex-col">
          {files.map(([f, label]) => (
            <a key={f} href={api.file(id, f)} download className="flex items-center gap-3 border-b border-mist py-2.5 text-body-sm text-graphite last:border-0 hover:text-google-blue">
              <FileTextIcon className="size-4 text-slate" />{label}<span className="ml-auto text-caption text-slate">{f}</span>
            </a>
          ))}
          {lesson.errors && Object.keys(lesson.errors).length > 0 && (
            <div className="mt-4 flex flex-col gap-2 rounded-2xl bg-red-tint p-4">
              <div className="flex items-center gap-2 text-body-sm font-medium text-danger"><AlertTriangleIcon className="size-4" /> Issues</div>
              {Object.entries(lesson.errors).map(([k, v]) => <p key={k} className="text-caption text-graphite"><b className="font-medium">{k}:</b> {v}</p>)}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------------------------------- */

export function LessonPage({ id }: { id: string }) {
  const { lessons, classroom, refreshLessons } = useStore();
  const dialogs = useDialogs();
  const [detail, setDetail] = React.useState<LessonDetail | null>(null);
  const [missing, setMissing] = React.useState(false);
  const load = React.useCallback(async () => {
    try { setDetail(await api.lesson(id)); } catch { setMissing(true); }
  }, [id]);
  const status = detail?.job.status;
  const active = status === "queued" || status === "running";
  React.useEffect(() => { void load(); }, [load]);
  React.useEffect(() => {                       // poll while the job is waiting or running (status changes)
    if (!active) return;
    const t = window.setInterval(load, 3000);
    return () => window.clearInterval(t);
  }, [active, load]);
  const live = useLive(id, status === "running", () => { void load(); void refreshLessons(); });

  if (missing) return <Empty icon={BookOpenIcon} title="Lesson not found" action={<Button variant="outline" onClick={() => go("/")}>Back to lessons</Button>} />;
  if (!detail) return <div className="flex items-center gap-2 py-20 text-slate"><LoaderCircleIcon className="size-4 animate-spin" /> Loading…</div>;

  const { job, lesson } = detail;
  const summary = lessons.find((l) => l.id === id) || job;
  const title = lesson?.notes?.title || lesson?.plan?.title || job.request;
  const posted = sectionsOf(id, classroom);
  const col = columnOf({ ...summary, board: classroom.lesson_meta[id]?.status ?? summary.board });
  const tabs: { id: string; label: string; icon: React.ComponentType<{ className?: string }>; show: boolean }[] = lesson ? [
    { id: "notes", label: "Notes", icon: BookOpenIcon, show: !!lesson.notes },
    { id: "flashcards", label: "Flashcards", icon: LayersIcon, show: !!lesson.flashcards?.cards?.length },
    { id: "quiz", label: "Quiz", icon: ListChecksIcon, show: !!lesson.quiz?.questions?.length },
    { id: "podcast", label: "Podcast", icon: HeadphonesIcon, show: !!lesson.podcast },
    { id: "video", label: "Video", icon: VideoIcon, show: !!lesson.video },
    { id: "code", label: "Code", icon: CodeIcon, show: !!lesson.code },
    { id: "handout", label: "Handout", icon: FileTextIcon, show: !!lesson.handout?.pdf },
    { id: "sims", label: "Simulations", icon: FlaskConicalIcon, show: !!lesson.simulations?.length },
    { id: "sources", label: "Sources", icon: LibraryIcon, show: !!lesson.sources },
    { id: "routing", label: "How it was made", icon: GaugeIcon, show: true },
  ].filter((t) => t.show) : [];

  return (
    <>
      <a href="#/" className="inline-flex items-center gap-2 self-start rounded-full px-3 py-1.5 -ml-3 text-body-sm text-slate hover:bg-white hover:text-charcoal-ink">
        <ArrowLeftIcon className="size-4" /> Lessons
      </a>
      <PageHeader title={title}
        description={title !== job.request ? job.request : undefined}
        actions={<>
          {active && <Button variant="outline" onClick={async () => { await api.cancel(id); await load(); }}><CircleIcon /> Cancel</Button>}
          {lesson?.handout?.pdf && <Button variant="outline" asChild><a href={api.file(id, lesson.handout.pdf)} download><DownloadIcon /> Handout</a></Button>}
          {status === "done" && !lesson?.out_of_scope && <Button onClick={() => dialogs.openAssign(id)}><SendIcon /> Post to sections</Button>}
          <LessonMenu lesson={summary} />
        </>}>
        <div className="flex flex-wrap items-center gap-2 text-caption">
          <Badge variant={col === "published" ? "green" : col === "review" ? "yellow" : col === "generating" ? "default" : "gray"}>
            {status === "failed" ? "Failed" : status === "cancelled" ? "Cancelled" : status === "interrupted" ? "Interrupted" :
              { queued: "Queued", generating: "Generating", review: "Ready for review", published: "Published", archived: "Archived" }[col]}
          </Badge>
          {lesson?.language && <Badge variant="outline">{lesson.language.native && lesson.language.native !== lesson.language.name ? `${lesson.language.name} · ${lesson.language.native}` : lesson.language.name}</Badge>}
          {lesson?.route?.subject && lesson.route.subject !== "other" && <Badge variant="outline">{lesson.route.subject}</Badge>}
          {lesson?.route?.level && <Badge variant="outline">{lesson.route.level}</Badge>}
          <span className="text-slate">Created {dateShort(job.created)}{lesson?.metrics ? ` · generated in ${duration(lesson.metrics.wall_seconds)}` : ""}</span>
          {posted.length > 0 && (
            <span className="ml-auto flex flex-wrap items-center gap-1.5 text-slate">
              Posted to
              {posted.map((s) => (
                <a key={s.id} href={`#/grading/section/${s.id}`} className="inline-flex items-center gap-1.5 rounded-full border border-mist bg-white px-2.5 py-0.5 text-graphite hover:border-google-blue">
                  <span className="size-2 rounded-full" style={{ background: s.color }} />{s.name}
                </a>
              ))}
            </span>
          )}
        </div>
      </PageHeader>

      {job.status === "failed" && <div className="rounded-3xl bg-red-tint p-5 text-body-sm text-danger">{job.error || "Generation failed."}</div>}
      {lesson?.out_of_scope && (
        <div className="rounded-3xl bg-yellow-tint p-5 text-body-sm text-graphite">
          The router judged this request out of scope for a tutor, so nothing was generated. Rephrase it as a learning topic.
        </div>
      )}
      {active && <Progress detail={detail} live={live} />}
      {lesson && tabs.length > 0 && !lesson.out_of_scope && (
        <Tabs defaultValue={tabs[0].id}>
          <TabsList>
            {tabs.map((t) => <TabsTrigger key={t.id} value={t.id}><t.icon />{t.label}</TabsTrigger>)}
          </TabsList>
          <TabsContent value="notes">{lesson.notes && <NotesTab id={id} lesson={lesson} />}</TabsContent>
          <TabsContent value="flashcards"><FlashcardsTab id={id} lesson={lesson} /></TabsContent>
          <TabsContent value="quiz"><QuizTab lesson={lesson} /></TabsContent>
          <TabsContent value="podcast">{lesson.podcast && <PodcastTab id={id} lesson={lesson} />}</TabsContent>
          <TabsContent value="video">{lesson.video && <VideoTab id={id} lesson={lesson} />}</TabsContent>
          <TabsContent value="code">{lesson.code && <CodeTab id={id} lesson={lesson} />}</TabsContent>
          <TabsContent value="handout">
            {lesson.handout?.pdf && (
              <React.Suspense fallback={<div className="flex items-center gap-2 p-12 text-slate"><LoaderCircleIcon className="size-4 animate-spin" /> Loading the PDF viewer…</div>}>
                <PdfViewer src={api.file(id, lesson.handout.pdf)} fileName={`${title}.pdf`} />
              </React.Suspense>
            )}
          </TabsContent>
          <TabsContent value="sims"><SimulationsTab lesson={lesson} /></TabsContent>
          <TabsContent value="sources"><SourcesTab lesson={lesson} /></TabsContent>
          <TabsContent value="routing"><RoutingTab id={id} lesson={lesson} /></TabsContent>
        </Tabs>
      )}
    </>
  );
}
