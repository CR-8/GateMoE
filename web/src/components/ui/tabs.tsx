import * as React from "react";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import { cn } from "@/lib/utils";

// Tab switcher: labels inside one pill container; the active tab is a subtle grey pill.
function Tabs({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Root>) {
  return <TabsPrimitive.Root data-slot="tabs" className={cn("flex flex-col gap-6", className)} {...props} />;
}
function TabsList({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.List>) {
  return <TabsPrimitive.List data-slot="tabs-list"
    className={cn("inline-flex max-w-full items-center gap-0.5 overflow-x-auto rounded-full border border-mist bg-white p-1 scrollbar-thin", className)} {...props} />;
}
function TabsTrigger({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Trigger>) {
  return <TabsPrimitive.Trigger data-slot="tabs-trigger"
    className={cn("inline-flex h-32 shrink-0 items-center gap-1.5 rounded-full px-4 text-body-sm text-slate transition-colors hover:text-charcoal-ink data-[state=active]:bg-canvas-white data-[state=active]:font-medium data-[state=active]:text-charcoal-ink [&_svg]:size-4", className)} {...props} />;
}
function TabsContent({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Content>) {
  return <TabsPrimitive.Content data-slot="tabs-content" className={cn("outline-none", className)} {...props} />;
}

export { Tabs, TabsList, TabsTrigger, TabsContent };
