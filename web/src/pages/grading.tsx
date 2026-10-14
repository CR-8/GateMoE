import * as React from "react";
import { toast } from "sonner";
import { CheckIcon, ChevronLeftIcon, ChevronRightIcon, ClipboardCheckIcon, LinkIcon, MessageSquarePlusIcon, PencilIcon, Trash2Icon, XIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Empty, PageHeader, StatCard } from "@/components/page";
import { api } from "@/lib/api";
import { go, useStore } from "@/lib/store";
import { ago, dateShort, initials } from "@/lib/format";
import { lessonTitle } from "@/lib/board";
import type { Feedback, QuizQuestion, Section, Student, Submission } from "@/lib/types";
import { cn } from "@/lib/utils";

const pts = (p: number) => (p > 0 ? `+${p}` : `${p}`);
const LETTERS = "ABCDEFGH";

function PointsBadge({ p }: { p: number }) {
  return <Badge variant={p > 0 ? "green" : p < 0 ? "red" : "gray"} className="tabular-nums">{p === 0 ? "0 pts" : `${pts(p)} pts`}</Badge>;
}

function NewFeedback({ onSaved, compact }: { onSaved?: (f: Feedback) => void; compact?: boolean }) {
  const { refreshClassroom } = useStore();
  const [text, setText] = React.useState("");
  const [points, setPoints] = React.useState("0");
  const save = async () => {
    try {
      const f = await api.addFeedback({ text, points: Number(points) || 0 });
      await refreshClassroom();
      setText(""); setPoints("0");
      onSaved?.(f);
      toast("Feedback saved to the bank");
    } catch (e) { toast.error((e as Error).message); }
  };
  return (
    <div className={cn("flex flex-col gap-2", !compact && "rounded-2xl bg-canvas-white p-3")}>
      <Input placeholder="e.g. Show your working for full marks" value={text} onChange={(e) => setText(e.target.value)} maxLength={240}
        onKeyDown={(e) => { if (e.key === "Enter" && text.trim()) void save(); }} />
      <div className="flex items-center gap-2">
        <Label htmlFor={compact ? "fbp2" : "fbp"} className="text-caption font-normal text-slate">Grade points</Label>
        <Input id={compact ? "fbp2" : "fbp"} type="number" step="0.5" min={-100} max={100} className="h-9 w-20" value={points} onChange={(e) => setPoints(e.target.value)} />
        <Button variant="outline" size="sm" className="ml-auto" disabled={!text.trim()} onClick={save}><MessageSquarePlusIcon /> {onSaved ? "Save & apply" : "Save"}</Button>
      </div>
    </div>
  );
}

function FeedbackBank() {
  const { classroom, refreshClassroom } = useStore();
  const [edit, setEdit] = React.useState<string | null>(null);
  const [draft, setDraft] = React.useState({ text: "", points: "0" });
  const bank = [...classroom.feedback].sort((a, b) => b.uses - a.uses);
  const saveEdit = async (id: string) => {
    try { await api.updateFeedback(id, { text: draft.text, points: Number(draft.points) || 0 }); await refreshClassroom(); setEdit(null); }
    catch (e) { toast.error((e as Error).message); }
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-subheading">Feedback bank</CardTitle>
        <CardDescription>Write a comment once, reuse it on every submission. Points adjust the grade automatically.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <NewFeedback />
        <div className="flex flex-col">
          {bank.map((f) => edit === f.id ? (
            <div key={f.id} className="flex flex-col gap-2 border-b border-mist py-3 last:border-0">
              <Input value={draft.text} onChange={(e) => setDraft({ ...draft, text: e.target.value })} />
              <div className="flex items-center gap-2">
                <Input type="number" step="0.5" className="h-9 w-20" value={draft.points} onChange={(e) => setDraft({ ...draft, points: e.target.value })} />
                <Button variant="ghost" size="sm" className="ml-auto" onClick={() => setEdit(null)}>Cancel</Button>
                <Button variant="outline" size="sm" onClick={() => saveEdit(f.id)}>Save</Button>
              </div>
            </div>
          ) : (
            <div key={f.id} className="group flex items-start gap-3 border-b border-mist py-3 last:border-0">
              <div className="min-w-0 flex-1">
                <p className="text-body-sm text-charcoal-ink">{f.text}</p>
                <div className="mt-1 flex items-center gap-2"><PointsBadge p={f.points} /><span className="text-caption text-slate">used {f.uses}×</span></div>
              </div>
              <div className="flex opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                <Button variant="ghost" size="icon-sm" aria-label="Edit" onClick={() => { setEdit(f.id); setDraft({ text: f.text, points: String(f.points) }); }}><PencilIcon /></Button>
                <Button variant="ghost" size="icon-sm" aria-label="Delete" onClick={async () => { await api.deleteFeedback(f.id); await refreshClassroom(); }}><Trash2Icon /></Button>
              </div>
            </div>
          ))}
          {bank.length === 0 && <p className="py-2 text-caption text-slate">No saved feedback yet.</p>}
        </div>
      </CardContent>
    </Card>
  );
}

type Row = { student: Student; section?: Section; sub?: Submission };

function GradeSheet({ rows, index, setIndex, questions }:
  { rows: Row[]; index: number | null; setIndex: (i: number | null) => void; questions: QuizQuestion[] }) {
  const { classroom, refreshClassroom } = useStore();
  const row = index != null ? rows[index] : undefined;
  const sub = row?.sub;
  const [ids, setIds] = React.useState<string[]>([]);
  const [comment, setComment] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  React.useEffect(() => { setIds(sub?.feedback_ids || []); setComment(sub?.comment || ""); }, [sub?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!row || !sub) return null;
  const bank = classroom.feedback;
  const adjust = ids.reduce((t, id) => t + (bank.find((f) => f.id === id)?.points || 0), 0);
  const grade = Math.max(0, Math.min(sub.max, sub.auto + adjust));
  const nextUngraded = () => {
    for (let k = 1; k <= rows.length; k++) {
      const j = (index! + k) % rows.length;
      if (rows[j].sub && !rows[j].sub!.graded) return j;
    }
    return null;
  };
  const save = async (advance: boolean) => {
    setBusy(true);
    try {
      await api.grade(sub.id, { feedback_ids: ids, comment });
      await refreshClassroom();
      toast(`${row.student.name}: ${grade} / ${sub.max}`);
      setIndex(advance ? nextUngraded() : null);
    } catch (e) { toast.error((e as Error).message); } finally { setBusy(false); }
  };
  const step = (d: number) => {
    for (let k = 1; k < rows.length; k++) {
      const j = (index! + d * k + rows.length) % rows.length;
      if (rows[j].sub) { setIndex(j); return; }
    }
  };
  return (
    <Dialog open onOpenChange={(o) => !o && setIndex(null)}>
      <DialogContent side="right">
        <DialogHeader>
          <div className="flex items-center gap-3">
            <span className="flex size-10 items-center justify-center rounded-full bg-[#f1f3f4] text-body-sm font-medium text-graphite">{initials(row.student.name)}</span>
            <div>
              <DialogTitle>{row.student.name}</DialogTitle>
              <DialogDescription>{row.section?.name} · submitted {ago(sub.submitted)}{sub.graded ? ` · graded ${ago(sub.graded)}` : ""}</DialogDescription>
            </div>
          </div>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-6 py-4">
          <div className="flex items-center gap-4 rounded-3xl bg-panel-tint p-5">
            <div>
              <div className="font-display text-heading-lg leading-none text-charcoal-ink tabular-nums">{grade}<span className="text-heading-sm text-slate"> / {sub.max}</span></div>
              <div className="mt-2 text-caption text-graphite">Auto-marked {sub.auto}{adjust ? ` ${adjust > 0 ? "+" : "−"} feedback ${Math.abs(adjust)}` : ""}{sub.auto + adjust !== grade ? " (kept within 0 and max)" : ""}</div>
            </div>
            <div className="ml-auto text-right font-display text-heading text-google-blue-deep tabular-nums">{sub.max ? Math.round((100 * grade) / sub.max) : 0}%</div>
          </div>

          <section className="flex flex-col gap-2">
            <Label>Feedback bank <span className="font-normal text-slate">· tap to apply</span></Label>
            <div className="flex flex-wrap gap-2">
              {bank.map((f) => {
                const on = ids.includes(f.id);
                return (
                  <button key={f.id} type="button" aria-pressed={on} onClick={() => setIds(on ? ids.filter((x) => x !== f.id) : [...ids, f.id])}
                    className={cn("inline-flex max-w-full items-center gap-2 rounded-full border px-3 py-1.5 text-left text-body-sm transition-colors",
                      on ? "border-google-blue bg-panel-tint text-google-blue-deep" : "border-mist text-graphite hover:bg-canvas-white")}>
                    {on && <CheckIcon className="size-3.5 shrink-0" />}
                    <span className="truncate">{f.text}</span>
                    <span className={cn("shrink-0 text-caption tabular-nums", f.points > 0 ? "text-classroom-green" : f.points < 0 ? "text-danger" : "text-slate")}>{pts(f.points)}</span>
                  </button>
                );
              })}
              {bank.length === 0 && <p className="text-caption text-slate">Save feedback below; it stays in the bank for every other submission.</p>}
            </div>
            <NewFeedback compact onSaved={(f) => setIds((cur) => [...cur, f.id])} />
          </section>

          <section className="flex flex-col gap-2">
            <Label htmlFor="cm">Personal comment</Label>
            <Textarea id="cm" className="min-h-20" value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Only this student sees it" />
          </section>

          <section className="flex flex-col gap-2">
            <Label>Answers</Label>
            <ol className="flex flex-col gap-2">
              {sub.answers.map((a, i) => {
                const ok = a != null && a === sub.correct[i];
                const q = questions[i];
                return (
                  <li key={i} className="flex gap-3 rounded-2xl border border-mist p-3">
                    <span className={cn("flex size-6 shrink-0 items-center justify-center rounded-full", ok ? "bg-green-tint text-classroom-green" : "bg-red-tint text-danger")}>
                      {ok ? <CheckIcon className="size-3.5" /> : <XIcon className="size-3.5" />}
                    </span>
                    <div className="min-w-0 text-body-sm">
                      <p className="text-charcoal-ink">{q?.question || `Question ${i + 1}`}</p>
                      <p className="mt-1 text-caption text-slate">
                        Answered {a == null ? "nothing" : `${LETTERS[a]}${q ? `: ${q.options[a] ?? ""}` : ""}`}
                        {!ok && <> · correct {LETTERS[sub.correct[i]]}{q ? `: ${q.options[sub.correct[i]] ?? ""}` : ""}</>}
                      </p>
                    </div>
                  </li>
                );
              })}
            </ol>
          </section>
        </DialogBody>
        <DialogFooter className="border-t border-mist">
          <Button variant="ghost" size="icon" onClick={() => step(-1)} aria-label="Previous student"><ChevronLeftIcon /></Button>
          <Button variant="ghost" size="icon" onClick={() => step(1)} aria-label="Next student"><ChevronRightIcon /></Button>
          <Button variant="outline" className="ml-auto" disabled={busy} onClick={() => save(false)}>Save</Button>
          <Button disabled={busy} onClick={() => save(true)}>Save &amp; next</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function GradingPage({ assignmentId, sectionId }: { assignmentId?: string; sectionId?: string }) {
  const { classroom, lessons } = useStore();
  const [secFilter, setSecFilter] = React.useState<string>(sectionId || "all");
  const [open, setOpen] = React.useState<number | null>(null);
  const [questions, setQuestions] = React.useState<QuizQuestion[]>([]);
  React.useEffect(() => setSecFilter(sectionId || "all"), [sectionId]);

  const posts = [...classroom.assignments]
    .filter((a) => !sectionId || a.section_ids.includes(sectionId))
    .sort((a, b) => b.posted - a.posted);
  // default: the latest post that still has submissions waiting for review, else the latest post
  const waiting = (aid: string) => classroom.submissions.some((s) => s.assignment_id === aid && !s.graded);
  const current = posts.find((a) => a.id === assignmentId) || posts.find((a) => waiting(a.id)) || posts[0];
  const lessonOf = (lid: string) => lessons.find((l) => l.id === lid);
  React.useEffect(() => {
    setQuestions([]);
    if (current) api.lesson(current.lesson_id).then((d) => setQuestions(d.lesson?.quiz?.questions || [])).catch(() => {});
  }, [current?.lesson_id]); // eslint-disable-line react-hooks/exhaustive-deps

  const filterSection = classroom.sections.find((s) => s.id === sectionId);
  if (!current) {
    return (
      <>
        <PageHeader title={filterSection ? `Grading · ${filterSection.name}` : "Grading"} />
        <Empty icon={ClipboardCheckIcon} title="Nothing to grade yet" action={<Button variant="outline" onClick={() => go("/")}>Go to lessons</Button>}>
          Post a lesson with a quiz to a section. Students take it on any computer on the LAN; it is marked automatically and lands here.
        </Empty>
      </>
    );
  }
  const secs = classroom.sections.filter((s) => current.section_ids.includes(s.id));
  const shownSecs = secFilter === "all" ? secs : secs.filter((s) => s.id === secFilter);
  const seen = new Set<string>();
  const rows: Row[] = [];
  for (const s of shownSecs) for (const sid of s.student_ids) {
    if (seen.has(sid)) continue;
    seen.add(sid);
    const student = classroom.students.find((x) => x.id === sid);
    if (student) rows.push({ student, section: s, sub: classroom.submissions.find((x) => x.assignment_id === current.id && x.student_id === sid) });
  }
  rows.sort((a, b) => Number(!!b.sub && !b.sub.graded) - Number(!!a.sub && !a.sub.graded) || a.student.name.localeCompare(b.student.name));
  const submitted = rows.filter((r) => r.sub);
  const graded = submitted.filter((r) => r.sub!.graded);
  const avg = submitted.length ? Math.round((100 * submitted.reduce((t, r) => t + (r.sub!.max ? r.sub!.grade / r.sub!.max : 0), 0)) / submitted.length) : null;
  const link = `${window.location.origin}${window.location.pathname}#/take/${current.id}`;
  const copy = async () => { try { await navigator.clipboard.writeText(link); toast("Student link copied"); } catch { toast(link); } };
  const lesson = lessonOf(current.lesson_id);

  return (
    <>
      <PageHeader title={filterSection ? `Grading · ${filterSection.name}` : "Grading"}
        description="Quizzes are marked automatically from the answer key. Apply saved feedback, each with grade points, to adjust a grade in one tap."
        actions={<>
          <Button variant="outline" asChild><a href={`#/take/${current.id}`} target="_blank" rel="noopener">Student view</a></Button>
          <Button onClick={copy}><LinkIcon /> Copy student link</Button>
        </>}>
        <div className="flex flex-wrap items-center gap-3">
          <Select value={current.id} onValueChange={(v) => go(`/grading/${v}${sectionId ? `/${sectionId}` : ""}`)}>
            <SelectTrigger className="h-11 w-full max-w-md rounded-full px-5"><SelectValue /></SelectTrigger>
            <SelectContent>
              {posts.map((a) => {
                const l = lessonOf(a.lesson_id);
                return <SelectItem key={a.id} value={a.id}>{a.title || (l ? lessonTitle(l) : "Lesson")} · {dateShort(a.posted)}</SelectItem>;
              })}
            </SelectContent>
          </Select>
          <div className="flex flex-wrap gap-1.5">
            {secs.length > 1 && (
              <button onClick={() => setSecFilter("all")} className={cn("h-9 rounded-full border px-4 text-body-sm", secFilter === "all" ? "border-charcoal-ink bg-charcoal-ink text-white" : "border-mist bg-white text-graphite hover:bg-canvas-white")}>All sections</button>
            )}
            {secs.map((s) => (
              <button key={s.id} onClick={() => setSecFilter(s.id)}
                className={cn("inline-flex h-9 items-center gap-2 rounded-full border px-4 text-body-sm", secFilter === s.id || secs.length === 1 ? "border-charcoal-ink bg-charcoal-ink text-white" : "border-mist bg-white text-graphite hover:bg-canvas-white")}>
                <span className="size-2 rounded-full" style={{ background: s.color }} />{s.name}
              </button>
            ))}
          </div>
        </div>
      </PageHeader>

      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard tint="blue" label="Submitted" value={`${submitted.length} / ${rows.length}`} hint={current.due ? `due ${current.due}` : `posted ${ago(current.posted)}`} />
        <StatCard tint="green" label="Average grade" value={avg == null ? "–" : `${avg}%`} hint={`${questions.length || "–"} questions · 2 points each`} />
        <StatCard tint="yellow" label="Reviewed" value={`${graded.length} / ${submitted.length}`} hint={submitted.length - graded.length ? `${submitted.length - graded.length} waiting for your review` : "all caught up"} />
      </div>

      <div className="grid gap-6 xl:grid-cols-[1fr_360px]">
        <Card className="overflow-hidden">
          <div className="flex items-center justify-between gap-2 border-b border-mist px-5 py-3">
            <span className="truncate text-body-sm text-slate">{lesson ? <a className="text-google-blue hover:underline" href={`#/lesson/${lesson.id}`}>{lessonTitle(lesson)}</a> : "Lesson"}</span>
            {current.note && <span className="truncate text-caption text-slate">“{current.note}”</span>}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-body-sm">
              <thead className="text-caption text-slate">
                <tr className="border-b border-mist">{["Student", "Status", "Auto", "Feedback", "Grade"].map((h) => <th key={h} className="px-5 py-3 font-medium">{h}</th>)}</tr>
              </thead>
              <tbody>
                {rows.map((r, i) => {
                  const s = r.sub;
                  const fbs = (s?.feedback_ids || []).map((id) => classroom.feedback.find((f) => f.id === id)).filter(Boolean) as Feedback[];
                  return (
                    <tr key={r.student.id} onClick={() => s && setOpen(i)}
                      className={cn("border-b border-mist last:border-0", s ? "cursor-pointer hover:bg-canvas-white" : "text-slate")}>
                      <td className="px-5 py-3">
                        <div className="flex items-center gap-3">
                          <span className="flex size-32 shrink-0 items-center justify-center rounded-full bg-[#f1f3f4] text-caption font-medium text-graphite">{initials(r.student.name)}</span>
                          <div className="min-w-0">
                            <div className="whitespace-nowrap text-charcoal-ink">{r.student.name}</div>
                            {secs.length > 1 && r.section && <div className="flex items-center gap-1.5 whitespace-nowrap text-caption text-slate"><span className="size-1.5 rounded-full" style={{ background: r.section.color }} />{r.section.name}</div>}
                          </div>
                        </div>
                      </td>
                      <td className="px-5 py-3">
                        {!s ? <Badge variant="gray">Not submitted</Badge> : s.graded ? <Badge variant="green">Reviewed</Badge> : <Badge variant="yellow">Needs review</Badge>}
                      </td>
                      <td className="px-5 py-3 whitespace-nowrap tabular-nums">{s ? `${s.auto} / ${s.max}` : "–"}</td>
                      <td className="max-w-[260px] px-5 py-3">
                        <div className="flex flex-wrap gap-1">
                          {fbs.slice(0, 2).map((f) => <Badge key={f.id} variant={f.points < 0 ? "red" : f.points > 0 ? "green" : "gray"} className="max-w-[200px] truncate">{f.text}</Badge>)}
                          {fbs.length > 2 && <Badge variant="gray">+{fbs.length - 2}</Badge>}
                        </div>
                      </td>
                      <td className="px-5 py-3 font-medium whitespace-nowrap text-charcoal-ink tabular-nums">{s ? `${s.grade} / ${s.max}` : ""}</td>
                    </tr>
                  );
                })}
                {rows.length === 0 && <tr><td colSpan={5} className="px-5 py-10 text-center text-slate">No students in {secFilter === "all" ? "these sections" : "this section"} yet.</td></tr>}
              </tbody>
            </table>
          </div>
        </Card>
        <FeedbackBank />
      </div>
      <GradeSheet rows={rows} index={open} setIndex={setOpen} questions={questions} />
    </>
  );
}
