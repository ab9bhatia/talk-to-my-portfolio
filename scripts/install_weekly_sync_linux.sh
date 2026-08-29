#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-install}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PORTFOLIO_PYTHON:-$REPO_DIR/.venv/bin/python}"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE="$UNIT_DIR/talktomyportfolio-weekly-sync.service"
TIMER="$UNIT_DIR/talktomyportfolio-weekly-sync.timer"
SYNC_TZ="${PORTFOLIO_SYNC_TIMEZONE:-Asia/Kolkata}"

if [[ "$ACTION" == "uninstall" ]]; then
  systemctl --user disable --now talktomyportfolio-weekly-sync.timer 2>/dev/null || true
  rm -f "$SERVICE" "$TIMER"
  systemctl --user daemon-reload
  echo "Removed talktomyportfolio-weekly-sync"
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

mkdir -p "$UNIT_DIR"
cat >"$SERVICE" <<EOF
[Unit]
Description=TalkToMyPortfolio weekly sync

[Service]
Type=oneshot
WorkingDirectory=$REPO_DIR
Environment=TZ=$SYNC_TZ
ExecStart=$PYTHON_BIN -m modules.portfolio.scripts.weekly_sync --mode auto
EOF

cat >"$TIMER" <<EOF
[Unit]
Description=Run TalkToMyPortfolio after Indian market close

[Timer]
OnCalendar=Fri *-*-* 18:30:00 $SYNC_TZ
OnCalendar=Sat *-*-* 09:00:00 $SYNC_TZ
Persistent=true
RandomizedDelaySec=5m
Unit=talktomyportfolio-weekly-sync.service

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now talktomyportfolio-weekly-sync.timer
systemctl --user list-timers talktomyportfolio-weekly-sync.timer
