"use client";

import { useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, type TooltipProps } from "recharts";
import type { StrategyPerf } from "@/lib/api";
import { EmptyState } from "@/components/ui/empty-state";
import { Select } from "@/components/ui/field";
import { SimulatedPill } from "@/components/ui/badge";
import { fmtNum } from "@/lib/format";

interface Row {
  key: string;
  name: string;
  value: number;
}

function numericKeys(strategies: StrategyPerf[]): string[] {
  const keys = new Set<string>();
  for (const s of strategies) {
    for (const [k, v] of Object.entries(s.summary ?? {})) {
      if (typeof v === "number" && Number.isFinite(v)) keys.add(k);
      else if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))) keys.add(k);
    }
  }
  return Array.from(keys).sort();
}

function ChartTip({ active, payload }: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload as Row;
  return (
    <div className="rounded-md border border-border bg-surface px-2 py-1 text-xs shadow-md">
      <div className="font-medium">
        {row.key} · {row.name}
      </div>
      <div className="tabular text-muted">{fmtNum(row.value, 4)}</div>
    </div>
  );
}

export function StrategyChart({ strategies }: { strategies: StrategyPerf[] }) {
  const metrics = useMemo(() => ["runs", ...numericKeys(strategies)], [strategies]);
  const [metric, setMetric] = useState("runs");
  const chosen = metrics.includes(metric) ? metric : "runs";

  const rows: Row[] = strategies.map((s) => ({
    key: s.key,
    name: s.name,
    value: chosen === "runs" ? s.runs : Number(s.summary?.[chosen] ?? 0),
  }));
  const hasRuns = strategies.some((s) => s.runs > 0);

  if (!strategies.length || !hasRuns) {
    return (
      <EmptyState
        title="No strategy runs yet"
        description={
          strategies.length
            ? `${strategies.length} strategies are defined but none has completed a run. Run a backtest to populate this comparison.`
            : "No strategy definitions were returned by the API. Strategy runs land with the backtesting milestone."
        }
      />
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-xs text-muted">
          <span>Metric</span>
          <Select aria-label="Chart metric" className="h-7 w-auto" value={chosen} onChange={(e) => setMetric(e.target.value)}>
            {metrics.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </Select>
        </div>
        <SimulatedPill />
      </div>
      <figure className="h-56 w-full" aria-label={`Bar chart of ${chosen} per strategy`}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 0 }} barCategoryGap="30%">
            <CartesianGrid vertical={false} stroke="var(--border)" />
            <XAxis dataKey="key" tickLine={false} axisLine={{ stroke: "var(--border-strong)" }} tick={{ fill: "var(--muted)", fontSize: 11 }} />
            <YAxis width={44} tickLine={false} axisLine={false} tick={{ fill: "var(--muted)", fontSize: 11 }} tickFormatter={(v: number) => fmtNum(v, 2)} allowDecimals={chosen !== "runs"} />
            <Tooltip cursor={{ fill: "var(--surface-2)" }} content={<ChartTip />} />
            <Bar dataKey="value" fill="var(--series-1)" radius={[4, 4, 0, 0]} maxBarSize={56} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </figure>
      <table className="w-full text-xs">
        <caption className="sr-only">Strategy comparison data</caption>
        <thead className="text-[11px] uppercase tracking-wide text-muted">
          <tr>
            <th scope="col" className="py-1 text-left font-medium">Strategy</th>
            <th scope="col" className="py-1 text-right font-medium">runs</th>
            {chosen !== "runs" ? <th scope="col" className="py-1 text-right font-medium">{chosen}</th> : null}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {strategies.map((s, i) => (
            <tr key={s.key}>
              <td className="py-1">
                <span className="font-mono text-faint">{s.key}</span> {s.name}
              </td>
              <td className="tabular py-1 text-right">{fmtNum(s.runs)}</td>
              {chosen !== "runs" ? <td className="tabular py-1 text-right">{fmtNum(rows[i].value, 4)}</td> : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
