"""OS-backed secret storage and verified migration from legacy SQLite columns."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import sqlite3
import subprocess
from pathlib import Path
from typing import Protocol

from modules.portfolio.paths import DATA_DIR


SERVICE_NAME = "TalkToMyPortfolio"
MIGRATED_SENTINEL = "[MIGRATED_TO_SECRET_STORE]"


class SecretBackend(Protocol):
    name: str

    def set(self, reference: str, value: str) -> None: ...
    def get(self, reference: str) -> str | None: ...
    def delete(self, reference: str) -> None: ...


class MacOSKeychainBackend:
    name = "macos-keychain"

    def set(self, reference: str, value: str) -> None:
        subprocess.run(
            ["security", "add-generic-password", "-U", "-s", SERVICE_NAME, "-a", reference, "-w", value],
            check=True, capture_output=True, text=True,
        )

    def get(self, reference: str) -> str | None:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", SERVICE_NAME, "-a", reference, "-w"],
            check=False, capture_output=True, text=True,
        )
        return result.stdout.rstrip("\n") if result.returncode == 0 else None

    def delete(self, reference: str) -> None:
        subprocess.run(
            ["security", "delete-generic-password", "-s", SERVICE_NAME, "-a", reference],
            check=False, capture_output=True, text=True,
        )


class KeyringBackend:
    name = "os-keyring"

    def __init__(self) -> None:
        import keyring  # type: ignore[import-not-found]

        self._keyring = keyring

    def set(self, reference: str, value: str) -> None:
        self._keyring.set_password(SERVICE_NAME, reference, value)

    def get(self, reference: str) -> str | None:
        return self._keyring.get_password(SERVICE_NAME, reference)

    def delete(self, reference: str) -> None:
        try:
            self._keyring.delete_password(SERVICE_NAME, reference)
        except Exception:
            pass


class EncryptedFileBackend:
    """AES-GCM fallback; the passphrase must come from outside the encrypted file."""

    name = "encrypted-local-fallback"

    def __init__(self, passphrase: str, path: Path | None = None) -> None:
        if len(passphrase) < 16:
            raise RuntimeError("Encrypted fallback requires a passphrase of at least 16 characters.")
        self.passphrase = passphrase
        self.path = path or DATA_DIR / "secrets.enc"

    def _key(self, salt: bytes) -> bytes:
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes

        return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600_000).derive(self.passphrase.encode())

    def _read(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        salt = base64.b64decode(payload["salt"])
        nonce = base64.b64decode(payload["nonce"])
        clear = AESGCM(self._key(salt)).decrypt(nonce, base64.b64decode(payload["ciphertext"]), SERVICE_NAME.encode())
        return json.loads(clear)

    def _write(self, values: dict[str, str]) -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        self.path.parent.mkdir(parents=True, exist_ok=True)
        salt, nonce = os.urandom(16), os.urandom(12)
        ciphertext = AESGCM(self._key(salt)).encrypt(nonce, json.dumps(values, sort_keys=True).encode(), SERVICE_NAME.encode())
        body = json.dumps({
            "version": 1,
            "salt": base64.b64encode(salt).decode(),
            "nonce": base64.b64encode(nonce).decode(),
            "ciphertext": base64.b64encode(ciphertext).decode(),
        })
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        temporary.write_text(body, encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)

    def set(self, reference: str, value: str) -> None:
        values = self._read()
        values[reference] = value
        self._write(values)

    def get(self, reference: str) -> str | None:
        return self._read().get(reference)

    def delete(self, reference: str) -> None:
        values = self._read()
        values.pop(reference, None)
        self._write(values)


def get_secret_backend() -> SecretBackend:
    if platform.system() == "Darwin" and Path("/usr/bin/security").exists():
        return MacOSKeychainBackend()
    try:
        return KeyringBackend()
    except Exception:
        passphrase = os.getenv("PORTFOLIO_SECRET_FALLBACK_PASSPHRASE", "")
        if passphrase:
            return EncryptedFileBackend(passphrase)
    raise RuntimeError("No OS secret store is available; configure an encrypted fallback passphrase.")


def _ensure_columns(conn: sqlite3.Connection, table: str) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if "secret_ref" not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN secret_ref TEXT")
    if "secret_backend" not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN secret_backend TEXT")
    if "migrated_at" not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN migrated_at TEXT")


def migration_preview(
    db_path: Path, *, table: str, account_column: str = "account_id", secret_column: str = "access_token",
) -> dict:
    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        secret_ref = "secret_ref" if "secret_ref" in columns else "NULL"
        rows = conn.execute(
            f"SELECT {account_column}, {secret_column}, {secret_ref} FROM {table}"
        ).fetchall()
    candidates = [row for row in rows if row[1] and row[1] != MIGRATED_SENTINEL and not row[2]]
    return {
        "database": db_path.name,
        "candidate_count": len(candidates),
        "accounts": [hashlib.sha256(str(row[0]).encode()).hexdigest()[:12] for row in candidates],
        "contains_secret_values": False,
        "requires_confirmation": True,
    }


def migrate_plaintext_secrets(
    db_path: Path, *, table: str, namespace: str, confirmed: bool,
    backend: SecretBackend | None = None, account_column: str = "account_id",
    secret_column: str = "access_token", now: str = "",
) -> dict:
    if not confirmed:
        raise ValueError("Explicit confirmation is required.")
    backend = backend or get_secret_backend()
    written: list[str] = []
    with sqlite3.connect(db_path) as conn:
        _ensure_columns(conn, table)
        conn.commit()
        rows = conn.execute(
            f"SELECT {account_column}, {secret_column} FROM {table} WHERE secret_ref IS NULL"
        ).fetchall()
        candidates = [row for row in rows if row[1] and row[1] != MIGRATED_SENTINEL]
        try:
            for account_id, secret in candidates:
                reference = f"{namespace}:{account_id}"
                backend.set(reference, secret)
                written.append(reference)
                if backend.get(reference) != secret:
                    raise RuntimeError("Secret-store verification failed; plaintext was retained.")
            conn.execute("BEGIN IMMEDIATE")
            for (account_id, _secret), reference in zip(candidates, written, strict=True):
                conn.execute(
                    f"UPDATE {table} SET {secret_column} = ?, secret_ref = ?, secret_backend = ?, migrated_at = ? WHERE {account_column} = ?",
                    (MIGRATED_SENTINEL, reference, backend.name, now, account_id),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            for reference in written:
                backend.delete(reference)
            raise
    os.chmod(db_path, 0o600)
    return {"migrated_count": len(written), "backend": backend.name, "verified": True}


def rollback_secret_migration(
    db_path: Path, *, table: str, confirmed: bool, backend: SecretBackend | None = None,
    account_column: str = "account_id", secret_column: str = "access_token",
) -> dict:
    if not confirmed:
        raise ValueError("Explicit confirmation is required.")
    backend = backend or get_secret_backend()
    restored: list[str] = []
    with sqlite3.connect(db_path) as conn:
        _ensure_columns(conn, table)
        conn.commit()
        rows = conn.execute(f"SELECT {account_column}, secret_ref FROM {table} WHERE secret_ref IS NOT NULL").fetchall()
        values = []
        for account_id, reference in rows:
            secret = backend.get(reference)
            if secret is None:
                raise RuntimeError("Rollback verification failed; secret store entry is unavailable.")
            values.append((account_id, reference, secret))
        conn.execute("BEGIN IMMEDIATE")
        for account_id, reference, secret in values:
            conn.execute(
                f"UPDATE {table} SET {secret_column} = ?, secret_ref = NULL, secret_backend = NULL, migrated_at = NULL WHERE {account_column} = ?",
                (secret, account_id),
            )
            restored.append(reference)
        conn.commit()
    for reference in restored:
        backend.delete(reference)
    return {"restored_count": len(restored), "verified": True}
