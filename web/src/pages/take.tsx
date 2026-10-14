/* Student view of a posted lesson (#/take/<assignment>): pick your name, study, take the quiz,
   see the auto-marked result with explanations. The answer key never reaches this page. */
import * as React from "react";
import { toast } from "sonner";
import { ArrowLeftIcon, CheckIcon, CircleCheckIcon, FileTextIcon, LoaderCircleIcon, SearchIcon, VideoIcon, XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { AudioPlayer } from "@/components/ui/audio-player";
import { Logo } from "@/components/app-sidebar";
import { Empty, Inline } from "@/components/page";
import { api } from "@/lib/api";
import { initials } from "@/lib/format";
import type { Submission, TakeView } from "@/lib/types";
import { cn } from "@/lib/utils";

const LETTERS = "ABCDEFGH";

export function TakePage({ id }: { id: string }) {
  const [view, setView] = React.useState<TakeView | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [who, setWho] = React.useState<string | null>(null);
  const [answers, setAnswers] = React.useState<(number | null)[]>([]);
  const [result, setResult] = React.useState<(Submission & { explanations: string[] }) | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [q, setQ] = React.useState("");
  const load = React.useCallback(() => api.take(id).then(setView).catch((e) => setError((e as Error).message)), [id]);
  React.useEffect(() => { void load(); }, [load]);

  if (error) return <div className="mx-auto max-w-xl p-32"><Empty title="This link does not work">{error}</Empty></div>;
  if (!view) return <div className="flex items-center justify-center gap-2 p-20 text-slate"><LoaderCircleIcon className="size-4 animate-spin" /> Loading…</div>;
  const student = view.students.find((s) => s.id === who);
  const file = (name: string) => api.file(view.lesson_id, name);
  const answered = answers.filter((a) => a != null).length;
  const submit = async () => {
    if (!who) return;
    setBusy(true);
    try {
      setResult(await api.submit({ assignment_id: id, student_id: who, answers }));
      window.scrollTo(0, 0);
      void load();
    } catch (e) { toast.error((e as Error).message); } finally { setBusy(false); }
  };
  const reset = () => { setWho(null); setResult(null); setAnswers([]); setQ(""); };

  return (
    <div className="min-h-full">
      <header className="sticky top-0 z-20 border-b border-mist bg-white">
        <div className="mx-auto flex max-w-3xl items-center gap-3 px-4 py-3">
          <Logo className="size-32" />
          <div className="min-w-0">
            <div className="truncate font-display text-subheading leading-tight">{view.title}</div>
            <div className="flex flex-wrap items-center gap-2 text-caption text-slate">
              {view.sections.map((s) => <span key={s.id} className="inline-flex items-center gap-1"><span className="size-2 rounded-full" style={{ background: s.color }} />{s.name}</span>)}
              {view.assignment.due && <span>· due {view.assignment.due}</span>}
            </div>
          </div>
          {student && <span className="ml-auto flex items-center gap-2 text-body-sm text-graphite">
            <span className="flex size-32 items-center justify-center rounded-full bg-panel-tint text-caption font-medium text-google-blue-deep">{initials(student.name)}</span>
            <span className="hidden sm:inline">{student.name}</span>
          </span>}
        </div>
      </header>

      <main className="mx-auto flex max-w-3xl flex-col gap-6 px-4 py-32">
        {!who && (
          <>
            <div>
              <h1 className="font-display text-heading">Who are you?</h1>
              <p className="mt-1 text-body-sm text-slate">Choose your name to start.{view.assignment.note ? ` Your teacher says: “${view.assignment.note}”` : ""}</p>
            </div>
            {view.students.length > 12 && (
              <div className="relative">
                <SearchIcon className="pointer-events-none absolute top-1/2 left-4 size-4 -translate-y-1/2 text-slate" />
                <Input className="h-11 rounded-full pl-11" placeholder="Find your name" value={q} onChange={(e) => setQ(e.target.value)} autoFocus />
              </div>
            )}
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              {view.students.filter((s) => s.name.toLowerCase().includes(q.trim().toLowerCase())).map((s) => (
                <button key={s.id} onClick={() => { setWho(s.id); setAnswers(view.questions.map(() => null)); }}
                  className="flex items-center gap-3 rounded-3xl border border-mist bg-white p-4 text-left hover:border-google-blue">
                  <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-[#f1f3f4] text-body-sm font-medium text-graphite">{initials(s.name)}</span>
                  <span className="min-w-0">
                    <span className="block truncate text-body-sm text-charcoal-ink">{s.name}</span>
                    {s.submitted && <span className="flex items-center gap-1 text-caption text-classroom-green"><CheckIcon className="size-3" /> Submitted</span>}
                  </span>
                </button>
              ))}
            </div>
            {view.students.length === 0 && <Empty title="No students in these sections">Ask your teacher to add you to the section.</Empty>}
          </>
        )}

        {who && !result && (
          <>
            {(view.key_points.length > 0 || Object.keys(view.files).length > 0) && (
              <Card>
                <CardContent className="flex flex-col gap-4">
                  <div className="font-display text-subheading">Study first</div>
                  {view.key_points.length > 0 && (
                    <ul className="flex flex-col gap-2">
                      {view.key_points.map((k, i) => <li key={i} className="flex gap-3 text-body-sm text-graphite"><CheckIcon className="mt-0.5 size-4 shrink-0 text-classroom-green" /><span><Inline text={k} /></span></li>)}
                    </ul>
                  )}
                  {view.files.podcast && <AudioPlayer src={file(view.files.podcast)} title="Lesson podcast" subtitle="Two hosts explain the topic" />}
                  {view.files.video && (
                    <details className="rounded-3xl border border-mist">
                      <summary className="flex cursor-pointer items-center gap-2 px-5 py-3 text-body-sm text-graphite"><VideoIcon className="size-4" /> Watch the video</summary>
                      <video controls preload="none" className="aspect-video w-full rounded-b-3xl bg-black" src={file(view.files.video)}>
                        {view.files.subtitles && <track kind="subtitles" default src={file(view.files.subtitles)} />}
                      </video>
                    </details>
                  )}
                  <div className="flex flex-wrap gap-2">
                    {view.files.handout && <Button variant="outline" size="sm" asChild><a href={file(view.files.handout)} target="_blank" rel="noopener"><FileTextIcon /> Handout (PDF)</a></Button>}
                    {view.files.notes && <Button variant="outline" size="sm" asChild><a href={file(view.files.notes)} target="_blank" rel="noopener"><FileTextIcon /> Notes</a></Button>}
                  </div>
                </CardContent>
              </Card>
            )}
            <div className="sticky top-[65px] z-10 -mx-4 flex items-center gap-4 bg-canvas-white/95 px-4 py-3 backdrop-blur">
              <Progress value={view.questions.length ? (100 * answered) / view.questions.length : 0} className="flex-1" />
              <span className="text-caption text-slate tabular-nums">{answered} / {view.questions.length}</span>
            </div>
            {view.questions.map((qq, i) => (
              <Card key={i}>
                <CardContent className="flex flex-col gap-4">
                  <div className="flex gap-3">
                    <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-panel-tint text-caption font-medium text-google-blue-deep">{i + 1}</span>
                    <p className="pt-0.5 text-body text-charcoal-ink">{qq.question}</p>
                  </div>
                  <div role="radiogroup" className="grid gap-2 sm:grid-cols-2">
                    {qq.options.map((o, oi) => {
                      const on = answers[i] === oi;
                      return (
                        <button key={oi} role="radio" aria-checked={on} onClick={() => setAnswers(answers.map((a, j) => (j === i ? oi : a)))}
                          className={cn("flex items-center gap-3 rounded-2xl border px-4 py-3 text-left text-body-sm transition-colors",
                            on ? "border-google-blue bg-panel-tint text-charcoal-ink" : "border-mist hover:bg-canvas-white")}>
                          <span className={cn("flex size-6 shrink-0 items-center justify-center rounded-full border text-caption", on ? "border-google-blue bg-google-blue text-white" : "border-mist text-slate")}>{LETTERS[oi]}</span>
                          {o}
                        </button>
                      );
                    })}
                  </div>
                </CardContent>
              </Card>
            ))}
            {view.questions.length === 0 && <Empty title="This lesson has no quiz">Study the material above.</Empty>}
            <div className="flex items-center justify-between gap-3">
              <Button variant="ghost" onClick={reset}><ArrowLeftIcon /> Not you?</Button>
              {view.questions.length > 0 && (
                <Button size="lg" disabled={busy || answered === 0} onClick={submit}>
                  {answered < view.questions.length ? `Submit (${view.questions.length - answered} unanswered)` : "Submit"}
                </Button>
              )}
            </div>
            {student?.submitted && <p className="text-center text-caption text-slate">You already submitted once; submitting again replaces your answers.</p>}
          </>
        )}

        {result && (
          <>
            <div className="flex flex-col items-center gap-3 rounded-3xl bg-green-tint px-6 py-10 text-center">
              <CircleCheckIcon className="size-10 text-classroom-green" />
              <div className="font-display text-heading-lg tabular-nums">{result.auto} / {result.max}</div>
              <p className="text-body-sm text-graphite">Submitted. {result.answers.filter((a, i) => a != null && a === result.correct[i]).length} of {result.answers.length} correct. Your teacher may add feedback.</p>
            </div>
            {view.questions.map((qq, i) => {
              const a = result.answers[i], c = result.correct[i], ok = a != null && a === c;
              return (
                <Card key={i}>
                  <CardContent className="flex flex-col gap-2">
                    <div className="flex gap-3">
                      <span className={cn("flex size-7 shrink-0 items-center justify-center rounded-full", ok ? "bg-green-tint text-classroom-green" : "bg-red-tint text-danger")}>
                        {ok ? <CheckIcon className="size-4" /> : <XIcon className="size-4" />}
                      </span>
                      <div className="min-w-0 text-body-sm">
                        <p className="text-body text-charcoal-ink">{qq.question}</p>
                        <p className="mt-1 text-slate">
                          {a == null ? "Not answered" : `Your answer: ${LETTERS[a]}. ${qq.options[a]}`}
                          {!ok && <> · Correct: {LETTERS[c]}. {qq.options[c]}</>}
                        </p>
                        {result.explanations[i] && <p className="mt-2 rounded-2xl bg-canvas-white px-4 py-3 text-graphite">{result.explanations[i]}</p>}
                      </div>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
            <Button variant="outline" className="self-center" onClick={reset}>Done</Button>
          </>
        )}
      </main>
    </div>
  );
}
