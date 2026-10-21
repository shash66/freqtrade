import type { ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/format";

type Variant = "primary" | "secondary" | "ghost" | "danger";

const variants: Record<Variant, string> = {
  primary: "bg-accent text-accent-fg border-accent hover:opacity-90",
  secondary: "bg-surface text-foreground border-border-strong hover:bg-surface-2",
  ghost: "bg-transparent text-muted border-transparent hover:bg-surface-2 hover:text-foreground",
  danger: "bg-bad-bg text-bad border-bad/40 hover:opacity-90",
};

export function Button({
  variant = "secondary",
  size = "md",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md" }) {
  return (
    <button
      type="button"
      className={cn(
        "inline-flex items-center justify-center gap-1 whitespace-nowrap rounded-md border font-medium disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "h-6 px-2 text-[11px]" : "h-8 px-3 text-xs",
        variants[variant],
        className,
      )}
      {...props}
    />
  );
}
