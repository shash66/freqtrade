#!/usr/bin/env bash
# Phase 2 live validation, read-only. Requires BASE_RPC_HTTP / ARBITRUM_RPC_HTTP (+ *_ARCHIVE_RPC for replays)
# and a running Anvil (the compose `anvil` service). Writes docs/phase2-results-<network>.md and .json.
set -euo pipefail
cd "$(dirname "$0")/.."
NET=${1:-base}; BLOCKS=${2:-20000}; REPLAYS=${3:-25}
docker compose run --rm -T -v "$PWD/docs:/out" worker \
  python -m app.validation.phase2 --network "$NET" --blocks "$BLOCKS" --replays "$REPLAYS" \
  --out "/out/phase2-results-$NET.md" --json "/out/phase2-results-$NET.json"
