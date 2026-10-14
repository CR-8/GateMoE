import * as React from "react";
import * as ToggleGroupPrimitive from "@radix-ui/react-toggle-group";
import { cn } from "@/lib/utils";

function ToggleGroup({ className, ...props }: React.ComponentProps<typeof ToggleGroupPrimitive.Root>) {
  return <ToggleGroupPrimitive.Root data-slot="toggle-group" className={cn("inline-flex items-center gap-1", className)} {...props} />;
}
// Matches the reference board header: outlined pills, the active one filled charcoal.
function ToggleGroupItem({ className, ...props }: React.ComponentProps<typeof ToggleGroupPrimitive.Item>) {
  return <ToggleGroupPrimitive.Item data-slot="toggle-group-item"
    className={cn("inline-flex h-9 items-center gap-1.5 rounded-full border border-mist bg-white px-4 text-body-sm text-graphite hover:bg-canvas-white data-[state=on]:border-charcoal-ink data-[state=on]:bg-charcoal-ink data-[state=on]:text-white [&_svg]:size-4", className)} {...props} />;
}

export { ToggleGroup, ToggleGroupItem };
