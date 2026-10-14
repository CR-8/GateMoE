import * as React from "react";
import * as SelectPrimitive from "@radix-ui/react-select";
import { CheckIcon, ChevronDownIcon } from "lucide-react";
import { cn } from "@/lib/utils";

const Select = SelectPrimitive.Root;
const SelectValue = SelectPrimitive.Value;

function SelectTrigger({ className, children, ...props }: React.ComponentProps<typeof SelectPrimitive.Trigger>) {
  return (
    <SelectPrimitive.Trigger data-slot="select-trigger"
      className={cn("flex h-10 w-full items-center justify-between gap-2 rounded-md border border-mist bg-white px-3 text-body-sm text-charcoal-ink outline-none focus-visible:border-google-blue data-[placeholder]:text-slate [&>span]:truncate", className)} {...props}>
      {children}
      <SelectPrimitive.Icon asChild><ChevronDownIcon className="size-4 text-slate" /></SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
  );
}
function SelectContent({ className, children, position = "popper", ...props }: React.ComponentProps<typeof SelectPrimitive.Content>) {
  return (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Content data-slot="select-content" position={position}
        className={cn("relative z-50 max-h-[320px] min-w-[128px] overflow-hidden rounded-2xl border border-mist bg-white", position === "popper" && "w-[var(--radix-select-trigger-width)] translate-y-1", className)} {...props}>
        <SelectPrimitive.Viewport className="p-1.5">{children}</SelectPrimitive.Viewport>
      </SelectPrimitive.Content>
    </SelectPrimitive.Portal>
  );
}
function SelectItem({ className, children, ...props }: React.ComponentProps<typeof SelectPrimitive.Item>) {
  return (
    <SelectPrimitive.Item data-slot="select-item"
      className={cn("relative flex w-full cursor-default items-center rounded-lg py-2 pr-32 pl-3 text-body-sm outline-none select-none data-[highlighted]:bg-canvas-white data-[disabled]:opacity-50", className)} {...props}>
      <span className="absolute right-2 flex size-4 items-center justify-center">
        <SelectPrimitive.ItemIndicator><CheckIcon className="size-4 text-google-blue" /></SelectPrimitive.ItemIndicator>
      </span>
      <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
    </SelectPrimitive.Item>
  );
}

export { Select, SelectValue, SelectTrigger, SelectContent, SelectItem };
