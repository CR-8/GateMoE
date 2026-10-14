import { AlertCircleIcon, CalendarIcon, CircleHelpIcon, LayersIcon, LoaderCircleIcon, MoreHorizontalIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useDialogs } from "@/components/dialogs";
import { api } from "@/lib/api";
import { columnOf, isFailed, lessonTitle } from "@/lib/board";
import { GATEWAY_META, STAGE_LABELS, dateShort, initials } from "@/lib/format";
import { go, useStore } from "@/lib/store";
import type { LessonSummary, Section } from "@/lib/types";
import { toast } from "sonner";

export function GatewayChips({ selected, max = 3 }: { selected?: string[]; max?: number }) {
  const gws = (selected || []).filter((g) => g in GATEWAY_META) as (keyof typeof GATEWAY_META)[];
  return (
    <div className="flex flex-wrap items-center gap-1">
      {gws.slice(0, max).map((g) => <Badge key={g} variant={GATEWAY_META[g].variant}>{GATEWAY_META[g].label}</Badge>)}
      {gws.length > max && <Badge variant="gray">+{gws.length - max}</Badge>}
    </div>
  );
}

export function SectionStack({ sections, max = 3 }: { sections: Section[]; max?: number }) {
  if (!sections.length) return null;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div className="flex -space-x-1.5">
          {sections.slice(0, max).map((s) => (
            <span key={s.id} className="flex size-6 items-center justify-center rounded-full text-[10px] font-medium text-white ring-2 ring-white"
              style={{ background: s.color }}>{initials(s.name)}</span>
          ))}
          {sections.length > max && (
            <span className="flex size-6 items-center justify-center rounded-full bg-[#f1f3f4] text-[10px] text-graphite ring-2 ring-white">+{sections.length - max}</span>
          )}
        </div>
      </TooltipTrigger>
      <TooltipContent>{sections.map((s) => s.name).join(", ")}</TooltipContent>
    </Tooltip>
  );
}

export function LessonMenu({ lesson }: { lesson: LessonSummary }) {
  const { refreshLessons } = useStore();
  const dialogs = useDialogs();
  const col = columnOf(lesson);
  const set = async (status: string) => {
    await api.setBoard(lesson.id, status);
    await refreshLessons();
    toast(status === "archived" ? "Lesson archived" : "Moved to review");
  };
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon-sm" className="-mt-1 -mr-2 size-7 text-slate" aria-label="Lesson actions"
          onPointerDown={(e) => e.stopPropagation()} onClick={(e) => e.stopPropagation()}>
          <MoreHorizontalIcon />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
        <DropdownMenuItem onSelect={() => go(`/lesson/${lesson.id}`)}>Open</DropdownMenuItem>
        {lesson.status === "done" && <DropdownMenuItem onSelect={() => dialogs.openAssign(lesson.id)}>Post to sections…</DropdownMenuItem>}
        {(lesson.status === "queued" || lesson.status === "running") && (
          <DropdownMenuItem variant="destructive" onSelect={async () => { await api.cancel(lesson.id); await refreshLessons(); }}>Cancel generation</DropdownMenuItem>
        )}
        {col === "published" && <DropdownMenuItem onSelect={() => set("review")}>Move back to review</DropdownMenuItem>}
        {(col === "review" || col === "published") && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => set("archived")}>Archive</DropdownMenuItem>
          </>
        )}
        {col === "archived" && <DropdownMenuItem onSelect={() => set("review")}>Restore</DropdownMenuItem>}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** Board card: gateway chips, title, summary, concept-map thumbnail, date, sections, counts. */
export function LessonCardBody({ lesson, sections }: { lesson: LessonSummary; sections: Section[] }) {
  const failed = isFailed(lesson);
  const running = lesson.status === "running";
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-start justify-between gap-2">
        {lesson.selected?.length ? <GatewayChips selected={lesson.selected} /> :
          <Badge variant="gray">{lesson.options?.mode === "manual" ? "Manual" : lesson.status === "queued" ? "Waiting" : "Routing"}</Badge>}
        <LessonMenu lesson={lesson} />
      </div>
      <div>
        <div className="line-clamp-2 font-display text-subheading leading-snug text-charcoal-ink">{lessonTitle(lesson)}</div>
        {lesson.summary && <p className="mt-1 line-clamp-2 text-body-sm text-slate">{lesson.summary}</p>}
      </div>
      {lesson.thumb && (
        <div className="flex h-28 items-center justify-center overflow-hidden rounded-2xl bg-canvas-white p-2">
          <img src={api.file(lesson.id, lesson.thumb)} alt="" loading="lazy" className="max-h-full max-w-full object-contain mix-blend-multiply" draggable={false} />
        </div>
      )}
      {running && (
        <div className="flex items-center gap-2 rounded-full bg-panel-tint px-3 py-1.5 text-caption text-google-blue-deep">
          <LoaderCircleIcon className="size-3.5 animate-spin" />
          <span className="truncate">{STAGE_LABELS[lesson.stage || ""] || "Working…"}</span>
        </div>
      )}
      {failed && (
        <div className="flex items-center gap-2 rounded-full bg-red-tint px-3 py-1.5 text-caption text-danger">
          <AlertCircleIcon className="size-3.5" />
          <span className="truncate">{lesson.status === "failed" ? lesson.error || "Generation failed" : lesson.status === "cancelled" ? "Cancelled" : "Interrupted (node restarted)"}</span>
        </div>
      )}
      {lesson.out_of_scope && <Badge variant="yellow" className="self-start">Out of scope for a tutor</Badge>}
      <div className="flex items-center gap-3 text-caption text-slate">
        <span className="inline-flex items-center gap-1.5 rounded-full border border-mist px-2.5 py-1">
          <CalendarIcon className="size-3.5" />{dateShort(lesson.created)}
        </span>
        {!!lesson.counts?.questions && <span className="inline-flex items-center gap-1"><CircleHelpIcon className="size-3.5" />{lesson.counts.questions}</span>}
        {!!lesson.counts?.cards && <span className="inline-flex items-center gap-1"><LayersIcon className="size-3.5" />{lesson.counts.cards}</span>}
        {lesson.language?.name && lesson.language.lang !== "en" && <span>{lesson.language.native || lesson.language.name}</span>}
        <span className="ml-auto"><SectionStack sections={sections} /></span>
      </div>
    </div>
  );
}
