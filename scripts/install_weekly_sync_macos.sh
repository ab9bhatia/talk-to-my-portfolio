#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-install}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PORTFOLIO_PYTHON:-$REPO_DIR/.venv/bin/python}"
LABEL="com.talktomyportfolio.weekly-sync"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="${PORTFOLIO_DATA_DIR:-$REPO_DIR/modules/portfolio/data}/logs"

if [[ "$ACTION" == "uninstall" ]]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Removed $LABEL"
  exit 0
fi

if [[ "$ACTION" != "install" ]]; then
  echo "Usage: $0 [install|uninstall]" >&2
  exit 2
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python not found at $PYTHON_BIN; set PORTFOLIO_PYTHON." >&2
  exit 1
fi

mkdir -p "$(dirname "$PLIST")" "$LOG_DIR"
cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON_BIN</string>
    <string>-m</string>
    <string>modules.portfolio.scripts.weekly_sync</string>
    <string>--mode</string>
    <string>auto</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO_DIR</string>
  <key>EnvironmentVariables</key>
  <dict><key>TZ</key><string>${PORTFOLIO_SYNC_TIMEZONE:-Asia/Kolkata}</string></dict>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Weekday</key><integer>6</integer><key>Hour</key><integer>18</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Weekday</key><integer>7</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer></dict>
  </array>
  <key>StandardOutPath</key><string>$LOG_DIR/weekly-sync.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/weekly-sync-error.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed $LABEL: Friday 18:30 and Saturday 09:00 in the macOS system timezone."
echo "Set the Mac timezone to ${PORTFOLIO_SYNC_TIMEZONE:-Asia/Kolkata} for the documented schedule."
