from __future__ import annotations

import asyncio
import base64
import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.datastructures import Headers, UploadFile

from main import app
from modules.portfolio.db.schema_migrations import UnsupportedSchemaError, ensure_database
from modules.portfolio.services import backup_restore
from modules.portfolio.services.diagnostics import build_support_bundle
from modules.portfolio.services.orders import trading_enabled
from modules.portfolio.services.portfolio_agent import _chat_messages, external_context_preview
from modules.portfolio.services.secret_storage import (
    MIGRATED_SENTINEL,
    migrate_plaintext_secrets,
    migration_preview,
)
from shared.config import APP_ROOT_PATH
from shared.security_redaction import redact_text
from shared.web.uploads import read_upload_bounded, validate_upload_name


class FakeSecretBackend:
    name = "fake-os-store"

    def __init__(self, *, corrupt=False):
        self.values = {}
        self.corrupt = corrupt

    def set(self, reference, value):
        self.values[reference] = f"broken-{value}" if self.corrupt else value

    def get(self, reference):
        return self.values.get(reference)

    def delete(self, reference):
        self.values.pop(reference, None)


def _legacy_token_db(path: Path, token="valid-token"):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE tokens (account_id TEXT PRIMARY KEY, access_token TEXT NOT NULL)")
        conn.execute("INSERT INTO tokens VALUES ('private-account-1', ?)", (token,))


def test_secret_migration_preserves_token_and_removes_plaintext_only_after_verification(tmp_path):
    path = tmp_path / "tokens.db"
    _legacy_token_db(path)
    backend = FakeSecretBackend()
    preview = migration_preview(path, table="tokens")
    assert preview["candidate_count"] == 1
    assert "valid-token" not in json.dumps(preview)
    result = migrate_plaintext_secrets(
        path, table="tokens", namespace="zerodha", confirmed=True, backend=backend,
        now="2026-08-29T00:00:00Z",
    )
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT access_token, secret_ref FROM tokens").fetchone()
    assert result["verified"] is True
    assert row[0] == MIGRATED_SENTINEL
    assert backend.get(row[1]) == "valid-token"


def test_failed_secret_migration_rolls_back_safely(tmp_path):
    path = tmp_path / "tokens.db"
    _legacy_token_db(path)
    backend = FakeSecretBackend(corrupt=True)
    with pytest.raises(RuntimeError, match="verification failed"):
        migrate_plaintext_secrets(
            path, table="tokens", namespace="zerodha", confirmed=True, backend=backend
        )
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT access_token FROM tokens").fetchone()[0] == "valid-token"
    assert backend.values == {}


def test_state_changing_endpoint_rejects_missing_csrf_when_basic_auth_enabled(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_HTTP_USER", "local-user")
    monkeypatch.setenv("PORTFOLIO_HTTP_PASSWORD", "strong-password")
    auth = base64.b64encode(b"local-user:strong-password").decode()
    response = TestClient(app).post(
        f"{APP_ROOT_PATH}/api/portfolio/alerts/evaluate",
        headers={"Authorization": f"Basic {auth}"},
        json={"events": []},
    )
    assert response.status_code == 403


def test_upload_blocks_unsupported_oversized_and_path_traversal():
    with pytest.raises(HTTPException) as traversal:
        validate_upload_name("../holdings.csv", allowed_extensions={".csv"})
    assert traversal.value.status_code == 400
    unsupported = UploadFile(
        io.BytesIO(b"data"), filename="holdings.exe",
        headers=Headers({"content-type": "application/octet-stream"}),
    )
    with pytest.raises(HTTPException) as file_type:
        asyncio.run(read_upload_bounded(unsupported, allowed_extensions={".csv"}))
    assert file_type.value.status_code == 415
    oversized = UploadFile(
        io.BytesIO(b"1234"), filename="holdings.csv",
        headers=Headers({"content-type": "text/csv"}),
    )
    with pytest.raises(HTTPException) as too_large:
        asyncio.run(read_upload_bounded(oversized, max_bytes=2, allowed_extensions={".csv"}))
    assert too_large.value.status_code == 413


def test_logs_redact_secrets_and_account_identifiers():
    text = redact_text(
        "account=private-account-1 api_key=topsecret password=hunter2",
        account_ids=["private-account-1"],
    )
    assert "private-account-1" not in text
    assert "topsecret" not in text
    assert "hunter2" not in text
    assert "acct-" in text


def test_backup_checksum_detects_corruption(tmp_path):
    path = tmp_path / "portfolio.ttmpbackup"
    backup_restore.create_encrypted_backup(path, password="correct horse battery")
    clear = backup_restore._decrypt(path.read_bytes(), "correct horse battery")
    source = zipfile.ZipFile(io.BytesIO(clear))
    files = {name: source.read(name) for name in source.namelist()}
    manifest = json.loads(files["manifest.json"])
    first = manifest["entries"][0]["path"]
    files[first] = files[first] + b"corruption"
    rebuilt = io.BytesIO()
    with zipfile.ZipFile(rebuilt, "w") as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    path.write_bytes(backup_restore._encrypt(rebuilt.getvalue(), "correct horse battery"))
    with pytest.raises(ValueError, match="checksum mismatch"):
        backup_restore.validate_backup(path, password="correct horse battery")


def test_restore_dry_run_changes_no_active_data(tmp_path):
    path = tmp_path / "portfolio.ttmpbackup"
    backup_restore.create_encrypted_backup(path, password="correct horse battery")
    before = {row["database"]: row["size_bytes"] for row in backup_restore.database_status()}
    result = backup_restore.restore_backup(path, password="correct horse battery", dry_run=True)
    after = {row["database"]: row["size_bytes"] for row in backup_restore.database_status()}
    assert result["active_data_changed"] is False
    assert before == after


def test_schema_migration_is_transactional(tmp_path):
    path = tmp_path / "migration.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE schema_migrations (singleton INTEGER PRIMARY KEY, version INTEGER, applied_at REAL)")
        conn.execute("INSERT INTO schema_migrations VALUES (1, 1, 0)")

    def broken(conn):
        conn.execute("CREATE TABLE should_rollback (id INTEGER)")
        raise RuntimeError("stop")

    with pytest.raises(RuntimeError, match="stop"):
        ensure_database(path, supported_version=2, migrations={2: broken})
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT version FROM schema_migrations").fetchone()[0] == 1
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='should_rollback'").fetchone() is None


def test_newer_unknown_schema_causes_safe_refusal(tmp_path):
    path = tmp_path / "future.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE schema_migrations (singleton INTEGER PRIMARY KEY, version INTEGER, applied_at REAL)")
        conn.execute("INSERT INTO schema_migrations VALUES (1, 99, 0)")
    with pytest.raises(UnsupportedSchemaError, match="newer than supported"):
        ensure_database(path, supported_version=1)


def test_support_bundle_excludes_secrets_and_raw_holdings_without_opt_in(monkeypatch):
    monkeypatch.setattr(
        "modules.portfolio.services.diagnostics.collect_diagnostics",
        lambda: {"status": "ok", "message": redact_text("api_key=supersecret")},
    )
    bundle = build_support_bundle(
        family={"portfolios": [{"account_id": "private-account", "holdings": [{"symbol": "SECRETSTOCK"}]}]},
        include_raw_holdings=False,
    )
    with zipfile.ZipFile(bundle) as archive:
        names = archive.namelist()
        body = b"".join(archive.read(name) for name in names)
    assert "holdings.opt-in.json" not in names
    assert b"SECRETSTOCK" not in body
    assert b"supersecret" not in body


def test_llm_context_preview_matches_transmitted_context():
    context = {
        "holdings": [{"symbol": "SAFE", "account_id": "private-account"}],
        "advisory": {"tax_note": "private tax", "recommendations": []},
    }
    preview = external_context_preview(context)
    messages = _chat_messages(context=context, question="review", thread=None)
    content = messages[-1]["content"]
    encoded = content.split("Portfolio context JSON:\n", 1)[1].split("\n\nFill the JSON schema", 1)[0]
    assert json.loads(encoded) == preview
    assert "private-account" not in encoded
    assert "private tax" not in encoded


def test_trading_remains_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TRADING_ENABLED", raising=False)
    assert trading_enabled() is False


def test_ci_runs_security_dependency_and_coverage_checks():
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "pip-audit" in workflow
    assert "bandit" in workflow
    assert "detect-secrets" in workflow
    assert "--cov" in workflow


def test_api_v1_remains_compatible():
    response = TestClient(app).get(f"{APP_ROOT_PATH}/api/portfolio/version")
    assert response.status_code == 200
    assert response.json()["contract_version"] == "2026-05-mobile-mvp-v1"
