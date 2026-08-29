# Security, backup, recovery, and release operations

TalkToMyPortfolio remains a local, single-user application. Bind to `127.0.0.1` by default. A public-internet deployment is unsupported without a TLS reverse proxy, strong identity provider, and a separate security review.

## Secure local and LAN setup

For localhost, keep `APP_HOST=127.0.0.1`. For a trusted LAN, set a strong Basic Auth password or a random bearer token, then rely on the built-in origin/CSRF check and rate limits:

```text
PORTFOLIO_HTTP_USER=local-user
PORTFOLIO_HTTP_PASSWORD=<long-random-password>
PORTFOLIO_BEARER_TOKEN=<long-random-token-for-api-clients>
TRADING_ENABLED=false
```

Basic Auth is convenient but the browser retains credentials for its session and it does not provide logout, per-device access, or TLS. Prefer a bearer token for non-browser LAN clients and HTTPS at the reverse proxy. State-changing Basic Auth calls must include same-origin `Origin`/`Referer` or the `X-Portfolio-CSRF` value from `GET /api/portfolio/security/csrf`.

Security headers include CSP, frame denial, MIME sniffing denial, restrictive referrer/permissions policy, and no-store caching. Login, sync, upload, and LLM endpoints have local in-memory rate limits. Uploads enforce size, basename, extension, MIME type, and image signatures where applicable.

## Move tokens out of plaintext SQLite

Migration is never automatic. The target is macOS Keychain, Windows Credential Manager/Linux Secret Service through `keyring`, or an AES-GCM local fallback when `PORTFOLIO_SECRET_FALLBACK_PASSPHRASE` is set outside the encrypted file.

1. Preview `GET /api/portfolio/security/secrets/migration-preview?store=zerodha` and repeat for `groww`.
2. Back up first.
3. Send `POST /api/portfolio/security/secrets/migrate` with `{"store":"zerodha","confirmed":true}`.
4. The app writes, reads back, and compares every secret before replacing plaintext with a non-secret sentinel. A failure retains plaintext and removes partial secret-store entries.
5. Use `/rollback` with explicit confirmation if the new backend must be reversed.
6. Revoke local token material with `DELETE /api/portfolio/security/secrets/{store}/{account_id}` and revoke the session at the broker after a lost-machine or credential incident.

Do not remove the legacy database manually. The application retains metadata and only clears plaintext after verification.

## Encrypted backup

Passwords are read from an environment variable, not command-line history:

```bash
export PORTFOLIO_BACKUP_PASSWORD='use-a-long-unique-password'
PYTHONPATH=. .venv/bin/python scripts/portfolio_recovery.py backup "$HOME/TTMP-backup.ttmpbackup"
PYTHONPATH=. .venv/bin/python scripts/portfolio_recovery.py validate "$HOME/TTMP-backup.ttmpbackup"
```

Backups use AES-GCM with a PBKDF2-derived key. The encrypted archive contains a manifest, SHA-256 checksums, schema/integrity metadata, redacted account configuration, non-secret SQLite stores, and weekly digests. `.env`, token databases, secret-store files, and raw OS credentials are excluded.

## Restore and disaster-recovery test

Dry-run is mandatory first and cannot change active data:

```bash
PYTHONPATH=. .venv/bin/python scripts/portfolio_recovery.py restore "$HOME/TTMP-backup.ttmpbackup"
```

Stop the app before an applied restore. The utility validates encryption and checksums, extracts into a temporary sibling directory, optionally restores selected `data/...` entries, saves pre-restore copies, and only then atomically replaces active files:

```bash
PYTHONPATH=. .venv/bin/python scripts/portfolio_recovery.py restore "$HOME/TTMP-backup.ttmpbackup" --apply --confirm RESTORE
```

Quarterly recovery drill: create a backup, validate it, run dry restore, copy the repository to a disposable directory with a fresh `PORTFOLIO_DATA_DIR`, apply the restore there, start the app, open System Health, and verify SQLite integrity plus Dashboard/Growth/Action Center totals. Never test an applied restore against the only active copy.

## Upgrade and rollback

1. Stop the app and scheduler.
2. Create and validate an encrypted backup.
3. Pull reviewed code, recreate/install the pinned environment, and run the complete test gate.
4. Start once. Every SQLite store receives formal schema metadata; real upgrades create a `.pre-migrate-...bak`, run transactionally, perform `integrity_check`, and refuse a newer unsupported schema.
5. Check `/portfolio/system-health` and perform the user-journey smoke test.

On failure, stop the app. Prefer the encrypted restore path. For a single migration, a `.pre-migrate-...bak` can be restored manually while stopped. Roll code and data back together; do not run older code against a newer schema.

## Incident playbook

- Lost machine: revoke every broker/LLM token at the provider, rotate LAN credentials, invalidate bearer tokens, restore only onto a trusted encrypted device, and inspect broker activity.
- Corrupt database: stop writers, preserve the corrupt file, validate the latest encrypted backup, dry-run restore, then selectively restore the affected `data/<name>.db`.
- Scheduler failure: run `PYTHONPATH=. .venv/bin/python scripts/portfolio_recovery.py diagnostics`, inspect System Health, then use the platform installer diagnostics in the weekly-sync operations guide.
- Support: download the redacted support bundle. It contains no raw holdings by default; `include_raw_holdings=true` is an explicit disclosure and still redacts account IDs/secrets.

## Privacy checklist

- Yahoo/market providers receive symbols only when symbol sharing is enabled; no account or tax profile is included.
- External LLM context defaults to removing account IDs, owners, lots, tax profiles, tax notes, and proceeds. Review `/api/portfolio/security/llm-context-preview`; it is the exact context serialization used for transmission.
- Web research, outbound notifications, sensitive LLM context, and raw support holdings are default-deny or explicit opt-in controls.
- Provider health records store provider, operation, latency, status, and error class only—never prompts or responses.

## Release protection

Require the `test` check on `main`, up-to-date branches, review approval, and no administrator bypass. CI pins dependencies and runs dependency audit, secret scan, Bandit, Ruff, compilation, JavaScript/scheduler syntax, the deterministic suite, and coverage reporting. Sign release tags (`git tag -s`) and attach only source/release notes—never `.env`, `data/`, backups, support bundles, or generated workbooks.
