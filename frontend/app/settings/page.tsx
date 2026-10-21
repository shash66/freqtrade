"use client";

import type { PublicSettings } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import { ApiErrorBanner } from "@/components/api-error-banner";
import { PageHeader } from "@/components/page-header";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Badge, StatusDot } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { KV } from "@/components/ui/kv";
import { Button } from "@/components/ui/button";

const LABELS: Record<string, string> = {
  app_mode: "App mode",
  read_only: "Read-only",
  min_net_profit_usd: "Min net profit (USD)",
  max_flash_loan_usd: "Max flash loan (USD)",
  min_flash_loan_usd: "Min flash loan (USD)",
  position_health_factor_alert: "Health-factor alert threshold",
  monitor_interval_seconds: "Monitor interval (s)",
  indexer_poll_interval_seconds: "Indexer poll interval (s)",
  indexer_max_block_range: "Indexer max block range",
  reorg_safety_blocks: "Reorg safety blocks",
  simulation_concurrency: "Simulation concurrency",
  raw_data_retention_days: "Raw data retention (days)",
};

const NETWORK_NAMES: Record<string, string> = { base: "Base", arbitrum: "Arbitrum", ethereum: "Ethereum" };

function renderValue(v: unknown) {
  if (typeof v === "boolean") return <StatusDot ok={v} label={v ? "true" : "false"} />;
  if (v === null || v === undefined) return "—";
  return String(v);
}

export default function SettingsPage() {
  const { data, error, loading, refresh } = useApi<PublicSettings>("/api/settings/public", 60_000);

  const scalar = data
    ? Object.entries(data).filter(([k, v]) => !["rpc_configured", "archive_rpc_configured", "live_trading_enabled"].includes(k) && (typeof v !== "object" || v === null))
    : [];
  const rpcSlugs = data ? Array.from(new Set([...Object.keys(data.rpc_configured ?? {}), ...Object.keys(data.archive_rpc_configured ?? {})])) : [];

  return (
    <div className="flex flex-col gap-3">
      <PageHeader
        title="Settings"
        description="Read-only view of the backend runtime configuration (GET /api/settings/public)."
        actions={
          <Button size="sm" onClick={refresh} disabled={loading}>
            Refresh
          </Button>
        }
      />

      <ApiErrorBanner error={error} onRetry={refresh} stale={Boolean(data)} />

      <div className="rounded-md border border-warn/30 bg-warn-bg px-3 py-2 text-xs text-warn">
        <span className="font-semibold">Live trading: disabled (not implemented in this build).</span>{" "}
        {data ? (
          <span>
            Backend reports <code className="font-mono">live_trading_enabled={String(data.live_trading_enabled)}</code>,{" "}
            <code className="font-mono">app_mode={String(data.app_mode ?? "simulation")}</code>.
          </span>
        ) : null}
      </div>

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <Card>
          <CardHeader title="Runtime parameters" actions={data?.read_only ? <Badge tone="neutral">read-only</Badge> : null} />
          <CardBody>
            {loading && !data ? (
              <div className="flex flex-col gap-2">
                {Array.from({ length: 8 }).map((_, i) => (
                  <Skeleton key={i} className="h-4" />
                ))}
              </div>
            ) : data ? (
              <KV items={scalar.map(([k, v]) => ({ k: LABELS[k] ?? k, v: renderValue(v), mono: typeof v !== "boolean" }))} />
            ) : null}
            <p className="mt-3 text-[11px] text-faint">Values are set through environment variables on the backend; this page cannot change them.</p>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="RPC endpoints" description="Credentials never leave the server" />
          <CardBody className="flex flex-col gap-3">
            <p className="text-xs text-muted">
              RPC URLs and API keys are read by the backend from its environment and are never returned by the API or
              embedded in this dashboard. Only a configured/not-configured flag is exposed.
            </p>
            {loading && !data ? (
              <Skeleton className="h-16" />
            ) : data ? (
              <table className="w-full text-xs">
                <thead className="text-[11px] uppercase tracking-wide text-muted">
                  <tr>
                    <th scope="col" className="py-1 text-left font-medium">Network</th>
                    <th scope="col" className="py-1 text-left font-medium">HTTP RPC</th>
                    <th scope="col" className="py-1 text-left font-medium">Archive RPC</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {rpcSlugs.map((slug) => (
                    <tr key={slug}>
                      <td className="py-1.5">{NETWORK_NAMES[slug] ?? slug}</td>
                      <td className="py-1.5">
                        <StatusDot ok={Boolean(data.rpc_configured?.[slug])} label={data.rpc_configured?.[slug] ? "configured" : "not configured"} />
                      </td>
                      <td className="py-1.5">
                        <StatusDot ok={Boolean(data.archive_rpc_configured?.[slug])} label={data.archive_rpc_configured?.[slug] ? "configured" : "not configured"} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : null}
            <p className="text-[11px] text-faint">
              Archive RPCs are required for historical fork simulation and deep backfills; public endpoints usually do not serve archive state.
            </p>
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
