#!/usr/bin/env bash
# Phase 3: profit verification and competitive execution research. Read-only; every execution happens on
# the private Anvil fork of the compose stack. Requires *_ARCHIVE_RPC (archive-capable, e.g. Alchemy) for
# replays, reconstruction and theoretical valuation, and *_RPC_HTTP for the live benchmark.
#
#   scripts/phase3-research.sh base [replays] [benchmark_minutes]
#
# Produces in docs/: phase3-theoretical-<net>.json, phase3-replays-<net>.{md,json},
# phase3-reconstruction-<net>-<block>.json (for each successful replay block listed in RECONSTRUCT_BLOCKS),
# phase3-benchmark-<net>.json, phase3-bidmodel-<net>.json and phase3-results.md.
set -euo pipefail
cd "$(dirname "$0")/.."
NET=${1:-base}; REPLAYS=${2:-40}; BENCH_MIN=${3:-120}
RUN="docker compose run --rm -T -v $PWD/docs:/out worker"

$RUN python -m app.validation.theoretical --network "$NET" --out "/out/phase3-theoretical-$NET.json"
$RUN python -m app.validation.phase2 --network "$NET" --blocks 0 --replays "$REPLAYS" --batch "phase3-$NET" \
  --out "/out/phase3-replays-$NET.md" --json "/out/phase3-replays-$NET.json"

# Reconstruct the important events: "block:borrower:collateral:debt:tx:debtToCover" entries, space separated.
for spec in ${RECONSTRUCT_EVENTS:-}; do
  IFS=: read -r BLOCK BORROWER COLL DEBT TX COVER <<<"$spec"
  $RUN python -m app.simulation.reconstruct --network "$NET" --block "$BLOCK" --borrower "$BORROWER" --collateral "$COLL" \
    --debt "$DEBT" --tx "$TX" --debt-to-cover "$COVER" --simulate --relevant-only \
    --out "/out/phase3-reconstruction-$NET-$BLOCK.json"
done

$RUN python -m app.competition.benchmark --network "$NET" --minutes "$BENCH_MIN" --hf-max 1000000 \
  --anvil "${ANVIL_RPC_URL:-http://anvil:8545}" --sim-every 100 --max-sims 12 --out "/out/phase3-benchmark-$NET.json"
$RUN python -m app.competition.bidmodel --network "$NET" --replays-json "/out/phase3-replays-$NET.json" \
  --benchmark-json "/out/phase3-benchmark-$NET.json" --eth-usd "${ETH_USD:-4000}" --out "/out/phase3-bidmodel-$NET.json"
$RUN python -m app.validation.phase3_report --docs /out --networks "$NET" --out /out/phase3-results.md
