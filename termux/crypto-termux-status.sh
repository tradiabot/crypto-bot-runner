#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
SESSION="${TMUX_SESSION:-crypto-termux}"
WATCHDOG_PID_FILE="${WATCHDOG_PID_FILE:-$HOME/.crypto-termux-watchdog.pid}"
WATCHDOG_LOG_FILE="${WATCHDOG_LOG_FILE:-$HOME/crypto-termux.log}"

printf 'Session: '
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "active"
else
  echo "inactive"
fi

if [[ -s "$WATCHDOG_PID_FILE" ]]; then
  pid="$(cat "$WATCHDOG_PID_FILE" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "Watchdog PID: $pid (active)"
  else
    echo "Watchdog PID file present but process inactive"
  fi
else
  echo "Watchdog PID: none"
fi

echo "Log: $WATCHDOG_LOG_FILE"
