import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

// Chips: fully rounded, tinted, caption size.
const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-caption font-medium whitespace-nowrap [&_svg]:size-3",
  {
    variants: {
      variant: {
        default: "bg-panel-tint text-google-blue-deep",
        green: "bg-green-tint text-classroom-green",
        yellow: "bg-yellow-tint text-[#b06000]",
        red: "bg-red-tint text-danger",
        purple: "bg-purple-tint text-[#9334e6]",
        gray: "bg-[#f1f3f4] text-graphite",
        outline: "border border-mist text-graphite bg-white",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

function Badge({ className, variant, ...props }: React.ComponentProps<"span"> & VariantProps<typeof badgeVariants>) {
  return <span data-slot="badge" className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
