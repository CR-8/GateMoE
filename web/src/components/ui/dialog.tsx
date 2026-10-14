import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { XIcon } from "lucide-react";
import { cn } from "@/lib/utils";

// Modal: scrim rgba(32,33,36,.5), white card, 8px radius, no shadow (design system).
function Dialog(props: React.ComponentProps<typeof DialogPrimitive.Root>) {
  return <DialogPrimitive.Root data-slot="dialog" {...props} />;
}
const DialogTrigger = DialogPrimitive.Trigger;
const DialogClose = DialogPrimitive.Close;

function DialogContent({ className, children, side, ...props }:
  React.ComponentProps<typeof DialogPrimitive.Content> & { side?: "center" | "right" }) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-[#20212480] data-[state=open]:animate-in data-[state=open]:fade-in-0" />
      <DialogPrimitive.Content data-slot="dialog-content"
        className={cn(side === "right"
          ? "fixed inset-y-0 right-0 z-50 flex w-full max-w-xl flex-col bg-white outline-none sm:rounded-l-3xl"
          : "fixed top-1/2 left-1/2 z-50 flex max-h-[90vh] w-[calc(100%-32px)] max-w-lg -translate-x-1/2 -translate-y-1/2 flex-col rounded-lg bg-white outline-none",
          className)} {...props}>
        {children}
        <DialogPrimitive.Close className="absolute top-4 right-4 rounded-full p-2 text-slate hover:bg-canvas-white hover:text-charcoal-ink">
          <XIcon className="size-5" />
          <span className="sr-only">Close</span>
        </DialogPrimitive.Close>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}
function DialogHeader({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="dialog-header" className={cn("flex flex-col gap-1 p-6 pb-2 pr-14", className)} {...props} />;
}
function DialogBody({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="dialog-body" className={cn("min-h-0 flex-1 overflow-y-auto px-6 py-2 scrollbar-thin", className)} {...props} />;
}
function DialogFooter({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="dialog-footer" className={cn("flex flex-wrap items-center justify-end gap-4 p-6", className)} {...props} />;
}
function DialogTitle({ className, ...props }: React.ComponentProps<typeof DialogPrimitive.Title>) {
  return <DialogPrimitive.Title data-slot="dialog-title" className={cn("font-display text-heading-sm text-charcoal-ink", className)} {...props} />;
}
function DialogDescription({ className, ...props }: React.ComponentProps<typeof DialogPrimitive.Description>) {
  return <DialogPrimitive.Description data-slot="dialog-description" className={cn("text-body-sm text-slate", className)} {...props} />;
}

export { Dialog, DialogTrigger, DialogClose, DialogContent, DialogHeader, DialogBody, DialogFooter, DialogTitle, DialogDescription };
