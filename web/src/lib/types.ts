export type Gateway = "notes" | "flashcards" | "quiz" | "podcast" | "video" | "code";
export const GATEWAYS: Gateway[] = ["notes", "flashcards", "quiz", "podcast", "video", "code"];

export type JobStatus = "queued" | "running" | "done" | "failed" | "cancelled" | "interrupted";

export interface LessonSummary {
  id: string; request: string; status: JobStatus; created: number; started?: number | null; finished?: number | null;
  error?: string | null; stage?: string | null; options: { language?: string; mode?: string | null; gateways?: string[] | null };
  title?: string; summary?: string; language?: { lang: string; name: string; native?: string };
  selected?: Gateway[]; route_mode?: string; subject?: string; level?: string;
  has?: Record<string, boolean>; counts?: { cards: number; questions: number; sources: number; sims: number };
  wall_s?: number; errors?: string[]; out_of_scope?: boolean; thumb?: string | null; board?: string | null;
}

export interface QuizQuestion { question: string; options: string[]; answer_index: number; explanation?: string }
export interface Lesson {
  request: string; created: number; language?: { lang: string; name: string; native?: string };
  route?: { mode: string; selected: Gateway[]; gateways: Record<string, number>; subject: string; level: string;
            video_engine: string; in_scope?: number | null; input_tokens?: number | null; latency_s?: number; cached?: boolean;
            subject_probs?: Record<string, number> };
  plan?: { title?: string; key_terms?: string[] };
  notes?: { title: string; sections: { heading: string; body: string }[]; key_points?: string[];
            glossary?: { term: string; definition: string }[] };
  flashcards?: { cards: { front: string; back: string }[] };
  quiz?: { questions: QuizQuestion[] };
  podcast?: { title?: string; turns: { speaker: string; text: string }[] };
  podcast_audio?: { audio: string; duration_s?: number; engine?: string | null;
                    turns?: { speaker: string; start_s?: number; end_s?: number; engine?: string; voice?: string }[] };
  video?: { title?: string; beats: { template: string; title?: string; narration?: string }[] };
  video_render?: { video: string; subtitles?: string; srt?: string; duration_s?: number; engine?: string; render_s?: number;
                   beats?: { errors?: string[] }[] };
  code?: { title?: string; code: string; explanation?: string; expected_output?: string; language?: string };
  concept_map?: { svg?: string; png?: string | null; nodes?: number; edges?: number };
  handout?: { pdf: string; bytes?: number; seconds?: number };
  simulations?: { id: string; title: string; zim: string; path: string; lang?: string; thumbnail?: string | null;
                  categories?: string[] }[];
  sources?: { title: string; zim: string; path: string; lang: string; score?: number; excerpt?: string }[];
  specialists?: { available: { id: string; available?: boolean }[]; activated: string[] };
  metrics?: { wall_seconds: number; stage_seconds: Record<string, number>; model_loads: number; model_load_seconds: number;
              model_peak_rss_mb: number; llm_calls: number; prompt_tokens: number; generated_tokens: number;
              cached_prompt_tokens?: number; min_mem_available_mb?: number; max_temp_c?: number; throttled_seen?: boolean };
  errors?: Record<string, string>; out_of_scope?: boolean; video_engine?: string;
}

export interface LiveEvent { kind: string; t?: number; stage?: string; ok?: boolean; seconds?: number; error?: string;
  [k: string]: unknown }

export interface LessonDetail { job: LessonSummary; stage: string | null; lesson: Lesson | null }

export interface KnowledgeArchive { name: string; file?: string; title?: string; lang?: string; kind?: string; date?: string;
  size_mb?: number; articles?: number; fulltext?: boolean; error?: string }
export interface KnowledgeState { snapshots: KnowledgeArchive[]; delta: KnowledgeArchive[];
  delta_status: { store?: Record<string, number>; delta_archives?: string[]; snapshot_archives?: string[]; runs?: number; error?: string } }

export interface Student { id: string; name: string }
export interface Section { id: string; name: string; kind: string; color: string; student_ids: string[]; archived: boolean }
export interface Assignment { id: string; lesson_id: string; section_ids: string[]; title: string; due?: string | null; note?: string; posted: number }
export interface Feedback { id: string; text: string; points: number; tag?: string; uses: number }
export interface Submission {
  id: string; assignment_id: string; student_id: string; answers: (number | null)[]; correct: number[];
  auto: number; max: number; adjust: number; grade: number; feedback_ids: string[]; comment: string; submitted: number; graded?: number;
}
export interface ClassroomState {
  students: Student[]; sections: Section[]; assignments: Assignment[]; feedback: Feedback[]; submissions: Submission[];
  lesson_meta: Record<string, { status?: string }>;
}

export interface TakeView {
  assignment: Assignment; lesson_id: string; title: string; language?: { lang: string; name: string };
  sections: { id: string; name: string; color: string }[];
  students: { id: string; name: string; submitted: boolean }[];
  questions: { question: string; options: string[] }[]; key_points: string[];
  files: { notes?: string; handout?: string; podcast?: string; video?: string; subtitles?: string };
}

export interface AppConfig {
  languages: Record<string, { name: string; native: string; tts?: string }>; gateways: Gateway[];
  router_mode: string; threshold: number; models: Record<string, string>;
  generate: boolean;      // false: browse, post and grade only (the server never loads a model)
  classroom: { palette: string[]; kinds: Record<string, string>; points_per_question: number };
}

export interface SystemState {
  sys: { mem_total_mb: number; mem_available_mb: number; swap_used_mb: number; temp_c: number | null; throttled: string | null };
  models: { loaded: string | null; pid?: number | null; history?: { event: string; role: string; seconds?: number }[] };
  specialists: { id: string; gateway: string; available?: boolean; size_mb?: number | null; lang?: string; error?: string }[];
  queue: number; generate?: boolean;
}
