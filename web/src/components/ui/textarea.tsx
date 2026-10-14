import * as React from "react";
import { cn } from "@/lib/utils";

function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea data-slot="textarea"
      className={cn("min-h-[96px] w-full rounded-md border border-mist bg-white px-3 py-2 text-body-sm text-charcoal-ink placeholder:text-slate outline-none focus-visible:border-google-blue focus-visible:shadow-[0_0_0_3px_#c4c6c7] disabled:opacity-50", className)}
      {...props} />
  );
}

export { Textarea };
