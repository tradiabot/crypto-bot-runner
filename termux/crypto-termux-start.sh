#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
TMPDIR="${TERMUX_TMPDIR:-$PREFIX/tmp}"
TMUX_TMPDIR="$TMPDIR"
PATH="$PREFIX/bin:$PATH"
export PREFIX TMPDIR TMUX_TMPDIR PATH
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SESSION="${TMUX_SESSION:-crypto-termux}"
WATCHDOG_LOG_FILE="${WATCHDOG_LOG_FILE:-$HOME/crypto-termux.log}"
SWARM_LOG_FILE="$SCRIPT_DIR/../.runtime/crypto-swarm.log"
WATCHDOG_COMMAND="exec \"$SCRIPT_DIR/crypto-termux-watchdog.sh\""
if [[ -x /root/projects/crypto-bot/crypto-swarm ]]; then
  WATCHDOG_COMMAND="env BOT_DIR=/root/projects/crypto-bot HOST_BOT_DIR=/root/projects/crypto-bot DIRECT_BOT=YES \"$SCRIPT_DIR/crypto-termux-watchdog.sh\""
fi
LOG_COMMAND="tail -F \"$WATCHDOG_LOG_FILE\" \"$SWARM_LOG_FILE\""

mkdir -p "$(dirname "$WATCHDOG_LOG_FILE")" "$SCRIPT_DIR/../.runtime"
rm -f "$SCRIPT_DIR/../.runtime/crypto-stop.request"

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
  tmux new-session -d -s "$SESSION" -n watchdog "$WATCHDOG_COMMAND"
else
  if ! tmux list-windows -t "$SESSION" -F '#{window_name}' | grep -Fxq watchdog; then
    tmux new-window -d -t "$SESSION" -n watchdog "$WATCHDOG_COMMAND"
  fi
fi

if ! tmux list-windows -t "$SESSION" -F '#{window_name}' | grep -Fxq logs; then
  tmux new-window -d -t "$SESSION" -n logs "$LOG_COMMAND"
fi

echo "Watchdog Termux activo en tmux: $SESSION"
echo "Logs: $WATCHDOG_LOG_FILE y $SWARM_LOG_FILE"
if [[ -t 0 && -t 1 ]]; then
  tmux select-window -t "$SESSION:logs"
  tmux attach -t "$SESSION"
else
  echo "Adjunta con: tmux attach -t $SESSION"
fi
