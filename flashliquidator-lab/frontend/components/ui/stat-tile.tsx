import type { ReactNode } from "react";
import { cn } from "@/lib/format";
import { Skeleton } from "@/components/ui/skeleton";

export function StatTile({
  label,
  value,
  hint,
  tag,
  loading,
  tone,
  className,
}: {
  label: ReactNode;
  value: ReactNode;
  hint?: ReactNode;
  tag?: ReactNode;
  loading?: boolean;
  tone?: "default" | "good" | "warn" | "bad" | "sim";
  className?: string;
}) {
  const valueTone =
    tone === "good" ? "text-good" : tone === "warn" ? "text-warn" : tone === "bad" ? "text-bad" : tone === "sim" ? "text-sim" : "";
  return (
    <div className={cn("flex min-w-0 flex-col gap-1 rounded-md border border-border bg-surface px-3 py-2", className)}>
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-[11px] uppercase tracking-wide text-muted">{label}</span>
        {tag}
      </div>
      {loading ? (
        <Skeleton className="h-6 w-20" />
      ) : (
        <span className={cn("tabular text-xl font-semibold leading-7", valueTone)}>{value}</span>
      )}
      {hint ? <span className="truncate text-[11px] text-faint">{hint}</span> : null}
    </div>
  );
}
