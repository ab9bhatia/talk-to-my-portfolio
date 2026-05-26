#!/usr/bin/env bash
# Create gitignored local config from templates (safe to run after clone).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  cp .env-example .env
  echo "Created .env"
else
  echo "Exists: .env"
fi

ACCOUNTS_JSON="modules/portfolio/accounts.json"
if [[ ! -f "$ACCOUNTS_JSON" ]]; then
  cp modules/portfolio/accounts.example.json "$ACCOUNTS_JSON"
  echo "Created $ACCOUNTS_JSON"
else
  echo "Exists: $ACCOUNTS_JSON"
fi

cat <<'EOF'

Next:
  1. Edit modules/portfolio/accounts.json — enable brokers, set unique "id" per account
  2. Edit .env — ZERODHA_* / GROWW_* suffix must match each "id" (UPPERCASE)
  3. Get keys: docs/broker-api-keys.md
  4. uvicorn main:app --reload --host 127.0.0.1 --port 8000
     → http://127.0.0.1:8000/portfolio → Connect Zerodha

EOF
