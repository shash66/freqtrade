import type { ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";

export function ApiErrorBanner({ error, onRetry, stale }: { error: ApiError | undefined; onRetry?: () => void; stale?: boolean }) {
  if (!error) return null;
  const unreachable = error.unreachable;
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-bad/40 bg-bad-bg px-3 py-2 text-xs text-bad"
    >
      <div className="min-w-0">
        <p className="font-semibold">{unreachable ? "The API is unreachable." : `API error ${error.status}`}</p>
        <p className="break-words opacity-90">
          {unreachable
            ? "The dashboard proxies /api to the backend (API_INTERNAL_URL). Check that the backend container is running."
            : error.message}
          {stale ? " Showing the last successful response." : ""}
        </p>
      </div>
      {onRetry ? (
        <Button size="sm" variant="danger" onClick={onRetry}>
          Retry
        </Button>
      ) : null}
    </div>
  );
}
