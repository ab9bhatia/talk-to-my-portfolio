"""Password-encrypted local backups with manifest checksums and staged restore."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from modules.portfolio.db.schema_migrations import database_status
from modules.portfolio.paths import DATA_DIR, MODULE_DIR


MAGIC = b"TTMP-BACKUP-V1\n"
EXCLUDED_NAMES = {"tokens.db", "groww_tokens.db", "secrets.enc", ".env"}


def _key(password: str, salt: bytes) -> bytes:
    if len(password) < 12:
        raise ValueError("Backup password must be at least 12 characters.")
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600_000).derive(password.encode())


def _safe_config() -> bytes | None:
    path = MODULE_DIR / "accounts.json"
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    secret_keys = {"api_key", "api_secret", "access_token", "password", "totp_token", "totp_secret"}

    def redact(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: ("[EXCLUDED]" if key.lower() in secret_keys else redact(item)) for key, item in value.items()}
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value

    return json.dumps(redact(payload), indent=2, sort_keys=True).encode()


def _backup_files() -> list[Path]:
    files = [path for path in sorted(DATA_DIR.glob("*.db")) if path.name not in EXCLUDED_NAMES]
    digest_dir = DATA_DIR / "weekly-digests"
    if digest_dir.is_dir():
        files.extend(path for path in sorted(digest_dir.rglob("*")) if path.is_file())
    return files


def _sqlite_snapshot(path: Path) -> bytes:
    """Capture a consistent database including committed WAL content."""
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as source:
        with sqlite3.connect(":memory:") as target:
            source.backup(target)
            row = target.execute("PRAGMA integrity_check").fetchone()
            if not row or row[0] != "ok":
                raise ValueError(f"Cannot back up corrupt database: {path.name}")
            return target.serialize()


def _archive_bytes() -> tuple[bytes, dict[str, Any]]:
    buffer = io.BytesIO()
    entries = []
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in _backup_files():
            relative = path.relative_to(DATA_DIR).as_posix()
            body = _sqlite_snapshot(path) if path.suffix == ".db" else path.read_bytes()
            arcname = f"data/{relative}"
            archive.writestr(arcname, body)
            entries.append({"path": arcname, "sha256": hashlib.sha256(body).hexdigest(), "size": len(body)})
        config = _safe_config()
        if config is not None:
            archive.writestr("config/accounts.redacted.json", config)
            entries.append({"path": "config/accounts.redacted.json", "sha256": hashlib.sha256(config).hexdigest(), "size": len(config)})
        manifest = {
            "format_version": 1,
            "created_at": time.time(),
            "contains_raw_secrets": False,
            "databases": database_status(),
            "entries": entries,
        }
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
    return buffer.getvalue(), manifest


def _encrypt(clear: bytes, password: str) -> bytes:
    salt, nonce = os.urandom(16), os.urandom(12)
    cipher = AESGCM(_key(password, salt)).encrypt(nonce, clear, MAGIC)
    envelope = json.dumps({
        "salt": base64.b64encode(salt).decode(),
        "nonce": base64.b64encode(nonce).decode(),
        "ciphertext": base64.b64encode(cipher).decode(),
    }, separators=(",", ":")).encode()
    return MAGIC + envelope


def _decrypt(body: bytes, password: str) -> bytes:
    if not body.startswith(MAGIC):
        raise ValueError("Unsupported backup format.")
    envelope = json.loads(body[len(MAGIC):])
    salt, nonce = base64.b64decode(envelope["salt"]), base64.b64decode(envelope["nonce"])
    return AESGCM(_key(password, salt)).decrypt(nonce, base64.b64decode(envelope["ciphertext"]), MAGIC)


def create_encrypted_backup(output: Path, *, password: str) -> dict[str, Any]:
    clear, manifest = _archive_bytes()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(_encrypt(clear, password))
    output.chmod(0o600)
    return {"path": str(output), "manifest": manifest, "encrypted": True}


def validate_backup(path: Path, *, password: str) -> dict[str, Any]:
    clear = _decrypt(path.read_bytes(), password)
    with zipfile.ZipFile(io.BytesIO(clear)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        for entry in manifest["entries"]:
            body = archive.read(entry["path"])
            if hashlib.sha256(body).hexdigest() != entry["sha256"]:
                raise ValueError(f"Backup checksum mismatch: {entry['path']}")
    return manifest


def restore_backup(
    path: Path, *, password: str, selected: list[str] | None = None, dry_run: bool = True,
) -> dict[str, Any]:
    manifest = validate_backup(path, password=password)
    clear = _decrypt(path.read_bytes(), password)
    selected_set = set(selected or [])
    entries = [
        entry for entry in manifest["entries"]
        if entry["path"].startswith("data/") and (not selected_set or entry["path"] in selected_set)
    ]
    if dry_run:
        return {"valid": True, "dry_run": True, "restorable": [entry["path"] for entry in entries], "active_data_changed": False}
    with tempfile.TemporaryDirectory(prefix="ttmp-restore-", dir=DATA_DIR.parent) as temp_name:
        stage = Path(temp_name)
        with zipfile.ZipFile(io.BytesIO(clear)) as archive:
            for entry in entries:
                relative = Path(entry["path"]).relative_to("data")
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("Unsafe backup path.")
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(entry["path"]))
        for entry in entries:
            staged = stage / Path(entry["path"]).relative_to("data")
            if staged.suffix == ".db":
                with sqlite3.connect(staged) as conn:
                    row = conn.execute("PRAGMA integrity_check").fetchone()
                if not row or row[0] != "ok":
                    raise ValueError(f"Staged database failed integrity check: {staged.name}")
        for entry in entries:
            relative = Path(entry["path"]).relative_to("data")
            source, destination = stage / relative, DATA_DIR / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                shutil.copy2(destination, destination.with_name(f"{destination.name}.pre-restore-{int(time.time())}.bak"))
            os.replace(source, destination)
            destination.chmod(0o600)
    return {"valid": True, "dry_run": False, "restored": [entry["path"] for entry in entries], "active_data_changed": True}
