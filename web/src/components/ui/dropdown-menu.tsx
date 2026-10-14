import * as React from "react";
import * as DropdownMenuPrimitive from "@radix-ui/react-dropdown-menu";
import { cn } from "@/lib/utils";

const DropdownMenu = DropdownMenuPrimitive.Root;
const DropdownMenuTrigger = DropdownMenuPrimitive.Trigger;

function DropdownMenuContent({ className, sideOffset = 6, ...props }: React.ComponentProps<typeof DropdownMenuPrimitive.Content>) {
  return (
    <DropdownMenuPrimitive.Portal>
      <DropdownMenuPrimitive.Content data-slot="dropdown-menu-content" sideOffset={sideOffset}
        className={cn("z-50 min-w-44 overflow-hidden rounded-2xl border border-mist bg-white p-1.5 text-body-sm text-charcoal-ink", className)} {...props} />
    </DropdownMenuPrimitive.Portal>
  );
}
function DropdownMenuItem({ className, variant, ...props }:
  React.ComponentProps<typeof DropdownMenuPrimitive.Item> & { variant?: "destructive" }) {
  return <DropdownMenuPrimitive.Item data-slot="dropdown-menu-item"
    className={cn("relative flex cursor-default items-center gap-2 rounded-lg px-3 py-2 outline-none select-none data-[highlighted]:bg-canvas-white data-[disabled]:opacity-50 [&_svg]:size-4 [&_svg]:text-slate",
      variant === "destructive" && "text-danger [&_svg]:text-danger", className)} {...props} />;
}
function DropdownMenuLabel({ className, ...props }: React.ComponentProps<typeof DropdownMenuPrimitive.Label>) {
  return <DropdownMenuPrimitive.Label className={cn("px-3 py-1.5 text-caption text-slate", className)} {...props} />;
}
function DropdownMenuSeparator({ className, ...props }: React.ComponentProps<typeof DropdownMenuPrimitive.Separator>) {
  return <DropdownMenuPrimitive.Separator className={cn("-mx-1.5 my-1 h-px bg-mist", className)} {...props} />;
}

export { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator };
