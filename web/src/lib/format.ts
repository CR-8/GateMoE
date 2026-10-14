import type { Gateway } from "./types";

export function ago(ts?: number | null): string {
  if (!ts) return "";
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(ts * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}
export const dateShort = (ts?: number | null) =>
  ts ? new Date(ts * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "";
export function duration(s?: number | null): string {
  if (s == null || !isFinite(s)) return "–";
  if (s < 60) return `${Math.round(s)} s`;
  const m = Math.floor(s / 60), r = Math.round(s % 60);
  return m < 60 ? `${m} min${r ? ` ${r} s` : ""}` : `${Math.floor(m / 60)} h ${m % 60} min`;
}
export const initials = (name: string) =>
  name.split(/\s+/).filter((w) => /[\p{L}\p{N}]/u.test(w)).slice(0, 2)
    .map((w) => Array.from(w.replace(/^[^\p{L}\p{N}]+/u, ""))[0]?.toUpperCase()).join("") || "?";

export const GATEWAY_META: Record<Gateway, { label: string; variant: "default" | "green" | "yellow" | "red" | "purple" | "gray" }> = {
  notes: { label: "Notes", variant: "default" },
  flashcards: { label: "Flashcards", variant: "yellow" },
  quiz: { label: "Quiz", variant: "green" },
  podcast: { label: "Podcast", variant: "purple" },
  video: { label: "Video", variant: "red" },
  code: { label: "Code", variant: "gray" },
};

export const STAGE_LABELS: Record<string, string> = {
  language: "Detecting language", route: "Routing (Clef-flash)", plan: "Planning", retrieve: "Searching offline knowledge",
  simulations: "Matching simulations", gen_notes: "Writing notes", gen_flashcards: "Writing flashcards", gen_quiz: "Writing quiz",
  gen_code: "Writing code", gen_podcast: "Writing podcast script", gen_video: "Planning video", check_video: "Checking video plan",
  unload_generator: "Freeing memory", tts_podcast: "Recording podcast", tts_narration: "Recording narration",
  render_video: "Rendering video", concept_map: "Drawing concept map", package: "Packaging", handout: "Typesetting handout",
};
