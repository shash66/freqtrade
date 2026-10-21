"use client";

import { useEffect, useMemo, useState } from "react";
import { api, ApiError, type BacktestJob, type BacktestRequest, type JobAccepted, type Network } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import { ApiErrorBanner } from "@/components/api-error-banner";
import { PageHeader } from "@/components/page-header";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Badge, SimulatedPill } from "@/components/ui/badge";
import { Checkbox, Field, Input } from "@/components/ui/field";
import { Button } from "@/components/ui/button";
import { Table, TableWrap, TBody, Td, Th, THead, Tr } from "@/components/ui/table";
import { EmptyState, MilestoneState } from "@/components/ui/empty-state";
import { KV } from "@/components/ui/kv";
import { fmtRelative, isoDate } from "@/lib/format";
import { AdminRequiredNotice } from "@/components/admin-control";

const STRATEGIES = [
  { key: "A", name: "Basic polling" },
  { key: "B", name: "Event-driven" },
  { key: "C", name: "Predictive optimized" },
];
const PRESETS = [7, 30, 90] as const;
const TERMINAL = new Set(["completed", "succeeded", "success", "done", "failed", "error", "cancelled", "canceled"]);

function splitList(s: string): string[] | null {
  const items = s.split(/[,\s]+/).map((x) => x.trim()).filter(Boolean);
  return items.length ? items : null;
}

function downloadData(filename: string, mime: string, content: string) {
  const a = document.createElement("a");
  a.href = `data:${mime};charset=utf-8,${encodeURIComponent(content)}`;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function toCsv(rows: Array<Record<string, unknown>>): string {
  if (!rows.length) return "";
  const cols = Array.from(rows.reduce((set, r) => { Object.keys(r).forEach((k) => set.add(k)); return set; }, new Set<string>()));
  const esc = (v: unknown) => {
    const s = v === null || v === undefined ? "" : typeof v === "object" ? JSON.stringify(v) : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [cols.join(","), ...rows.map((r) => cols.map((c) => esc(r[c])).join(","))].join("\n");
}

function resultRows(job: BacktestJob | null): Array<Record<string, unknown>> {
  if (!job?.results) return [];
  if (Array.isArray(job.results)) return job.results;
  const r = job.results as Record<string, unknown>;
  const inner = (r.rows ?? r.items ?? r.strategies) as unknown;
  if (Array.isArray(inner)) return inner as Array<Record<string, unknown>>;
  return Object.entries(r).map(([k, v]) => (typeof v === "object" && v !== null ? { key: k, ...(v as Record<string, unknown>) } : { key: k, value: v }));
}

export default function BacktestsPage() {
  const networksApi = useApi<Network[]>("/api/networks", 60_000);
  const today = useMemo(() => new Date(), []);
  const [preset, setPreset] = useState<number | "custom">(30);
  const [from, setFrom] = useState(() => isoDate(new Date(today.getTime() - 30 * 86400_000)));
  const [to, setTo] = useState(() => isoDate(today));
  const [networks, setNetworks] = useState<string[]>([]);
  const [debtAssets, setDebtAssets] = useState("");
  const [collateralAssets, setCollateralAssets] = useState("");
  const [strategies, setStrategies] = useState<string[]>(["A", "B", "C"]);
  const [seed, setSeed] = useState("42");

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<ApiError | undefined>();
  const [accepted, setAccepted] = useState<JobAccepted | null>(null);
  const [job, setJob] = useState<BacktestJob | null>(null);
  const [pollError, setPollError] = useState<ApiError | undefined>();

  // Default to all enabled networks once they load.
  useEffect(() => {
    if (networksApi.data && networks.length === 0) {
      setNetworks(networksApi.data.filter((n) => n.enabled).map((n) => n.slug));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [networksApi.data]);

  const applyPreset = (days: number) => {
    setPreset(days);
    setFrom(isoDate(new Date(Date.now() - days * 86400_000)));
    setTo(isoDate(new Date()));
  };

  // Poll the job while it is running.
  useEffect(() => {
    if (!accepted) return;
    let cancelled = false;
    let timer: number | undefined;
    const tick = async () => {
      try {
        const j = await api.backtest(accepted.job_id);
        if (cancelled) return;
        setJob(j);
        setPollError(undefined);
        if (!TERMINAL.has(String(j.status).toLowerCase())) timer = window.setTimeout(tick, 3000);
      } catch (e) {
        if (cancelled) return;
        setPollError(e instanceof ApiError ? e : new ApiError(0, String(e)));
        timer = window.setTimeout(tick, 5000);
      }
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [accepted]);

  const run = async () => {
    setSubmitting(true);
    setSubmitError(undefined);
    setJob(null);
    const body: BacktestRequest = {
      networks,
      date_from: `${from}T00:00:00Z`,
      date_to: `${to}T23:59:59Z`,
      strategies,
      debt_assets: splitList(debtAssets),
      collateral_assets: splitList(collateralAssets),
      random_seed: Number(seed) || 0,
    };
    try {
      const acc = await api.startBacktest(body);
      setAccepted(acc);
    } catch (e) {
      setSubmitError(e instanceof ApiError ? e : new ApiError(0, String(e)));
    } finally {
      setSubmitting(false);
    }
  };

  const rows = resultRows(job);
  const notReady = submitError?.notFound;
  const needsAdmin = submitError?.unauthorized;
  const canRun = networks.length > 0 && strategies.length > 0 && from <= to && !submitting;

  return (
    <div className="flex flex-col gap-3">
      <PageHeader
        title="Historical Backtesting"
        description="Replay strategies A/B/C over indexed history. Results are simulated outcome classes."
      />

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
        <Card className="xl:col-span-1">
          <CardHeader title="Parameters" />
          <CardBody className="flex flex-col gap-3">
            <fieldset className="flex flex-col gap-2">
              <legend className="text-[11px] uppercase tracking-wide text-muted">Date range</legend>
              <div className="flex flex-wrap gap-1">
                {PRESETS.map((d) => (
                  <Button key={d} size="sm" variant={preset === d ? "primary" : "secondary"} onClick={() => applyPreset(d)}>
                    {d} days
                  </Button>
                ))}
                <Button size="sm" variant={preset === "custom" ? "primary" : "secondary"} onClick={() => setPreset("custom")}>
                  Custom
                </Button>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <Field label="From">
                  <Input type="date" value={from} max={to} onChange={(e) => { setFrom(e.target.value); setPreset("custom"); }} />
                </Field>
                <Field label="To">
                  <Input type="date" value={to} min={from} onChange={(e) => { setTo(e.target.value); setPreset("custom"); }} />
                </Field>
              </div>
            </fieldset>

            <fieldset className="flex flex-col gap-1">
              <legend className="mb-1 text-[11px] uppercase tracking-wide text-muted">Networks</legend>
              {networksApi.error ? (
                <p className="text-xs text-bad">{networksApi.error.unreachable ? "Could not load networks: the API is unreachable." : `Could not load networks: ${networksApi.error.message}`}</p>
              ) : null}
              {(networksApi.data ?? []).map((n) => (
                <Checkbox
                  key={n.slug}
                  label={`${n.name} (${n.chain_id})${n.has_archive_rpc ? "" : " · no archive RPC"}`}
                  checked={networks.includes(n.slug)}
                  onChange={(e) => setNetworks((cur) => (e.target.checked ? [...cur, n.slug] : cur.filter((s) => s !== n.slug)))}
                />
              ))}
              {networksApi.data && networksApi.data.length === 0 ? <p className="text-xs text-faint">No networks configured.</p> : null}
            </fieldset>

            <Field label="Debt assets" hint="Comma-separated addresses or symbols; empty = all">
              <Input placeholder="USDC, WETH" value={debtAssets} onChange={(e) => setDebtAssets(e.target.value)} />
            </Field>
            <Field label="Collateral assets" hint="Comma-separated; empty = all">
              <Input placeholder="WETH, wstETH" value={collateralAssets} onChange={(e) => setCollateralAssets(e.target.value)} />
            </Field>

            <fieldset className="flex flex-col gap-1">
              <legend className="mb-1 text-[11px] uppercase tracking-wide text-muted">Strategies</legend>
              {STRATEGIES.map((s) => (
                <Checkbox
                  key={s.key}
                  label={<><span className="font-mono">{s.key}</span> {s.name}</>}
                  checked={strategies.includes(s.key)}
                  onChange={(e) => setStrategies((cur) => (e.target.checked ? [...cur, s.key] : cur.filter((k) => k !== s.key)))}
                />
              ))}
            </fieldset>

            <Field label="Random seed" hint="Seeds the Monte Carlo competitor model for reproducible runs">
              <Input type="number" inputMode="numeric" value={seed} onChange={(e) => setSeed(e.target.value)} />
            </Field>

            <Button variant="primary" disabled={!canRun} onClick={run}>
              {submitting ? "Submitting…" : "Run backtest"}
            </Button>
          </CardBody>
        </Card>

        <div className="flex flex-col gap-3 xl:col-span-2">
          <Card>
            <CardHeader
              title="Job status"
              actions={
                job ? (
                  <Badge data-testid="job-status" tone={TERMINAL.has(String(job.status).toLowerCase()) ? (/fail|error|cancel/i.test(String(job.status)) ? "bad" : "good") : "warn"}>
                    {String(job.status)}
                  </Badge>
                ) : accepted ? (
                  <Badge data-testid="job-status" tone="warn">{accepted.status}</Badge>
                ) : null
              }
            />
            <CardBody className="flex flex-col gap-2">
              {notReady ? (
                <MilestoneState feature="Backtesting" milestone={4} />
              ) : (
                <>
                  {needsAdmin ? <AdminRequiredNotice /> : <ApiErrorBanner error={submitError} />}
                  {pollError?.unauthorized ? <AdminRequiredNotice /> : <ApiErrorBanner error={pollError} stale={Boolean(job)} />}
                  {!accepted ? (
                    <EmptyState className="py-5" title="No job submitted" description="Configure the parameters and press Run backtest." />
                  ) : (
                    <>
                      <KV
                        items={[
                          { k: "Job id", v: String(accepted.job_id), mono: true },
                          { k: "Status URL", v: accepted.status_url, mono: true },
                          { k: "Status", v: job ? String(job.status) : accepted.status },
                          ...(job?.message ? [{ k: "Message", v: String(job.message) }] : []),
                          ...(job?.error ? [{ k: "Error", v: <span className="text-bad">{String(job.error)}</span> }] : []),
                        ]}
                      />
                      {typeof job?.progress === "number" ? (
                        <div className="flex items-center gap-2 text-xs">
                          <progress className="h-2 w-full" max={1} value={Math.min(1, Math.max(0, job.progress > 1 ? job.progress / 100 : job.progress))} />
                          <span className="tabular w-12 text-right">{Math.round((job.progress > 1 ? job.progress : job.progress * 100))}%</span>
                        </div>
                      ) : null}
                      {job && !TERMINAL.has(String(job.status).toLowerCase()) ? <p className="text-[11px] text-faint">Polling every 3s…</p> : null}
                    </>
                  )}
                </>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="Results"
              actions={
                <>
                  <SimulatedPill />
                  <Button size="sm" disabled={!job} onClick={() => job && downloadData(`backtest-${accepted?.job_id ?? "job"}.json`, "application/json", JSON.stringify(job, null, 2))}>
                    Export JSON
                  </Button>
                  <Button size="sm" disabled={!rows.length} onClick={() => downloadData(`backtest-${accepted?.job_id ?? "job"}.csv`, "text/csv", toCsv(rows))}>
                    Export CSV
                  </Button>
                </>
              }
            />
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
                          <Td key={c} mono={typeof r[c] !== "string"}>
                            {typeof r[c] === "object" && r[c] !== null ? JSON.stringify(r[c]) : String(r[c] ?? "—")}
                          </Td>
                        ))}
                      </Tr>
                    ))}
                  </TBody>
                </Table>
              </TableWrap>
            ) : (
              <CardBody>
                <EmptyState
                  className="py-5"
                  title="No results"
                  description={job ? `The job has no result rows yet (status ${String(job.status)}, checked ${fmtRelative(Date.now())}).` : "Results appear here once a backtest job completes."}
                />
              </CardBody>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
