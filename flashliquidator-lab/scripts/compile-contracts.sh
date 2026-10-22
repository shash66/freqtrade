#!/usr/bin/env bash
# Compiles contracts/src/FlashLiquidator.sol with solc-js (no Foundry needed) and installs the artifact that the
# backend's fork simulator deploys on Anvil. Foundry's `forge build` output (contracts/out/) is used instead when present.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -d contracts/node_modules/@openzeppelin/contracts ]; then
  (cd contracts && npm init -y >/dev/null 2>&1 || true && npm install --silent solc@0.8.24 @openzeppelin/contracts@5.0.2)
fi
NODE_PATH=contracts/node_modules node scripts/compile-contracts.js
cp contracts/out-solcjs/FlashLiquidator.json backend/app/simulation/artifacts/FlashLiquidator.json
cp contracts/out-solcjs/CometLiquidator.json backend/app/simulation/artifacts/CometLiquidator.json
cp contracts/out-solcjs/FlashArb.json backend/app/simulation/artifacts/FlashArb.json
echo "artifacts installed: backend/app/simulation/artifacts/{FlashLiquidator,CometLiquidator,FlashArb}.json"
