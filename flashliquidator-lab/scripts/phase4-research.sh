#!/usr/bin/env bash
# Phase 4: competitive strategy optimization. Read-only; forks run on the compose stack's private Anvil.
# Requires the Phase 3 artefacts (scripts/phase3-research.sh) and *_ARCHIVE_RPC / *_RPC_HTTP for the network.
#
#   scripts/phase4-research.sh base
#
# Produces in docs/: phase4-observability-<net>.json, phase4-latency-<net>.json, phase4-strategies-<net>.json,
# phase4-markets.json and phase4-results.md.
set -euo pipefail
cd "$(dirname "$0")/.."
NET=${1:-base}
ANVIL=${ANVIL_RPC_URL:-http://anvil:8545}
RUN="docker compose run --rm -T -v $PWD/docs:/out worker"

$RUN python -m app.research.observability --network "$NET" --anvil "$ANVIL" \
  --timegate-json "/out/phase3-timegate-$NET.json" --replays-json "/out/phase3-replays-$NET.json" --out "/out/phase4-observability-$NET.json"
$RUN python -m app.competition.latency --network "$NET" --positions 5 --rounds 10 --anvil "$ANVIL" --evm-rounds 3 --out "/out/phase4-latency-$NET.json"
$RUN python -m app.competition.bidmodel --network "$NET" --replays-json "/out/phase3-replays-$NET.json" --eth-usd "${ETH_USD:-4000}" --out "/out/phase3-bidmodel-$NET.json"
$RUN python -m app.competition.compare --network "$NET" --observability-json "/out/phase4-observability-$NET.json" \
  --replays-json "/out/phase3-replays-$NET.json" --latency-json "/out/phase4-latency-$NET.json" --bidmodel-json "/out/phase3-bidmodel-$NET.json" \
  --out "/out/phase4-strategies-$NET.json"
$RUN python -m app.protocols.registry --out /out/phase4-markets.json --sample-history-blocks 100
$RUN python -m app.validation.phase4_report --docs /out --networks "$NET" --out /out/phase4-results.md
