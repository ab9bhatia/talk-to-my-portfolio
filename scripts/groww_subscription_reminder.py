#!/usr/bin/env python3
"""Optional reminder before Groww Trade API subscription renews (email + macOS notification)."""

from __future__ import annotations

import argparse
import os
import smtplib
import subprocess
import sys
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")

REMINDER_DATE = date(2026, 6, 20)
SUBJECT = "Reminder: Review Groww Trading API subscription"
BODY = """This is a scheduled reminder to review your Groww Trading API subscription before any paid plan renews.

Actions:
1. Open https://groww.in/user/profile/trading-apis
2. Cancel or adjust auto-renew if you no longer need the API
3. Revoke keys at https://groww.in/trade-api/api-keys if unused

— portfolio/scripts/groww_subscription_reminder.py
"""


def send_via_smtp() -> bool:
    to_addr = os.getenv("GMAIL_REMINDER_TO", "").strip()
    from_addr = os.getenv("GMAIL_REMINDER_FROM", "").strip()
    app_password = os.getenv("GMAIL_REMINDER_APP_PASSWORD", "").strip()
    if not (to_addr and from_addr and app_password):
        return False

    msg = MIMEMultipart()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = SUBJECT
    msg.attach(MIMEText(BODY, "plain"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(from_addr, app_password)
        smtp.sendmail(from_addr, [to_addr], msg.as_string())
    return True


def notify_macos(title: str, message: str) -> None:
    script = f'display notification "{message}" with title "{title}"'
    subprocess.run(["osascript", "-e", script], check=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Send even if not REMINDER_DATE")
    args = parser.parse_args()

    today = date.today()
    if not args.force and today != REMINDER_DATE:
        print(f"Not reminder day ({REMINDER_DATE}); use --force to send anyway.")
        return 0

    if args.dry_run:
        print("Would send reminder:", SUBJECT)
        return 0

    sent = send_via_smtp()
    notify_macos("Groww API", "Review Trading API subscription")
    print("Email sent." if sent else "Email skipped (set GMAIL_REMINDER_* in .env).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
