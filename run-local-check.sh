#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# run-local-check.sh - Run Amazon job check locally
# Usage: ./run-local-check.sh
# -----------------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# -- Load .env if present ------------------------------------------------------
for ENV_FILE in "$SCRIPT_DIR/.env" "$SCRIPT_DIR/.env.local"; do
  if [[ ! -f "$ENV_FILE" ]]; then
    continue
  fi
  echo "Loading ${ENV_FILE##*/}..."
  set -o allexport
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +o allexport
done

# -- Check required vars -------------------------------------------------------
MISSING=0
if [[ "${DRY_RUN:-0}" != "1" ]]; then
  for VAR in TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID; do
    if [[ -z "${!VAR:-}" ]]; then
      echo "Missing required env var: $VAR"
      MISSING=1
    fi
  done
fi

if [[ $MISSING -eq 1 ]]; then
  echo ""
  echo "Create a .env.local file with:"
  echo "  TELEGRAM_BOT_TOKEN=your_bot_token"
  echo "  TELEGRAM_CHAT_ID=your_chat_id"
  echo ""
  echo "For a no-alert test run:"
  echo "  DRY_RUN=1 ./run-local-check.sh --dry-run"
  exit 1
fi

# -- Install deps if needed ----------------------------------------------------
if ! python3 -c "import playwright" >/dev/null 2>&1; then
  echo "Installing Python dependencies..."
  cd "$SCRIPT_DIR"
  python3 -m pip install -r requirements.txt
fi

# -- Run check -----------------------------------------------------------------
echo "Running Amazon job check at $(date)..."
echo ""
cd "$SCRIPT_DIR"
python3 -m checker.main run "$@"
echo ""
echo "Done"
