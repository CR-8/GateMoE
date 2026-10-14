/* PdfViewer - a pdfcn style viewer on react-pdf (pdf.js, worker bundled: no CDN, works offline).
   Page navigation, zoom, fit-to-width, download. */
import * as React from "react";
import { Document, Page, pdfjs } from "react-pdf";
import workerSrc from "pdfjs-dist/legacy/build/pdf.worker.min.mjs?url";
import { ChevronLeftIcon, ChevronRightIcon, DownloadIcon, MinusIcon, PlusIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

pdfjs.GlobalWorkerOptions.workerSrc = workerSrc;

export function PdfViewer({ src, fileName = "handout.pdf", className }: { src: string; fileName?: string; className?: string }) {
  const [pages, setPages] = React.useState(0);
  const [page, setPage] = React.useState(1);
  const [zoom, setZoom] = React.useState(1);
  const [width, setWidth] = React.useState(720);
  const box = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(Math.min(900, el.clientWidth - 32)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return (
    <div className={cn("flex flex-col overflow-hidden rounded-3xl border border-mist bg-white", className)}>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-mist px-4 py-2">
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon-sm" disabled={page <= 1} onClick={() => setPage(page - 1)} aria-label="Previous page"><ChevronLeftIcon /></Button>
          <span className="min-w-20 text-center text-body-sm text-graphite tabular-nums">{pages ? `${page} / ${pages}` : "…"}</span>
          <Button variant="ghost" size="icon-sm" disabled={page >= pages} onClick={() => setPage(page + 1)} aria-label="Next page"><ChevronRightIcon /></Button>
        </div>
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon-sm" onClick={() => setZoom(Math.max(0.5, zoom - 0.25))} aria-label="Zoom out"><MinusIcon /></Button>
          <button className="min-w-14 rounded-full px-2 text-body-sm text-graphite hover:bg-canvas-white" onClick={() => setZoom(1)}>{Math.round(zoom * 100)}%</button>
          <Button variant="ghost" size="icon-sm" onClick={() => setZoom(Math.min(3, zoom + 0.25))} aria-label="Zoom in"><PlusIcon /></Button>
          <Button variant="outline" size="sm" asChild className="ml-2"><a href={src} download={fileName}><DownloadIcon /> Download</a></Button>
        </div>
      </div>
      <div ref={box} className="min-h-[420px] flex-1 overflow-auto bg-canvas-white p-4 scrollbar-thin">
        <Document file={src} onLoadSuccess={({ numPages }) => { setPages(numPages); setPage(1); }}
          loading={<div className="p-12 text-center text-slate">Loading PDF…</div>}
          error={<div className="p-12 text-center text-slate">The PDF could not be shown. <a className="text-google-blue underline" href={src}>Open it</a></div>}>
          <Page pageNumber={page} width={width * zoom} renderTextLayer={false} renderAnnotationLayer={false} />
        </Document>
      </div>
    </div>
  );
}
