#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

DEBIAN_NAME="${DEBIAN_NAME:-debian}"
DEBIAN_REPO_DIR="${DEBIAN_REPO_DIR:-/root/projects/crypto-bot}"
TERMUX_BOOT_DIR="${TERMUX_BOOT_DIR:-$HOME/.termux/boot}"
TERMUX_BIN_DIR="${TERMUX_BIN_DIR:-$HOME/bin}"
PID_FILE="${PID_FILE:-$HOME/.crypto-bot.pid}"
LOG_FILE="${LOG_FILE:-$HOME/crypto-termux.log}"

mkdir -p "$TERMUX_BOOT_DIR" "$TERMUX_BIN_DIR"

cat > "$TERMUX_BIN_DIR/crypto-bot" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

DEBIAN_NAME="${DEBIAN_NAME}"
DEBIAN_REPO_DIR="${DEBIAN_REPO_DIR}"
PID_FILE="${PID_FILE}"
LOG_FILE="${LOG_FILE}"

start_bot() {
  termux-wake-lock >/dev/null 2>&1 || true
  if [[ -s "\$PID_FILE" ]]; then
    pid="\$(cat "\$PID_FILE" 2>/dev/null || true)"
    if [[ -n "\$pid" ]] && kill -0 "\$pid" 2>/dev/null; then
      echo "crypto-bot ya está activo (PID \$pid)"
      return 0
    fi
  fi
  nohup proot-distro login "\$DEBIAN_NAME" --shared-tmp -- bash -lc "cd '\$DEBIAN_REPO_DIR' && exec ./crypto-live" >>"\$LOG_FILE" 2>&1 &
  printf '%s\n' "\$!" > "\$PID_FILE"
  echo "crypto-bot iniciado (PID \$!)"
}

stop_bot() {
  if [[ -s "\$PID_FILE" ]]; then
    pid="\$(cat "\$PID_FILE" 2>/dev/null || true)"
    if [[ -n "\$pid" ]]; then
      kill "\$pid" 2>/dev/null || true
    fi
    rm -f "\$PID_FILE"
  fi
  pkill -f "proot-distro login \$DEBIAN_NAME --shared-tmp -- bash -lc" 2>/dev/null || true
  echo "crypto-bot detenido"
}

status_bot() {
  if [[ -s "\$PID_FILE" ]] && kill -0 "\$(cat "\$PID_FILE" 2>/dev/null || true)" 2>/dev/null; then
    echo "crypto-bot: active (PID \$(cat "\$PID_FILE"))"
  else
    echo "crypto-bot: inactive"
  fi
  echo "log: \$LOG_FILE"
}

restart_bot() {
  stop_bot
  sleep 1
  start_bot
}

repair_bot() {
  if [[ -s "\$PID_FILE" ]]; then
    pid="\$(cat "\$PID_FILE" 2>/dev/null || true)"
    if [[ -n "\$pid" ]] && ! kill -0 "\$pid" 2>/dev/null; then
      rm -f "\$PID_FILE"
      echo "crypto-bot: pid file stale, cleaned"
    fi
  fi
  status_bot
}

case "\${1:-start}" in
  start) start_bot ;;
  stop) stop_bot ;;
  status) status_bot ;;
  restart) restart_bot ;;
  repair) repair_bot ;;
  logs) exec tail -f "\$LOG_FILE" ;;
  boot)
    mkdir -p "\$HOME/.termux/boot"
    cat > "\$HOME/.termux/boot/crypto-bot.sh" <<'BOOT'
#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
exec crypto-bot start
BOOT
    chmod +x "\$HOME/.termux/boot/crypto-bot.sh"
    echo "Termux:Boot instalado en ~/.termux/boot/crypto-bot.sh"
    ;;
  *)
    echo "Uso: crypto-bot {start|stop|status|restart|logs|boot}"
    exit 1
    ;;
esac
EOF

chmod +x "$TERMUX_BIN_DIR/crypto-bot"

cat > "$TERMUX_BOOT_DIR/crypto-bot.sh" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
exec crypto-bot start
EOF

chmod +x "$TERMUX_BOOT_DIR/crypto-bot.sh"

echo "Instalado: $TERMUX_BIN_DIR/crypto-bot"
echo "Boot hook: $TERMUX_BOOT_DIR/crypto-bot.sh"
echo "Usa: crypto-bot start | status | stop | restart | repair | logs | boot"
echo "Recuerda instalar la app Termux:Boot y quitar la optimizacion de bateria para Termux."
