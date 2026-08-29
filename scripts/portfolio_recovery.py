#!/usr/bin/env python3
"""Offline backup, validation, restore, and diagnostics commands."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from modules.portfolio.services.backup_restore import (
    create_encrypted_backup,
    restore_backup,
    validate_backup,
)
from modules.portfolio.services.diagnostics import collect_diagnostics


def _password(env_name: str) -> str:
    value = os.getenv(env_name, "")
    if not value:
        raise SystemExit(f"Set {env_name}; passwords are not accepted on the command line.")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="TalkToMyPortfolio recovery utility")
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("path", type=Path)
    backup.add_argument("--password-env", default="PORTFOLIO_BACKUP_PASSWORD")
    validate = commands.add_parser("validate")
    validate.add_argument("path", type=Path)
    validate.add_argument("--password-env", default="PORTFOLIO_BACKUP_PASSWORD")
    restore = commands.add_parser("restore")
    restore.add_argument("path", type=Path)
    restore.add_argument("--password-env", default="PORTFOLIO_BACKUP_PASSWORD")
    restore.add_argument("--select", action="append")
    restore.add_argument("--apply", action="store_true")
    restore.add_argument("--confirm", choices=["RESTORE"])
    commands.add_parser("diagnostics")
    args = parser.parse_args()

    if args.command == "backup":
        result = create_encrypted_backup(args.path, password=_password(args.password_env))
    elif args.command == "validate":
        result = validate_backup(args.path, password=_password(args.password_env))
    elif args.command == "restore":
        if args.apply and args.confirm != "RESTORE":
            raise SystemExit("Use --confirm RESTORE with --apply. Dry-run is the default.")
        result = restore_backup(
            args.path,
            password=_password(args.password_env),
            selected=args.select,
            dry_run=not args.apply,
        )
    else:
        result = collect_diagnostics()
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
