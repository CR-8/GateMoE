import { ClipboardCheckIcon, LayoutGridIcon, LibraryIcon, MenuIcon, PlusIcon, ServerIcon, UsersIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { useDialogs } from "@/components/dialogs";
import { go, useStore } from "@/lib/store";
import { cn } from "@/lib/utils";

export const NAV = [
  { path: "", label: "Overview", icon: LayoutGridIcon },
  { path: "sections", label: "Sections", icon: UsersIcon },
  { path: "grading", label: "Grading", icon: ClipboardCheckIcon },
  { path: "knowledge", label: "Knowledge", icon: LibraryIcon },
  { path: "system", label: "System", icon: ServerIcon },
];

export function Logo({ className }: { className?: string }) {
  // a gate: two posts and a lintel, in the accent colour (no gradients, no shadow)
  return (
    <svg viewBox="0 0 32 32" className={cn("size-9", className)} aria-hidden>
      <rect width="32" height="32" rx="10" fill="#1a73e8" />
      <path d="M9 24V12m14 12V12M7 10h18" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" />
      <circle cx="16" cy="18" r="2.6" fill="#fff" />
    </svg>
  );
}

function NodeStatus() {
  const { system } = useStore();
  const s = system?.sys;
  const used = s && s.mem_total_mb ? 100 * (1 - s.mem_available_mb / s.mem_total_mb) : 0;
  const loaded = system?.models.loaded;
  return (
    <button onClick={() => go("/system")} className="w-full rounded-2xl-2 bg-canvas-white p-4 text-left hover:bg-[#f1f3f4]">
      <div className="flex items-center gap-2 text-caption text-slate">
        <span className={cn("size-2 rounded-full", system ? "bg-classroom-green" : "bg-danger")} />
        {system ? "Node online" : "Node unreachable"}
        {!!system?.queue && <span className="ml-auto text-google-blue-deep">{system.queue} in queue</span>}
      </div>
      <div className="mt-3 flex items-baseline justify-between text-caption text-graphite">
        <span>Memory</span>
        <span className="tabular-nums">{s ? `${((s.mem_total_mb - s.mem_available_mb) / 1024).toFixed(1)} / ${(s.mem_total_mb / 1024).toFixed(1)} GB` : "–"}</span>
      </div>
      <Progress value={used} className="mt-1.5" indicatorClassName={used > 90 ? "bg-danger" : undefined} />
      <div className="mt-3 truncate text-caption text-slate">
        {loaded ? <>Model in RAM: <span className="text-graphite">{loaded}</span></> : "No model in RAM (idle)"}
      </div>
    </button>
  );
}

export function AppSidebar({ route }: { route: string[] }) {
  const { classroom, lessons } = useStore();
  const dialogs = useDialogs();
  const active = route[0] === "lesson" ? "" : route[0] || "";
  const sections = classroom.sections.filter((s) => !s.archived);
  const toGrade = classroom.submissions.filter((s) => !s.graded).length;
  const generating = lessons.filter((l) => l.status === "running" || l.status === "queued").length;
  return (
    <aside className="sticky top-4 m-4 mr-0 hidden h-[calc(100vh-32px)] w-[264px] shrink-0 flex-col rounded-3xl border border-mist bg-white lg:flex">
      <div className="flex items-center gap-3 px-5 pt-5 pb-4">
        <Logo />
        <div className="min-w-0">
          <div className="font-display text-subheading leading-tight text-charcoal-ink">GateMoE</div>
          <div className="text-caption text-slate">Offline learning node</div>
        </div>
      </div>
      <nav className="flex flex-col gap-0.5 px-3">
        {NAV.map((n) => {
          const on = active === n.path;
          const count = n.path === "grading" ? toGrade : n.path === "" ? generating : 0;
          return (
            <a key={n.path} href={`#/${n.path}`} aria-current={on ? "page" : undefined}
              className={cn("flex h-10 items-center gap-3 rounded-full px-4 text-body-sm text-graphite transition-colors hover:bg-canvas-white",
                on && "bg-panel-tint font-medium text-google-blue-deep hover:bg-panel-tint")}>
              <n.icon className="size-[18px]" />
              {n.label}
              {count > 0 && <span className={cn("ml-auto rounded-full px-2 text-caption tabular-nums", on ? "bg-white" : "bg-[#f1f3f4]")}>{count}</span>}
            </a>
          );
        })}
      </nav>

      <div className="mt-6 flex items-center justify-between px-7 pb-1">
        <span className="text-caption font-medium tracking-[0.6px] text-slate uppercase">Sections</span>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="icon-sm" className="-mr-2 size-7" onClick={() => dialogs.openSection()} aria-label="New section">
              <PlusIcon />
            </Button>
          </TooltipTrigger>
          <TooltipContent>New section</TooltipContent>
        </Tooltip>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto px-3 scrollbar-thin">
        {sections.length === 0 && (
          <button onClick={() => dialogs.openSection()} className="mx-4 rounded-2xl border border-dashed border-mist p-3 text-left text-caption text-slate hover:border-google-blue hover:text-google-blue">
            Group students into periods, clubs or tutoring groups.
          </button>
        )}
        {sections.map((s) => (
          <a key={s.id} href={`#/grading/section/${s.id}`}
            className={cn("flex h-9 items-center gap-3 rounded-full px-4 text-body-sm text-graphite hover:bg-canvas-white",
              route[0] === "grading" && route[2] === s.id && "bg-canvas-white")}>
            <span className="size-2.5 shrink-0 rounded-full" style={{ background: s.color }} />
            <span className="truncate">{s.name}</span>
            <span className="ml-auto text-caption text-slate tabular-nums">{s.student_ids.length}</span>
          </a>
        ))}
        <a href="#/sections/archived" className="px-4 py-2 text-caption text-google-blue hover:underline">See archived</a>
      </div>
      <div className="p-3"><NodeStatus /></div>
    </aside>
  );
}

export function MobileNav({ route }: { route: string[] }) {
  const active = route[0] === "lesson" ? "" : route[0] || "";
  const cur = NAV.find((n) => n.path === active) ?? NAV[0];
  return (
    <div className="sticky top-0 z-30 flex items-center gap-3 border-b border-mist bg-white px-4 py-3 lg:hidden">
      <Logo className="size-32" />
      <span className="font-display text-subheading">GateMoE</span>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="secondary" size="sm" className="ml-auto"><MenuIcon /> {cur.label}</Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {NAV.map((n) => (
            <DropdownMenuItem key={n.path} onSelect={() => go(`/${n.path}`)}><n.icon /> {n.label}</DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
