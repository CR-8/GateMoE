/* Kanban - same API shape as Kibo UI's kanban (KanbanProvider / KanbanBoard / KanbanHeader /
   KanbanCards / KanbanCard), built on dnd-kit. Columns accept drops; `onDataChange` receives the
   items with the moved card's `column` updated, and `canMove` can veto a move. */
import * as React from "react";
import {
  DndContext, DragOverlay, KeyboardSensor, PointerSensor, closestCorners, useDraggable, useDroppable,
  useSensor, useSensors, type DragEndEvent, type DragStartEvent,
} from "@dnd-kit/core";
import { cn } from "@/lib/utils";

export type KanbanItem = { id: string; column: string; name?: string };
export type KanbanColumn = { id: string; name: string; color?: string };

type Ctx = { columns: KanbanColumn[]; activeId: string | null };
const KanbanContext = React.createContext<Ctx>({ columns: [], activeId: null });

export function KanbanProvider<T extends KanbanItem>({
  columns, data, onDataChange, canMove, renderOverlay, children, className,
}: {
  columns: KanbanColumn[]; data: T[]; onDataChange?: (data: T[], moved: T, to: string) => void;
  canMove?: (item: T, to: string) => boolean; renderOverlay?: (item: T) => React.ReactNode;
  children: (column: KanbanColumn) => React.ReactNode; className?: string;
}) {
  const [activeId, setActiveId] = React.useState<string | null>(null);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }), useSensor(KeyboardSensor));
  const onStart = (e: DragStartEvent) => setActiveId(String(e.active.id));
  const onEnd = (e: DragEndEvent) => {
    setActiveId(null);
    const item = data.find((d) => d.id === String(e.active.id));
    if (!item || !e.over) return;
    const overId = String(e.over.id);
    const to = columns.some((c) => c.id === overId) ? overId : data.find((d) => d.id === overId)?.column;
    if (!to || to === item.column || (canMove && !canMove(item, to))) return;
    const moved = { ...item, column: to };
    onDataChange?.(data.map((d) => (d.id === item.id ? moved : d)), moved, to);
  };
  const active = activeId ? data.find((d) => d.id === activeId) : undefined;
  return (
    <KanbanContext.Provider value={{ columns, activeId }}>
      <DndContext sensors={sensors} collisionDetection={closestCorners} onDragStart={onStart} onDragEnd={onEnd}
        onDragCancel={() => setActiveId(null)}>
        <div className={cn("grid auto-cols-[minmax(272px,1fr)] grid-flow-col gap-4", className)}>
          {columns.map((c) => <React.Fragment key={c.id}>{children(c)}</React.Fragment>)}
        </div>
        <DragOverlay dropAnimation={null}>{active && renderOverlay ? renderOverlay(active) : null}</DragOverlay>
      </DndContext>
    </KanbanContext.Provider>
  );
}

export function KanbanBoard({ id, className, children }: { id: string; className?: string; children: React.ReactNode }) {
  const { isOver, setNodeRef } = useDroppable({ id });
  return (
    <div ref={setNodeRef} data-over={isOver || undefined}
      className={cn("flex min-h-[160px] flex-col gap-3 rounded-3xl p-1 transition-colors data-[over]:bg-panel-tint/60", className)}>
      {children}
    </div>
  );
}

export function KanbanHeader({ className, children }: { className?: string; children: React.ReactNode }) {
  return <div className={cn("flex items-center gap-2 px-2 pt-1 font-display text-subheading text-charcoal-ink", className)}>{children}</div>;
}

export function KanbanCards({ className, children }: { className?: string; children: React.ReactNode }) {
  return <div className={cn("flex flex-1 flex-col gap-3", className)}>{children}</div>;
}

export function KanbanCard({ id, disabled, className, children }:
  { id: string; disabled?: boolean; className?: string; children: React.ReactNode }) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id, disabled });
  return (
    <div ref={setNodeRef} {...attributes} {...listeners}
      className={cn("rounded-2xl-2 border border-mist bg-white p-4 text-left outline-none transition-[border-color,opacity] hover:border-cloud-shadow focus-visible:border-google-blue",
        !disabled && "cursor-grab active:cursor-grabbing", isDragging && "opacity-40", className)}>
      {children}
    </div>
  );
}

export const useKanban = () => React.useContext(KanbanContext);
