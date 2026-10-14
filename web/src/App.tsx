import { AppSidebar, MobileNav } from "@/components/app-sidebar";
import { DialogsProvider } from "@/components/dialogs";
import { useRoute } from "@/lib/store";
import { BoardPage } from "@/pages/board";
import { LessonPage } from "@/pages/lesson";
import { SectionsPage } from "@/pages/sections";
import { GradingPage } from "@/pages/grading";
import { TakePage } from "@/pages/take";
import { KnowledgePage } from "@/pages/knowledge";
import { SystemPage } from "@/pages/system";

function Page({ route }: { route: string[] }) {
  switch (route[0]) {
    case "lesson": return <LessonPage key={route[1]} id={route[1]} />;
    case "sections": return <SectionsPage archived={route[1] === "archived"} />;
    case "grading": return <GradingPage assignmentId={route[1] === "section" ? undefined : route[1]} sectionId={route[2]} />;
    case "knowledge": return <KnowledgePage />;
    case "system": return <SystemPage />;
    default: return <BoardPage />;
  }
}

export function App() {
  const route = useRoute();
  // the student quiz view is a page of its own: no teacher navigation
  if (route[0] === "take") return <TakePage id={route[1]} />;
  return (
    <DialogsProvider>
      <div className="flex min-h-full">
        <AppSidebar route={route} />
        <div className="flex min-w-0 flex-1 flex-col">
          <MobileNav route={route} />
          <main className="mx-auto flex w-full max-w-[1440px] flex-1 flex-col gap-6 px-4 py-6 sm:px-32 lg:py-32">
            <Page route={route} />
          </main>
        </div>
      </div>
    </DialogsProvider>
  );
}
