#!/usr/bin/env bash
# First-run helper: creates .env from the example, generates POSTGRES_PASSWORD and the admin hash.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then echo ".env exists; not overwriting"; exit 0; fi
cp .env.example .env
PG=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
sed -i "s|POSTGRES_PASSWORD=change-me|POSTGRES_PASSWORD=$PG|; s|fll:change-me@postgres|fll:$PG@postgres|" .env
read -r -s -p "Choose an admin password: " PW; echo
HASH=$(docker compose run --rm -T api python -c "from app.core.security import hash_password; print(hash_password('$PW'))" 2>/dev/null | tail -1)
# bcrypt hashes contain '$'; Compose env files interpolate '$VAR', so every '$' must be written as '$$'.
ESCAPED=$(printf '%s' "$HASH" | sed 's/\$/$$/g')
sed -i "s|ADMIN_PASSWORD_HASH=|ADMIN_PASSWORD_HASH=$ESCAPED|" .env
echo "Wrote .env. Now add your RPC endpoints (BASE_RPC_HTTP, ARBITRUM_RPC_HTTP, ...) and run: docker compose up -d --build"
