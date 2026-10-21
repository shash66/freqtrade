"use client";

import { useState } from "react";
import { api, ApiError, type StrategyCompareRequest, type StrategyCompareResponse } from "@/lib/api";
import { ApiErrorBanner } from "@/components/api-error-banner";
import { PageHeader } from "@/components/page-header";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Badge, SimulatedPill } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableWrap, TBody, Td, Th, THead, Tr } from "@/components/ui/table";
import { EmptyState, MilestoneState } from "@/components/ui/empty-state";
import { KV } from "@/components/ui/kv";
import { cn } from "@/lib/format";

const STRATEGIES = [
  {
    key: "A",
    name: "Basic polling",
    blurb: "Polls getUserAccountData for tracked borrowers on a fixed interval and submits the first profitable candidate.",
    traits: ["fixed interval", "no mempool", "lowest complexity"],
  },
  {
    key: "B",
    name: "Event-driven",
    blurb: "Reacts to oracle price updates and Aave events, re-evaluating only the positions exposed to the moved asset.",
    traits: ["price-feed triggers", "targeted re-checks", "lower latency"],
  },
  {
    key: "C",
    name: "Predictive optimized",
    blurb: "Pre-computes liquidation thresholds per position, pre-signs calldata and ranks by expected net profit under competition.",
    traits: ["threshold pre-computation", "gas bidding model", "highest complexity"],
  },
];

interface SliderDef {
  key: keyof StrategyCompareRequest;
  label: string;
  min: number;
  max: number;
  step: number;
  unit: string;
}

const SLIDERS: SliderDef[] = [
  { key: "our_delay_ms", label: "Our detection delay", min: 0, max: 5000, step: 50, unit: "ms" },
  { key: "competitor_delay_ms", label: "Competitor delay", min: 0, max: 5000, step: 50, unit: "ms" },
  { key: "gas_bid_gwei", label: "Gas bid", min: 0, max: 500, step: 1, unit: "gwei" },
  { key: "candidate_expiry_probability", label: "Candidate expiry probability", min: 0, max: 1, step: 0.01, unit: "" },
];

function rowsOf(res: StrategyCompareResponse | null): Array<Record<string, unknown>> {
  if (!res) return [];
  if (Array.isArray(res.results)) return res.results;
  const strategies = (res as { strategies?: unknown }).strategies;
  if (Array.isArray(strategies)) return strategies as Array<Record<string, unknown>>;
  if (strategies && typeof strategies === "object") {
    return Object.entries(strategies as Record<string, unknown>).map(([k, v]) => ({ strategy: k, ...((v as Record<string, unknown>) ?? {}) }));
  }
  return [];
}

export default function CompetitionPage() {
  const [params, setParams] = useState<StrategyCompareRequest>({
    our_delay_ms: 500,
    competitor_delay_ms: 300,
    gas_bid_gwei: 20,
    candidate_expiry_probability: 0.2,
    strategies: ["A", "B", "C"],
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | undefined>();
  const [result, setResult] = useState<StrategyCompareResponse | null>(null);

  const compare = async () => {
    setBusy(true);
    setError(undefined);
    try {
      setResult(await api.compareStrategies(params));
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, String(e)));
    } finally {
      setBusy(false);
    }
  };

  const rows = rowsOf(result);
  const extra = result ? Object.entries(result).filter(([k, v]) => k !== "results" && k !== "strategies" && (typeof v !== "object" || v === null)) : [];

  return (
    <div className="flex flex-col gap-3">
      <PageHeader
        title="Competition Lab"
        description="Seeded Monte Carlo model of competing liquidators. Every output is a modeled estimate."
      />

      <div className="grid grid-cols-1 gap-2 lg:grid-cols-3">
        {STRATEGIES.map((s) => {
          const on = params.strategies?.includes(s.key) ?? true;
          return (
            <Card key={s.key} className={cn(!on && "opacity-60")}>
              <CardHeader
                title={
                  <span className="inline-flex items-center gap-2">
                    <span className="rounded-sm border border-accent/40 bg-info-bg px-1.5 font-mono text-accent">{s.key}</span>
                    {s.name}
                  </span>
                }
                actions={
                  <label className="inline-flex items-center gap-1 text-[11px]">
                    <input
                      type="checkbox"
                      className="size-3.5 accent-[var(--accent)]"
                      checked={on}
                      onChange={(e) =>
                        setParams((p) => ({
                          ...p,
                          strategies: e.target.checked ? [...(p.strategies ?? []), s.key] : (p.strategies ?? []).filter((k) => k !== s.key),
                        }))
                      }
                    />
                    include
                  </label>
                }
              />
              <CardBody className="flex flex-col gap-2">
                <p className="text-xs text-muted">{s.blurb}</p>
                <div className="flex flex-wrap gap-1">
                  {s.traits.map((t) => (
                    <Badge key={t}>{t}</Badge>
                  ))}
                </div>
              </CardBody>
            </Card>
          );
        })}
      </div>

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
        <Card>
          <CardHeader title="Model inputs" description="Adjust and re-run; the backend seeds its RNG" />
          <CardBody className="flex flex-col gap-3">
            {SLIDERS.map((s) => {
              const v = params[s.key] as number;
              const id = `slider-${s.key}`;
              return (
                <div key={s.key} className="flex flex-col gap-1">
                  <div className="flex items-center justify-between text-xs">
                    <label htmlFor={id}>{s.label}</label>
                    <span className="tabular font-mono">
                      {s.step < 1 ? v.toFixed(2) : v} {s.unit}
                    </span>
                  </div>
                  <input
                    id={id}
                    type="range"
                    min={s.min}
                    max={s.max}
                    step={s.step}
                    value={v}
                    onChange={(e) => setParams((p) => ({ ...p, [s.key]: Number(e.target.value) }))}
                    className="w-full"
                  />
                  <div className="flex justify-between text-[10px] text-faint">
                    <span>{s.min}{s.unit && ` ${s.unit}`}</span>
                    <span>{s.max}{s.unit && ` ${s.unit}`}</span>
                  </div>
                </div>
              );
            })}
            <Button variant="primary" disabled={busy || !(params.strategies?.length)} onClick={compare}>
              {busy ? "Comparing…" : "Compare strategies"}
            </Button>
          </CardBody>
        </Card>

        <Card className="xl:col-span-2">
          <CardHeader
            title="Results"
            actions={
              <>
                <Badge tone="warn" className="uppercase tracking-wide">
                  modeled estimate, not measured
                </Badge>
                <SimulatedPill />
              </>
            }
          />
          <CardBody className="flex flex-col gap-2">
            <p className="rounded-md border border-warn/30 bg-warn-bg px-3 py-2 text-xs text-warn">
              <span className="font-semibold">Modeled estimate, not measured.</span> Win rates and profits below come from a seeded
              Monte Carlo model fed with historical liquidation statistics. They are not observed outcomes.
            </p>
            {error?.notFound ? (
              <MilestoneState feature="Strategy comparison" milestone={5} />
            ) : (
              <>
                <ApiErrorBanner error={error} />
                {!result ? (
                  <EmptyState className="py-5" title="No comparison run" description="Set the model inputs and press Compare strategies." />
                ) : (
                  <>
                    {extra.length ? <KV items={extra.map(([k, v]) => ({ k, v: String(v) }))} /> : null}
                    {rows.length ? (
                      <TableWrap>
                        <Table>
                          <THead>
                            <tr>
                              {Object.keys(rows[0]).map((c) => (
                                <Th key={c}>{c}</Th>
                              ))}
                            </tr>
                          </THead>
                          <TBody>
                            {rows.map((r, i) => (
                              <Tr key={i}>
                                {Object.keys(rows[0]).map((c) => (
                                  <Td key={c} mono={typeof r[c] === "number"}>
                                    {typeof r[c] === "object" && r[c] !== null ? JSON.stringify(r[c]) : String(r[c] ?? "—")}
                                  </Td>
                                ))}
                              </Tr>
                            ))}
                          </TBody>
                        </Table>
                      </TableWrap>
                    ) : (
                      <pre className="overflow-x-auto rounded-md bg-surface-2 p-2 font-mono text-[11px]">{JSON.stringify(result, null, 2)}</pre>
                    )}
                  </>
                )}
              </>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
