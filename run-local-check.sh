#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# run-local-check.sh - Run Amazon job check locally
# Usage: ./run-local-check.sh
# -----------------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# -- Load .env if present ------------------------------------------------------
ENV_FILE="$SCRIPT_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
  echo "Loading .env..."
  set -o allexport
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +o allexport
else
  echo "No .env file found - using existing environment variables"
fi

# -- Check required vars -------------------------------------------------------
MISSING=0
for VAR in TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID; do
  if [[ -z "${!VAR:-}" ]]; then
    echo "Missing required env var: $VAR"
    MISSING=1
  fi
done

if [[ $MISSING -eq 1 ]]; then
  echo ""
  echo "Create a .env file with:"
  echo "  TELEGRAM_BOT_TOKEN=your_bot_token"
  echo "  TELEGRAM_CHAT_ID=your_chat_id"
  echo "  PUSHOVER_TOKEN=your_app_token     # optional"
  echo "  PUSHOVER_USER=your_user_key       # optional"
  exit 1
fi

# -- Install deps if needed ----------------------------------------------------
if [[ ! -d "$SCRIPT_DIR/node_modules" ]]; then
  echo "Installing dependencies..."
  cd "$SCRIPT_DIR"
  npm install
  npx playwright install chromium
fi

# -- Run check -----------------------------------------------------------------
echo "Running Amazon job check at $(date)..."
echo ""
cd "$SCRIPT_DIR"
node check-amazon.js
echo ""
echo "Done"
