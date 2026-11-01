#!/bin/bash
# Instala (ou reinstala) o agente launchd que roda scripts/update_daily.sh seg–sex às 19:00.
#   bash scripts/install_schedule.sh            instala/atualiza
#   bash scripts/install_schedule.sh --remove   remove

set -eu

LABEL="com.imea.daily-update"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true

if [ "${1:-}" = "--remove" ]; then
  rm -f "$TARGET"
  echo "Agente $LABEL removido."
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs/imea"
sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" -e "s|__HOME__|$HOME|g" \
  "$PROJECT_DIR/scripts/$LABEL.plist" > "$TARGET"
plutil -lint "$TARGET" >/dev/null
launchctl bootstrap "$DOMAIN" "$TARGET"
echo "Agente $LABEL instalado: seg–sex 19:00. Logs em ~/Library/Logs/imea/update.log"
