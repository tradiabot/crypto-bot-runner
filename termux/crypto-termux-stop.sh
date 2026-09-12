#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
SESSION="${TMUX_SESSION:-crypto-termux}"
WATCHDOG_PID_FILE="${WATCHDOG_PID_FILE:-$HOME/.crypto-termux-watchdog.pid}"
WATCHDOG_LOG_FILE="${WATCHDOG_LOG_FILE:-$HOME/crypto-termux.log}"

if [[ -s "$WATCHDOG_PID_FILE" ]]; then
  pid="$(cat "$WATCHDOG_PID_FILE" 2>/dev/null || true)"
  if [[ -n "$pid" ]]; then
    kill "$pid" 2>/dev/null || true
  fi
  rm -f "$WATCHDOG_PID_FILE"
fi

tmux kill-session -t "$SESSION" 2>/dev/null || true
echo "Crypto Termux detenido"
