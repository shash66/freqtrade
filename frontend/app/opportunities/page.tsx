"use client";

import { useCallback, useMemo, useState } from "react";
import { buildQuery, OPPORTUNITY_STATUSES, type Network, type Opportunity, type Page } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import { ApiErrorBanner } from "@/components/api-error-banner";
import { PageHeader } from "@/components/page-header";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Badge, SimulatedPill, type BadgeTone } from "@/components/ui/badge";
import { Field, Input, Select } from "@/components/ui/field";
import { Button } from "@/components/ui/button";
import { Table, TableWrap, TBody, Td, THead, Tr, SortableTh, Th } from "@/components/ui/table";
import { SkeletonRows } from "@/components/ui/skeleton";
import { EmptyState, MilestoneState } from "@/components/ui/empty-state";
import { CopyButton } from "@/components/ui/copy-button";
import { Drawer } from "@/components/ui/drawer";
import { KV } from "@/components/ui/kv";
import { fmtBps, fmtHealthFactor, fmtNum, fmtTime, fmtUsd, shortAddr } from "@/lib/format";

const STATUS_TONE: Record<string, BadgeTone> = {
  candidate: "info",
  simulated: "sim",
  invalid: "bad",
  expired: "neutral",
  incomplete: "warn",
};

type SortDir = "asc" | "desc";
const PAGE_SIZE = 50;
/** Columns the backend can order by; everything else is sorted client-side within the page. */
const SERVER_SORT: Record<string, string> = { attainable_net_profit_usd: "attainable", net_profit_usd: "net_profit", created_at: "created", health_factor: "health_factor" };

function numeric(v: string | number | null | undefined): number {
  if (v === null || v === undefined) return Number.NEGATIVE_INFINITY;
  if (v === "Infinity") return Number.POSITIVE_INFINITY;
  const n = Number(v);
  return Number.isFinite(n) ? n : Number.NEGATIVE_INFINITY;
}

export default function OpportunitiesPage() {
  const [network, setNetwork] = useState("");
  const [status, setStatus] = useState("");
  const [minProfit, setMinProfit] = useState("");
  const [sort, setSort] = useState<{ field: string; dir: SortDir } | null>({ field: "attainable_net_profit_usd", dir: "desc" });
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Opportunity | null>(null);

  const networks = useApi<Network[]>("/api/networks", 60_000);

  const path = useMemo(
    () =>
      `/api/opportunities${buildQuery({
        network,
        status,
        min_profit_usd: minProfit,
        sort: sort ? SERVER_SORT[sort.field] : undefined,
        limit: PAGE_SIZE,
        offset,
      })}`,
    [network, status, minProfit, sort, offset],
  );
  const { data, error, loading, refresh } = useApi<Page<Opportunity>>(path, 15_000);

  const onSort = useCallback((field: string) => {
    setSort((s) => (s?.field === field ? { field, dir: s.dir === "asc" ? "desc" : "asc" } : { field, dir: "desc" }));
    setOffset(0);
  }, []);

  const rows = useMemo(() => {
    const items = data?.items ?? [];
    if (!sort) return items;
    const f = sort.field as keyof Opportunity;
    const sign = sort.dir === "asc" ? 1 : -1;
    return [...items].sort((a, b) => {
      const av = a[f];
      const bv = b[f];
      if (typeof av === "string" && typeof bv === "string" && Number.isNaN(Number(av))) return av.localeCompare(bv) * sign;
      return (numeric(av as string | number | null) - numeric(bv as string | number | null)) * sign;
    });
  }, [data, sort]);

  const notReady = error?.notFound;
  const closeDrawer = useCallback(() => setSelected(null), []);

  return (
    <div className="flex flex-col gap-3">
      <PageHeader
        title="Opportunity Explorer"
        description="Liquidation candidates evaluated by the profitability engine. Simulated outcomes never represent on-chain earnings."
        actions={
          <Button size="sm" onClick={refresh} disabled={loading}>
            Refresh
          </Button>
        }
      />

      <Card>
        <CardBody className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Network">
            <Select
              value={network}
              onChange={(e) => {
                setNetwork(e.target.value);
                setOffset(0);
              }}
            >
              <option value="">All networks</option>
              {(networks.data ?? []).map((n) => (
                <option key={n.slug} value={n.slug}>
                  {n.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Status">
            <Select
              value={status}
              onChange={(e) => {
                setStatus(e.target.value);
                setOffset(0);
              }}
            >
              <option value="">Any status</option>
              {OPPORTUNITY_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Min net profit (USD)">
            <Input
              type="number"
              inputMode="decimal"
              step="0.01"
              placeholder="0"
              value={minProfit}
              onChange={(e) => {
                setMinProfit(e.target.value);
                setOffset(0);
              }}
            />
          </Field>
          <div className="flex items-end">
            <Button
              variant="ghost"
              onClick={() => {
                setNetwork("");
                setStatus("");
                setMinProfit("");
                setOffset(0);
              }}
            >
              Clear filters
            </Button>
          </div>
        </CardBody>
      </Card>

      {!notReady ? <ApiErrorBanner error={error} onRetry={refresh} stale={Boolean(data)} /> : null}

      <Card>
        <CardHeader
          title="Opportunities"
          description={data ? (data.total ? `${fmtNum(data.total)} total · showing ${offset + 1}–${Math.min(offset + PAGE_SIZE, data.total)}` : "0 total") : undefined}
          actions={<SimulatedPill />}
        />
        {notReady ? (
          <CardBody>
            <MilestoneState feature="Opportunity data" milestone={2} />
          </CardBody>
        ) : loading && !data ? (
          <SkeletonRows rows={8} cols={8} />
        ) : !rows.length ? (
          <CardBody>
            <EmptyState title="No opportunities match" description="Adjust the filters or wait for the indexer and profitability engine to evaluate positions." />
          </CardBody>
        ) : (
          <TableWrap>
            <Table>
              <THead>
                <tr>
                  <SortableTh label="Network" field="network" sort={sort} onSort={onSort} />
                  <Th>Borrower</Th>
                  <SortableTh label="Health factor" field="health_factor" sort={sort} onSort={onSort} align="right" />
                  <Th>Debt asset</Th>
                  <Th>Collateral asset</Th>
                  <SortableTh label="Repayment" field="repayment_usd" sort={sort} onSort={onSort} align="right" />
                  <SortableTh label="Bonus" field="bonus_bps" sort={sort} onSort={onSort} align="right" />
                  <SortableTh label="Flash-loan fee" field="flash_loan_fee" sort={sort} onSort={onSort} align="right" />
                  <SortableTh label="Swap impact" field="swap_impact_bps" sort={sort} onSort={onSort} align="right" />
                  <SortableTh label="Gas" field="gas_usd" sort={sort} onSort={onSort} align="right" />
                  <SortableTh label="Net profit" field="net_profit_usd" sort={sort} onSort={onSort} align="right" />
                  <SortableTh label="Attainable (modeled)" field="attainable_net_profit_usd" sort={sort} onSort={onSort} align="right" />
                  <SortableTh label="Status" field="status" sort={sort} onSort={onSort} />
                </tr>
              </THead>
              <TBody>
                {rows.map((o) => {
                  const net = numeric(o.net_profit_usd);
                  return (
                    <Tr
                      key={o.id}
                      className="cursor-pointer"
                      tabIndex={0}
                      onClick={() => setSelected(o)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          setSelected(o);
                        }
                      }}
                      aria-label={`Open opportunity ${o.id}`}
                    >
                      <Td>{o.network}</Td>
                      <Td mono>
                        <span className="inline-flex items-center gap-1">
                          <span title={o.borrower}>{shortAddr(o.borrower)}</span>
                          <CopyButton value={o.borrower} />
                        </span>
                      </Td>
                      <Td align="right" className={numeric(o.health_factor) < 1 ? "text-bad" : ""}>
                        {fmtHealthFactor(o.health_factor)}
                      </Td>
                      <Td mono title={o.debt_asset}>
                        {shortAddr(o.debt_asset)}
                      </Td>
                      <Td mono title={o.collateral_asset}>
                        {shortAddr(o.collateral_asset)}
                      </Td>
                      <Td align="right" title={`raw ${o.repayment}`}>
                        {fmtUsd(o.repayment_usd)}
                      </Td>
                      <Td align="right">{fmtBps(o.bonus_bps)}</Td>
                      <Td align="right">{fmtNum(o.flash_loan_fee, 6)}</Td>
                      <Td align="right">{fmtBps(o.swap_impact_bps)}</Td>
                      <Td align="right">{fmtUsd(o.gas_usd)}</Td>
                      <Td align="right" className={net > 0 ? "font-semibold text-good" : net < 0 && Number.isFinite(net) ? "text-bad" : ""}>
                        {fmtUsd(o.net_profit_usd)}
                      </Td>
                      <Td align="right" title={o.observability ? `observability: ${o.observability}` : undefined}>
                        {fmtUsd(o.attainable_net_profit_usd)}
                      </Td>
                      <Td>
                        <Badge tone={STATUS_TONE[o.status] ?? "neutral"}>{o.status}</Badge>
                      </Td>
                    </Tr>
                  );
                })}
              </TBody>
            </Table>
          </TableWrap>
        )}
        {data && data.total > PAGE_SIZE ? (
          <div className="flex items-center justify-between border-t border-border px-3 py-2 text-xs">
            <Button size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
              Previous
            </Button>
            <span className="text-muted">
              page {Math.floor(offset / PAGE_SIZE) + 1} of {Math.ceil(data.total / PAGE_SIZE)}
            </span>
            <Button size="sm" disabled={offset + PAGE_SIZE >= data.total} onClick={() => setOffset(offset + PAGE_SIZE)}>
              Next
            </Button>
          </div>
        ) : null}
      </Card>

      <Drawer open={selected !== null} onClose={closeDrawer} title={selected ? `Opportunity #${selected.id}` : "Opportunity"}>
        {selected ? (
          <div className="flex flex-col gap-3 text-xs">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={STATUS_TONE[selected.status] ?? "neutral"}>{selected.status}</Badge>
              <Badge tone="sim">{selected.outcome_class}</Badge>
              <SimulatedPill />
            </div>
            <KV
              items={[
                { k: "Network", v: selected.network },
                { k: "Borrower", v: <span className="inline-flex items-center gap-1">{selected.borrower}<CopyButton value={selected.borrower} /></span>, mono: true },
                { k: "Health factor", v: fmtHealthFactor(selected.health_factor) },
                { k: "Debt asset", v: <span className="inline-flex items-center gap-1">{selected.debt_asset}<CopyButton value={selected.debt_asset} /></span>, mono: true },
                { k: "Collateral asset", v: <span className="inline-flex items-center gap-1">{selected.collateral_asset}<CopyButton value={selected.collateral_asset} /></span>, mono: true },
                { k: "Repayment (raw)", v: selected.repayment, mono: true },
                { k: "Repayment (USD)", v: fmtUsd(selected.repayment_usd) },
                { k: "Liquidation bonus", v: `${fmtBps(selected.bonus_bps)} (${selected.bonus_bps} bps)` },
                { k: "Flash-loan fee", v: selected.flash_loan_fee, mono: true },
                { k: "Swap impact", v: selected.swap_impact_bps === null ? "— (no quote)" : `${fmtBps(selected.swap_impact_bps)} (${selected.swap_impact_bps} bps)` },
                { k: "Gas (USD)", v: fmtUsd(selected.gas_usd) },
                { k: "Net profit (USD)", v: <span className="inline-flex items-center gap-2">{fmtUsd(selected.net_profit_usd)}<SimulatedPill /></span> },
                { k: "Block", v: fmtNum(selected.block_number), mono: true },
                { k: "Created", v: fmtTime(selected.created_at) },
              ]}
            />
            <div>
              <h3 className="mb-1 text-[11px] uppercase tracking-wide text-muted">Incomplete reasons</h3>
              {selected.incomplete_reasons?.length ? (
                <ul className="list-inside list-disc rounded-sm border border-warn/30 bg-warn-bg px-2 py-1 text-warn">
                  {selected.incomplete_reasons.map((r, i) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
              ) : (
                <p className="text-faint">None recorded.</p>
              )}
            </div>
          </div>
        ) : null}
      </Drawer>
    </div>
  );
}
