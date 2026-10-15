import * as React from "react";
import { toast } from "sonner";
import { ArchiveIcon, ArchiveRestoreIcon, MoreHorizontalIcon, PencilIcon, PlusIcon, SearchIcon, Trash2Icon, UserPlusIcon, UsersIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Empty, PageHeader } from "@/components/page";
import { useDialogs } from "@/components/dialogs";
import { api } from "@/lib/api";
import { go, useStore } from "@/lib/store";
import { initials } from "@/lib/format";
import type { Section } from "@/lib/types";
import { cn } from "@/lib/utils";


function SectionCard({ s, onDelete }: { s: Section; onDelete: (s: Section) => void }) {
  const { classroom, config, refreshClassroom } = useStore();
  const dialogs = useDialogs();
  const posts = classroom.assignments.filter((a) => a.section_ids.includes(s.id));
  const subs = classroom.submissions.filter((x) => posts.some((a) => a.id === x.assignment_id) && s.student_ids.includes(x.student_id));
  const avg = subs.length ? Math.round((100 * subs.reduce((t, x) => t + (x.max ? x.grade / x.max : 0), 0)) / subs.length) : null;
  const names = s.student_ids.map((id) => classroom.students.find((x) => x.id === id)?.name).filter(Boolean) as string[];
  const archive = async (archived: boolean) => {
    await api.updateSection(s.id, { archived });
    await refreshClassroom();
    toast(archived ? `“${s.name}” archived` : `“${s.name}” restored`, archived ? {
      action: { label: "Undo", onClick: async () => { await api.updateSection(s.id, { archived: false }); await refreshClassroom(); } },
    } : undefined);
  };
  return (
    <Card className={cn("flex flex-col", s.archived && "bg-canvas-white")}>
      <CardContent className="flex flex-1 flex-col gap-5">
        <div className="flex items-start gap-3">
          <span className={cn("flex size-11 shrink-0 items-center justify-center rounded-2xl font-display text-subheading text-white", s.archived && "opacity-50")}
            style={{ background: s.color }}>{initials(s.name)}</span>
          <div className="min-w-0 flex-1">
            <div className="truncate font-display text-subheading text-charcoal-ink">{s.name}</div>
            <div className="mt-0.5 flex items-center gap-1.5"><Badge variant="gray">{config?.classroom.kinds[s.kind] || s.kind}</Badge>{s.archived && <Badge variant="outline">Archived</Badge>}</div>
          </div>
          <DropdownMenu>
            <DropdownMenuTrigger asChild><Button variant="ghost" size="icon-sm" aria-label="Section actions"><MoreHorizontalIcon /></Button></DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {!s.archived && <DropdownMenuItem onSelect={() => dialogs.openSection(s)}><PencilIcon /> Edit</DropdownMenuItem>}
              {!s.archived && <DropdownMenuItem onSelect={() => archive(true)}><ArchiveIcon /> Archive</DropdownMenuItem>}
              {s.archived && <DropdownMenuItem onSelect={() => archive(false)}><ArchiveRestoreIcon /> Restore</DropdownMenuItem>}
              {s.archived && <><DropdownMenuSeparator /><DropdownMenuItem variant="destructive" onSelect={() => onDelete(s)}><Trash2Icon /> Delete permanently</DropdownMenuItem></>}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
        <div className="grid grid-cols-3 gap-2 text-center">
          {[["students", s.student_ids.length], ["lessons", posts.length], ["avg. grade", avg == null ? "–" : `${avg}%`]].map(([k, v]) => (
            <div key={k as string} className="rounded-2xl bg-canvas-white py-2.5">
              <div className="font-display text-heading-sm tabular-nums">{v}</div>
              <div className="text-caption text-slate">{k}</div>
            </div>
          ))}
        </div>
        <div className="flex items-center gap-3">
          <div className="flex -space-x-1.5">
            {names.slice(0, 6).map((n) => (
              <span key={n} title={n} className="flex size-7 items-center justify-center rounded-full bg-[#f1f3f4] text-[10px] font-medium text-graphite ring-2 ring-white">{initials(n)}</span>
            ))}
            {names.length > 6 && <span className="flex size-7 items-center justify-center rounded-full bg-[#f1f3f4] text-[10px] text-graphite ring-2 ring-white">+{names.length - 6}</span>}
          </div>
          {names.length === 0 && <span className="text-caption text-slate">No students yet</span>}
          {s.archived ? (
            <Button variant="outline" size="sm" className="ml-auto" onClick={() => archive(false)}><ArchiveRestoreIcon /> Restore</Button>
          ) : (
            <Button variant="outline" size="sm" className="ml-auto" onClick={() => go(`/grading/section/${s.id}`)}>Grading</Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function Roster() {
  const { classroom, refreshClassroom } = useStore();
  const [names, setNames] = React.useState("");
  const [q, setQ] = React.useState("");
  const add = async () => {
    const list = names.split(/[\n,]/).map((s) => s.trim()).filter(Boolean);
    if (!list.length) return;
    try {
      const added = await api.addStudents(list);
      await refreshClassroom();
      setNames("");
      toast(added.length ? `Added ${added.length} student${added.length > 1 ? "s" : ""}` : "Already on the roster");
    } catch (e) { toast.error((e as Error).message); }
  };
  const remove = async (id: string, name: string) => {
    await api.removeStudent(id);
    await refreshClassroom();
    toast(`${name} removed from the roster`);
  };
  const shown = classroom.students.filter((s) => s.name.toLowerCase().includes(q.trim().toLowerCase()));
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-subheading">Roster</CardTitle>
        <CardDescription>{classroom.students.length} students on this node. No accounts: students pick their name when they take a quiz on the LAN.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input placeholder="Add students: names separated by commas" value={names} onChange={(e) => setNames(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") void add(); }} />
          <Button variant="outline" onClick={add} disabled={!names.trim()}><UserPlusIcon /> Add</Button>
        </div>
        {classroom.students.length > 8 && (
          <div className="relative">
            <SearchIcon className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-slate" />
            <Input className="pl-9" placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
        )}
        <div className="flex flex-col">
          {shown.map((st) => {
            const secs = classroom.sections.filter((s) => !s.archived && s.student_ids.includes(st.id));
            const n = classroom.submissions.filter((x) => x.student_id === st.id).length;
            return (
              <div key={st.id} className="group flex items-center gap-3 border-b border-mist py-2.5 last:border-0">
                <span className="flex size-32 shrink-0 items-center justify-center rounded-full bg-[#f1f3f4] text-caption font-medium text-graphite">{initials(st.name)}</span>
                <span className="min-w-0 flex-1 truncate text-body-sm text-charcoal-ink">{st.name}</span>
                <div className="hidden flex-wrap justify-end gap-1 sm:flex">
                  {secs.map((s) => (
                    <span key={s.id} className="inline-flex items-center gap-1.5 rounded-full border border-mist px-2 py-0.5 text-caption text-graphite">
                      <span className="size-2 rounded-full" style={{ background: s.color }} />{s.name}
                    </span>
                  ))}
                </div>
                <span className="w-[96px] text-right text-caption text-slate">{n} submission{n === 1 ? "" : "s"}</span>
                <Button variant="ghost" size="icon-sm" className="text-slate opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
                  onClick={() => remove(st.id, st.name)} aria-label={`Remove ${st.name}`}><Trash2Icon /></Button>
              </div>
            );
          })}
          {classroom.students.length === 0 && <p className="py-4 text-body-sm text-slate">Add the students of your institute to create sections.</p>}
        </div>
      </CardContent>
    </Card>
  );
}

export function SectionsPage({ archived }: { archived: boolean }) {
  const { classroom, refreshClassroom } = useStore();
  const dialogs = useDialogs();
  const [del, setDel] = React.useState<Section | null>(null);
  const list = classroom.sections.filter((s) => s.archived === archived);
  const nArch = classroom.sections.filter((s) => s.archived).length;
  const doDelete = async () => {
    if (!del) return;
    await api.deleteSection(del.id);
    await refreshClassroom();
    toast(`“${del.name}” deleted`);
    setDel(null);
  };
  return (
    <>
      <PageHeader title="Sections"
        description="Group students the way your institute works: class periods, blocks, clubs or tutoring groups. Post a lesson once to several sections, then grade by section."
        actions={<>
          <ToggleGroup type="single" value={archived ? "archived" : "active"} onValueChange={(v) => v && go(v === "archived" ? "/sections/archived" : "/sections")}>
            <ToggleGroupItem value="active">Active</ToggleGroupItem>
            <ToggleGroupItem value="archived">Archived{nArch ? ` (${nArch})` : ""}</ToggleGroupItem>
          </ToggleGroup>
          <Button onClick={() => dialogs.openSection()}><PlusIcon /> New section</Button>
        </>} />
      {list.length === 0 ? (
        <Empty icon={archived ? ArchiveIcon : UsersIcon} title={archived ? "No archived sections" : "No sections yet"}
          action={!archived && <Button variant="outline" onClick={() => dialogs.openSection()}><PlusIcon /> Create a section</Button>}>
          {archived ? "Archiving a section hides it from posting and grading. Its students stay on the roster and it can be restored at any time."
            : "Create a section for each period or group you teach, then post lessons to several of them at once."}
        </Empty>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {list.map((s) => <SectionCard key={s.id} s={s} onDelete={setDel} />)}
        </div>
      )}
      {!archived && <Roster />}
      <Dialog open={!!del} onOpenChange={(o) => !o && setDel(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete “{del?.name}”?</DialogTitle>
            <DialogDescription>The section is removed from every post. Students stay on the roster and their grades are kept. This cannot be undone.</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDel(null)}>Cancel</Button>
            <Button variant="destructive" onClick={doDelete}>Delete</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
