#!/usr/bin/env bash
# Deja la bandeja (bandeja.py) corriendo sola en esta Mac: arranca con la sesión, procesa la cola de
# lecciones cada BANDEJA_INTERVAL segundos y se reinicia si se cae. Hace falta una sola máquina del
# equipo con Hermes y la bóveda; si está apagada, las lecciones esperan en la cola del server.
#
#   agent/hermes/bandeja-servicio.sh            instala o actualiza
#   agent/hermes/bandeja-servicio.sh --quitar   la saca
#
# Log: ~/.claude/build101/bandeja.log
set -euo pipefail

LABEL=com.build101.bandeja
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
HERE="$(cd "$(dirname "$0")" && pwd)"
# El clon de siempre, no un worktree que después se borra
REPO="${BUILD101_HOME:-$(cd "$HERE/../.." && pwd)}"
VAULT="${VAULT:-$(dirname "$REPO")/build101-vault}"
LOG="$HOME/.claude/build101/bandeja.log"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [ "${1:-}" = "--quitar" ]; then
  rm -f "$PLIST"
  echo "Bandeja sacada."
  exit 0
fi

[ -n "${BUILD101_KEY:-}" ] || { echo "Falta BUILD101_KEY"; exit 1; }
command -v hermes >/dev/null || { echo "Falta hermes: corré agent/hermes/setup.sh"; exit 1; }
[ -f "$VAULT/reglas/AGENTS.md" ] || { echo "No encuentro la bóveda en $VAULT"; exit 1; }
PY="$(command -v python3)"
mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"

# El plist guarda la key: queda solo legible por vos
umask 077
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>-u</string><string>$REPO/agent/hermes/bandeja.py</string></array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>BUILD101_KEY</key><string>$BUILD101_KEY</string>
    <key>BUILD101_URL</key><string>${BUILD101_URL:-https://54-89-124-87.sslip.io/api/hooks/claude-code}</string>
    <key>VAULT</key><string>$VAULT</string>
    <key>BANDEJA_INTERVAL</key><string>${BANDEJA_INTERVAL:-120}</string>
    <key>PATH</key><string>$(dirname "$(command -v hermes)"):/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
    <key>HOME</key><string>$HOME</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>60</integer>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Bandeja corriendo desde $REPO sobre $VAULT. Log: $LOG"
echo "Para sacarla: agent/hermes/bandeja-servicio.sh --quitar"
