import * as React from "react";
import { toast } from "sonner";
import { ArchiveIcon, BookOpenCheckIcon, ColumnsIcon, ListIcon, PlusIcon, SearchIcon, SendIcon, SparklesIcon, TimerIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { KanbanBoard, KanbanCard, KanbanCards, KanbanHeader, KanbanProvider } from "@/components/ui/kanban";
import { Empty, PageHeader, StatCard } from "@/components/page";
import { GatewayChips, LessonCardBody, LessonMenu, SectionStack } from "@/components/lesson-card";
import { useDialogs } from "@/components/dialogs";
import { api } from "@/lib/api";
import { COLUMNS, columnOf, isFailed, lessonTitle, sectionsOf, type Column } from "@/lib/board";
import { ago, duration } from "@/lib/format";
import { go, useStore } from "@/lib/store";
import type { LessonSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

function readView(): "board" | "list" {
  try { return localStorage.getItem("gm.view") === "list" ? "list" : "board"; } catch { return "board"; }
}

function Stats({ lessons }: { lessons: LessonSummary[] }) {
  const { classroom } = useStore();
  const done = lessons.filter((l) => l.status === "done" && !l.out_of_scope);
  const weekAgo = Date.now() / 1000 - 7 * 86400;
  const times = done.map((l) => l.wall_s).filter((x): x is number => typeof x === "number").sort((a, b) => a - b);
  const mean = times.length ? times.reduce((a, b) => a + b, 0) / times.length : null;
  const median = times.length ? times[Math.floor(times.length / 2)] : null;
  const published = done.filter((l) => columnOf(l) === "published").length;
  const reached = new Set(classroom.sections.filter((s) => classroom.assignments.some((a) => a.section_ids.includes(s.id)))
    .flatMap((s) => s.student_ids)).size;
  return (
    <div className="grid gap-4 sm:grid-cols-3">
      <StatCard tint="blue" icon={BookOpenCheckIcon} label="Lessons generated" value={done.length}
        hint={`${done.filter((l) => l.created > weekAgo).length} this week · all on this node, offline`} />
      <StatCard tint="green" icon={TimerIcon} label="Average generation time" value={duration(mean)}
        hint={median != null ? `median ${duration(median)} per lesson` : "no finished lessons yet"} />
      <StatCard tint="yellow" icon={SendIcon} label="Posted to sections" value={done.length ? `${Math.round((100 * published) / done.length)}%` : "–"}
        hint={`${classroom.assignments.length} posts · ${reached} students reached`} />
    </div>
  );
}

function Board({ lessons }: { lessons: LessonSummary[] }) {
  const { classroom, config, refreshLessons } = useStore();
  const dialogs = useDialogs();
  const items = lessons.map((l) => ({ ...l, column: columnOf(l) as string }));
  return (
    <div className="-mx-1 overflow-x-auto px-1 pb-2 scrollbar-thin">
      <KanbanProvider
        columns={COLUMNS} data={items}
        canMove={(item, to) => item.status === "done" && (to === "review" || to === "published")}
        onDataChange={async (_d, moved, to) => {
          if (to === "published") { dialogs.openAssign(moved.id); return; }    // publishing = posting to sections
          await api.setBoard(moved.id, to);
          await refreshLessons();
          toast("Moved back to review");
        }}
        renderOverlay={(item) => (
          <div className="w-[300px] rotate-2 rounded-2xl-2 border border-google-blue bg-white p-4">
            <LessonCardBody lesson={item} sections={sectionsOf(item.id, classroom)} />
          </div>
        )}>
        {(column) => {
          const cards = items.filter((i) => i.column === column.id);
          return (
            <KanbanBoard id={column.id}>
              <KanbanHeader>
                <span className="size-2.5 rounded-full" style={{ background: column.color }} />
                {column.name}
                <span className="rounded-full bg-white px-2 text-caption text-slate tabular-nums">{cards.length}</span>
                {column.id === "queued" && config?.generate !== false && (
                  <Button variant="ghost" size="icon-sm" className="ml-auto size-7" onClick={dialogs.openNewLesson} aria-label="New lesson"><PlusIcon /></Button>
                )}
              </KanbanHeader>
              <KanbanCards>
                {cards.map((l) => (
                  <KanbanCard key={l.id} id={l.id} disabled={l.status !== "done"} className={cn(isFailed(l) && "border-red-tint")}>
                    <div role="link" tabIndex={-1} onClick={() => go(`/lesson/${l.id}`)}>
                      <LessonCardBody lesson={l} sections={sectionsOf(l.id, classroom)} />
                    </div>
                  </KanbanCard>
                ))}
                {cards.length === 0 && (
                  <div className="rounded-2xl-2 border border-dashed border-mist px-4 py-32 text-center text-caption text-slate">
                    {column.id === "published" ? "Drag a reviewed lesson here to post it" : column.id === "review" ? "Finished lessons land here" : "Nothing here"}
                  </div>
                )}
              </KanbanCards>
            </KanbanBoard>
          );
        }}
      </KanbanProvider>
    </div>
  );
}

const STATUS_BADGE: Record<Column | "archived", { label: string; variant: "default" | "green" | "yellow" | "gray" | "red" }> = {
  queued: { label: "Queued", variant: "gray" }, generating: { label: "Generating", variant: "default" },
  review: { label: "Ready for review", variant: "yellow" }, published: { label: "Published", variant: "green" },
  archived: { label: "Archived", variant: "gray" },
};

function List({ lessons }: { lessons: LessonSummary[] }) {
  const { classroom } = useStore();
  return (
    <Card className="overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[760px] text-left text-body-sm">
          <thead className="border-b border-mist text-caption text-slate">
            <tr>{["Lesson", "Status", "Sections", "Language", "Generation", "Created", ""].map((h) => <th key={h} className="px-5 py-3 font-medium">{h}</th>)}</tr>
          </thead>
          <tbody>
            {lessons.map((l) => {
              const col = columnOf(l);
              const st = isFailed(l) ? { label: l.status === "failed" ? "Failed" : l.status === "cancelled" ? "Cancelled" : "Interrupted", variant: "red" as const } : STATUS_BADGE[col];
              return (
                <tr key={l.id} onClick={() => go(`/lesson/${l.id}`)} className="cursor-pointer border-b border-mist last:border-0 hover:bg-canvas-white">
                  <td className="max-w-[380px] px-5 py-3">
                    <div className="truncate font-medium text-charcoal-ink">{lessonTitle(l)}</div>
                    <div className="mt-1"><GatewayChips selected={l.selected} max={4} /></div>
                  </td>
                  <td className="px-5 py-3"><Badge variant={st.variant}>{st.label}</Badge></td>
                  <td className="px-5 py-3"><SectionStack sections={sectionsOf(l.id, classroom)} /></td>
                  <td className="px-5 py-3 text-graphite">{l.language?.name || (l.options?.language === "auto" ? "Auto" : l.options?.language)}</td>
                  <td className="px-5 py-3 text-graphite tabular-nums">{duration(l.wall_s)}</td>
                  <td className="px-5 py-3 text-slate">{ago(l.created)}</td>
                  <td className="px-3 py-3" onClick={(e) => e.stopPropagation()}><LessonMenu lesson={l} /></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

export function BoardPage() {
  const { lessons, config } = useStore();
  const canGenerate = config?.generate !== false;
  const dialogs = useDialogs();
  const [view, setView] = React.useState(readView);
  const [q, setQ] = React.useState("");
  const [archived, setArchived] = React.useState(false);
  React.useEffect(() => { try { localStorage.setItem("gm.view", view); } catch { /* private mode */ } }, [view]);
  const needle = q.trim().toLowerCase();
  const matching = lessons.filter((l) => !needle || `${l.title || ""} ${l.request} ${l.subject || ""}`.toLowerCase().includes(needle));
  const nArchived = matching.filter((l) => columnOf(l) === "archived").length;
  const shown = matching.filter((l) => (columnOf(l) === "archived") === archived);
  return (
    <>
      <PageHeader title="Lessons" description="Generated on this node from offline knowledge. Review them, then post them to your sections."
        actions={<>
          <ToggleGroup type="single" value={view} onValueChange={(v) => v && setView(v as "board" | "list")} aria-label="View">
            <ToggleGroupItem value="list"><ListIcon /> List</ToggleGroupItem>
            <ToggleGroupItem value="board"><ColumnsIcon /> Board</ToggleGroupItem>
          </ToggleGroup>
          <Button onClick={dialogs.openNewLesson} disabled={!canGenerate}
            title={canGenerate ? undefined : "Lesson generation is switched off on this server"}><PlusIcon /> New lesson</Button>
        </>}>
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative w-full max-w-md">
            <SearchIcon className="pointer-events-none absolute top-1/2 left-4 size-4 -translate-y-1/2 text-slate" />
            <Input className="h-11 rounded-full pl-11" placeholder="Search lessons" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          {(nArchived > 0 || archived) && (
            <Button variant={archived ? "dark" : "secondary"} size="sm" onClick={() => setArchived(!archived)}>
              <ArchiveIcon /> {archived ? "Showing archived" : `Archived (${nArchived})`}
            </Button>
          )}
        </div>
      </PageHeader>
      {!canGenerate && (
        <div className="rounded-3xl bg-yellow-tint px-5 py-4 text-body-sm text-graphite">
          Browse-and-grade mode: this server does not generate lessons (it was started with <code className="font-mono text-[13px]">--no-generate</code>,
          e.g. while a benchmark uses the models). Lessons added with <code className="font-mono text-[13px]">gatemoe import</code> appear here.
        </div>
      )}
      {!archived && <Stats lessons={lessons} />}
      {lessons.length === 0 ? (
        <Empty icon={SparklesIcon} title="No lessons yet"
          action={canGenerate && <Button variant="outline" onClick={dialogs.openNewLesson}><PlusIcon /> Create the first lesson</Button>}>
          {canGenerate ? "Ask for a topic in any supported language. The router picks notes, flashcards, a quiz, a podcast, a video or code, and only those are generated."
            : "Generation is off on this server. Add finished lesson folders with: gatemoe import <folder>"}
        </Empty>
      ) : archived || view === "list" ? (
        shown.length ? <List lessons={shown} /> : <Empty title={archived ? "No archived lessons" : "No lessons match"} />
      ) : (
        <Board lessons={shown} />
      )}
    </>
  );
}
