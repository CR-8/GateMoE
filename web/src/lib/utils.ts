import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

// tailwind-merge must know the design system's named font sizes (index.css @theme), otherwise it
// reads `text-heading-sm` as a colour and drops it when a colour class (`text-charcoal-ink`) follows.
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "font-size": [{ text: ["caption", "body-sm", "body", "subheading", "heading-sm", "heading", "heading-lg", "display"] }],
    },
  },
});

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
