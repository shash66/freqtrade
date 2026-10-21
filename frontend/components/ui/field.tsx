import type { InputHTMLAttributes, ReactNode, Ref, SelectHTMLAttributes } from "react";
import { cn } from "@/lib/format";

export function Field({ label, hint, children, className }: { label: ReactNode; hint?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <label className={cn("flex min-w-0 flex-col gap-1", className)}>
      <span className="text-[11px] uppercase tracking-wide text-muted">{label}</span>
      {children}
      {hint ? <span className="text-[11px] text-faint">{hint}</span> : null}
    </label>
  );
}

const control =
  "h-8 w-full min-w-0 rounded-md border border-border-strong bg-surface px-2 text-xs text-foreground placeholder:text-faint disabled:opacity-60";

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement> & { ref?: Ref<HTMLInputElement> }) {
  return <input className={cn(control, props.type === "number" && "tabular", className)} {...props} />;
}

export function Select({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cn(control, "pr-6", className)} {...props} />;
}

export function Checkbox({ label, className, ...props }: InputHTMLAttributes<HTMLInputElement> & { label: ReactNode }) {
  return (
    <label className={cn("inline-flex cursor-pointer items-center gap-2 text-xs", className)}>
      <input type="checkbox" className="size-3.5 accent-[var(--accent)]" {...props} />
      <span>{label}</span>
    </label>
  );
}
