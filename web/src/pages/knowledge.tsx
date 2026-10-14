import { ArrowRightIcon, DatabaseIcon, GitBranchPlusIcon, LibraryIcon, RefreshCwIcon, SearchCheckIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Empty, PageHeader, StatCard } from "@/components/page";
import { api } from "@/lib/api";
import { usePoll } from "@/lib/store";
import type { KnowledgeArchive } from "@/lib/types";

const gb = (mb?: number) => (mb == null ? "–" : mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`);

function ArchiveTable({ rows, delta }: { rows: KnowledgeArchive[]; delta?: boolean }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[620px] text-left text-body-sm">
        <thead className="text-caption text-slate">
          <tr className="border-b border-mist">{["Archive", "Language", "Kind", "Date", "Articles", "Size"].map((h) => <th key={h} className="px-5 py-3 font-medium">{h}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((z) => (
            <tr key={z.name} className="border-b border-mist last:border-0">
              <td className="max-w-[340px] px-5 py-3">
                <div className="truncate text-charcoal-ink">{z.title || z.name}</div>
                <div className="truncate text-caption text-slate">{z.file || z.name}</div>
                {z.error && <div className="text-caption text-danger">{z.error}</div>}
              </td>
              <td className="px-5 py-3"><Badge variant="outline">{z.lang || "–"}</Badge></td>
              <td className="px-5 py-3">
                <Badge variant={delta ? "purple" : z.kind === "simulations" ? "green" : "gray"}>{delta ? "delta" : z.kind || "articles"}</Badge>
                {z.fulltext && <Badge variant="outline" className="ml-1">full-text</Badge>}
              </td>
              <td className="px-5 py-3 text-graphite">{z.date || "–"}</td>
              <td className="px-5 py-3 text-graphite tabular-nums">{z.articles?.toLocaleString() ?? "–"}</td>
              <td className="px-5 py-3 text-graphite tabular-nums">{gb(z.size_mb)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const FLOW = [
  { icon: RefreshCwIcon, title: "Snapshot layer", text: "Kiwix archives (Wikipedia, LibreTexts, PhET) refreshed when a newer dated build appears: downloaded, verified, swapped in atomically." },
  { icon: GitBranchPlusIcon, title: "Delta layer (KDD)", text: "New research between snapshots: arXiv and an inbox folder, de-duplicated, kept only if it is novel against the snapshot, packed as a small ZIM." },
  { icon: SearchCheckIcon, title: "Retrieval", text: "Every lesson searches both layers on this node. Sources cite the archive, so a teacher can see when content came from the delta." },
];

export function KnowledgePage() {
  const { data, error, refresh } = usePoll(api.knowledge, 0);
  const snaps = data?.snapshots || [];
  const delta = data?.delta || [];
  const st = data?.delta_status || {};
  const storeDocs = Object.values(st.store || {}).reduce((a, b) => a + b, 0);
  return (
    <>
      <PageHeader title="Knowledge"
        description="Everything a lesson cites comes from archives stored on this node, so answers stay accurate without internet access. KDD keeps them current."
        actions={<Button variant="outline" onClick={() => void refresh()}><RefreshCwIcon /> Refresh</Button>} />
      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard tint="blue" icon={LibraryIcon} label="Snapshot archives" value={snaps.length}
          hint={`${gb(snaps.reduce((a, z) => a + (z.size_mb || 0), 0))} · ${new Set(snaps.map((z) => z.lang)).size} languages`} />
        <StatCard tint="purple" icon={GitBranchPlusIcon} label="Delta documents" value={storeDocs}
          hint={`${delta.length} delta archive${delta.length === 1 ? "" : "s"} built`} />
        <StatCard tint="green" icon={DatabaseIcon} label="KDD runs" value={st.runs ?? 0} hint="gatemoe kdd run, e.g. weekly from a timer" />
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        {FLOW.map((f, i) => (
          <div key={f.title} className="relative flex gap-4 rounded-3xl border border-mist bg-white p-5">
            <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-panel-tint text-google-blue"><f.icon className="size-5" /></span>
            <div>
              <div className="font-display text-subheading">{f.title}</div>
              <p className="mt-1 text-body-sm text-slate">{f.text}</p>
            </div>
            {i < FLOW.length - 1 && <ArrowRightIcon className="absolute top-1/2 -right-3.5 z-10 hidden size-5 -translate-y-1/2 rounded-full bg-canvas-white text-slate lg:block" />}
          </div>
        ))}
      </div>
      {error && <div className="rounded-3xl bg-red-tint p-5 text-body-sm text-danger">{error}</div>}
      <Card className="overflow-hidden">
        <CardHeader className="pb-4"><CardTitle className="text-subheading">Snapshots</CardTitle><CardDescription>Kiwix ZIM archives in the knowledge folder.</CardDescription></CardHeader>
        {snaps.length ? <ArchiveTable rows={snaps} /> : <CardContent><Empty icon={LibraryIcon} title="No archives installed">Run scripts/download_models.sh or gatemoe kdd snapshots --apply.</Empty></CardContent>}
      </Card>
      <div className="grid items-start gap-6 lg:grid-cols-[1fr_380px]">
        <Card className="overflow-hidden">
          <CardHeader className="pb-4"><CardTitle className="text-subheading">Delta archives</CardTitle><CardDescription>Built by KDD from new material; searched alongside the snapshots.</CardDescription></CardHeader>
          {delta.length ? <ArchiveTable rows={delta} delta /> : <CardContent className="text-body-sm text-slate">No delta archive yet.</CardContent>}
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-subheading">Update from the node</CardTitle><CardDescription>Run on the server (or from a systemd timer).</CardDescription></CardHeader>
          <CardContent className="flex flex-col gap-3">
            {[["Newer snapshots", "gatemoe kdd snapshots --apply"], ["Ingest research + build delta", "gatemoe kdd run"], ["Weak topics in past lessons", "gatemoe kdd gaps"], ["After a new snapshot: drop what it now covers", "gatemoe kdd prune"]].map(([k, c]) => (
              <div key={c}>
                <div className="text-caption text-slate">{k}</div>
                <code className="mt-1 block rounded-xl bg-[#f1f3f4] px-3 py-2 font-mono text-[13px] text-charcoal-ink">{c}</code>
              </div>
            ))}
            {st.store && Object.keys(st.store).length > 0 && (
              <div className="flex flex-wrap gap-1.5 pt-1">{Object.entries(st.store).map(([lg, n]) => <Badge key={lg} variant="outline">{lg}: {n} docs</Badge>)}</div>
            )}
          </CardContent>
        </Card>
      </div>
    </>
  );
}
