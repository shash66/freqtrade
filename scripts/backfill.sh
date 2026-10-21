#!/usr/bin/env bash
# Bounded historical backfill of Aave pool events. Usage: scripts/backfill.sh base 20000000 20100000
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose run --rm -T indexer python -m app.indexer.cli backfill "$@"
