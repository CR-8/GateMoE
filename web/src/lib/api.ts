import type { AppConfig, Assignment, ClassroomState, Feedback, KnowledgeState, LessonDetail, LessonSummary, Section,
  Student, Submission, SystemState, TakeView } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { ...init, headers: { "Content-Type": "application/json", ...(init?.headers || {}) } });
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j); } catch { /* text */ }
    throw new ApiError(r.status, msg || `HTTP ${r.status}`);
  }
  return r.json() as Promise<T>;
}
const body = (b: unknown) => JSON.stringify(b);

export const api = {
  config: () => call<AppConfig>("/api/config"),
  system: () => call<SystemState>("/api/system"),
  lessons: () => call<LessonSummary[]>("/api/lessons"),
  lesson: (id: string) => call<LessonDetail>(`/api/lessons/${id}`),
  create: (b: { request: string; language: string; mode: string | null; gateways?: string[] | null }) =>
    call<LessonSummary>("/api/lessons", { method: "POST", body: body(b) }),
  cancel: (id: string) => call<LessonSummary>(`/api/lessons/${id}/cancel`, { method: "POST" }),
  setBoard: (id: string, status: string) => call(`/api/lessons/${id}/meta`, { method: "PATCH", body: body({ status }) }),
  file: (id: string, name: string) => `/api/lessons/${id}/files/${encodeURIComponent(name).replace(/%2F/g, "/")}`,
  events: (id: string) => `/api/lessons/${id}/events`,
  knowledge: () => call<KnowledgeState>("/api/knowledge"),
  zim: (zim: string, path: string) => `/zim/${encodeURIComponent(zim)}/${String(path).split("/").map(encodeURIComponent).join("/")}`,

  classroom: () => call<ClassroomState>("/api/classroom"),
  addStudents: (names: string[]) => call<Student[]>("/api/students", { method: "POST", body: body({ names }) }),
  removeStudent: (id: string) => call(`/api/students/${id}`, { method: "DELETE" }),
  addSection: (b: { name: string; student_ids: string[]; color?: string; kind?: string }) =>
    call<Section>("/api/sections", { method: "POST", body: body(b) }),
  updateSection: (id: string, b: Partial<Section>) => call<Section>(`/api/sections/${id}`, { method: "PATCH", body: body(b) }),
  deleteSection: (id: string) => call(`/api/sections/${id}`, { method: "DELETE" }),
  assign: (b: { lesson_id: string; section_ids: string[]; title?: string; due?: string | null; note?: string }) =>
    call<Assignment>("/api/assignments", { method: "POST", body: body(b) }),
  unassign: (id: string) => call(`/api/assignments/${id}`, { method: "DELETE" }),
  addFeedback: (b: { text: string; points: number; tag?: string }) => call<Feedback>("/api/feedback", { method: "POST", body: body(b) }),
  updateFeedback: (id: string, b: Partial<Feedback>) => call<Feedback>(`/api/feedback/${id}`, { method: "PATCH", body: body(b) }),
  deleteFeedback: (id: string) => call(`/api/feedback/${id}`, { method: "DELETE" }),
  submit: (b: { assignment_id: string; student_id: string; answers: (number | null)[] }) =>
    call<Submission & { explanations: string[] }>("/api/submissions", { method: "POST", body: body(b) }),
  take: (id: string) => call<TakeView>(`/api/assignments/${id}/take`),
  grade: (id: string, b: { feedback_ids?: string[]; comment?: string }) =>
    call<Submission>(`/api/submissions/${id}`, { method: "PATCH", body: body(b) }),
};
