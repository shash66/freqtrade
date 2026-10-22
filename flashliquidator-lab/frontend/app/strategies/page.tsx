"use client";

import type { StrategyRanking, StrategyRankingRow } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import { ApiErrorBanner } from "@/components/api-error-banner";
import { PageHeader } from "@/components/page-header";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Badge, type BadgeTone } from "@/components/ui/badge";
import { StatTile } from "@/components/ui/stat-tile";
import { Table, TableWrap, TBody, Td, Th, THead, Tr } from "@/components/ui/table";
import { EmptyState } from "@/components/ui/empty-state";
import { Button } from "@/components/ui/button";

const STRATEGY_LABELS: Record<string, string> = {
  compound_v3: "Compound V3 liquidation",
  dex_arb: "DEX arbitrage",
  yield_opt: "Yield optimisation",
  aave_v3: "Aave V3 liquidation",
};

const CLASS_TONE: Record<string, BadgeTone> = { simulated: "sim", theoretical: "neutral", modeled: "info", passive_yield: "accent", onchain_actual: "good" };
const VERDICT_TONE: Record<string, BadgeTone> = { positive_attainable: "good", positive_passive: "accent", not_estimable: "warn", negative: "bad", no_opportunity: "neutral" };
const VERDICT_LABEL: Record<string, string> = {
  positive_attainable: "Positive attainable",
  positive_passive: "Positive passive yield",
  not_estimable: "Not estimable",
  negative: "Negative",
  no_opportunity: "No opportunity",
};
const RISK_TONE: Record<string, BadgeTone> = { low: "good", medium: "warn", high: "bad", unknown: "neutral" };

function usd(v: string | null | undefined, digits = 2) {
  if (v === null || v === undefined) return "—";
  const n = Number(v);
  return Number.isFinite(n) ? `${n < 0 ? "−" : ""}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })}` : v;
}

function pct(v: string | null | undefined) {
  if (v === null || v === undefined) return "—";
  const n = Number(v);
  return Number.isFinite(n) ? `${Math.round(n * 100)}%` : v;
}

function Money({ value, cls, digits }: { value: string | null; cls?: string; digits?: number }) {
  return (
    <span className="inline-flex flex-col items-end gap-0.5">
      <span>{usd(value, digits)}</span>
      {value !== null && cls ? (
        <Badge tone={CLASS_TONE[cls] ?? "neutral"} className="uppercase tracking-wide">
          {cls.replace("_", " ")}
        </Badge>
      ) : null}
    </span>
  );
}

function Row({ r }: { r: StrategyRankingRow }) {
  return (
    <Tr>
      <Td className="font-medium">
        <div>{STRATEGY_LABELS[r.strategy] ?? r.strategy}</div>
        <div className="text-faint text-xs">{r.kind}</div>
      </Td>
      <Td className="capitalize">{r.network}</Td>
      <Td align="right" title={r.capital_note}>
        {usd(r.capital_usd, 0)}
      </Td>
      <Td title={r.flash_loan_note}>{r.flash_loan === null ? "—" : r.flash_loan ? "Yes" : "No"}</Td>
      <Td align="right">
        <Money value={r.gross_profit_usd} cls={r.gross_class} />
      </Td>
      <Td align="right">
        <Money value={r.net_profit_complete_usd} cls={r.net_class} />
      </Td>
      <Td align="right" title={[r.p_capture_model ?? "", ...r.p_capture_assumptions].join("\n")}>
        {pct(r.p_capture)}
        {r.p_capture_model ? <div className="text-faint text-xs">{r.p_capture_model.replaceAll("_", " ")}</div> : null}
      </Td>
      <Td align="right">
        <Money value={r.attainable_profit_usd} cls={r.attainable_profit_usd !== null ? "modeled" : undefined} />
      </Td>
      <Td className="max-w-[16rem] whitespace-normal text-xs" title={r.frequency_observed}>
        {r.frequency_per_day !== null ? <div className="font-medium">{Number(r.frequency_per_day).toFixed(2)} / day</div> : null}
        <div className="text-muted">{r.frequency_observed}</div>
      </Td>
      <Td>
        <Badge tone={RISK_TONE[r.risk] ?? "neutral"}>{r.risk}</Badge>
      </Td>
      <Td>
        <div className="flex flex-wrap gap-1">
          {r.evidence_quality.length === 0 ? <span className="text-faint">none</span> : null}
          {r.evidence_quality.map((e) => (
            <Badge key={e} tone={e === "fork" ? "sim" : e === "historical" ? "info" : e === "shadow" ? "accent" : "neutral"}>
              {e.replace("_", " ")}
            </Badge>
          ))}
        </div>
      </Td>
      <Td title={r.verdict_reason}>
        <Badge tone={VERDICT_TONE[r.verdict] ?? "neutral"}>{VERDICT_LABEL[r.verdict] ?? r.verdict}</Badge>
      </Td>
    </Tr>
  );
}

export default function StrategiesPage() {
  const { data, error, loading, refresh } = useApi<StrategyRanking>("/api/research/strategies", 60_000);
  const rows = data?.rows ?? [];
  const positive = data?.conclusion.strategies_with_positive_attainable_net_profit ?? [];
  const passive = data?.conclusion.strategies_with_positive_passive_yield ?? [];

  return (
    <div className="flex flex-col gap-3">
      <PageHeader
        title="Strategies"
        description="Phase 5 multi-strategy ranking: Compound V3 liquidations, DEX arbitrage and yield optimisation on Base and Arbitrum, side by side. Every figure names its outcome class; simulated profit is never combined with anything realised. Read-only, built from committed research artefacts."
        actions={
          <Button size="sm" onClick={refresh} disabled={loading}>
            Refresh
          </Button>
        }
      />
      <ApiErrorBanner error={error} onRetry={refresh} stale={Boolean(data)} />

      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <StatTile label="Strategy × network rows" value={rows.length || "—"} hint={`${new Set(rows.map((r) => r.strategy)).size} strategies`} loading={loading} />
        <StatTile label="Positive attainable (transaction)" value={data ? positive.length : "—"} hint={positive.join(", ") || "none qualifies"} loading={loading} tone={positive.length ? "good" : "bad"} />
        <StatTile label="Positive passive yield" value={data ? passive.length : "—"} hint={passive.join(", ") || "none"} loading={loading} tone="sim" />
        <StatTile label="Infrastructure (est.)" value={data ? `$${data.infra_cost_usd_month}/mo` : "—"} hint="one research node; an explicit assumption" loading={loading} />
      </div>

      <Card>
        <CardHeader title="Unified ranking" description={data?.conclusion.statement ?? "Waiting for artefacts."} />
        <CardBody>
          {rows.length === 0 ? (
            <EmptyState title="No strategy artefacts yet" description="Run scripts/phase5-research.sh to produce docs/phase5-*.json." />
          ) : (
            <TableWrap>
              <Table>
                <THead>
                  <Tr>
                    <Th>Strategy</Th>
                    <Th>Network</Th>
                    <Th align="right">Capital</Th>
                    <Th>Flash loan</Th>
                    <Th align="right">Gross profit</Th>
                    <Th align="right">Complete net profit</Th>
                    <Th align="right">Capture probability</Th>
                    <Th align="right">Attainable profit</Th>
                    <Th>Frequency (observed)</Th>
                    <Th>Risk</Th>
                    <Th>Evidence quality</Th>
                    <Th>Verdict</Th>
                  </Tr>
                </THead>
                <TBody>
                  {rows.map((r) => (
                    <Row key={`${r.strategy}-${r.network}`} r={r} />
                  ))}
                </TBody>
              </Table>
            </TableWrap>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Evaluation metrics" description="Opportunity counts, executable simulations, competition losses, profit per attempt, capital efficiency, worst-case loss and repeatability, with sample sizes and uncertainty." />
        <CardBody>
          {rows.length === 0 ? (
            <EmptyState title="Nothing to compare yet" />
          ) : (
            <TableWrap>
              <Table>
                <THead>
                  <Tr>
                    <Th>Strategy / network</Th>
                    <Th align="right">Opportunities</Th>
                    <Th align="right">Executable</Th>
                    <Th align="right">Simulated (ok)</Th>
                    <Th align="right">Net profitable (complete)</Th>
                    <Th align="right">Lost to competition</Th>
                    <Th align="right">Expected / attempt</Th>
                    <Th>Capital efficiency</Th>
                    <Th align="right">Worst-case loss</Th>
                    <Th>Repeatability</Th>
                    <Th>Uncertainty</Th>
                  </Tr>
                </THead>
                <TBody>
                  {rows.map((r) => (
                    <Tr key={`${r.strategy}-${r.network}-m`}>
                      <Td className="font-medium">
                        {STRATEGY_LABELS[r.strategy] ?? r.strategy} <span className="text-faint capitalize">/ {r.network}</span>
                      </Td>
                      <Td align="right">
                        {r.opportunities}
                        {r.sample_size.replay_opportunities !== null && r.sample_size.replay_opportunities !== undefined ? (
                          <span className="text-faint"> (+{r.sample_size.replay_opportunities} historical)</span>
                        ) : null}
                      </Td>
                      <Td align="right">{r.executable}</Td>
                      <Td align="right">
                        {r.simulated} ({r.simulated_success})
                      </Td>
                      <Td align="right">{r.net_profitable_complete}</Td>
                      <Td align="right">{r.lost_to_competition ?? "—"}</Td>
                      <Td align="right">{usd(r.expected_profit_per_attempt_usd)}</Td>
                      <Td className="text-xs">{r.capital_efficiency ?? "—"}</Td>
                      <Td align="right">{usd(r.worst_case_loss_usd)}</Td>
                      <Td className="max-w-[16rem] whitespace-normal text-xs">{r.repeatability}</Td>
                      <Td className="max-w-[20rem] whitespace-normal text-xs text-muted">{r.uncertainty.length ? r.uncertainty.join("; ") : "—"}</Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </TableWrap>
          )}
        </CardBody>
      </Card>

      {data ? (
        <Card>
          <CardHeader title="Reading rules" description="How to read this page without mixing evidence classes." />
          <CardBody>
            <ul className="list-disc pl-5 text-sm text-muted">
              {data.rules.map((x) => (
                <li key={x}>{x}</li>
              ))}
              <li>Nothing on this page is realised revenue: the lab has no execution path and holds no funds.</li>
            </ul>
          </CardBody>
        </Card>
      ) : null}
    </div>
  );
}
