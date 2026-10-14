import * as React from "react";
import { api } from "./api";
import type { AppConfig, ClassroomState, LessonSummary, SystemState } from "./types";

function usePoll<T>(fn: () => Promise<T>, ms: number, deps: unknown[] = []) {
  const [data, setData] = React.useState<T | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const refresh = React.useCallback(async () => {
    try { setData(await fn()); setError(null); } catch (e) { setError((e as Error).message); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  React.useEffect(() => {
    void refresh();
    if (!ms) return;
    const t = window.setInterval(() => { if (document.visibilityState === "visible") void refresh(); }, ms);
    return () => window.clearInterval(t);
  }, [refresh, ms]);
  return { data, error, refresh };
}

interface Store {
  config: AppConfig | null;
  lessons: LessonSummary[]; refreshLessons: () => Promise<void>;
  classroom: ClassroomState; refreshClassroom: () => Promise<void>;
  system: SystemState | null;
}
const EMPTY: ClassroomState = { students: [], sections: [], assignments: [], feedback: [], submissions: [], lesson_meta: {} };
const StoreContext = React.createContext<Store | null>(null);

export function StoreProvider({ children }: { children: React.ReactNode }) {
  const config = usePoll(api.config, 0);
  const lessons = usePoll(api.lessons, 4000);
  const classroom = usePoll(api.classroom, 15000);
  const system = usePoll(api.system, 5000);
  const value: Store = {
    config: config.data, lessons: lessons.data || [], refreshLessons: lessons.refresh,
    classroom: classroom.data || EMPTY, refreshClassroom: classroom.refresh, system: system.data,
  };
  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}

export function useStore(): Store {
  const s = React.useContext(StoreContext);
  if (!s) throw new Error("useStore outside StoreProvider");
  return s;
}

export { usePoll };

/* Hash router: #/, #/lesson/<id>, #/sections, #/grading, #/take/<assignment>, #/knowledge, #/system */
export function useRoute(): string[] {
  const read = () => window.location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const [parts, setParts] = React.useState(read);
  React.useEffect(() => {
    const on = () => { setParts(read()); window.scrollTo(0, 0); };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return parts;
}
export const go = (path: string) => { window.location.hash = path.startsWith("#") ? path : `#${path}`; };
