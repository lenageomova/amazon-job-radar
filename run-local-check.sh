#!/bin/zsh
set -euo pipefail

PROJECT_DIR="/Users/ellingrov/Documents/amazon-job-radar"
ENV_FILE="$PROJECT_DIR/.env.local"
LOG_DIR="$PROJECT_DIR/output/local-runner"
LOG_FILE="$LOG_DIR/amazon-job-radar.log"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

mkdir -p "$LOG_DIR"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

cd "$PROJECT_DIR"

{
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting Amazon job check"
  npm run check
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Amazon job check completed"
} >>"$LOG_FILE" 2>&1
