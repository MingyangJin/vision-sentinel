import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium tracking-tight transition-colors",
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary/15 text-primary",
        secondary: "border-border bg-secondary text-secondary-foreground",
        outline: "border-border text-muted-foreground",
        success:
          "border-emerald-500/30 bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
        warning:
          "border-amber-500/30 bg-amber-500/15 text-amber-700 dark:text-amber-300",
        danger:
          "border-rose-500/30 bg-rose-500/15 text-rose-600 dark:text-rose-400",
        info:
          "border-sky-500/30 bg-sky-500/15 text-sky-700 dark:text-sky-300",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
