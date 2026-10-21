import type { ReactNode } from "react";
import { cn } from "@/lib/format";

export function EmptyState({
  title,
  description,
  action,
  className,
  icon,
}: {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
  icon?: ReactNode;
}) {
  return (
    <div
      role="status"
      className={cn(
        "flex flex-col items-center justify-center gap-1 rounded-md border border-dashed border-border-strong px-4 py-8 text-center",
        className,
      )}
    >
      {icon ? <div className="mb-1 text-faint">{icon}</div> : null}
      <p className="text-sm font-medium">{title}</p>
      {description ? <p className="max-w-md text-xs text-muted">{description}</p> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

/** Shown when an endpoint 404s because its milestone has not shipped yet. */
export function MilestoneState({ feature, milestone, className }: { feature: string; milestone: number; className?: string }) {
  return (
    <EmptyState
      className={className}
      title={`${feature}: not available yet`}
      description={`This lands in milestone ${milestone}. The page layout and inputs are wired to the API shape, but the backend endpoint does not exist in this build.`}
      icon={
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden>
          <rect x="3" y="4" width="18" height="16" rx="2" />
          <path d="M3 9h18M8 2v4M16 2v4" />
        </svg>
      }
    />
  );
}
