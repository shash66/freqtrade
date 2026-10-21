import type { Network, RpcStatus } from "@/lib/api";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Badge, StatusDot } from "@/components/ui/badge";
import { CopyButton } from "@/components/ui/copy-button";
import { explorerAddress, fmtNum, shortAddr } from "@/lib/format";

function RpcRow({ rpc }: { rpc: RpcStatus }) {
  const state = !rpc.configured ? "not configured" : rpc.ok ? "ok" : "down";
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-0.5 text-xs">
      <span className="inline-flex items-center gap-2">
        <StatusDot ok={rpc.ok} pending={!rpc.configured} />
        <span className="font-medium uppercase">{rpc.kind}</span>
        <span className="text-muted">{state}</span>
      </span>
      <span className="tabular inline-flex gap-3 text-muted">
        <span title="latency">{rpc.latency_ms !== null ? `${rpc.latency_ms} ms` : "— ms"}</span>
        <span title="block height">#{rpc.block_number !== null ? fmtNum(rpc.block_number) : "—"}</span>
      </span>
      {rpc.error ? <span className="w-full break-words font-mono text-[11px] text-bad">{rpc.error}</span> : null}
    </div>
  );
}

export function NetworkCard({ network }: { network: Network }) {
  return (
    <Card>
      <CardHeader
        title={
          <span className="inline-flex items-center gap-2">
            {network.name}
            <span className="font-mono text-[11px] font-normal text-muted">chain {network.chain_id}</span>
          </span>
        }
        actions={
          <>
            {network.enabled ? <Badge tone="good">enabled</Badge> : <Badge tone="neutral">disabled</Badge>}
            {network.deployment_verified ? (
              <Badge tone="good">deployment verified</Badge>
            ) : (
              <Badge tone="bad">not verified</Badge>
            )}
          </>
        }
      />
      <CardBody className="flex flex-col gap-2">
        <div className="flex flex-col gap-1">
          {network.rpc.length ? network.rpc.map((r) => <RpcRow key={r.kind} rpc={r} />) : <p className="text-xs text-muted">No RPC status.</p>}
          {!network.has_archive_rpc ? <p className="text-[11px] text-faint">Archive RPC not configured; historical replay is limited.</p> : null}
        </div>
        {network.verification_error ? (
          <p className="break-words rounded-sm border border-bad/30 bg-bad-bg px-2 py-1 font-mono text-[11px] text-bad">
            {network.verification_error}
          </p>
        ) : null}
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
          <dt className="text-muted">Pool</dt>
          <dd className="inline-flex min-w-0 items-center gap-1 font-mono">
            <a
              className="truncate text-accent underline-offset-2 hover:underline"
              href={explorerAddress(network.explorer_url, network.pool_address)}
              target="_blank"
              rel="noreferrer"
              title={network.pool_address}
            >
              {shortAddr(network.pool_address, 8, 6)}
            </a>
            <CopyButton value={network.pool_address} />
          </dd>
          <dt className="text-muted">Oracle</dt>
          <dd className="inline-flex min-w-0 items-center gap-1 font-mono">
            <span className="truncate" title={network.oracle_address}>
              {shortAddr(network.oracle_address, 8, 6)}
            </span>
            <CopyButton value={network.oracle_address} />
          </dd>
          <dt className="text-muted">Address book</dt>
          <dd className="font-mono">{network.address_book_version}</dd>
          <dt className="text-muted">Native</dt>
          <dd>{network.native_symbol}</dd>
        </dl>
      </CardBody>
    </Card>
  );
}
