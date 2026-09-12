#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
PATH="$PREFIX/bin:$PATH"
export PREFIX PATH
DEBIAN_NAME="${DEBIAN_NAME:-debian}"
BOT_DIR="${BOT_DIR:-/root/projects/crypto-bot}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOST_BOT_DIR="${HOST_BOT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
LOG_FILE="${LOG_FILE:-$HOME/crypto-termux.log}"
OUTER_PID_FILE="${WATCHDOG_PID_FILE:-$HOME/.crypto-termux-watchdog.pid}"
OUTER_LOCK_FILE="${WATCHDOG_LOCK_FILE:-$HOME/.crypto-termux-watchdog.lock}"
RESTART_DELAY="${RESTART_DELAY:-5}"
ACTIVE_POLL_SECONDS="${ACTIVE_POLL_SECONDS:-15}"
HEARTBEAT_STALE_SECONDS="${TERMUX_HEARTBEAT_STALE_SECONDS:-180}"
STOP_FILE="$HOST_BOT_DIR/.runtime/crypto-stop.request"
TERMUX_KEEP_AWAKE="${TERMUX_KEEP_AWAKE:-YES}"
bot_pid=""

log_msg() {
  printf '%(%FT%TZ)T TERMUX watchdog: %s\n' -1 "$*" >>"$LOG_FILE" 2>/dev/null || true
}

pause() {
  local seconds="$1" deadline
  sleep "$seconds" 2>/dev/null && return 0
  log_msg "sleep no disponible; usando espera interna"
  deadline=$((EPOCHSECONDS + seconds))
  while (( EPOCHSECONDS < deadline )); do
    :
  done
}

ensure_wake_lock() {
  [[ "$TERMUX_KEEP_AWAKE" == "YES" ]] || return 0
  if termux-wake-lock >/dev/null 2>&1; then
    log_msg "wake-lock activo"
  else
    log_msg "ADVERTENCIA: termux-wake-lock no disponible; Android puede suspender el bot"
  fi
}

mkdir -p "$(dirname "$LOG_FILE")"
exec 9>"$OUTER_LOCK_FILE"
if ! flock -n 9; then
  log_msg "otra instancia ya esta activa"
  exit 0
fi
printf '%s\n' "$$" >"$OUTER_PID_FILE"
ensure_wake_lock

cleanup() {
  if [[ -n "$bot_pid" ]] && kill -0 "$bot_pid" 2>/dev/null; then
    kill "$bot_pid" 2>/dev/null || true
    wait "$bot_pid" 2>/dev/null || true
  fi
  if [[ -s "$OUTER_PID_FILE" ]]; then
    local recorded_pid=""
    IFS= read -r recorded_pid <"$OUTER_PID_FILE" 2>/dev/null || true
    if [[ "$recorded_pid" == "$$" ]]; then
      rm -f "$OUTER_PID_FILE"
    fi
  fi
  if [[ "$TERMUX_KEEP_AWAKE" == "YES" ]]; then
    termux-wake-unlock >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT
trap 'exit 0' HUP INT TERM

active_bot_pid() {
  local heartbeat="$HOST_BOT_DIR/.runtime/crypto-auto-heartbeat.json"
  local payload pid epoch now age
  [[ -s "$heartbeat" ]] || return 1
  payload=""
  IFS= read -r payload <"$heartbeat" || [[ -n "$payload" ]] || return 1
  [[ "$payload" =~ \"pid\"[[:space:]]*:[[:space:]]*([0-9]+) ]] || return 1
  pid="${BASH_REMATCH[1]}"
  [[ "$payload" =~ \"epoch\"[[:space:]]*:[[:space:]]*([0-9]+)(\.[0-9]+)? ]] || return 1
  epoch="${BASH_REMATCH[1]}"
  now="${EPOCHSECONDS:-0}"
  if [[ ! "$now" =~ ^[0-9]+$ ]] || (( now == 0 )); then
    printf -v now '%(%s)T' -1
  fi
  age=$((now-epoch))
  (( age >= 0 && age <= HEARTBEAT_STALE_SECONDS )) || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  printf '%s\n' "$pid"
}

stale_heartbeat_pid() {
  local heartbeat="$HOST_BOT_DIR/.runtime/crypto-auto-heartbeat.json"
  local payload pid epoch now age
  [[ -s "$heartbeat" ]] || return 1
  payload=""
  IFS= read -r payload <"$heartbeat" || [[ -n "$payload" ]] || return 1
  [[ "$payload" =~ \"pid\"[[:space:]]*:[[:space:]]*([0-9]+) ]] || return 1
  pid="${BASH_REMATCH[1]}"
  [[ "$payload" =~ \"epoch\"[[:space:]]*:[[:space:]]*([0-9]+)(\.[0-9]+)? ]] || return 1
  epoch="${BASH_REMATCH[1]}"
  now="${EPOCHSECONDS:-0}"
  [[ "$now" =~ ^[0-9]+$ ]] && (( now > 0 )) || printf -v now '%(%s)T' -1
  age=$((now-epoch))
  (( age > HEARTBEAT_STALE_SECONDS )) || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  printf '%s\n' "$pid"
}

recover_stale_heartbeat() {
  local stale_pid=""
  stale_pid="$(stale_heartbeat_pid || true)"
  [[ -n "$stale_pid" ]] || return 1
  log_msg "heartbeat vencido tras pausa; terminando AUTO PID=$stale_pid para recuperación limpia"
  kill -TERM "$stale_pid" 2>/dev/null || true
  pause 5
  kill -0 "$stale_pid" 2>/dev/null && kill -KILL "$stale_pid" 2>/dev/null || true
  rm -f "$HOST_BOT_DIR/.runtime/crypto-auto-heartbeat.json"
  return 0
}

start_bot() {
  local code started now last_tick
  if [[ "${DIRECT_BOT:-NO}" == "YES" ]]; then
    /bin/bash -lc "cd '$BOT_DIR' && exec ./crypto-live" &
  else
    proot-distro login "$DEBIAN_NAME" --shared-tmp -- /bin/bash -lc "cd '$BOT_DIR' && exec ./crypto-live" &
  fi
  bot_pid=$!
  started="${EPOCHSECONDS:-0}"
  last_tick="$started"
  while kill -0 "$bot_pid" 2>/dev/null; do
    now="${EPOCHSECONDS:-0}"
    if (( now > last_tick + ACTIVE_POLL_SECONDS * 3 )); then
      log_msg "reanudación detectada tras $((now-last_tick))s; verificando heartbeat"
      ensure_wake_lock
    fi
    last_tick="$now"
    if (( now > 0 && started > 0 && now-started >= HEARTBEAT_STALE_SECONDS )) && ! active_bot_pid >/dev/null; then
      log_msg "bot heartbeat vencido; terminando proceso para relanzarlo"
      kill "$bot_pid" 2>/dev/null || true
      set +e
      wait "$bot_pid"
      set -e
      bot_pid=""
      return 124
    fi
    pause "$ACTIVE_POLL_SECONDS"
  done
  set +e
  wait "$bot_pid"
  code=$?
  set -e
  bot_pid=""
  return "$code"
}

if [[ "${1:-}" == "--once" ]]; then
  start_bot
  exit $?
fi

while true; do
  if [[ -e "$STOP_FILE" ]]; then
    log_msg "parada solicitada"
    exit 0
  fi
  if active_bot_pid >/dev/null; then
    log_msg "monitoring existing bot heartbeat=active"
    while active_bot_pid >/dev/null; do
      if [[ -e "$STOP_FILE" ]]; then
        log_msg "parada solicitada"
        exit 0
      fi
      pause "$ACTIVE_POLL_SECONDS"
    done
    recover_stale_heartbeat || true
    log_msg "existing bot stopped; taking over"
    continue
  fi

  log_msg "launching bot"
  if start_bot >>"$LOG_FILE" 2>&1; then
    code=0
  else
    code=$?
  fi
  log_msg "bot exited code=$code; relaunching in ${RESTART_DELAY}s"
  pause "$RESTART_DELAY"
done
