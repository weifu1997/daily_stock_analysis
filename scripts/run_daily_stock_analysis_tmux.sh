#!/usr/bin/env bash
set -euo pipefail

WORKDIR="/root/projects/daily_stock_analysis"
SESSION_NAME="daily_stock_analysis"
SCRIPT="$WORKDIR/scripts/run_daily_stock_analysis.sh"
LOG_FILE="$WORKDIR/logs/tmux_daily_stock_analysis.log"

mkdir -p "$WORKDIR/logs"

if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  echo "$(date '+%F %T') tmux session already exists: $SESSION_NAME" | tee -a "$LOG_FILE"
  exit 0
fi

tmux new-session -d -s "$SESSION_NAME" "bash -lc 'cd "$WORKDIR" && exec "$SCRIPT"'"
echo "$(date '+%F %T') started tmux session: $SESSION_NAME" | tee -a "$LOG_FILE"
