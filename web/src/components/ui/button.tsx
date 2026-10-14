import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

// Design system: every button is a pill (radius 200px). One filled ("default") button per view;
// pair it with "outline" (ghost pill) for secondary actions.
const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-full text-body-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg:not([class*='size-'])]:size-4 shrink-0 [&_svg]:shrink-0 outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
  {
    variants: {
      variant: {
        default: "bg-google-blue text-white hover:bg-google-blue-deep",
        outline: "border border-mist bg-transparent text-google-blue hover:bg-canvas-white hover:border-google-blue",
        secondary: "bg-white border border-mist text-graphite hover:bg-canvas-white",
        ghost: "text-graphite hover:bg-black/5",
        link: "text-google-blue underline-offset-2 hover:underline hover:text-google-blue-deep rounded-md",
        destructive: "bg-danger text-white hover:bg-danger/90",
        dark: "bg-charcoal-ink text-white hover:bg-graphite",
      },
      size: {
        default: "h-10 px-6",
        sm: "h-32 px-4 text-caption",
        lg: "h-12 px-32 text-body",
        icon: "size-10",
        "icon-sm": "size-32",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

function Button({ className, variant, size, asChild = false, ...props }:
  React.ComponentProps<"button"> & VariantProps<typeof buttonVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot : "button";
  return <Comp data-slot="button" className={cn(buttonVariants({ variant, size, className }))} {...props} />;
}

export { Button, buttonVariants };
