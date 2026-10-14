import * as React from "react";
import { cn } from "@/lib/utils";

export function PageHeader({ title, description, actions, children, className }:
  { title: React.ReactNode; description?: React.ReactNode; actions?: React.ReactNode; children?: React.ReactNode; className?: string }) {
  return (
    <header className={cn("flex flex-col gap-4", className)}>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-4">
        <div className="min-w-0 flex-1">
          <h1 className="font-display text-heading text-charcoal-ink">{title}</h1>
          {description && <p className="mt-1 text-body-sm text-slate">{description}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {children}
    </header>
  );
}

export function Empty({ icon: Icon, title, children, action, className }:
  { icon?: React.ComponentType<{ className?: string }>; title: string; children?: React.ReactNode; action?: React.ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-col items-center gap-3 rounded-3xl border border-dashed border-mist px-6 py-14 text-center", className)}>
      {Icon && <div className="flex size-12 items-center justify-center rounded-full bg-panel-tint text-google-blue"><Icon className="size-6" /></div>}
      <div className="font-display text-subheading text-charcoal-ink">{title}</div>
      {children && <div className="max-w-md text-body-sm text-slate">{children}</div>}
      {action}
    </div>
  );
}

const TINTS = { blue: "bg-panel-tint", green: "bg-green-tint", yellow: "bg-yellow-tint", purple: "bg-purple-tint", red: "bg-red-tint" };

/** Tinted metric tile (reference screenshot: three tinted cards above the board). */
export function StatCard({ label, value, hint, icon: Icon, tint = "blue" }:
  { label: string; value: React.ReactNode; hint?: React.ReactNode; icon?: React.ComponentType<{ className?: string }>; tint?: keyof typeof TINTS }) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-3 rounded-3xl p-5", TINTS[tint])}>
      <div className="flex items-center justify-between gap-2 text-body-sm text-graphite">
        <span className="truncate">{label}</span>
        {Icon && <span className="flex size-32 shrink-0 items-center justify-center rounded-full bg-white/70"><Icon className="size-4" /></span>}
      </div>
      <div className="font-display text-heading text-charcoal-ink tabular-nums">{value}</div>
      {hint && <div className="truncate text-caption text-slate">{hint}</div>}
    </div>
  );
}

/** **bold** only, everything else stays text (model output is never rendered as HTML). */
export function Inline({ text }: { text: string }) {
  return <>{String(text).split(/(\*\*[^*]+\*\*)/g).map((p, i) =>
    /^\*\*[^*]+\*\*$/.test(p) ? <strong key={i} className="font-medium">{p.slice(2, -2)}</strong> : <React.Fragment key={i}>{p}</React.Fragment>)}</>;
}

export function RichText({ text, className }: { text: string; className?: string }) {
  const blocks: React.ReactNode[] = [];
  let list: string[] = [];
  const flush = () => {
    if (list.length) blocks.push(<ul key={blocks.length} className="ml-5 list-disc space-y-1">{list.map((l, i) => <li key={i}><Inline text={l} /></li>)}</ul>);
    list = [];
  };
  for (const raw of String(text || "").split("\n")) {
    const line = raw.trim();
    if (/^[-*•]\s+/.test(line)) list.push(line.replace(/^[-*•]\s+/, ""));
    else { flush(); if (line) blocks.push(<p key={blocks.length}><Inline text={line.replace(/^#+\s*/, "")} /></p>); }
  }
  flush();
  return <div className={cn("space-y-3 text-body leading-relaxed text-graphite", className)}>{blocks}</div>;
}
