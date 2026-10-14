import * as React from "react";
import { cn } from "@/lib/utils";

function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <input type={type} data-slot="input"
      className={cn("h-10 w-full min-w-0 rounded-md border border-mist bg-white px-3 text-body-sm text-charcoal-ink placeholder:text-slate outline-none transition-shadow focus-visible:border-google-blue focus-visible:shadow-[0_0_0_3px_#c4c6c7] disabled:opacity-50", className)}
      {...props} />
  );
}

export { Input };
