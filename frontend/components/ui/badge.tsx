import type { HTMLAttributes } from "react";
import { cn } from "@/lib/format";

export type BadgeTone = "neutral" | "good" | "warn" | "bad" | "info" | "sim" | "accent";

const tones: Record<BadgeTone, string> = {
  neutral: "bg-surface-2 text-muted border-border",
  good: "bg-good-bg text-good border-good/30",
  warn: "bg-warn-bg text-warn border-warn/30",
  bad: "bg-bad-bg text-bad border-bad/30",
  info: "bg-info-bg text-accent border-accent/30",
  sim: "bg-sim-bg text-sim border-sim/30",
  accent: "bg-accent text-accent-fg border-accent",
};

export function Badge({ tone = "neutral", className, ...props }: HTMLAttributes<HTMLSpanElement> & { tone?: BadgeTone }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-sm border px-1.5 py-px text-[11px] font-medium leading-4",
        tones[tone],
        className,
      )}
      {...props}
    />
  );
}

/** Every value derived from a local fork simulation must carry this pill. */
export function SimulatedPill({ className }: { className?: string }) {
  return (
    <Badge tone="sim" className={cn("uppercase tracking-wide", className)} title="Result of a local fork simulation, not on-chain earnings">
      simulated
    </Badge>
  );
}

export function StatusDot({ ok, label, pending }: { ok: boolean; label?: string; pending?: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs">
      <span
        aria-hidden
        className={cn(
          "inline-block size-2 rounded-full",
          pending ? "bg-warn animate-pulse-soft" : ok ? "bg-good" : "bg-bad",
        )}
      />
      {label ? <span>{label}</span> : <span className="sr-only">{ok ? "ok" : "down"}</span>}
    </span>
  );
}
