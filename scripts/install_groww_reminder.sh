#!/usr/bin/env bash
# Install optional macOS launchd job for Groww subscription reminder.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PLIST_EXAMPLE="$REPO/scripts/com.portfolio.groww-reminder.plist.example"
PLIST_DST="$HOME/Library/LaunchAgents/com.portfolio.groww-reminder.plist"

if [[ ! -f "$PLIST_EXAMPLE" ]]; then
  echo "Missing $PLIST_EXAMPLE"
  exit 1
fi

sed -e "s|/path/to/portfolio|$REPO|g" "$PLIST_EXAMPLE" > "$PLIST_DST"
launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"
echo "Installed $PLIST_DST"
echo "Set GMAIL_REMINDER_TO, GMAIL_REMINDER_FROM, GMAIL_REMINDER_APP_PASSWORD in .env for email."
