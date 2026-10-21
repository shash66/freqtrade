"use client";

import { useState, type FormEvent } from "react";
import { api, ApiError, type Network, type SimulationRequest, type SimulationResult } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import { ApiErrorBanner } from "@/components/api-error-banner";
import { PageHeader } from "@/components/page-header";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Badge, SimulatedPill } from "@/components/ui/badge";
import { Field, Input, Select } from "@/components/ui/field";
import { Button } from "@/components/ui/button";
import { EmptyState, MilestoneState } from "@/components/ui/empty-state";
import { KV } from "@/components/ui/kv";
import { fmtUsd } from "@/lib/format";

const ADDR = /^0x[0-9a-fA-F]{40}$/;
const DIGITS = /^[0-9]+$/;

interface FormState {
  network: string;
  block_number: string;
  borrower: string;
  collateral_asset: string;
  debt_asset: string;
  debt_to_cover_raw: string;
  slippage_bps: string;
  min_profit_usd: string;
  gas_price_wei: string;
  flash_loan_provider: "aave_v3";
}

const initial: FormState = {
  network: "",
  block_number: "",
  borrower: "",
  collateral_asset: "",
  debt_asset: "",
  debt_to_cover_raw: "",
  slippage_bps: "50",
  min_profit_usd: "0",
  gas_price_wei: "",
  flash_loan_provider: "aave_v3",
};

function validate(f: FormState): Record<string, string> {
  const e: Record<string, string> = {};
  if (!f.network) e.network = "Select a network";
  if (!ADDR.test(f.borrower)) e.borrower = "Must be a 0x-prefixed 20-byte address";
  if (!ADDR.test(f.collateral_asset)) e.collateral_asset = "Must be a 0x-prefixed 20-byte address";
  if (!ADDR.test(f.debt_asset)) e.debt_asset = "Must be a 0x-prefixed 20-byte address";
  if (!DIGITS.test(f.debt_to_cover_raw)) e.debt_to_cover_raw = "Raw integer amount in the debt asset's smallest unit";
  if (f.block_number && !DIGITS.test(f.block_number)) e.block_number = "Integer block number";
  const sl = Number(f.slippage_bps);
  if (!Number.isInteger(sl) || sl < 1 || sl > 5000) e.slippage_bps = "1–5000 bps";
  if (f.min_profit_usd !== "" && !Number.isFinite(Number(f.min_profit_usd))) e.min_profit_usd = "Decimal USD amount";
  if (f.gas_price_wei && !DIGITS.test(f.gas_price_wei)) e.gas_price_wei = "Integer wei";
  return e;
}

export default function SimulatorPage() {
  const networks = useApi<Network[]>("/api/networks", 60_000);
  const [form, setForm] = useState<FormState>(initial);
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | undefined>();
  const [result, setResult] = useState<SimulationResult | null>(null);

  const errors = validate(form);
  const set = (k: keyof FormState) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const submit = async (ev: FormEvent) => {
    ev.preventDefault();
    setTouched(true);
    if (Object.keys(errors).length) return;
    setBusy(true);
    setError(undefined);
    setResult(null);
    const body: SimulationRequest = {
      network: form.network,
      borrower: form.borrower,
      collateral_asset: form.collateral_asset,
      debt_asset: form.debt_asset,
      debt_to_cover_raw: form.debt_to_cover_raw,
      block_number: form.block_number ? Number(form.block_number) : null,
      slippage_bps: Number(form.slippage_bps),
      min_profit_usd: form.min_profit_usd || "0",
      gas_price_wei: form.gas_price_wei || null,
      flash_loan_provider: "aave_v3",
    };
    try {
      setResult(await api.simulate(body));
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, String(e)));
    } finally {
      setBusy(false);
    }
  };

  const err = (k: keyof FormState) => (touched && errors[k] ? <span className="text-bad">{errors[k]}</span> : undefined);
  const breakdown = result?.breakdown && typeof result.breakdown === "object" ? result.breakdown : null;
  const otherFields = result
    ? Object.entries(result).filter(([k, v]) => !["breakdown", "calldata", "revert_reason", "net_profit_usd", "status", "success", "outcome_class"].includes(k) && (typeof v !== "object" || v === null))
    : [];

  return (
    <div className="flex flex-col gap-3">
      <PageHeader
        title="Flash-Loan Simulator"
        description="Simulate one liquidation on a local Anvil fork. Nothing is signed, broadcast or submitted."
      />

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <Card>
          <CardHeader title="Simulation request" description="POST /api/simulations" />
          <CardBody>
            <form className="grid grid-cols-1 gap-3 sm:grid-cols-2" onSubmit={submit} noValidate>
              <Field label="Network" hint={err("network")}>
                <Select value={form.network} onChange={set("network")} required>
                  <option value="">Select…</option>
                  {(networks.data ?? []).map((n) => (
                    <option key={n.slug} value={n.slug}>
                      {n.name} ({n.chain_id})
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Block number (optional)" hint={err("block_number") ?? "Empty = latest; historical blocks need an archive RPC"}>
                <Input inputMode="numeric" placeholder="latest" value={form.block_number} onChange={set("block_number")} />
              </Field>
              <Field label="Borrower" hint={err("borrower")} className="sm:col-span-2">
                <Input className="font-mono" placeholder="0x…" value={form.borrower} onChange={set("borrower")} spellCheck={false} />
              </Field>
              <Field label="Collateral asset" hint={err("collateral_asset")}>
                <Input className="font-mono" placeholder="0x…" value={form.collateral_asset} onChange={set("collateral_asset")} spellCheck={false} />
              </Field>
              <Field label="Debt asset" hint={err("debt_asset")}>
                <Input className="font-mono" placeholder="0x…" value={form.debt_asset} onChange={set("debt_asset")} spellCheck={false} />
              </Field>
              <Field label="debt_to_cover_raw" hint={err("debt_to_cover_raw") ?? "Smallest unit of the debt asset"} className="sm:col-span-2">
                <Input className="font-mono" inputMode="numeric" placeholder="1000000" value={form.debt_to_cover_raw} onChange={set("debt_to_cover_raw")} />
              </Field>
              <Field label="Slippage (bps)" hint={err("slippage_bps")}>
                <Input type="number" min={1} max={5000} value={form.slippage_bps} onChange={set("slippage_bps")} />
              </Field>
              <Field label="Min profit (USD)" hint={err("min_profit_usd")}>
                <Input type="number" step="0.01" value={form.min_profit_usd} onChange={set("min_profit_usd")} />
              </Field>
              <Field label="Gas price (wei, optional)" hint={err("gas_price_wei") ?? "Empty = node estimate"}>
                <Input className="font-mono" inputMode="numeric" placeholder="auto" value={form.gas_price_wei} onChange={set("gas_price_wei")} />
              </Field>
              <Field label="Flash-loan provider" hint="Only Aave V3 is supported in this build">
                <Select value="aave_v3" disabled>
                  <option value="aave_v3">aave_v3</option>
                </Select>
              </Field>
              <div className="flex flex-wrap items-center gap-2 sm:col-span-2">
                <Button type="submit" variant="primary" disabled={busy}>
                  {busy ? "Simulating…" : "Run simulation"}
                </Button>
                <Button type="button" variant="ghost" onClick={() => { setForm(initial); setTouched(false); setResult(null); setError(undefined); }}>
                  Reset
                </Button>
                <SimulatedPill />
              </div>
            </form>
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Result"
            actions={
              result ? (
                <Badge tone={result.success === true ? "good" : result.success === false ? "bad" : "neutral"}>
                  {result.success === true ? "success" : result.success === false ? "reverted" : String(result.status ?? "unknown")}
                </Badge>
              ) : null
            }
          />
          <CardBody className="flex flex-col gap-2">
            <p className="rounded-md border border-sim/30 bg-sim-bg px-3 py-2 text-xs text-sim">
              Any calldata shown here is for local fork simulation only. This platform never signs or submits transactions.
            </p>
            {error?.notFound ? (
              <MilestoneState feature="Fork simulation" milestone={4} />
            ) : (
              <>
                <ApiErrorBanner error={error} />
                {!result ? (
                  <EmptyState className="py-5" title="No simulation yet" description="Fill in the request and press Run simulation." />
                ) : (
                  <>
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                      <div className="rounded-md border border-border bg-surface-2 px-3 py-2">
                        <div className="flex items-center justify-between text-[11px] uppercase tracking-wide text-muted">
                          <span>Net profit</span>
                          <SimulatedPill />
                        </div>
                        <div className="tabular text-xl font-semibold">{fmtUsd(result.net_profit_usd ?? null)}</div>
                      </div>
                      <div className="rounded-md border border-border bg-surface-2 px-3 py-2">
                        <div className="text-[11px] uppercase tracking-wide text-muted">Outcome class</div>
                        <div className="text-sm font-medium">{String(result.outcome_class ?? "simulated")}</div>
                      </div>
                    </div>
                    {result.revert_reason ? (
                      <div className="rounded-md border border-bad/30 bg-bad-bg px-3 py-2 text-xs text-bad">
                        <span className="font-semibold">Revert reason:</span> <span className="font-mono break-all">{result.revert_reason}</span>
                      </div>
                    ) : null}
                    {breakdown ? (
                      <div>
                        <h3 className="mb-1 text-[11px] uppercase tracking-wide text-muted">Breakdown</h3>
                        <KV items={Object.entries(breakdown).map(([k, v]) => ({ k, v: typeof v === "object" && v !== null ? JSON.stringify(v) : String(v), mono: true }))} />
                      </div>
                    ) : null}
                    {otherFields.length ? <KV items={otherFields.map(([k, v]) => ({ k, v: String(v) }))} /> : null}
                    {typeof result.calldata === "string" && result.calldata ? (
                      <details className="text-xs">
                        <summary className="cursor-pointer text-muted">Calldata (local simulation only)</summary>
                        <pre className="mt-1 overflow-x-auto break-all whitespace-pre-wrap rounded-md bg-surface-2 p-2 font-mono text-[11px]">{result.calldata}</pre>
                      </details>
                    ) : null}
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
