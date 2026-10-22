#!/usr/bin/env bash
# Phase 5: multi-strategy profitability research. Read-only; forks run on the compose stack's private Anvil.
# Requires *_ARCHIVE_RPC / *_RPC_HTTP for the network. Minutes of shadow monitoring: SHADOW_MINUTES (default 30).
#
#   scripts/phase5-research.sh base
#   scripts/phase5-research.sh arbitrum
#
# Produces in docs/: phase5-<strategy>-<net>-{discover,simulate,replay,shadow}.json for compound_v3, dex_arb and
# yield_opt, phase5-comet-history-<net>.json (Base: the window of the Phase 3 dataset), phase5-ranking.json and
# phase5-results.md. Every step writes its own artefact so a failed step can be rerun alone.
set -euo pipefail
cd "$(dirname "$0")/.."
NET=${1:-base}
ANVIL=${ANVIL_RPC_URL:-http://anvil:8545}
RUN="docker compose run --rm -T -v $PWD/docs:/out worker"
SHADOW_MINUTES=${SHADOW_MINUTES:-30}
ARCHIVE_VAR="$(echo "$NET" | tr a-z A-Z)_ARCHIVE_RPC"
ARCHIVE_RPC=${!ARCHIVE_VAR:-}

# historical window: Base = the Phase 3 dataset window (1.9 days); Arbitrum = the same span of time ending near the head
if [ "$NET" = "base" ]; then FROM=52560000; TO=52640886; STEP=400; else FROM=516700000; TO=517350000; STEP=3250; fi

# Strategy A: Compound V3 (history scan needs an archive endpoint with eth_getLogs; Alchemy free = 10-block spans, slow)
if [ "$NET" = "base" ] && [ -n "$ARCHIVE_RPC" ]; then
  $RUN python -m app.research.comet_history --network "$NET" --rpc "$ARCHIVE_RPC" --from "$FROM" --to "$TO" --span 10 --out "/out/phase5-comet-history-$NET.json"
fi
$RUN python -m app.plugins.run --strategy compound_v3 --network "$NET" --mode simulate --anvil "$ANVIL" --force-sim 1 \
  $( [ -f "docs/phase5-comet-history-$NET.json" ] && echo "--history /out/phase5-comet-history-$NET.json" ) --out "/out/phase5-compound_v3-$NET-simulate.json"
$RUN python -m app.plugins.run --strategy compound_v3 --network "$NET" --mode discover --out "/out/phase5-compound_v3-$NET-discover.json"
if [ -f "docs/phase5-comet-history-$NET.json" ]; then
  $RUN python -m app.plugins.run --strategy compound_v3 --network "$NET" --mode replay --anvil "$ANVIL" --history "/out/phase5-comet-history-$NET.json" \
    --from "$FROM" --to "$TO" --out "/out/phase5-compound_v3-$NET-replay.json"
fi

# Strategy B: DEX arbitrage (validation simulations run the two best rows even when every spread is negative)
$RUN python -m app.plugins.run --strategy dex_arb --network "$NET" --mode discover --out "/out/phase5-dex_arb-$NET-discover.json"
$RUN python -m app.plugins.run --strategy dex_arb --network "$NET" --mode simulate --anvil "$ANVIL" --force-sim 2 --out "/out/phase5-dex_arb-$NET-simulate.json"
STRATEGY_DEX_ARB_STEP=$STEP $RUN python -m app.plugins.run --strategy dex_arb --network "$NET" --mode replay --from "$FROM" --to "$TO" --out "/out/phase5-dex_arb-$NET-replay.json"
$RUN python -m app.plugins.run --strategy dex_arb --network "$NET" --mode shadow --minutes "$SHADOW_MINUTES" --poll 2 --out "/out/phase5-dex_arb-$NET-shadow.json"

# Strategy C: yield (7 daily samples ending at the head)
$RUN python -m app.plugins.run --strategy yield_opt --network "$NET" --mode discover --out "/out/phase5-yield_opt-$NET-discover.json"
HEAD=$($RUN python -c "from app.chains.config import build_networks; from app.chains.rpc import RpcClient; from app.core.config import get_settings; s=get_settings(); n=build_networks(s)['$NET']; print(RpcClient('h', n.rpc_http or n.archive_rpc).w3.eth.block_number)" | tail -1)
DAY=$([ "$NET" = "base" ] && echo 43200 || echo 345600)
$RUN python -m app.plugins.run --strategy yield_opt --network "$NET" --mode replay --from $((HEAD - 7 * DAY)) --to "$HEAD" --out "/out/phase5-yield_opt-$NET-replay.json"

# Ranking and report (over every network that has artefacts)
$RUN python -m app.plugins.ranking --docs /out --out /out/phase5-ranking.json
$RUN python -m app.validation.phase5_report --docs /out --networks base,arbitrum --out /out/phase5-results.md
