#!/usr/bin/env bash
set -euo pipefail

WORKDIR="/root/projects/daily_stock_analysis"
LOG_FILE="$WORKDIR/logs/cron_daily_stock_analysis.log"
LOCK_FILE="/tmp/daily_stock_analysis.lock"
PYTHON_BIN="$WORKDIR/.venv/bin/python"
SESSION_NAME="daily_stock_analysis"
SESSION_META_FILE="$WORKDIR/logs/.daily_stock_analysis.session.meta"
EXPECTED_ENTRY="script"
EXPECTED_CMD="main.py"

mkdir -p "$WORKDIR/logs"

log_runtime_meta() {
  {
    echo "$(date '+%F %T') ===== daily_stock_analysis runtime meta ====="
    echo "$(date '+%F %T') cwd=$(pwd)"
    echo "$(date '+%F %T') workdir=$WORKDIR"
    echo "$(date '+%F %T') python_bin=$PYTHON_BIN"
    echo "$(date '+%F %T') python_path=$(readlink -f "$PYTHON_BIN" 2>/dev/null || echo "$PYTHON_BIN")"
    echo "$(date '+%F %T') python_version=$($PYTHON_BIN -V 2>&1 || true)"
    echo "$(date '+%F %T') git_commit=$(git -C "$WORKDIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    echo "$(date '+%F %T') git_branch=$(git -C "$WORKDIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
    echo "$(date '+%F %T') git_status=$(git -C "$WORKDIR" status --short 2>/dev/null | tr '\n' ' ' | sed 's/[[:space:]]\+/ /g' | sed 's/[[:space:]]$//')"
    echo "$(date '+%F %T') tmux_session_present=$(tmux has-session -t "$SESSION_NAME" 2>/dev/null && echo yes || echo no)"
    echo "$(date '+%F %T') tmux_env=${TMUX:-none}"
    echo "$(date '+%F %T') entry=${DAILY_STOCK_ANALYSIS_ENTRY:-unset}"
    echo "$(date '+%F %T') =========================================="
  } >> "$LOG_FILE"
}

log_tmux_session_details() {
  local session_present="$1"
  if [[ "$session_present" != "yes" ]]; then
    echo "$(date '+%F %T') tmux session details: <none>" >> "$LOG_FILE"
    return 0
  fi

  local session_info pane_info
  session_info=$(tmux display-message -p -t "$SESSION_NAME" \
    'session_id=#{session_id} session_name=#{session_name} windows=#{session_windows} attached=#{session_attached} activity=#{session_activity}' 2>/dev/null || true)
  pane_info=$(tmux list-panes -t "$SESSION_NAME" -F \
    'pane_id=#{pane_id} pane_index=#{pane_index} pane_pid=#{pane_pid} pane_current_command=#{pane_current_command} pane_start_command=#{pane_start_command} pane_start_path=#{pane_start_path} pane_dead=#{pane_dead_status}' 2>/dev/null || true)
  local last_log_line last_log_ts session_age_seconds
  last_log_line=$(tail -n 1 "$LOG_FILE" 2>/dev/null || true)
  last_log_ts=$(printf '%s\n' "$last_log_line" | awk 'match($0,/^[0-9-]+ [0-9:]+/){print substr($0,RSTART,RLENGTH)}' | tail -n 1)
  session_age_seconds="unknown"
  if [[ -f "$SESSION_META_FILE" ]]; then
    session_age_seconds=$(python - <<'PY' "$SESSION_META_FILE"
import sys
from pathlib import Path
from datetime import datetime
p = Path(sys.argv[1])
start = None
for line in p.read_text(encoding='utf-8').splitlines():
    if line.startswith('START_TIME='):
        start = line.split('=', 1)[1].strip()
        break
if not start:
    print('unknown')
    raise SystemExit(0)
try:
    dt = datetime.strptime(start, '%Y-%m-%d %H:%M:%S')
    print(int((datetime.now() - dt).total_seconds()))
except Exception:
    print('unknown')
PY
)
  fi

  {
    echo "$(date '+%F %T') ===== tmux session details: $SESSION_NAME ====="
    [[ -n "$session_info" ]] && echo "$(date '+%F %T') $session_info"
    [[ -n "$pane_info" ]] && echo "$(date '+%F %T') $pane_info"
    echo "$(date '+%F %T') session_meta_file=$SESSION_META_FILE"
    echo "$(date '+%F %T') session_age_seconds=$session_age_seconds"
    echo "$(date '+%F %T') last_log_line=${last_log_line:-none}"
    echo "$(date '+%F %T') last_log_ts=${last_log_ts:-none}"
    echo "$(date '+%F %T') ============================================="
  } >> "$LOG_FILE"
}

write_session_meta() {
  local git_commit git_branch start_time python_path
  git_commit=$(git -C "$WORKDIR" rev-parse --short HEAD 2>/dev/null || echo unknown)
  git_branch=$(git -C "$WORKDIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)
  start_time=$(date '+%F %T')
  python_path=$(readlink -f "$PYTHON_BIN" 2>/dev/null || echo "$PYTHON_BIN")
  cat > "$SESSION_META_FILE" <<EOF
SESSION_NAME=$SESSION_NAME
WORKDIR=$WORKDIR
PYTHON_BIN=$PYTHON_BIN
PYTHON_PATH=$python_path
GIT_COMMIT=$git_commit
GIT_BRANCH=$git_branch
ENTRY=$EXPECTED_ENTRY
CMD=$EXPECTED_CMD
START_TIME=$start_time
EOF
}

session_should_restart="no"
session_present="no"
existing_commit="unknown"
existing_entry="unknown"
existing_cmd="unknown"
existing_pid=""

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "$(date '+%F %T') already running, skip" >> "$LOG_FILE"
  exit 0
fi

cd "$WORKDIR"
log_runtime_meta
if command -v tmux >/dev/null 2>&1; then
  if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    session_present="yes"
  fi
  log_tmux_session_details "$session_present"

  if [[ "$session_present" == "yes" ]]; then
    if [[ -f "$SESSION_META_FILE" ]]; then
      # shellcheck disable=SC1090
      source "$SESSION_META_FILE" || true
      existing_commit="${GIT_COMMIT:-unknown}"
      existing_entry="${ENTRY:-unknown}"
      existing_cmd="${CMD:-unknown}"
      existing_pid="${PANE_PID:-}"
    fi

    pane_pid="$(tmux list-panes -t "$SESSION_NAME" -F '#{pane_pid}' 2>/dev/null | head -n 1 || true)"
    pane_cmd="$(tmux list-panes -t "$SESSION_NAME" -F '#{pane_current_command}' 2>/dev/null | head -n 1 || true)"
    pane_path="$(tmux list-panes -t "$SESSION_NAME" -F '#{pane_start_path}' 2>/dev/null | head -n 1 || true)"
    if [[ -n "$pane_pid" ]]; then
      if ! kill -0 "$pane_pid" 2>/dev/null; then
        session_should_restart="yes"
        echo "$(date '+%F %T') stale tmux session detected: pane pid $pane_pid is not alive" >> "$LOG_FILE"
      fi
    else
      session_should_restart="yes"
      echo "$(date '+%F %T') stale tmux session detected: pane pid unavailable" >> "$LOG_FILE"
    fi

    current_commit="$(git -C "$WORKDIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    if [[ "$existing_commit" != "$current_commit" ]]; then
      session_should_restart="yes"
      echo "$(date '+%F %T') old tmux session detected: git_commit $existing_commit -> $current_commit" >> "$LOG_FILE"
    fi
    if [[ "$existing_entry" != "$EXPECTED_ENTRY" || "$existing_cmd" != "$EXPECTED_CMD" ]]; then
      session_should_restart="yes"
      echo "$(date '+%F %T') session command mismatch: ENTRY=$existing_entry CMD=$existing_cmd expected ENTRY=$EXPECTED_ENTRY CMD=$EXPECTED_CMD" >> "$LOG_FILE"
    fi

    if [[ "$session_should_restart" == "yes" ]]; then
      echo "$(date '+%F %T') restarting tmux session: $SESSION_NAME" >> "$LOG_FILE"
      tmux kill-session -t "$SESSION_NAME" 2>/dev/null || true
      sleep 1
      session_present="no"
    else
      echo "$(date '+%F %T') tmux session already exists and is current: $SESSION_NAME" >> "$LOG_FILE"
      exit 0
    fi
  fi

  write_session_meta
  tmux new-session -d -s "$SESSION_NAME" "bash -lc 'cd \"$WORKDIR\" && export DAILY_STOCK_ANALYSIS_ENTRY=$EXPECTED_ENTRY && exec \"$PYTHON_BIN\" main.py >> \"$LOG_FILE\" 2>&1'"
  echo "$(date '+%F %T') started tmux session: $SESSION_NAME" >> "$LOG_FILE"
  log_tmux_session_details "yes"
  exit 0
fi

write_session_meta
export DAILY_STOCK_ANALYSIS_ENTRY="$EXPECTED_ENTRY"
exec "$PYTHON_BIN" main.py >> "$LOG_FILE" 2>&1
