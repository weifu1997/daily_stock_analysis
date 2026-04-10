#!/usr/bin/env bash
set -euo pipefail

WORKDIR="/root/projects/daily_stock_analysis"
LOG_FILE="$WORKDIR/logs/cron_daily_stock_analysis.log"
LOCK_FILE="/tmp/daily_stock_analysis.lock"
PYTHON_BIN="$WORKDIR/.venv/bin/python"

mkdir -p "$WORKDIR/logs"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "$(date '+%F %T') already running, skip" >> "$LOG_FILE"
  exit 0
fi

cd "$WORKDIR"
if command -v tmux >/dev/null 2>&1; then
  if tmux has-session -t daily_stock_analysis 2>/dev/null; then
    echo "$(date '+%F %T') tmux session already exists: daily_stock_analysis" >> "$LOG_FILE"
    exit 0
  fi
  tmux new-session -d -s daily_stock_analysis "bash -lc 'cd \"$WORKDIR\" && export DAILY_STOCK_ANALYSIS_ENTRY=script && exec \"$PYTHON_BIN\" main.py >> \"$LOG_FILE\" 2>&1'"
  echo "$(date '+%F %T') started tmux session: daily_stock_analysis" >> "$LOG_FILE"
  exit 0
fi

export DAILY_STOCK_ANALYSIS_ENTRY=script
exec "$PYTHON_BIN" main.py >> "$LOG_FILE" 2>&1
