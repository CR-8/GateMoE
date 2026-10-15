import { CpuIcon, HardDriveIcon, LayersIcon, ListOrderedIcon, ThermometerIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { PageHeader, StatCard } from "@/components/page";
import { useStore } from "@/lib/store";
import { duration } from "@/lib/format";
import type { SystemState } from "@/lib/types";

// "tts:piper:hi_IN-pratham-medium.onnx" -> "piper · hi_IN-pratham-medium"
const shortName = (id: string) => id.replace(/^[a-z_]+:/, "").replace(/\.(onnx|gguf|zim)$/, "").replace(":", " · ");

const GROUPS: [string, string][] = [["router", "Router"], ["text", "Text generation"], ["speech", "Speech"], ["video", "Video"],
  ["handout", "Handout"], ["knowledge", "Knowledge"]];

type Spec = SystemState["specialists"][number];

function SpecRow({ x }: { x: Spec }) {
  return (
    <div className="flex items-center gap-2 rounded-2xl bg-canvas-white px-3 py-2 text-body-sm"
      title={x.error || (x as { path?: string }).path || x.id}>
      <span className={x.available ? "size-2 shrink-0 rounded-full bg-classroom-green" : x.error ? "size-2 shrink-0 rounded-full bg-danger" : "size-2 shrink-0 rounded-full bg-cloud-shadow"} />
      <span className="min-w-0 flex-1 truncate text-graphite">{x.error ? `Could not check: ${x.error}` : shortName(x.id)}</span>
      {x.size_mb ? <span className="text-caption text-slate tabular-nums">{x.size_mb >= 1024 ? `${(x.size_mb / 1024).toFixed(1)} GB` : `${x.size_mb} MB`}</span> : null}
      {!x.available && !x.error && <Badge variant="gray">not installed</Badge>}
    </div>
  );
}

export function SystemPage() {
  const { system, config, lessons } = useStore();
  const s = system?.sys;
  const usedPct = s && s.mem_total_mb ? 100 * (1 - s.mem_available_mb / s.mem_total_mb) : 0;
  const done = lessons.filter((l) => l.status === "done").length;
  const specs = system?.specialists || [];
  return (
    <>
      <PageHeader title="System" description="This node: one large model in RAM at a time, swapped per stage. Every parameter lives in the YAML config on the server." />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard tint="blue" icon={HardDriveIcon} label="Memory in use" value={s ? `${((s.mem_total_mb - s.mem_available_mb) / 1024).toFixed(1)} GB` : "–"}
          hint={s ? `of ${(s.mem_total_mb / 1024).toFixed(1)} GB · swap ${s.swap_used_mb} MB` : "node unreachable"} />
        <StatCard tint="purple" icon={CpuIcon} label="Model in RAM" value={system?.models.loaded ? "1" : "0"} hint={system?.models.loaded || "idle: nothing loaded"} />
        <StatCard tint="yellow" icon={ListOrderedIcon} label="Queue" value={system?.queue ?? "–"} hint={`${done} lessons finished on this node`} />
        <StatCard tint="green" icon={ThermometerIcon} label="CPU temperature" value={s?.temp_c != null ? `${s.temp_c.toFixed(0)} °C` : "–"} hint={s?.throttled ? `throttled: ${s.throttled}` : "no throttling reported"} />
      </div>
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle className="text-subheading">Memory</CardTitle><CardDescription>Updated every 5 seconds</CardDescription></CardHeader>
          <CardContent className="flex flex-col gap-4">
            <Progress value={usedPct} className="h-3" indicatorClassName={usedPct > 90 ? "bg-danger" : undefined} />
            <div className="flex justify-between text-caption text-slate tabular-nums">
              <span>{usedPct.toFixed(0)}% used</span><span>{s ? `${(s.mem_available_mb / 1024).toFixed(2)} GB available` : ""}</span>
            </div>
            <div className="flex flex-col gap-2 pt-2">
              <div className="text-body-sm font-medium">Recent model swaps</div>
              {(system?.models.history || []).slice().reverse().map((h, i) => (
                <div key={i} className="flex items-center gap-3 text-body-sm">
                  <Badge variant={h.event === "load" ? "default" : "gray"}>{h.event}</Badge>
                  <span className="text-graphite">{h.role}</span>
                  <span className="ml-auto text-caption text-slate tabular-nums">{h.seconds != null ? duration(h.seconds) : ""}</span>
                </div>
              ))}
              {!system?.models.history?.length && <p className="text-caption text-slate">No model has been loaded since the server started.</p>}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-subheading">Configuration</CardTitle><CardDescription>Read-only here; edit the YAML on the server and restart.</CardDescription></CardHeader>
          <CardContent>
            <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-body-sm">
              {[["Router mode", config?.router_mode], ["Router threshold", config?.threshold], ["Router model", config?.models.router],
                ["Generator model", config?.models.generator], ["Languages", config ? Object.keys(config.languages).length : null]].map(([k, v]) => (
                <div key={k as string} className="contents"><dt className="text-slate">{k}</dt><dd className="truncate text-right text-charcoal-ink">{v ?? "–"}</dd></div>
              ))}
            </dl>
            {config && <div className="mt-4 flex flex-wrap gap-1.5">{Object.values(config.languages).map((l) => <Badge key={l.name} variant="outline">{l.native || l.name}</Badge>)}</div>}
          </CardContent>
        </Card>
      </div>
      <Card>
        <CardHeader><CardTitle className="text-subheading">Specialists</CardTitle>
          <CardDescription>{specs.filter((x) => x.available).length} of {specs.length} installed on this node{system?.generate === false ? " · generation is off on this server (browse-and-grade mode)" : ". The router activates only what a request needs."}</CardDescription></CardHeader>
        <CardContent className="grid gap-6 md:grid-cols-2 xl:grid-cols-3">
          {GROUPS.map(([g, label]) => {
            const items = specs.filter((x) => x.gateway === g).sort((a, b) => Number(!!b.available) - Number(!!a.available));
            if (!items.length) return null;
            return (
              <div key={g} className="flex flex-col gap-2">
                <div className="flex items-center gap-2 text-body-sm font-medium"><LayersIcon className="size-4 text-slate" />{label}</div>
                {items.filter((x) => x.available || x.error).map((x) => <SpecRow key={x.id} x={x} />)}
                {items.some((x) => !x.available && !x.error) && (
                  <details className="group rounded-2xl">
                    <summary className="cursor-pointer list-none rounded-2xl px-3 py-2 text-caption text-slate hover:bg-canvas-white">
                      {items.filter((x) => !x.available && !x.error).length} not installed <span className="group-open:hidden">· show</span>
                    </summary>
                    <div className="mt-2 flex flex-col gap-2">
                      {items.filter((x) => !x.available && !x.error).map((x) => <SpecRow key={x.id} x={x} />)}
                    </div>
                  </details>
                )}
              </div>
            );
          })}
        </CardContent>
      </Card>
    </>
  );
}
