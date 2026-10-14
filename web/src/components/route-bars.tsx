import { GATEWAY_META } from "@/lib/format";
import { GATEWAYS, type Gateway } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Router output: P(yes) per gateway against the threshold; selected gateways in the accent colour. */
export function RouteBars({ probs, selected, threshold }: { probs?: Record<string, number>; selected?: string[]; threshold: number }) {
  return (
    <div className="flex flex-col gap-2.5">
      {GATEWAYS.map((g: Gateway) => {
        const p = probs?.[g] ?? 0;
        const on = selected?.includes(g);
        return (
          <div key={g} className="grid grid-cols-[92px_1fr_44px] items-center gap-3 text-body-sm">
            <span className={cn("truncate", on ? "font-medium text-charcoal-ink" : "text-slate")}>{GATEWAY_META[g].label}</span>
            <div className="relative h-2 rounded-full bg-[#f1f3f4]">
              <div className={cn("absolute inset-y-0 left-0 rounded-full", on ? "bg-google-blue" : "bg-cloud-shadow")} style={{ width: `${Math.max(1, p * 100)}%` }} />
              <div className="absolute -inset-y-1 w-0.5 rounded-full bg-charcoal-ink/60" style={{ left: `${threshold * 100}%` }} title={`threshold ${threshold}`} />
            </div>
            <span className={cn("text-right tabular-nums", on ? "text-google-blue-deep" : "text-slate")}>{p.toFixed(2)}</span>
          </div>
        );
      })}
    </div>
  );
}
