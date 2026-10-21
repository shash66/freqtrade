"use client";

import { useApi } from "@/lib/use-api";
import type { Overview } from "@/lib/api";
import { ApiErrorBanner } from "@/components/api-error-banner";
import { PageHeader } from "@/components/page-header";
import { NetworkCard } from "@/components/network-card";
import { StrategyChart } from "@/components/strategy-chart";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Badge, SimulatedPill, StatusDot } from "@/components/ui/badge";
import { StatTile } from "@/components/ui/stat-tile";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/ui/empty-state";
import { KV } from "@/components/ui/kv";
import { Button } from "@/components/ui/button";
import { fmtNum, fmtRelative, fmtTime, fmtUsd } from "@/lib/format";

export default function OverviewPage() {
  const { data, error, loading, refreshing, lastUpdated, refresh } = useApi<Overview>("/api/overview", 15_000);
  const networks = data?.networks ?? [];

  return (
    <div className="flex flex-col gap-3">
      <PageHeader
        title="Overview"
        description={
          data ? (
            <>
              Generated {fmtRelative(data.generated_at)} · polling every 15s{refreshing ? " · refreshing…" : ""}
            </>
          ) : (
            "Network, position and simulation status"
          )
        }
        actions={
          <>
            {lastUpdated ? <span className="text-[11px] text-faint">updated {fmtRelative(lastUpdated)}</span> : null}
            <Button size="sm" onClick={refresh} disabled={loading}>
              Refresh
            </Button>
          </>
        }
      />

      <ApiErrorBanner error={error} onRetry={refresh} stale={Boolean(data)} />

      {data ? (
        <p className="rounded-md border border-sim/30 bg-sim-bg px-3 py-2 text-xs text-sim">
          <span className="mr-2 font-semibold uppercase tracking-wide">Outcome class</span>
          {data.outcome_class_note}
        </p>
      ) : null}

      {/* Simulated headline tiles */}
      <section aria-labelledby="sim-tiles" className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <h3 id="sim-tiles" className="sr-only">
          Simulated totals
        </h3>
        <StatTile
          label="Profitable simulated opportunities"
          value={data ? fmtNum(data.profitable_simulated_opportunities) : "—"}
          loading={loading && !data}
          tag={<SimulatedPill />}
          tone="sim"
          hint="successful fork simulations with net profit > 0"
        />
        <StatTile
          label="Estimated simulated profit"
          value={data ? fmtUsd(data.estimated_total_simulated_profit_usd) : "—"}
          loading={loading && !data}
          tag={<SimulatedPill />}
          tone="sim"
          hint="sum of simulated net profit; never realised revenue"
        />
      </section>

      {/* Per-network position tiles */}
      <Card>
        <CardHeader title="Positions by network" description="Counts from the indexed borrower set" />
        <CardBody>
          {loading && !data ? (
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-16" />
              ))}
            </div>
          ) : networks.length ? (
            <div className="flex flex-col gap-3">
              {networks.map((n) => (
                <div key={n.slug}>
                  <div className="mb-1 flex items-center gap-2 text-xs">
                    <span className="font-semibold">{n.name}</span>
                    <span className="font-mono text-faint">{n.slug}</span>
                  </div>
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                    <StatTile label="Active borrowers" value={fmtNum(data?.active_borrowers[n.slug] ?? 0)} />
                    <StatTile
                      label="Near liquidation"
                      value={fmtNum(data?.near_liquidation_positions[n.slug] ?? 0)}
                      tone={(data?.near_liquidation_positions[n.slug] ?? 0) > 0 ? "warn" : "default"}
                      hint="1.0 ≤ HF < alert threshold"
                    />
                    <StatTile
                      label="Eligible (HF < 1)"
                      value={fmtNum(data?.eligible_positions[n.slug] ?? 0)}
                      tone={(data?.eligible_positions[n.slug] ?? 0) > 0 ? "bad" : "default"}
                      hint="health factor below 1 with debt"
                    />
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState title="No networks configured" description="Configure at least one network RPC on the backend." />
          )}
        </CardBody>
      </Card>

      {/* Network cards */}
      <section aria-labelledby="networks-h" className="flex flex-col gap-2">
        <h3 id="networks-h" className="text-[11px] uppercase tracking-wide text-muted">
          Networks
        </h3>
        {loading && !data ? (
          <div className="grid grid-cols-1 gap-2 lg:grid-cols-2 2xl:grid-cols-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-40" />
            ))}
          </div>
        ) : networks.length ? (
          <div className="grid grid-cols-1 gap-2 lg:grid-cols-2 2xl:grid-cols-3">
            {networks.map((n) => (
              <NetworkCard key={n.slug} network={n} />
            ))}
          </div>
        ) : null}
      </section>

      <div className="grid grid-cols-1 gap-2 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title="Strategy comparison" description="Per-strategy runs and summary metrics from the last run" />
          <CardBody>
            {loading && !data ? <Skeleton className="h-56" /> : <StrategyChart strategies={data?.strategies ?? []} />}
          </CardBody>
        </Card>

        <div className="flex flex-col gap-2">
          <Card>
            <CardHeader
              title="Simulation service"
              actions={
                data ? (
                  <Badge tone={data.simulation_service.status === "ok" ? "good" : data.simulation_service.status === "not_implemented" ? "neutral" : "warn"}>
                    {String(data.simulation_service.status)}
                  </Badge>
                ) : null
              }
            />
            <CardBody>
              {loading && !data ? (
                <Skeleton className="h-12" />
              ) : data ? (
                <KV
                  items={Object.entries(data.simulation_service)
                    .filter(([k]) => k !== "status")
                    .map(([k, v]) => ({
                      k,
                      v: typeof v === "boolean" ? <StatusDot ok={v} label={v ? "yes" : "no"} /> : String(v),
                    }))}
                />
              ) : null}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Indexer checkpoints" description="Last block per stream" />
            <CardBody>
              {loading && !data ? (
                <Skeleton className="h-12" />
              ) : data && Object.keys(data.indexer).length ? (
                <ul className="flex flex-col divide-y divide-border text-xs">
                  {Object.entries(data.indexer).map(([slug, cp]) => (
                    <li key={slug} className="flex flex-wrap items-center justify-between gap-x-3 py-1">
                      <span>
                        <span className="font-medium">{slug}</span> <span className="font-mono text-faint">{cp.stream}</span>
                      </span>
                      <span className="tabular text-muted" title={fmtTime(cp.updated_at)}>
                        #{fmtNum(cp.last_block)} · {fmtRelative(cp.updated_at)}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <EmptyState className="py-4" title="No checkpoints" description="The indexer has not written a checkpoint yet." />
              )}
            </CardBody>
          </Card>
        </div>
      </div>
    </div>
  );
}
