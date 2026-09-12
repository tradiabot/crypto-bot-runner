#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="${LOG_FILE:-$HOME/crypto-termux.log}"
SESSION="${TMUX_SESSION:-crypto-termux}"

mkdir -p "$(dirname "$LOG_FILE")"
{
  echo "$(date -u +%FT%TZ) BOOT: starting crypto bot"
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "$(date -u +%FT%TZ) BOOT: tmux session already active"
    exit 0
  fi
  exec "$SCRIPT_DIR/crypto-termux-start.sh"
} >>"$LOG_FILE" 2>&1
