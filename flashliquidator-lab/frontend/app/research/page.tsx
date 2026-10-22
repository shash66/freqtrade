"use client";

import type { ResearchSummary } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import { ApiErrorBanner } from "@/components/api-error-banner";
import { PageHeader } from "@/components/page-header";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { StatTile } from "@/components/ui/stat-tile";
import { Table, TableWrap, TBody, Td, Th, THead, Tr } from "@/components/ui/table";
import { EmptyState } from "@/components/ui/empty-state";
import { Button } from "@/components/ui/button";

const CATEGORY_LABELS: Record<string, string> = {
  publicly_observable: "Publicly observable",
  predictable_not_yet_eligible: "Predictable, not yet eligible",
  intra_block_only: "Intra-block only",
  unknown: "Unknown",
};

function ms(v: number | null | undefined, digits = 0) {
  return v === null || v === undefined ? "—" : `${v.toFixed(digits)} ms`;
}

function usd(v: string | null | undefined) {
  if (v === null || v === undefined) return "—";
  const n = Number(v);
  return Number.isFinite(n) ? `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : v;
}

export default function ResearchPage() {
  const { data, error, loading, refresh } = useApi<ResearchSummary>("/api/research/summary", 60_000);
  const nets = data?.networks ?? [];
  const markets = data?.market_rows ?? [];

  return (
    <div className="flex flex-col gap-3">
      <PageHeader
        title="Research"
        description="Phase 4 comparison across networks and protocols: observable opportunities, execution latency by stage, and strategy verdicts. Read-only; built from committed research artefacts."
        actions={
          <Button size="sm" onClick={refresh} disabled={loading}>
            Refresh
          </Button>
        }
      />
      <ApiErrorBanner error={error} onRetry={refresh} stale={Boolean(data)} />

      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <StatTile label="Markets registered" value={data?.markets?.markets ?? "—"} hint={`${data?.markets?.verified ?? 0} verified live`} loading={loading} />
        <StatTile label="Protocols" value={(data?.markets?.protocols ?? []).length || "—"} hint={(data?.markets?.protocols ?? []).join(", ")} loading={loading} />
        <StatTile
          label="Observable before winner (Base)"
          value={nets[0]?.observability ? `${Math.round((nets[0].observability.observable_before_winner_rate ?? 0) * 100)}%` : "—"}
          hint={nets[0]?.observability ? `${nets[0].observability.observable_before_winner} of ${nets[0].observability.events} events` : undefined}
          loading={loading}
          tone="sim"
        />
        <StatTile
          label="Fast path, median (Base)"
          value={ms(nets[0]?.latency_stages?.fast_total_ms?.median)}
          hint={`fork simulation ${ms(nets[0]?.latency_stages?.evm_simulation_ms?.median)}`}
          loading={loading}
        />
      </div>

      <Card>
        <CardHeader title="Opportunity observability by network" description="What a public observer could have known before the winning transaction (Phase 4 categories)." />
        <CardBody>
          {nets.length === 0 ? (
            <EmptyState title="No artefacts yet" description="Run scripts/phase4-research.sh to produce the observability files." />
          ) : (
            <TableWrap>
              <Table>
                <THead>
                  <Tr>
                    <Th>Network</Th>
                    <Th align="right">Events</Th>
                    {Object.values(CATEGORY_LABELS).map((l) => (
                      <Th key={l} align="right">
                        {l}
                      </Th>
                    ))}
                    <Th align="right">Observable before winner</Th>
                    <Th align="right">Lead (median)</Th>
                    <Th align="right">Simulated profit</Th>
                  </Tr>
                </THead>
                <TBody>
                  {nets.map((n) => (
                    <Tr key={n.network}>
                      <Td className="font-medium capitalize">{n.network}</Td>
                      <Td align="right">{n.observability?.events ?? "—"}</Td>
                      {Object.keys(CATEGORY_LABELS).map((k) => (
                        <Td key={k} align="right">
                          {n.observability?.categories?.[k] ?? "—"}
                        </Td>
                      ))}
                      <Td align="right">{n.observability ? `${Math.round((n.observability.observable_before_winner_rate ?? 0) * 100)}%` : "—"}</Td>
                      <Td align="right">{n.observability?.lead_seconds?.median !== null && n.observability?.lead_seconds?.median !== undefined ? `${n.observability.lead_seconds.median} s` : "—"}</Td>
                      <Td align="right">
                        {usd(n.replays?.net_profit_usd_total_complete)}{" "}
                        <span className="text-faint">({n.replays?.profitable ?? 0} of {n.replays?.liquidation_events ?? 0})</span>
                      </Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </TableWrap>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Execution latency by stage" description="Monotonic-clock measurements of the fast candidate path and the full fork simulation (median / p90)." />
        <CardBody>
          <TableWrap>
            <Table>
              <THead>
                <Tr>
                  <Th>Network</Th>
                  <Th align="right">RPC round trip</Th>
                  <Th align="right">Head poll</Th>
                  <Th align="right">Protocol reads</Th>
                  <Th align="right">Math</Th>
                  <Th align="right">Quotes + L1 fee</Th>
                  <Th align="right">Fast path total</Th>
                  <Th align="right">Fork simulation</Th>
                </Tr>
              </THead>
              <TBody>
                {nets.map((n) => {
                  const s = n.latency_stages ?? {};
                  const cell = (k: string, d = 0) => (s[k]?.n ? `${ms(s[k].median, d)} / ${ms(s[k].p90, d)}` : "—");
                  return (
                    <Tr key={n.network}>
                      <Td className="font-medium capitalize">{n.network}</Td>
                      <Td align="right">{cell("rpc_rtt_ms")}</Td>
                      <Td align="right">{cell("head_poll_ms")}</Td>
                      <Td align="right">{cell("rpc_reads_ms")}</Td>
                      <Td align="right">{cell("math_ms", 2)}</Td>
                      <Td align="right">{cell("quote_ms")}</Td>
                      <Td align="right">{cell("fast_total_ms")}</Td>
                      <Td align="right">{cell("evm_simulation_ms")}</Td>
                    </Tr>
                  );
                })}
              </TBody>
            </Table>
          </TableWrap>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Strategy verdicts on the historical dataset" description="Expected profit after realistic costs and competition, 95% bootstrap interval; modeled, never earned." />
        <CardBody>
          <TableWrap>
            <Table>
              <THead>
                <Tr>
                  <Th>Network</Th>
                  <Th>Strategy</Th>
                  <Th align="right">Expected profit</Th>
                  <Th align="right">95% interval</Th>
                  <Th>Verdict</Th>
                </Tr>
              </THead>
              <TBody>
                {nets.flatMap((n) =>
                  Object.entries(n.strategies ?? {}).map(([name, v]) => (
                    <Tr key={`${n.network}-${name}`}>
                      <Td className="capitalize">{n.network}</Td>
                      <Td className="font-mono text-[11px]">{name}</Td>
                      <Td align="right">{usd(v.expected_profit_total_usd)}</Td>
                      <Td align="right">
                        {usd(v.ci95?.[0])} … {usd(v.ci95?.[1])}
                      </Td>
                      <Td>
                        <Badge tone={v.positive_after_costs_and_competition ? "good" : v.point_estimate_positive ? "warn" : "bad"}>{v.statement}</Badge>
                      </Td>
                    </Tr>
                  )),
                )}
              </TBody>
            </Table>
          </TableWrap>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Markets" description="Every market the adapters know; verified live where an endpoint is configured and reachable." />
        <CardBody>
          <TableWrap>
            <Table>
              <THead>
                <Tr>
                  <Th>Market</Th>
                  <Th>Protocol</Th>
                  <Th>Network</Th>
                  <Th align="right">Chain</Th>
                  <Th>Status</Th>
                  <Th align="right">Liquidation events, last 100 blocks</Th>
                </Tr>
              </THead>
              <TBody>
                {markets.map((m) => (
                  <Tr key={m.market}>
                    <Td className="font-mono text-[11px]">{m.market}</Td>
                    <Td>{m.protocol}</Td>
                    <Td>{m.network}</Td>
                    <Td align="right">{m.chain_id}</Td>
                    <Td>
                      <Badge tone={m.status === "verified" ? "good" : m.status === "not_configured" ? "neutral" : "bad"}>{m.status}</Badge>
                    </Td>
                    <Td align="right">{m.recent_liquidation_events_last_blocks?.events ?? "—"}</Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          </TableWrap>
        </CardBody>
      </Card>
    </div>
  );
}
