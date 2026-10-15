/* App-wide dialogs (one instance each), opened from anywhere through useDialogs():
   new lesson, post a lesson to sections (case study: post once to several sections),
   create / edit a section. */
import * as React from "react";
import { toast } from "sonner";
import { CheckIcon, SearchIcon, SparklesIcon, UserPlusIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { api } from "@/lib/api";
import { go, useStore } from "@/lib/store";
import { GATEWAY_META } from "@/lib/format";
import { lessonTitle } from "@/lib/board";
import { GATEWAYS, type Gateway, type Section } from "@/lib/types";
import { cn } from "@/lib/utils";


type Dialogs = { openNewLesson: () => void; openAssign: (lessonId: string) => void; openSection: (s?: Section) => void };
const Ctx = React.createContext<Dialogs>({ openNewLesson() {}, openAssign() {}, openSection() {} });
export const useDialogs = () => React.useContext(Ctx);

export function DialogsProvider({ children }: { children: React.ReactNode }) {
  const [newOpen, setNewOpen] = React.useState(false);
  const [assignId, setAssignId] = React.useState<string | null>(null);
  const [section, setSection] = React.useState<Section | null | undefined>(undefined);  // undefined = closed, null = new
  const value = React.useMemo<Dialogs>(() => ({
    openNewLesson: () => setNewOpen(true), openAssign: (id) => setAssignId(id), openSection: (s) => setSection(s ?? null),
  }), []);
  return (
    <Ctx.Provider value={value}>
      {children}
      <NewLessonDialog open={newOpen} onOpenChange={setNewOpen} />
      <AssignDialog lessonId={assignId} onClose={() => setAssignId(null)} />
      <SectionDialog section={section} onClose={() => setSection(undefined)} />
    </Ctx.Provider>
  );
}

function NewLessonDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const { config, lessons, refreshLessons } = useStore();
  // shortcuts: this node's own recent requests (nothing hard-coded)
  const recent = [...new Set(lessons.filter((l) => l.status === "done").map((l) => l.request.trim()))].slice(0, 4);
  const [request, setRequest] = React.useState("");
  const [language, setLanguage] = React.useState("auto");
  const [mode, setMode] = React.useState("clef");
  const [gws, setGws] = React.useState<Gateway[]>(["notes", "quiz"]);
  const [busy, setBusy] = React.useState(false);
  const [perLine, setPerLine] = React.useState(false);
  const lines = request.split("\n").map((s) => s.trim()).filter(Boolean);
  const many = perLine && lines.length > 1;
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!request.trim()) return;
    setBusy(true);
    const todo = many ? lines : [request];
    let queued = 0;
    let first = "";
    try {
      for (const r of todo) {        // the server queues them; one lesson is generated at a time
        const job = await api.create({ request: r, language, mode, gateways: mode === "manual" ? gws : null });
        first ||= job.id;
        queued += 1;
      }
    } catch (err) {
      toast.error(queued ? `${queued} queued, then: ${(err as Error).message}` : (err as Error).message);
    } finally {
      setBusy(false);
      await refreshLessons();
    }
    if (queued === todo.length) {
      onOpenChange(false);
      setRequest("");
      toast(queued > 1 ? `${queued} lessons queued` : "Lesson queued",
        queued === 1 ? { action: { label: "Open", onClick: () => go(`/lesson/${first}`) } } : undefined);
    } else if (queued) {
      setRequest(todo.slice(queued).join("\n"));     // keep the ones that were not accepted
    }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <form onSubmit={submit} className="flex min-h-0 flex-col">
          <DialogHeader>
            <DialogTitle>New lesson</DialogTitle>
            <DialogDescription>Describe what to teach, in any supported language. The router decides which artefacts to make.</DialogDescription>
          </DialogHeader>
          <DialogBody className="flex flex-col gap-5 pb-2">
            <div className="flex flex-col gap-2">
              <Label htmlFor="req">Request</Label>
              <Textarea id="req" autoFocus value={request} onChange={(e) => setRequest(e.target.value)} maxLength={2000}
                placeholder="e.g. Explain refraction of light for class 8, with flashcards and a podcast"
                onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void submit(e); }} />
              {lines.length > 1 && (
                <label className="flex cursor-pointer items-center gap-2 text-body-sm text-graphite">
                  <Checkbox checked={perLine} onCheckedChange={(c) => setPerLine(!!c)} />
                  One lesson per line <span className="text-slate">({lines.length} lessons, generated one after another)</span>
                </label>
              )}
              {recent.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-caption text-slate">Recent:</span>
                  {recent.map((x) => (
                    <button type="button" key={x} onClick={() => setRequest(x)}
                      className="max-w-full truncate rounded-full border border-mist px-3 py-1 text-caption text-graphite hover:border-google-blue hover:text-google-blue">{x}</button>
                  ))}
                </div>
              )}
            </div>
            <div className="grid gap-5 sm:grid-cols-2">
              <div className="flex flex-col gap-2">
                <Label>Language</Label>
                <Select value={language} onValueChange={setLanguage}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="auto">Detect from the request</SelectItem>
                    {Object.entries(config?.languages || {}).map(([k, v]) => (
                      <SelectItem key={k} value={k}>{v.name}{v.native && v.native !== v.name ? ` · ${v.native}` : ""}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="flex flex-col gap-2">
                <Label>Routing</Label>
                <ToggleGroup type="single" value={mode} onValueChange={(v) => v && setMode(v)}>
                  <ToggleGroupItem value="clef"><SparklesIcon /> Router</ToggleGroupItem>
                  <ToggleGroupItem value="all">All</ToggleGroupItem>
                  <ToggleGroupItem value="manual">Pick</ToggleGroupItem>
                </ToggleGroup>
              </div>
            </div>
            {mode === "manual" && (
              <div className="flex flex-wrap gap-2">
                {GATEWAYS.map((g) => {
                  const on = gws.includes(g);
                  return (
                    <button type="button" key={g} aria-pressed={on}
                      onClick={() => setGws(on ? gws.filter((x) => x !== g) : [...gws, g])}
                      className={cn("inline-flex h-32 items-center gap-1.5 rounded-full border px-3 text-body-sm",
                        on ? "border-google-blue bg-panel-tint text-google-blue-deep" : "border-mist text-graphite hover:bg-canvas-white")}>
                      {on && <CheckIcon className="size-3.5" />}{GATEWAY_META[g].label}
                    </button>
                  );
                })}
              </div>
            )}
            {mode === "clef" && (
              <p className="text-caption text-slate">
                Clef-flash scores every gateway; only those above the threshold ({config?.threshold ?? 0.5}) are generated, so the node does no unrequested work.
              </p>
            )}
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={busy || !request.trim() || (mode === "manual" && !gws.length)}>{many ? `Generate ${lines.length}` : "Generate"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function AssignDialog({ lessonId, onClose }: { lessonId: string | null; onClose: () => void }) {
  const { lessons, classroom, refreshClassroom, refreshLessons } = useStore();
  const dialogs = useDialogs();
  const lesson = lessons.find((l) => l.id === lessonId);
  const sections = classroom.sections.filter((s) => !s.archived);
  const [picked, setPicked] = React.useState<string[]>([]);
  const [title, setTitle] = React.useState("");
  const [due, setDue] = React.useState("");
  const [note, setNote] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  React.useEffect(() => {
    if (lesson) { setTitle(lessonTitle(lesson)); setPicked([]); setDue(""); setNote(""); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lessonId]);
  const already = new Set(classroom.assignments.filter((a) => a.lesson_id === lessonId).flatMap((a) => a.section_ids));
  const submit = async () => {
    if (!lessonId) return;
    setBusy(true);
    try {
      await api.assign({ lesson_id: lessonId, section_ids: picked, title, due: due || null, note });
      await Promise.all([refreshClassroom(), refreshLessons()]);
      toast(`Posted to ${picked.length} section${picked.length > 1 ? "s" : ""}`);
      onClose();
    } catch (err) { toast.error((err as Error).message); } finally { setBusy(false); }
  };
  const students = new Set(sections.filter((s) => picked.includes(s.id)).flatMap((s) => s.student_ids)).size;
  return (
    <Dialog open={!!lessonId} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Post to sections</DialogTitle>
          <DialogDescription>Post once; every selected section receives the lesson and its quiz.</DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-5">
          <div className="flex flex-col gap-2">
            <Label htmlFor="as-title">Title</Label>
            <Input id="as-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={140} />
          </div>
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <Label>Sections</Label>
              {sections.length > 1 && (
                <button className="text-caption text-google-blue hover:underline"
                  onClick={() => setPicked(picked.length === sections.length ? [] : sections.map((s) => s.id))}>
                  {picked.length === sections.length ? "Clear" : "Select all"}
                </button>
              )}
            </div>
            {sections.length === 0 ? (
              <div className="flex items-center justify-between gap-4 rounded-2xl-2 bg-canvas-white p-4 text-body-sm text-slate">
                No sections yet.
                <Button variant="outline" size="sm" onClick={() => { onClose(); dialogs.openSection(); }}>Create a section</Button>
              </div>
            ) : (
              <div className="grid gap-2 sm:grid-cols-2">
                {sections.map((s) => {
                  const on = picked.includes(s.id);
                  return (
                    <label key={s.id} className={cn("flex cursor-pointer items-center gap-3 rounded-2xl border p-3 transition-colors",
                      on ? "border-google-blue bg-panel-tint" : "border-mist hover:bg-canvas-white")}>
                      <Checkbox checked={on} onCheckedChange={(c) => setPicked(c ? [...picked, s.id] : picked.filter((x) => x !== s.id))} />
                      <span className="size-2.5 shrink-0 rounded-full" style={{ background: s.color }} />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-body-sm text-charcoal-ink">{s.name}</span>
                        <span className="block text-caption text-slate">{s.student_ids.length} students{already.has(s.id) ? " · already posted" : ""}</span>
                      </span>
                    </label>
                  );
                })}
              </div>
            )}
          </div>
          <div className="grid gap-5 sm:grid-cols-2">
            <div className="flex flex-col gap-2">
              <Label htmlFor="as-due">Due (optional)</Label>
              <Input id="as-due" type="date" value={due} onChange={(e) => setDue(e.target.value)} />
            </div>
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="as-note">Note to students (optional)</Label>
            <Textarea id="as-note" className="min-h-[64px]" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} />
          </div>
        </DialogBody>
        <DialogFooter>
          {picked.length > 0 && <span className="mr-auto text-caption text-slate">{students} students will receive it</span>}
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={busy || !picked.length} onClick={submit}>Post</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function SectionDialog({ section, onClose }: { section: Section | null | undefined; onClose: () => void }) {
  const { classroom, config, refreshClassroom } = useStore();
  const PALETTE = config?.classroom.palette ?? [];
  const KINDS = Object.entries(config?.classroom.kinds ?? {});
  const open = section !== undefined;
  const [name, setName] = React.useState("");
  const [kind, setKind] = React.useState("period");
  const [color, setColor] = React.useState("");
  const [ids, setIds] = React.useState<string[]>([]);
  const [q, setQ] = React.useState("");
  const [newNames, setNewNames] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  React.useEffect(() => {
    if (!open) return;
    setName(section?.name || ""); setKind(section?.kind || "period");
    setColor(section?.color || PALETTE[classroom.sections.length % Math.max(1, PALETTE.length)] || "");
    setIds(section?.student_ids || []); setQ(""); setNewNames("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, section?.id]);
  const shown = classroom.students.filter((s) => s.name.toLowerCase().includes(q.toLowerCase()));
  const addNew = async () => {
    const names = newNames.split(/[\n,]/).map((s) => s.trim()).filter(Boolean);
    if (!names.length) return;
    try {
      const added = await api.addStudents(names);
      await refreshClassroom();
      setIds((cur) => [...cur, ...added.map((s) => s.id)]);
      setNewNames("");
      toast(added.length ? `Added ${added.length} student${added.length > 1 ? "s" : ""}` : "Those students are already on the roster");
    } catch (err) { toast.error((err as Error).message); }
  };
  const submit = async () => {
    setBusy(true);
    try {
      const c = color || undefined;
      if (section) await api.updateSection(section.id, { name, kind, color: c, student_ids: ids });
      else await api.addSection({ name, kind, color: c, student_ids: ids });
      await refreshClassroom();
      toast(section ? "Section updated" : `Section “${name}” created`);
      onClose();
    } catch (err) { toast.error((err as Error).message); } finally { setBusy(false); }
  };
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{section ? "Edit section" : "New section"}</DialogTitle>
          <DialogDescription>Sections can be class periods, blocks, clubs or tutoring groups. A student can be in several.</DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-5">
          <div className="grid gap-5 sm:grid-cols-[1fr_180px]">
            <div className="flex flex-col gap-2">
              <Label htmlFor="sec-name">Name</Label>
              <Input id="sec-name" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Period 3 · Physics" maxLength={60} />
            </div>
            <div className="flex flex-col gap-2">
              <Label>Type</Label>
              <Select value={kind} onValueChange={setKind}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>{KINDS.map(([v, l]) => <SelectItem key={v} value={v}>{l}</SelectItem>)}</SelectContent>
              </Select>
            </div>
          </div>
          <div className="flex flex-col gap-2">
            <Label>Colour</Label>
            <div className="flex flex-wrap gap-2">
              {PALETTE.map((c) => (
                <button key={c} type="button" aria-label={`Colour ${c}`} onClick={() => setColor(c)}
                  className={cn("flex size-32 items-center justify-center rounded-full ring-offset-2 transition", color === c && "ring-2 ring-charcoal-ink")}
                  style={{ background: c }}>{color === c && <CheckIcon className="size-4 text-white" />}</button>
              ))}
            </div>
          </div>
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <Label>Students <span className="font-normal text-slate">· {ids.length} selected</span></Label>
              {classroom.students.length > 0 && (
                <button className="text-caption text-google-blue hover:underline"
                  onClick={() => setIds(ids.length === classroom.students.length ? [] : classroom.students.map((s) => s.id))}>
                  {ids.length === classroom.students.length ? "Clear" : "Select all"}
                </button>
              )}
            </div>
            {classroom.students.length > 6 && (
              <div className="relative">
                <SearchIcon className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-slate" />
                <Input className="pl-9" placeholder="Search the roster" value={q} onChange={(e) => setQ(e.target.value)} />
              </div>
            )}
            <div className="max-h-52 overflow-y-auto rounded-2xl border border-mist p-1.5 scrollbar-thin">
              {shown.length === 0 && <div className="p-3 text-body-sm text-slate">{classroom.students.length ? "No match." : "The roster is empty: add students below."}</div>}
              {shown.map((s) => (
                <label key={s.id} className="flex cursor-pointer items-center gap-3 rounded-lg px-2.5 py-2 hover:bg-canvas-white">
                  <Checkbox checked={ids.includes(s.id)} onCheckedChange={(c) => setIds(c ? [...ids, s.id] : ids.filter((x) => x !== s.id))} />
                  <span className="text-body-sm">{s.name}</span>
                  <span className="ml-auto flex gap-1">
                    {classroom.sections.filter((x) => !x.archived && x.id !== section?.id && x.student_ids.includes(s.id)).slice(0, 2).map((x) => (
                      <Badge key={x.id} variant="gray">{x.name}</Badge>
                    ))}
                  </span>
                </label>
              ))}
            </div>
            <div className="flex gap-2">
              <Input placeholder="Add students: names separated by commas" value={newNames} onChange={(e) => setNewNames(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void addNew(); } }} />
              <Button type="button" variant="outline" onClick={addNew} disabled={!newNames.trim()}><UserPlusIcon /> Add</Button>
            </div>
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={busy || !name.trim()} onClick={submit}>{section ? "Save" : "Create section"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
