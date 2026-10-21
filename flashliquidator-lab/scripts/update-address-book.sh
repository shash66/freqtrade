#!/usr/bin/env bash
# Prints the current Aave V3 Base/Arbitrum addresses from the official npm address book so that
# backend/app/aave/address_book.py can be refreshed and reviewed by hand (never edited blindly).
set -euo pipefail
TMP=$(mktemp -d); cd "$TMP"
npm pack @bgd-labs/aave-address-book --silent >/dev/null
tar xzf bgd-labs-aave-address-book-*.tgz
echo "address book version: $(node -p "require('./package/package.json').version")"
for f in AaveV3Base AaveV3Arbitrum; do
  echo "== $f"
  grep -A1 -E "constant (POOL|POOL_IMPL|POOL_ADDRESSES_PROVIDER|ORACLE|AAVE_PROTOCOL_DATA_PROVIDER|UI_POOL_DATA_PROVIDER) ?=" "package/src/$f.sol" \
    | grep -oE "constant \w+|0x[0-9a-fA-F]{40}" | paste - - || true
  grep -E "_UNDERLYING =|_DECIMALS =" "package/src/$f.sol" | sed 's/  */ /g;s/internal constant //'
done
rm -rf "$TMP"
