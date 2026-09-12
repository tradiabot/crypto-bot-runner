#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
BOOT_DIR="${BOOT_DIR:-$HOME/.termux/boot}"
BIN_DIR="${BIN_DIR:-$HOME/bin}"
DEBIAN_NAME="${DEBIAN_NAME:-debian}"
DEBIAN_REPO_DIR="${DEBIAN_REPO_DIR:-/root/projects/crypto-bot}"
mkdir -p "$BOOT_DIR" "$BIN_DIR"

cat > "$BIN_DIR/crypto-bot" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
ACTION="\${1:-start}"
LOG_FILE="\${LOG_FILE:-\$HOME/crypto-termux.log}"
PID_FILE="\${PID_FILE:-\$HOME/.crypto-termux.pid}"
SESSION="\${TMUX_SESSION:-crypto-termux}"
start_bot() {
  termux-wake-lock >/dev/null 2>&1 || true
  nohup proot-distro login "$DEBIAN_NAME" --shared-tmp -- bash -lc 'cd "$DEBIAN_REPO_DIR" && exec ./crypto-live' >>"\$LOG_FILE" 2>&1 &
  printf '%s\n' "\$!" > "\$PID_FILE"
  echo "crypto-bot iniciado (PID \$!)"
}
stop_bot() {
  if [[ -s "\$PID_FILE" ]]; then
    pid="\$(cat "\$PID_FILE" 2>/dev/null || true)"
    [[ -n "\$pid" ]] && kill "\$pid" 2>/dev/null || true
    rm -f "\$PID_FILE"
  fi
  pkill -f "proot-distro login $DEBIAN_NAME --shared-tmp -- bash -lc cd $DEBIAN_REPO_DIR && exec ./crypto-live" 2>/dev/null || true
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
case "\$ACTION" in
  start) start_bot ;;
  stop) stop_bot ;;
  restart) stop_bot; sleep 1; start_bot ;;
  status) status_bot ;;
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
    echo "Uso: crypto-bot {start|stop|status|restart|boot}"
    exit 1
    ;;
esac
EOF
chmod +x "$BIN_DIR/crypto-bot"

echo "Comando instalado: $BIN_DIR/crypto-bot"
echo "Usa: crypto-bot start"
echo "Para autoarranque: crypto-bot boot"
echo "Recuerda instalar la app Termux:Boot y quitar la optimizacion de bateria para Termux."
