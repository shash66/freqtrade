#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
echo "== backend"; (cd backend && python -m pytest -q)
if command -v forge >/dev/null; then echo "== contracts"; (cd contracts && forge test); else echo "== contracts: SKIPPED (forge not installed)"; fi
if [ -d frontend/node_modules ]; then echo "== frontend"; (cd frontend && npm run lint && npm run build); fi
