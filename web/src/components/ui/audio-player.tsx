/* AudioPlayer - an audio-cn style composable player (shadcn primitives): play/pause, seek
   slider, time, skip +/-10 s, speed, download. Works fully offline with any <audio> source. */
import * as React from "react";
import { DownloadIcon, PauseIcon, PlayIcon, RotateCcwIcon, RotateCwIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { cn } from "@/lib/utils";

const fmt = (s: number) => (isFinite(s) ? `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}` : "0:00");
const SPEEDS = [0.75, 1, 1.25, 1.5, 2];

export function AudioPlayer({ src, title, subtitle, className, onTime }:
  { src: string; title?: string; subtitle?: string; className?: string; onTime?: (t: number) => void }) {
  const ref = React.useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = React.useState(false);
  const [t, setT] = React.useState(0);
  const [dur, setDur] = React.useState(0);
  const [speed, setSpeed] = React.useState(1);
  const toggle = () => { const a = ref.current; if (!a) return; if (a.paused) void a.play(); else a.pause(); };
  const seek = (v: number) => { const a = ref.current; if (a) { a.currentTime = Math.max(0, Math.min(dur || 0, v)); setT(a.currentTime); } };
  React.useEffect(() => { if (ref.current) ref.current.playbackRate = speed; }, [speed]);
  return (
    <div className={cn("flex flex-col gap-4 rounded-3xl border border-mist bg-white p-5", className)}>
      <audio ref={ref} src={src} preload="metadata"
        onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)}
        onLoadedMetadata={(e) => setDur(e.currentTarget.duration)}
        onTimeUpdate={(e) => { setT(e.currentTarget.currentTime); onTime?.(e.currentTarget.currentTime); }} />
      <div className="flex items-center gap-4">
        <Button size="icon" className="size-12 shrink-0" onClick={toggle} aria-label={playing ? "Pause" : "Play"}>
          {playing ? <PauseIcon className="size-5" fill="currentColor" /> : <PlayIcon className="size-5 translate-x-px" fill="currentColor" />}
        </Button>
        <div className="min-w-0 flex-1">
          {title && <div className="truncate font-display text-subheading text-charcoal-ink">{title}</div>}
          {subtitle && <div className="truncate text-caption text-slate">{subtitle}</div>}
        </div>
        <a href={src} download className="rounded-full p-2 text-slate hover:bg-canvas-white hover:text-charcoal-ink" aria-label="Download audio">
          <DownloadIcon className="size-4" />
        </a>
      </div>
      <Slider value={[t]} max={dur || 1} step={0.1} onValueChange={(v) => seek(v[0])} />
      <div className="flex items-center justify-between gap-2 text-caption text-slate tabular-nums">
        <span>{fmt(t)}</span>
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon-sm" onClick={() => seek(t - 10)} aria-label="Back 10 seconds"><RotateCcwIcon /></Button>
          <Button variant="ghost" size="icon-sm" onClick={() => seek(t + 10)} aria-label="Forward 10 seconds"><RotateCwIcon /></Button>
          <Button variant="secondary" size="sm" className="h-7 min-w-14 px-3"
            onClick={() => setSpeed(SPEEDS[(SPEEDS.indexOf(speed) + 1) % SPEEDS.length])}>{speed}×</Button>
        </div>
        <span>{fmt(dur)}</span>
      </div>
    </div>
  );
}
