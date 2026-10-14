import type { ClassroomState, LessonSummary } from "./types";

export type Column = "queued" | "generating" | "review" | "published";
export const COLUMNS: { id: Column; name: string; color: string }[] = [
  { id: "queued", name: "Queued", color: "#5f6368" },
  { id: "generating", name: "Generating", color: "#1a73e8" },
  { id: "review", name: "Ready for review", color: "#e37400" },
  { id: "published", name: "Published", color: "#188038" },
];

/** Board column of a lesson: job status first, then the teacher's status (lesson_meta). */
export function columnOf(l: LessonSummary): Column | "archived" {
  if (l.status === "queued") return "queued";
  if (l.status === "running") return "generating";
  if (l.board === "archived") return "archived";
  if (l.status === "done" && l.board === "published") return "published";
  return "review";
}

export const isFailed = (l: LessonSummary) => ["failed", "cancelled", "interrupted"].includes(l.status);

export function sectionsOf(lessonId: string, cls: ClassroomState) {
  const ids = new Set(cls.assignments.filter((a) => a.lesson_id === lessonId).flatMap((a) => a.section_ids));
  return cls.sections.filter((s) => ids.has(s.id));
}

export function lessonTitle(l: { title?: string; request: string }) {
  return l.title || (l.request.length > 80 ? `${l.request.slice(0, 77)}…` : l.request);
}
