"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { NAV } from "@/components/nav";
import { Badge, StatusDot } from "@/components/ui/badge";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { useLive } from "@/lib/use-live";
import { useApi } from "@/lib/use-api";
import type { Health } from "@/lib/api";
import { cn } from "@/lib/format";

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  return (
    <ul className="flex flex-col gap-0.5">
      {NAV.map((item) => {
        const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
        return (
          <li key={item.href}>
            <Link
              href={item.href}
              onClick={onNavigate}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex items-center gap-2 rounded-md px-2 py-1.5 text-xs",
                active ? "bg-surface-3 font-semibold text-foreground" : "text-muted hover:bg-surface-2 hover:text-foreground",
              )}
            >
              <span
                aria-hidden
                className={cn(
                  "inline-flex w-7 shrink-0 justify-center rounded-sm border px-1 font-mono text-[10px] leading-4",
                  active ? "border-accent/40 bg-info-bg text-accent" : "border-border text-faint",
                )}
              >
                {item.short}
              </span>
              <span className="truncate">{item.label}</span>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

function LiveIndicator() {
  const live = useLive();
  const label = live.status === "connected" ? "live" : live.status === "connecting" ? "connecting" : "reconnecting";
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-md border border-border bg-surface-2 px-2 py-0.5 text-[11px]"
      title={`WebSocket /ws/updates: ${live.status}${live.lastType ? ` (last: ${live.lastType})` : ""}`}
      role="status"
      aria-live="polite"
    >
      <StatusDot ok={live.status === "connected"} pending={live.status !== "connected"} />
      <span className="font-medium">{label}</span>
    </span>
  );
}

function HealthChip() {
  const { data, error } = useApi<Health>("/api/health", 15_000);
  if (error) {
    return (
      <Badge tone="bad" title={error.message}>
        API unreachable
      </Badge>
    );
  }
  if (!data) return <Badge tone="neutral">API …</Badge>;
  return (
    <Badge tone={data.status === "ok" ? "good" : "warn"} title={`db ${data.database ? "ok" : "down"} · redis ${data.redis ? "ok" : "down"}`}>
      API {data.status} · v{data.version}
    </Badge>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();
  const current = NAV.find((n) => (n.href === "/" ? pathname === "/" : pathname.startsWith(n.href)));

  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  return (
    <div className="flex min-h-dvh">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded-md focus:bg-accent focus:px-2 focus:py-1 focus:text-accent-fg"
      >
        Skip to content
      </a>

      {/* Desktop sidebar */}
      <aside className="sticky top-0 hidden h-dvh w-56 shrink-0 flex-col border-r border-border bg-surface md:flex" aria-label="Primary">
        <div className="border-b border-border px-3 py-3">
          <Link href="/" className="block">
            <span className="block text-sm font-semibold tracking-tight">FlashLiquidator Lab</span>
            <span className="block text-[11px] text-muted">Aave V3 liquidation research</span>
          </Link>
        </div>
        <nav className="flex-1 overflow-y-auto p-2">
          <NavLinks />
        </nav>
        <div className="border-t border-border p-2 text-[11px] text-faint">
          <p>Simulation only. No transactions are ever submitted.</p>
        </div>
      </aside>

      {/* Mobile drawer */}
      {open ? (
        <div className="fixed inset-0 z-40 md:hidden">
          <button type="button" aria-label="Close navigation" className="absolute inset-0 bg-black/40" onClick={() => setOpen(false)} />
          <nav
            aria-label="Primary"
            className="relative flex h-full w-64 max-w-[85vw] flex-col border-r border-border bg-surface p-2"
          >
            <div className="mb-2 flex items-center justify-between px-1">
              <span className="text-sm font-semibold">FlashLiquidator Lab</span>
              <button type="button" className="rounded-sm px-2 py-1 text-xs text-muted hover:bg-surface-2" onClick={() => setOpen(false)}>
                Close
              </button>
            </div>
            <NavLinks onNavigate={() => setOpen(false)} />
          </nav>
        </div>
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 border-b border-border bg-surface/95 backdrop-blur">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2 md:px-4">
            <button
              type="button"
              className="inline-flex h-8 items-center rounded-md border border-border px-2 text-xs md:hidden"
              aria-label="Open navigation"
              aria-expanded={open}
              onClick={() => setOpen(true)}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
                <path d="M4 7h16M4 12h16M4 17h16" />
              </svg>
            </button>
            <div className="min-w-0 flex-1">
              <h1 className="truncate text-sm font-semibold leading-5">{current?.label ?? "FlashLiquidator Lab"}</h1>
              {current?.description ? <p className="hidden truncate text-[11px] text-muted sm:block">{current.description}</p> : null}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="warn" className="uppercase tracking-wide" title="app_mode=simulation; the service never signs or sends transactions">
                Simulation · read-only
              </Badge>
              <HealthChip />
              <LiveIndicator />
              <ThemeToggle />
            </div>
          </div>
        </header>
        <main id="main" className="min-w-0 flex-1 px-3 py-3 md:px-4 md:py-4">
          {children}
        </main>
        <footer className="border-t border-border px-3 py-2 text-[11px] text-faint md:px-4">
          FlashLiquidator Lab · research tooling · all profit figures are simulated outcome classes, never realised revenue.
        </footer>
      </div>
    </div>
  );
}
