import type { ReactNode } from "react";
import { cn } from "@/lib/format";

/** Compact definition list for key/value panels. */
export function KV({ items, className }: { items: Array<{ k: ReactNode; v: ReactNode; mono?: boolean }>; className?: string }) {
  return (
    <dl className={cn("grid grid-cols-[minmax(0,40%)_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs", className)}>
      {items.map((it, i) => (
        <div key={i} className="contents">
          <dt className="truncate text-muted">{it.k}</dt>
          <dd className={cn("min-w-0 break-all", it.mono && "font-mono")}>{it.v}</dd>
        </div>
      ))}
    </dl>
  );
}
