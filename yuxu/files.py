from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import re
import sqlite3
import tempfile
import threading
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from . import __version__, planning, store
from .contract import BusinessError


RESTORE_PREVIEWS_SCHEMA_SQL = """CREATE TABLE IF NOT EXISTS yuxu_restore_previews (
    preview_id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL REFERENCES yuxu_principals(principal_id),
    ledger_id TEXT NOT NULL REFERENCES yuxu_ledgers(ledger_id),
    backup_id TEXT NOT NULL,
    database_sha256 TEXT NOT NULL,
    ledger_bindings_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT
);"""

BACKUP_SCHEDULE_SCHEMA_SQL = """CREATE TABLE IF NOT EXISTS yuxu_backup_schedule (
    schedule_id INTEGER PRIMARY KEY CHECK (schedule_id = 1),
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    interval_days INTEGER NOT NULL CHECK (interval_days BETWEEN 1 AND 365),
    retention_count INTEGER NOT NULL CHECK (retention_count BETWEEN 1 AND 90),
    owner_principal_id TEXT NOT NULL REFERENCES yuxu_principals(principal_id),
    owner_ledger_id TEXT NOT NULL REFERENCES yuxu_ledgers(ledger_id),
    last_attempt_at TEXT,
    last_success_at TEXT,
    last_success_backup_id TEXT,
    last_success_sha256 TEXT,
    last_error_at TEXT,
    last_error_code TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS yuxu_scheduled_backups (
    backup_id TEXT PRIMARY KEY,
    scheduled_at TEXT NOT NULL,
    archive_sha256 TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_yuxu_scheduled_backups_schedule
    ON yuxu_scheduled_backups(scheduled_at, backup_id);"""


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS yuxu_export_previews (
    preview_id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL REFERENCES yuxu_principals(principal_id),
    ledger_id TEXT NOT NULL REFERENCES yuxu_ledgers(ledger_id),
    format TEXT NOT NULL CHECK (format IN ('csv', 'xlsx')),
    selection_json TEXT NOT NULL,
    fields_json TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    record_count INTEGER NOT NULL CHECK (record_count >= 0),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT
);

CREATE TABLE IF NOT EXISTS yuxu_export_files (
    file_id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL REFERENCES yuxu_principals(principal_id),
    ledger_id TEXT NOT NULL REFERENCES yuxu_ledgers(ledger_id),
    filename TEXT NOT NULL,
    format TEXT NOT NULL CHECK (format IN ('csv', 'xlsx')),
    record_count INTEGER NOT NULL CHECK (record_count >= 0),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS yuxu_report_artifacts (
    file_id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL REFERENCES yuxu_principals(principal_id),
    ledger_id TEXT NOT NULL REFERENCES yuxu_ledgers(ledger_id),
    filename TEXT NOT NULL,
    format TEXT NOT NULL CHECK (format IN ('png', 'pdf')),
    content BLOB NOT NULL,
    report_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_report_artifact_expiry ON yuxu_report_artifacts(expires_at);

CREATE TABLE IF NOT EXISTS yuxu_export_presets (
    preset_id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL REFERENCES yuxu_principals(principal_id),
    ledger_id TEXT NOT NULL REFERENCES yuxu_ledgers(ledger_id),
    name TEXT NOT NULL,
    hide_merchant INTEGER NOT NULL CHECK (hide_merchant IN (0, 1)),
    hide_note INTEGER NOT NULL CHECK (hide_note IN (0, 1)),
    hide_exact_date INTEGER NOT NULL CHECK (hide_exact_date IN (0, 1)),
    version INTEGER NOT NULL CHECK (version > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (principal_id, ledger_id, name)
);

""" + RESTORE_PREVIEWS_SCHEMA_SQL + "\n" + BACKUP_SCHEDULE_SCHEMA_SQL


_DATE = {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"}
_STRING = {"type": "string", "minLength": 1, "maxLength": 200}
_EXPORT_FIELDS = ["account_id", "account_name", "transaction_id", "date", "kind", "amount_minor", "category", "merchant", "estimated", "confirmation_status", "note"]
_PRIVATE_EXPORT_FIELDS = {"account_id", "account_name", "transaction_id", "merchant", "note"}
_DEFAULT_EXPORT_FIELDS = ["account_id", "account_name", "transaction_id", "date", "kind", "amount_minor", "category", "merchant", "estimated", "confirmation_status"]
_TRANSIENT_TABLES = {
    "yuxu_principals", "yuxu_ledgers", "yuxu_access", "yuxu_access_extensions",
    "yuxu_api_tokens", "yuxu_sessions", "yuxu_export_previews", "yuxu_export_files", "yuxu_restore_previews",
    "yuxu_backup_schedule", "yuxu_scheduled_backups", "yuxu_private_read_grants", "yuxu_local_review_links",
    "yuxu_report_artifacts",
    "insight_merchant_refs", "maintenance_import_previews", "maintenance_reconciliation_previews",
    "ledger_change_previews", "ledger_conversation_references", "ledger_search_sessions",
}

MAX_BACKUP_UPLOAD_BYTES = 256 * 1024 * 1024
MAX_BACKUP_DATABASE_BYTES = 512 * 1024 * 1024
MAX_BACKUP_MANIFEST_BYTES = 64 * 1024
BACKUP_STREAM_CHUNK_BYTES = 1024 * 1024
BACKUP_SCHEDULER_POLL_SECONDS = 300
_BACKUP_IO_LOCK = threading.Lock()
_BACKUP_LOGGER = logging.getLogger(__name__)


def _object(properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


def _fail(status: str, message: str, code: str, details: dict | None = None) -> None:
    raise BusinessError(status, message, details, code)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 200:
        _fail("invalid_input", f"{field} 必须是 1 至 200 个字符。", "invalid_input", {"field": field})
    return value.strip()


def _uuid(value: object, field: str) -> str:
    value = _text(value, field)
    try:
        return str(uuid.UUID(value))
    except ValueError:
        _fail("invalid_input", f"{field} 无效。", "invalid_id", {"field": field})


def _date(value: object, field: str) -> str:
    if not isinstance(value, str):
        _fail("invalid_input", f"{field} 必须是 ISO 日期。", "invalid_date", {"field": field})
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        _fail("invalid_input", f"{field} 必须是 YYYY-MM-DD。", "invalid_date", {"field": field})
    if parsed.isoformat() != value:
        _fail("invalid_input", f"{field} 必须是 YYYY-MM-DD。", "invalid_date", {"field": field})
    return value


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime | None = None) -> str:
    return (value or _now()).isoformat(timespec="seconds")


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _schema_digest(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE type IN ('table', 'index', 'trigger', 'view') AND name NOT LIKE 'sqlite_%' "
        "ORDER BY type, name"
    ).fetchall()
    payload = json.dumps([tuple(row) for row in rows], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _ledger_summaries(conn: sqlite3.Connection) -> list[dict]:
    ledgers = conn.execute(
        "SELECT ledger_id, account_id, account_name FROM yuxu_ledgers ORDER BY account_name, account_id"
    ).fetchall()
    result = []
    for ledger in ledgers:
        summary = conn.execute(
            "SELECT COUNT(*) AS transaction_count, "
            "COALESCE(SUM(CASE WHEN kind = 'income' THEN amount_minor ELSE -amount_minor END), 0) AS balance_minor "
            "FROM ledger_transactions WHERE ledger_id = ? AND confirmation_status = 'confirmed' AND voided_at IS NULL",
            (ledger["ledger_id"],),
        ).fetchone()
        result.append({
            "ledger_id": ledger["ledger_id"], "account_id": ledger["account_id"],
            "account_name": ledger["account_name"], "transaction_count": summary["transaction_count"],
            "balance_minor": summary["balance_minor"],
        })
    return result


def _backup_dir() -> Path:
    return store.data_dir() / "backups"


def _export_dir() -> Path:
    return store.data_dir() / "exports"


def _create_backup_snapshot(conn: sqlite3.Connection) -> dict:
    backup_dir = _backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_id = str(uuid.uuid4())
    created_at = _timestamp()
    temporary_database = backup_dir / f".{backup_id}.sqlite3"
    temporary_archive = backup_dir / f".{backup_id}.zip"
    final_archive = backup_dir / f"{backup_id}.zip"
    try:
        source_path = store.database_path().resolve()
        source = sqlite3.connect(f"{source_path.as_uri()}?mode=ro", uri=True)
        target = sqlite3.connect(temporary_database)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        snapshot = sqlite3.connect(temporary_database)
        snapshot.row_factory = sqlite3.Row
        try:
            snapshot.execute("DELETE FROM yuxu_sessions")
            snapshot.execute("DELETE FROM yuxu_api_tokens")
            snapshot.execute("DELETE FROM yuxu_export_previews")
            snapshot.execute("DELETE FROM yuxu_export_files")
            snapshot.execute("DELETE FROM yuxu_report_artifacts")
            snapshot.execute("DELETE FROM yuxu_restore_previews")
            snapshot.execute("DELETE FROM yuxu_backup_schedule")
            snapshot.execute("DELETE FROM yuxu_scheduled_backups")
            snapshot.execute("DELETE FROM yuxu_private_read_grants")
            snapshot.execute("DELETE FROM yuxu_local_review_links")
            snapshot.execute("DELETE FROM insight_merchant_refs")
            snapshot.execute("DELETE FROM maintenance_import_previews")
            snapshot.execute("DELETE FROM maintenance_reconciliation_previews")
            snapshot.execute("DELETE FROM ledger_change_previews")
            snapshot.execute("DELETE FROM ledger_conversation_references")
            snapshot.execute("DELETE FROM ledger_search_sessions")
            snapshot.execute("UPDATE yuxu_principals SET password_salt = randomblob(16), password_hash = randomblob(32)")
            snapshot.commit()
            snapshot.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            journal_mode = snapshot.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
            if journal_mode != "delete":
                raise sqlite3.DatabaseError("backup snapshot could not be normalized")
            schema_sha256 = _schema_digest(snapshot)
            ledgers = _ledger_summaries(snapshot)
        finally:
            snapshot.close()
        database_sha256 = _hash_file(temporary_database)
        manifest = {
            "format_version": 1,
            "app_version": __version__,
            "backup_id": backup_id,
            "created_at": created_at,
            "database_sha256": database_sha256,
            "schema_sha256": schema_sha256,
            "ledgers": ledgers,
        }
        with zipfile.ZipFile(temporary_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(temporary_database, "database.sqlite3")
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        temporary_archive.replace(final_archive)
    finally:
        temporary_database.unlink(missing_ok=True)
        temporary_archive.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm"):
            Path(str(temporary_database) + suffix).unlink(missing_ok=True)
    return {"backup_id": backup_id, "filename": final_archive.name, "format_version": 1, "sha256": _hash_file(final_archive), "created_at": created_at, "ledgers": ledgers}


def _create_backup(conn: sqlite3.Connection) -> dict:
    with _BACKUP_IO_LOCK:
        return _create_backup_snapshot(conn)


def _read_backup(
    conn: sqlite3.Connection,
    backup_id: object,
    *,
    archive_path: Path | None = None,
    max_database_bytes: int | None = None,
) -> tuple[sqlite3.Connection, dict, str]:
    backup_id = _uuid(backup_id, "backup_id")
    path = archive_path or (_backup_dir() / f"{backup_id}.zip")
    if not path.is_file():
        _fail("not_found", "备份文件不存在。", "backup_not_found")
    temporary = None
    candidate = None
    source = None
    returned = False
    try:
        try:
            with zipfile.ZipFile(path, "r") as archive:
                members = archive.infolist()
                names = [item.filename for item in members]
                if len(members) != 2 or len(set(names)) != 2 or set(names) != {"database.sqlite3", "manifest.json"}:
                    _fail("invalid_input", "备份包包含未知或缺失文件。", "invalid_backup_contents")
                member_by_name = {item.filename: item for item in members}
                database_member = member_by_name["database.sqlite3"]
                manifest_member = member_by_name["manifest.json"]
                if manifest_member.file_size > MAX_BACKUP_MANIFEST_BYTES:
                    _fail("invalid_input", "备份清单超过大小限制。", "invalid_backup_manifest")
                if max_database_bytes is not None and database_member.file_size > max_database_bytes:
                    _fail("invalid_input", "备份数据库超过本机 ZIP 解压容量限制。", "backup_database_too_large")
                if any(item.flag_bits & 0x1 or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) for item in members):
                    _fail("invalid_input", "备份包使用了不支持的压缩或加密方式。", "invalid_backup_contents")
                manifest = json.loads(archive.read(manifest_member).decode("utf-8"))
                temporary = _backup_dir() / f".preview-{uuid.uuid4()}.sqlite3"
                database_sha = hashlib.sha256()
                database_size = 0
                with archive.open(database_member, "r") as source_file, temporary.open("xb") as target_file:
                    for chunk in iter(lambda: source_file.read(BACKUP_STREAM_CHUNK_BYTES), b""):
                        database_size += len(chunk)
                        if max_database_bytes is not None and database_size > max_database_bytes:
                            _fail("invalid_input", "备份数据库超过本机 ZIP 解压容量限制。", "backup_database_too_large")
                        database_sha.update(chunk)
                        target_file.write(chunk)
                database_sha256 = database_sha.hexdigest()
        except BusinessError:
            raise
        except (OSError, RuntimeError, zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError):
            _fail("invalid_input", "备份包无法读取。", "invalid_backup")
        if not isinstance(manifest, dict):
            _fail("invalid_input", "备份清单格式无效。", "invalid_backup_manifest")
        if manifest.get("backup_id") != backup_id or manifest.get("database_sha256") != database_sha256:
            _fail("invalid_input", "备份包校验值不匹配。", "backup_checksum_mismatch")
        if manifest.get("format_version") != 1 or manifest.get("app_version") != __version__:
            _fail("conflict", "备份包版本与当前余序不兼容。", "incompatible_backup_version")
        if not isinstance(manifest.get("schema_sha256"), str) or not isinstance(manifest.get("ledgers"), list):
            _fail("invalid_input", "备份清单缺少必要信息。", "invalid_backup_manifest")
        candidate = sqlite3.connect(temporary)
        candidate.row_factory = sqlite3.Row
        check = candidate.execute("PRAGMA quick_check").fetchone()[0]
        if check != "ok" or _schema_digest(candidate) != manifest["schema_sha256"]:
            _fail("invalid_input", "备份数据库结构或完整性校验失败。", "invalid_backup_database")
        if manifest["ledgers"] != _ledger_summaries(candidate):
            _fail("invalid_input", "备份清单与数据库内容不匹配。", "backup_manifest_mismatch")
        store.migrate_schema(candidate)
        if _schema_digest(candidate) != _schema_digest(conn):
            _fail("conflict", "备份数据库结构与当前安装不一致。", "incompatible_backup_schema")
        candidate.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        journal_mode = candidate.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
        if journal_mode != "delete":
            _fail("invalid_input", "备份数据库无法安全读取。", "invalid_backup_database")
        source = sqlite3.connect(":memory:")
        candidate.backup(source)
        source.row_factory = sqlite3.Row
        if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            _fail("invalid_input", "备份数据库无法安全读取。", "invalid_backup_database")
        returned = True
        return source, manifest, database_sha256
    except sqlite3.DatabaseError:
        _fail("invalid_input", "备份数据库无法安全读取。", "invalid_backup_database")
    finally:
        if candidate is not None:
            candidate.close()
        if not returned and source is not None:
            source.close()
        if temporary is not None:
            for suffix in ("-wal", "-shm"):
                Path(str(temporary) + suffix).unlink(missing_ok=True)
            temporary.unlink(missing_ok=True)


def install_uploaded_backup(conn: sqlite3.Connection, stream, content_length: int) -> dict:
    if isinstance(content_length, bool) or not isinstance(content_length, int) or not 1 <= content_length <= MAX_BACKUP_UPLOAD_BYTES:
        _fail("invalid_input", "ZIP 文件超过本机上传容量限制。", "backup_upload_too_large")
    backup_dir = _backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)
    staging_path = backup_dir / f".upload-{uuid.uuid4()}.zip"
    try:
        with _BACKUP_IO_LOCK:
            with staging_path.open("xb") as destination:
                remaining = content_length
                while remaining:
                    chunk = stream.read(min(BACKUP_STREAM_CHUNK_BYTES, remaining))
                    if not chunk:
                        _fail("invalid_input", "ZIP 上传未完整接收。", "backup_upload_incomplete")
                    destination.write(chunk)
                    remaining -= len(chunk)
                destination.flush()
                os.fsync(destination.fileno())

            archive_sha256 = _hash_file(staging_path)
            try:
                with zipfile.ZipFile(staging_path, "r") as archive:
                    members = archive.infolist()
                    names = [item.filename for item in members]
                    if len(members) != 2 or len(set(names)) != 2 or set(names) != {"database.sqlite3", "manifest.json"}:
                        _fail("invalid_input", "备份包包含未知或重复文件。", "invalid_backup_contents")
                    member_by_name = {item.filename: item for item in members}
                    if member_by_name["manifest.json"].file_size > MAX_BACKUP_MANIFEST_BYTES:
                        _fail("invalid_input", "备份清单超过大小限制。", "invalid_backup_manifest")
                    if member_by_name["database.sqlite3"].file_size > MAX_BACKUP_DATABASE_BYTES:
                        _fail("invalid_input", "备份数据库超过本机 ZIP 解压容量限制。", "backup_database_too_large")
                    if any(item.flag_bits & 0x1 or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) for item in members):
                        _fail("invalid_input", "备份包使用了不支持的压缩或加密方式。", "invalid_backup_contents")
                    manifest = json.loads(archive.read(member_by_name["manifest.json"]).decode("utf-8"))
            except BusinessError:
                raise
            except (OSError, RuntimeError, zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError):
                _fail("invalid_input", "备份包无法读取。", "invalid_backup")
            if not isinstance(manifest, dict):
                _fail("invalid_input", "备份清单格式无效。", "invalid_backup_manifest")
            backup_id = _uuid(manifest.get("backup_id"), "backup_id")
            source, verified_manifest, _ = _read_backup(
                conn, backup_id, archive_path=staging_path, max_database_bytes=MAX_BACKUP_DATABASE_BYTES,
            )
            source.close()
            final_path = backup_dir / f"{backup_id}.zip"
            if final_path.exists() or final_path.is_symlink():
                if final_path.is_symlink() or not final_path.is_file() or _hash_file(final_path) != archive_sha256:
                    _fail("conflict", "同一备份 ID 已存在不同文件，未覆盖现有备份。", "backup_id_conflict")
                return {
                    "backup_id": backup_id, "filename": final_path.name,
                    "format_version": verified_manifest["format_version"], "created_at": verified_manifest["created_at"],
                    "sha256": archive_sha256, "ledgers": verified_manifest["ledgers"],
                }
            try:
                os.link(staging_path, final_path)
            except FileExistsError:
                if final_path.is_symlink() or not final_path.is_file() or _hash_file(final_path) != archive_sha256:
                    _fail("conflict", "同一备份 ID 已存在不同文件，未覆盖现有备份。", "backup_id_conflict")
            except OSError:
                _fail("internal_error", "无法原子保存 ZIP 备份。", "backup_install_failed")
            return {
                "backup_id": backup_id, "filename": final_path.name,
                "format_version": verified_manifest["format_version"], "created_at": verified_manifest["created_at"],
                "sha256": archive_sha256, "ledgers": verified_manifest["ledgers"],
            }
    finally:
        staging_path.unlink(missing_ok=True)


def create_isolated_restore_drill(conn: sqlite3.Connection, backup_id: str | None = None) -> dict:
    if backup_id is None:
        candidates = []
        for path in _backup_dir().glob("*.zip"):
            try:
                with zipfile.ZipFile(path, "r") as archive:
                    manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
                candidates.append((manifest["created_at"], manifest["backup_id"]))
            except (OSError, KeyError, TypeError, zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError):
                continue
        if not candidates:
            _fail("not_found", "没有可用的本机备份。", "backup_not_found")
        backup_id = max(candidates)[1]
    source, manifest, database_sha256 = _read_backup(conn, backup_id)
    try:
        with tempfile.TemporaryDirectory(prefix="drill-", dir=_backup_dir()) as directory:
            isolated_path = Path(directory) / "restore-drill.sqlite3"
            isolated = sqlite3.connect(isolated_path)
            isolated.row_factory = sqlite3.Row
            try:
                source.backup(isolated)
                integrity_check = isolated.execute("PRAGMA quick_check").fetchone()[0]
                ledgers = _ledger_summaries(isolated)
                recurring = []
                for ledger in ledgers:
                    rules = isolated.execute(
                        "SELECT COUNT(*) AS rule_count FROM yuxu_recurring_rules WHERE ledger_id = ?",
                        (ledger["ledger_id"],),
                    ).fetchone()["rule_count"]
                    history = isolated.execute(
                        "SELECT COUNT(*) AS price_history_count FROM yuxu_recurring_price_history WHERE ledger_id = ?",
                        (ledger["ledger_id"],),
                    ).fetchone()["price_history_count"]
                    occurrences = isolated.execute(
                        "SELECT COUNT(*) AS occurrence_count, MIN(period) AS first_period, MAX(period) AS last_period "
                        "FROM yuxu_recurring_occurrences WHERE ledger_id = ?",
                        (ledger["ledger_id"],),
                    ).fetchone()
                    recurring.append({
                        "ledger_id": ledger["ledger_id"], "rule_count": rules, "price_history_count": history,
                        "occurrence_count": occurrences["occurrence_count"],
                        "due_periods": [period for period in (occurrences["first_period"], occurrences["last_period"]) if period],
                    })
            finally:
                isolated.close()
        if integrity_check != "ok":
            _fail("invalid_input", "隔离恢复演练完整性校验失败。", "backup_drill_integrity_failed")
        return {
            "backup_id": manifest["backup_id"], "format_version": manifest["format_version"],
            "database_sha256": database_sha256, "integrity_check": integrity_check, "ledgers": ledgers, "recurring": recurring,
        }
    finally:
        source.close()


def _export_rows(conn: sqlite3.Connection, ctx: dict, selection: dict) -> list[dict]:
    conditions = ["t.ledger_id = ?", "t.voided_at IS NULL"]
    values: list[object] = [ctx["ledger_id"]]
    if selection["start_date"]:
        conditions.append("t.occurred_on >= ?")
        values.append(selection["start_date"])
    if selection["end_date"]:
        conditions.append("t.occurred_on <= ?")
        values.append(selection["end_date"])
    rows = conn.execute(
        "SELECT t.transaction_id, t.version, t.occurred_on, t.kind, t.amount_minor, t.estimated, "
        "t.confirmation_status, t.category_id, c.path AS category_path, t.merchant, t.note "
        "FROM ledger_transactions AS t LEFT JOIN ledger_categories AS c "
        "ON c.ledger_id = t.ledger_id AND c.category_id = t.category_id WHERE " + " AND ".join(conditions) +
        " ORDER BY t.occurred_on, t.transaction_id",
        values,
    ).fetchall()
    project_transactions = set(planning.project_transaction_ids(conn, ctx, selection["project_id"])) if selection["project_id"] else None
    data = []
    for row in rows:
        if project_transactions is not None and row["transaction_id"] not in project_transactions:
            continue
        data.append({
            "account_id": ctx["account_id"], "account_name": ctx["account_name"],
            "transaction_id": row["transaction_id"], "version": row["version"],
            "date": row["occurred_on"][:7] if selection.get("hide_exact_date") else row["occurred_on"],
            "kind": row["kind"], "amount_minor": row["amount_minor"], "category": row["category_path"] or "",
            "merchant": row["merchant"] or "", "estimated": bool(row["estimated"]),
            "confirmation_status": row["confirmation_status"], "note": row["note"] or "",
        })
    return data


def _redact_export_rows(conn: sqlite3.Connection, ctx: dict, selection: dict, rows: list[dict]) -> list[dict]:
    profile_id = selection.get("redaction_profile_id")
    if not profile_id:
        return rows
    from . import maintenance

    profile = maintenance.get_redaction_profile(conn, ctx["ledger_id"], profile_id)
    if profile is None:
        _fail("not_found", "脱敏预设不存在。", "redaction_profile_not_found")
    if profile["version"] != selection["redaction_profile_version"]:
        _fail("conflict", "预览后脱敏预设已变化，请重新预览。", "redaction_profile_changed")
    return maintenance.redact_export_rows(conn, ctx["ledger_id"], profile_id, rows)


def _selection(conn: sqlite3.Connection, ctx: dict, params: dict) -> tuple[dict, list[str]]:
    start = _date(params["start_date"], "start_date") if params.get("start_date") is not None else None
    end = _date(params["end_date"], "end_date") if params.get("end_date") is not None else None
    if start and end and start > end:
        _fail("invalid_input", "start_date 不能晚于 end_date。", "invalid_date_range")
    format_name = params.get("format")
    if format_name not in ("csv", "xlsx"):
        _fail("invalid_input", "format 必须是 csv 或 xlsx。", "invalid_export_format")
    include_notes = params.get("include_notes")
    if not isinstance(include_notes, bool):
        _fail("invalid_input", "include_notes 必须是布尔值。", "invalid_export_fields")
    project_id = _text(params["project_id"], "project_id") if params.get("project_id") is not None else None
    preset_id = _uuid(params["preset_id"], "preset_id") if params.get("preset_id") is not None else None
    preset = None
    if preset_id:
        preset = conn.execute(
            "SELECT hide_merchant, hide_note, hide_exact_date FROM yuxu_export_presets "
            "WHERE preset_id = ? AND principal_id = ? AND ledger_id = ?",
            (preset_id, ctx["principal_id"], ctx["ledger_id"]),
        ).fetchone()
        if preset is None:
            _fail("not_found", "脱敏导出预设不存在。", "export_preset_not_found")
    redaction_profile_id = _uuid(params["redaction_profile_id"], "redaction_profile_id") if params.get("redaction_profile_id") is not None else None
    redaction_profile = None
    if redaction_profile_id:
        from . import maintenance

        redaction_profile = maintenance.get_redaction_profile(conn, ctx["ledger_id"], redaction_profile_id)
        if redaction_profile is None:
            _fail("not_found", "脱敏预设不存在。", "redaction_profile_not_found")
    fields = params.get("fields")
    if fields is None:
        fields = list(_DEFAULT_EXPORT_FIELDS) + (["note"] if include_notes else [])
        if ctx.get("source") == "mcp" and not include_notes:
            fields = [field for field in fields if field not in _PRIVATE_EXPORT_FIELDS]
    if not isinstance(fields, list) or not fields or any(not isinstance(field, str) or field not in _EXPORT_FIELDS for field in fields) or len(set(fields)) != len(fields):
        _fail("invalid_input", "fields 必须是不重复的可导出字段列表。", "invalid_export_fields")
    if preset:
        hidden = {
            field for field, column in (("merchant", "hide_merchant"), ("note", "hide_note"))
            if preset[column]
        }
        fields = [field for field in fields if field not in hidden]
    if redaction_profile:
        hidden = set()
        if redaction_profile["merchant_policy"] == "omit":
            hidden.add("merchant")
        if redaction_profile["note_policy"] == "omit":
            hidden.add("note")
        if redaction_profile["date_precision"] == "omit":
            hidden.add("date")
        fields = [field for field in fields if field not in hidden]
    if not fields:
        _fail("invalid_input", "脱敏预设隐藏后没有可导出的字段。", "invalid_export_fields")
    if "note" in fields and not include_notes:
        _fail("invalid_input", "导出备注需要显式设置 include_notes。", "private_field_not_selected")
    selection = {
        "start_date": start, "end_date": end, "project_id": project_id, "format": format_name,
        "include_notes": include_notes, "preset_id": preset_id,
        "redaction_profile_id": redaction_profile_id,
        "redaction_profile_version": redaction_profile["version"] if redaction_profile else None,
        "hide_merchant": bool(preset["hide_merchant"]) if preset else False,
        "hide_note": bool(preset["hide_note"]) if preset else False,
        "hide_exact_date": bool(preset["hide_exact_date"]) if preset else False,
    }
    return selection, fields


def _preview_export(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    selection, fields = _selection(conn, ctx, params)
    rows = _redact_export_rows(conn, ctx, selection, _export_rows(conn, ctx, selection))
    redaction_profile = None
    if selection["redaction_profile_id"]:
        from . import maintenance

        redaction_profile = maintenance.get_redaction_profile(conn, ctx["ledger_id"], selection["redaction_profile_id"])
    snapshot = [{"transaction_id": row["transaction_id"], "version": row["version"]} for row in rows]
    now = _now()
    preview_id = str(uuid.uuid4())
    expires_at = _timestamp(now + timedelta(minutes=30))
    conn.execute(
        "INSERT INTO yuxu_export_previews "
        "(preview_id, principal_id, ledger_id, format, selection_json, fields_json, snapshot_json, record_count, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (preview_id, ctx["principal_id"], ctx["ledger_id"], selection["format"], json.dumps(selection, sort_keys=True), json.dumps(fields), json.dumps(snapshot), len(rows), _timestamp(now), expires_at),
    )
    return {
        "preview_id": preview_id, "account_id": ctx["account_id"], "account_name": ctx["account_name"],
        "web_confirmation_required": bool(_PRIVATE_EXPORT_FIELDS.intersection(fields)),
        "review_url": f"{ctx['origin']}/#export-preview={preview_id}" if ctx.get("origin") else None,
        "start_date": selection["start_date"], "end_date": selection["end_date"], "project_id": selection["project_id"],
        "record_count": len(rows), "fields": fields, "format": selection["format"], "expires_at": expires_at,
        "date_precision": redaction_profile["date_precision"] if redaction_profile else "month" if selection["hide_exact_date"] else "day",
        "redaction_profile_id": selection["redaction_profile_id"],
        "omitted_fields": sorted({
            *(["merchant"] if selection["hide_merchant"] else []),
            *(["note"] if selection["hide_note"] else []),
            *(["date"] if selection["hide_exact_date"] else []),
            *(["merchant"] if redaction_profile and redaction_profile["merchant_policy"] == "omit" else []),
            *(["note"] if redaction_profile and redaction_profile["note_policy"] == "omit" else []),
            *(["date"] if redaction_profile and redaction_profile["date_precision"] == "omit" else []),
        }),
    }


def _list_export_presets(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    rows = conn.execute(
        "SELECT preset_id, name, hide_merchant, hide_note, hide_exact_date, version "
        "FROM yuxu_export_presets WHERE principal_id = ? AND ledger_id = ? ORDER BY name, preset_id",
        (ctx["principal_id"], ctx["ledger_id"]),
    ).fetchall()
    return {"presets": [
        {**dict(row), "hide_merchant": bool(row["hide_merchant"]), "hide_note": bool(row["hide_note"]), "hide_exact_date": bool(row["hide_exact_date"])}
        for row in rows
    ]}


def _upsert_export_preset(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    name = _text(params.get("name"), "name")
    flags = (params.get("hide_merchant"), params.get("hide_note"), params.get("hide_exact_date"))
    if any(not isinstance(value, bool) for value in flags):
        _fail("invalid_input", "脱敏选项必须是布尔值。", "invalid_export_preset")
    preset_id_value = params.get("preset_id")
    expected_version = params.get("expected_version")
    now = _timestamp()
    if preset_id_value is None:
        if expected_version is not None:
            _fail("invalid_input", "新预设不能带 expected_version。", "invalid_export_preset")
        if conn.execute(
            "SELECT 1 FROM yuxu_export_presets WHERE principal_id = ? AND ledger_id = ? AND name = ?",
            (ctx["principal_id"], ctx["ledger_id"], name),
        ).fetchone():
            _fail("conflict", "同一账本中的预设名称不能重复。", "export_preset_name_conflict")
        preset_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO yuxu_export_presets "
            "(preset_id, principal_id, ledger_id, name, hide_merchant, hide_note, hide_exact_date, version, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (preset_id, ctx["principal_id"], ctx["ledger_id"], name, *[int(value) for value in flags], now, now),
        )
        version = 1
    else:
        preset_id = _uuid(preset_id_value, "preset_id")
        if type(expected_version) is not int or expected_version < 1:
            _fail("invalid_input", "更新预设必须提供正整数 expected_version。", "invalid_export_preset")
        row = conn.execute(
            "SELECT version FROM yuxu_export_presets WHERE preset_id = ? AND principal_id = ? AND ledger_id = ?",
            (preset_id, ctx["principal_id"], ctx["ledger_id"]),
        ).fetchone()
        if row is None:
            _fail("not_found", "脱敏导出预设不存在。", "export_preset_not_found")
        if row["version"] != expected_version:
            _fail("conflict", "预设版本已变化，请重新读取后再保存。", "version_conflict", {"current_version": row["version"]})
        if conn.execute(
            "SELECT 1 FROM yuxu_export_presets WHERE principal_id = ? AND ledger_id = ? AND name = ? AND preset_id <> ?",
            (ctx["principal_id"], ctx["ledger_id"], name, preset_id),
        ).fetchone():
            _fail("conflict", "同一账本中的预设名称不能重复。", "export_preset_name_conflict")
        version = expected_version + 1
        conn.execute(
            "UPDATE yuxu_export_presets SET name = ?, hide_merchant = ?, hide_note = ?, hide_exact_date = ?, version = ?, updated_at = ? "
            "WHERE preset_id = ? AND principal_id = ? AND ledger_id = ?",
            (name, *[int(value) for value in flags], version, now, preset_id, ctx["principal_id"], ctx["ledger_id"]),
        )
    return {
        "preset_id": preset_id, "name": name, "hide_merchant": flags[0], "hide_note": flags[1],
        "hide_exact_date": flags[2], "version": version,
    }


def _column_name(index: int) -> str:
    value = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        value = chr(65 + remainder) + value
    return value


def _xlsx_bytes(fields: list[str], rows: list[dict]) -> bytes:
    spreadsheet = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    relationships = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    package_rel = "http://schemas.openxmlformats.org/package/2006/relationships"
    ET.register_namespace("", spreadsheet)
    ET.register_namespace("r", relationships)
    worksheet = ET.Element(f"{{{spreadsheet}}}worksheet")
    sheet_data = ET.SubElement(worksheet, f"{{{spreadsheet}}}sheetData")
    for row_index, values in enumerate([[field for field in fields]] + [[row[field] for field in fields] for row in rows], start=1):
        row_element = ET.SubElement(sheet_data, f"{{{spreadsheet}}}row", {"r": str(row_index)})
        for col_index, value in enumerate(values, start=1):
            cell = ET.SubElement(row_element, f"{{{spreadsheet}}}c", {"r": f"{_column_name(col_index)}{row_index}"})
            if isinstance(value, bool):
                cell.set("t", "b")
                ET.SubElement(cell, f"{{{spreadsheet}}}v").text = "1" if value else "0"
            elif isinstance(value, int):
                ET.SubElement(cell, f"{{{spreadsheet}}}v").text = str(value)
            else:
                cell.set("t", "inlineStr")
                inline = ET.SubElement(cell, f"{{{spreadsheet}}}is")
                ET.SubElement(inline, f"{{{spreadsheet}}}t").text = str(value)
    workbook = ET.Element(f"{{{spreadsheet}}}workbook")
    sheets = ET.SubElement(workbook, f"{{{spreadsheet}}}sheets")
    ET.SubElement(sheets, f"{{{spreadsheet}}}sheet", {"name": "流水", "sheetId": "1", f"{{{relationships}}}id": "rId1"})
    content_types = ET.Element("{http://schemas.openxmlformats.org/package/2006/content-types}Types")
    ET.SubElement(content_types, "{http://schemas.openxmlformats.org/package/2006/content-types}Default", {"Extension": "rels", "ContentType": "application/vnd.openxmlformats-package.relationships+xml"})
    ET.SubElement(content_types, "{http://schemas.openxmlformats.org/package/2006/content-types}Default", {"Extension": "xml", "ContentType": "application/xml"})
    ET.SubElement(content_types, "{http://schemas.openxmlformats.org/package/2006/content-types}Override", {"PartName": "/xl/workbook.xml", "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"})
    ET.SubElement(content_types, "{http://schemas.openxmlformats.org/package/2006/content-types}Override", {"PartName": "/xl/worksheets/sheet1.xml", "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"})
    root_rels = ET.Element(f"{{{package_rel}}}Relationships")
    ET.SubElement(root_rels, f"{{{package_rel}}}Relationship", {"Id": "rId1", "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument", "Target": "xl/workbook.xml"})
    workbook_rels = ET.Element(f"{{{package_rel}}}Relationships")
    ET.SubElement(workbook_rels, f"{{{package_rel}}}Relationship", {"Id": "rId1", "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet", "Target": "worksheets/sheet1.xml"})
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", ET.tostring(content_types, encoding="utf-8", xml_declaration=True))
        archive.writestr("_rels/.rels", ET.tostring(root_rels, encoding="utf-8", xml_declaration=True))
        archive.writestr("xl/workbook.xml", ET.tostring(workbook, encoding="utf-8", xml_declaration=True))
        archive.writestr("xl/_rels/workbook.xml.rels", ET.tostring(workbook_rels, encoding="utf-8", xml_declaration=True))
        archive.writestr("xl/worksheets/sheet1.xml", ET.tostring(worksheet, encoding="utf-8", xml_declaration=True))
    return output.getvalue()


def _confirm_export(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    preview_id = _uuid(params.get("preview_id"), "preview_id")
    preview = conn.execute(
        "SELECT * FROM yuxu_export_previews WHERE preview_id = ? AND principal_id = ? AND ledger_id = ?",
        (preview_id, ctx["principal_id"], ctx["ledger_id"]),
    ).fetchone()
    if preview is None:
        _fail("not_found", "导出预览不存在。", "export_preview_not_found")
    if preview["consumed_at"]:
        _fail("conflict", "该导出预览已使用。", "export_preview_used")
    if preview["expires_at"] <= _timestamp():
        _fail("conflict", "导出预览已过期，请重新预览。", "export_preview_expired")
    selection = json.loads(preview["selection_json"])
    fields = json.loads(preview["fields_json"])
    shareable = not _PRIVATE_EXPORT_FIELDS.intersection(fields)
    if ctx.get("source") == "mcp" and not shareable:
        _fail("forbidden", "请由本人在本机网页确认含私密字段的导出。", "web_confirmation_required")
    rows = _redact_export_rows(conn, ctx, selection, _export_rows(conn, ctx, selection))
    snapshot = [{"transaction_id": row["transaction_id"], "version": row["version"]} for row in rows]
    if snapshot != json.loads(preview["snapshot_json"]):
        _fail("conflict", "预览后流水范围或版本已变化，请重新预览。", "export_snapshot_changed")
    values = [[row[field] for field in fields] for row in rows]
    if selection["format"] == "csv":
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow(fields)
        writer.writerows([
            ["'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value
             for value in row]
            for row in values
        ])
        content = ("\ufeff" + buffer.getvalue()).encode("utf-8")
        content_type = "text/csv; charset=utf-8"
        extension = "csv"
    else:
        content = _xlsx_bytes(fields, rows)
        content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        extension = "xlsx"
    export_dir = _export_dir()
    export_dir.mkdir(parents=True, exist_ok=True)
    file_id = str(uuid.uuid4())
    filename = f"Yuxu-Export-{file_id}.{extension}"
    path = export_dir / filename
    path.write_bytes(content)
    expires_at = _timestamp(_now() + timedelta(days=7))
    conn.execute(
        "INSERT INTO yuxu_export_files(file_id, principal_id, ledger_id, filename, format, record_count, created_at, shareable, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (file_id, ctx["principal_id"], ctx["ledger_id"], filename, selection["format"], len(rows), _timestamp(), int(shareable), expires_at),
    )
    conn.execute("UPDATE yuxu_export_previews SET consumed_at = ? WHERE preview_id = ?", (_timestamp(), preview_id))
    return {"file_id": file_id, "filename": filename, "download_url": f"/api/files/{file_id}",
            "review_url": f"{ctx['origin']}/#export={file_id}" if ctx.get("origin") else None,
            "record_count": len(rows), "format": selection["format"], "content_type": content_type,
            "expires_at": expires_at, "shareable": shareable}


def find_export(conn: sqlite3.Connection, principal_id: str, ledger_id: str, file_id: str) -> dict | None:
    params = (file_id, principal_id, ledger_id, _timestamp())
    report = conn.execute(
        "SELECT * FROM yuxu_report_artifacts WHERE file_id=? AND principal_id=? AND ledger_id=? AND expires_at>?", params,
    ).fetchone()
    if report:
        return {**dict(report), "shareable": True,
                "content_type": {"png": "image/png", "pdf": "application/pdf"}[report["format"]],
                "summary": json.loads(report["report_json"])}
    row = conn.execute(
        "SELECT * FROM yuxu_export_files WHERE file_id=? AND principal_id=? AND ledger_id=? "
        "AND (expires_at IS NULL OR expires_at>?)", params,
    ).fetchone()
    if row is None:
        return None
    export_dir = _export_dir().resolve()
    path = (export_dir / row["filename"]).resolve()
    if not path.is_relative_to(export_dir) or not path.is_file():
        return None
    content_type = "text/csv; charset=utf-8" if row["format"] == "csv" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return {**dict(row), "path": path, "content_type": content_type}


def _get_export_artifact(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    file_id = _uuid(params.get("file_id"), "file_id")
    item = find_export(conn, ctx["principal_id"], ctx["ledger_id"], file_id)
    if item is None:
        _fail("not_found", "导出文件不存在或已过期。", "export_not_found")
    return {
        "file_id": file_id, "filename": item["filename"], "format": item["format"],
        "content_type": item["content_type"], "created_at": item["created_at"], "expires_at": item["expires_at"],
        "review_url": f"{ctx['origin']}/#export={file_id}",
        "download_url": f"/api/files/{file_id}", "delivery": "web",
        "inline_available": item["format"] == "png" and bool(item["shareable"]),
        "summary": item.get("summary"), "record_count": item.get("record_count"),
    }


def _get_ledger_export_preview(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    row = conn.execute(
        "SELECT * FROM yuxu_export_previews WHERE preview_id=? AND principal_id=? AND ledger_id=? AND expires_at>?",
        (_uuid(params.get("preview_id"), "preview_id"), ctx["principal_id"], ctx["ledger_id"], _timestamp()),
    ).fetchone()
    if not row or row["consumed_at"]:
        _fail("not_found", "导出预览不存在、已过期或已确认。", "export_preview_not_found")
    selection = json.loads(row["selection_json"])
    return {"preview_id": row["preview_id"], "format": row["format"], "record_count": row["record_count"],
            "fields": json.loads(row["fields_json"]), "expires_at": row["expires_at"],
            "start_date": selection["start_date"], "end_date": selection["end_date"],
            "project_id": selection["project_id"]}


def _create_backup_action(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    return _create_backup(conn)


def _list_backups(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    backup_dir = _backup_dir()
    if not backup_dir.is_dir():
        return {"backups": []}
    backups = []
    for path in sorted(backup_dir.glob("*.zip"), key=lambda item: item.name, reverse=True):
        try:
            with zipfile.ZipFile(path, "r") as archive:
                manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            backups.append({
                "backup_id": manifest.get("backup_id"), "filename": path.name,
                "format_version": manifest.get("format_version"), "app_version": manifest.get("app_version"),
                "created_at": manifest.get("created_at"), "ledgers": manifest.get("ledgers", []),
            })
        except (OSError, zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError):
            backups.append({"backup_id": path.stem, "filename": path.name, "status": "invalid"})
    return {"backups": backups}


def _next_backup_run(row, now: datetime | None = None) -> str | None:
    if row is None or not row["enabled"]:
        return None
    last_attempt_at = row["last_attempt_at"]
    if last_attempt_at is None:
        return _timestamp(now)
    due_at = datetime.fromisoformat(last_attempt_at) + timedelta(days=row["interval_days"])
    return _timestamp(due_at)


def _latest_backup_drill(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT backup_id, result_json, created_at FROM maintenance_backup_drills "
        "ORDER BY created_at DESC, drill_id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    result = json.loads(row["result_json"])
    status = "passed" if result.get("integrity_check") == "ok" and result.get("live_ledger_summaries_unchanged") is True else "failed"
    return {"backup_id": row["backup_id"], "created_at": row["created_at"], "status": status}


def _backup_schedule_status(conn: sqlite3.Connection, now: datetime | None = None) -> dict:
    row = conn.execute("SELECT * FROM yuxu_backup_schedule WHERE schedule_id = 1").fetchone()
    last_success = None
    last_error = None
    if row is not None and row["last_success_at"] is not None:
        last_success = {
            "backup_id": row["last_success_backup_id"], "created_at": row["last_success_at"],
            "sha256": row["last_success_sha256"],
        }
    if row is not None and row["last_error_at"] is not None:
        last_error = {"at": row["last_error_at"], "code": row["last_error_code"]}
    return {
        "enabled": bool(row["enabled"]) if row is not None else False,
        "interval_days": row["interval_days"] if row is not None else 7,
        "retention_count": row["retention_count"] if row is not None else 7,
        "next_run_at": _next_backup_run(row, now),
        "last_success": last_success,
        "last_error": last_error,
        "last_drill": _latest_backup_drill(conn),
    }


def _get_backup_schedule(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    return _backup_schedule_status(conn)


def _set_backup_schedule(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    enabled = params.get("enabled")
    interval_days = params.get("interval_days")
    retention_count = params.get("retention_count")
    if not isinstance(enabled, bool) or isinstance(interval_days, bool) or not isinstance(interval_days, int) or not 1 <= interval_days <= 365:
        _fail("invalid_input", "备份启用状态和周期无效。", "invalid_backup_schedule")
    if isinstance(retention_count, bool) or not isinstance(retention_count, int) or not 1 <= retention_count <= 90:
        _fail("invalid_input", "备份保留份数无效。", "invalid_backup_schedule")
    conn.execute(
        "INSERT INTO yuxu_backup_schedule "
        "(schedule_id, enabled, interval_days, retention_count, owner_principal_id, owner_ledger_id, updated_at) "
        "VALUES (1, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(schedule_id) DO UPDATE SET enabled = excluded.enabled, interval_days = excluded.interval_days, "
        "retention_count = excluded.retention_count, owner_principal_id = excluded.owner_principal_id, "
        "owner_ledger_id = excluded.owner_ledger_id, updated_at = excluded.updated_at",
        (int(enabled), interval_days, retention_count, ctx["principal_id"], ctx["ledger_id"], _timestamp()),
    )
    return _backup_schedule_status(conn)


def _prune_scheduled_backups(conn: sqlite3.Connection, retention_count: int) -> int:
    backup_dir = _backup_dir()
    rows = conn.execute(
        "SELECT backup_id, archive_sha256 FROM yuxu_scheduled_backups "
        "ORDER BY scheduled_at DESC, backup_id DESC"
    ).fetchall()
    removed = 0
    for row in rows[retention_count:]:
        backup_id = _uuid(row["backup_id"], "backup_id")
        path = backup_dir / f"{backup_id}.zip"
        should_unlink = False
        if path.is_file() and not path.is_symlink():
            try:
                should_unlink = _hash_file(path) == row["archive_sha256"]
            except OSError:
                continue
        if should_unlink:
            try:
                path.unlink()
            except OSError:
                continue
            removed += 1
        with store.transaction(conn, write=True):
            conn.execute("DELETE FROM yuxu_scheduled_backups WHERE backup_id = ?", (backup_id,))
    return removed


def backup_scheduler_tick(conn: sqlite3.Connection, now: datetime | None = None) -> dict:
    now = now or _now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    now_text = _timestamp(now)
    due = False
    retention_count = 7
    with store.transaction(conn, write=True):
        row = conn.execute("SELECT * FROM yuxu_backup_schedule WHERE schedule_id = 1").fetchone()
        if row is None or not row["enabled"]:
            return {"status": "disabled"}
        retention_count = row["retention_count"]
        next_run_at = _next_backup_run(row, now)
        due = next_run_at is not None and now >= datetime.fromisoformat(next_run_at)
        if not due:
            result = {"status": "not_due", "next_run_at": next_run_at}
        else:
            scopes = store.access_scopes(conn, row["owner_principal_id"], row["owner_ledger_id"])
            if "maintenance" not in scopes:
                conn.execute(
                    "UPDATE yuxu_backup_schedule SET enabled = 0, last_attempt_at = ?, last_error_at = ?, "
                    "last_error_code = ?, updated_at = ? WHERE schedule_id = 1",
                    (now_text, now_text, "maintenance_scope_revoked", now_text),
                )
                return {"status": "disabled", "code": "maintenance_scope_revoked"}
            conn.execute(
                "UPDATE yuxu_backup_schedule SET last_attempt_at = ?, updated_at = ? WHERE schedule_id = 1",
                (now_text, now_text),
            )
            result = {"status": "due"}
    if not due:
        _prune_scheduled_backups(conn, retention_count)
        return result

    try:
        backup = _create_backup(conn)
    except Exception as error:
        code = error.code if isinstance(error, BusinessError) and isinstance(error.code, str) else "scheduled_backup_failed"
        with store.transaction(conn, write=True):
            conn.execute(
                "UPDATE yuxu_backup_schedule SET last_error_at = ?, last_error_code = ?, updated_at = ? WHERE schedule_id = 1",
                (now_text, code, now_text),
            )
        return {"status": "failed", "code": code}

    with store.transaction(conn, write=True):
        conn.execute(
            "INSERT INTO yuxu_scheduled_backups (backup_id, scheduled_at, archive_sha256) VALUES (?, ?, ?)",
            (backup["backup_id"], now_text, backup["sha256"]),
        )
        conn.execute(
            "UPDATE yuxu_backup_schedule SET last_success_at = ?, last_success_backup_id = ?, last_success_sha256 = ?, "
            "last_error_at = NULL, last_error_code = NULL, updated_at = ? WHERE schedule_id = 1",
            (backup["created_at"], backup["backup_id"], backup["sha256"], now_text),
        )
        retention_count = conn.execute(
            "SELECT retention_count FROM yuxu_backup_schedule WHERE schedule_id = 1"
        ).fetchone()["retention_count"]
    _prune_scheduled_backups(conn, retention_count)
    return {"status": "created", **backup}


def backup_scheduler_loop(stop_event) -> None:
    while not stop_event.is_set():
        try:
            if store.is_initialized():
                conn = store.connect()
                try:
                    backup_scheduler_tick(conn)
                finally:
                    conn.close()
        except Exception:
            _BACKUP_LOGGER.warning("本机定期备份调度周期失败。")
        if stop_event.wait(BACKUP_SCHEDULER_POLL_SECONDS):
            return


def _restore_owner_map(conn: sqlite3.Connection, ledgers: list[dict]) -> dict[str, str]:
    owners = {}
    for ledger in ledgers:
        rows = conn.execute(
            "SELECT principal_id FROM yuxu_access WHERE ledger_id = ? AND scope = 'own_write'",
            (ledger["ledger_id"],),
        ).fetchall()
        if len(rows) != 1:
            _fail("conflict", "每本账必须只有一位已验证的本机所有者。", "backup_identity_mapping_invalid")
        owners[ledger["ledger_id"]] = rows[0]["principal_id"]
    if len(set(owners.values())) != len(owners):
        _fail("conflict", "两本账必须属于不同的本机所有者。", "backup_identity_mapping_invalid")
    return owners


def _restore_binding_state(
    source: sqlite3.Connection,
    conn: sqlite3.Connection,
    requested_bindings: object,
) -> dict:
    source_ledgers = _ledger_summaries(source)
    target_ledgers = [dict(row) for row in conn.execute(
        "SELECT ledger_id, account_id, account_name FROM yuxu_ledgers ORDER BY account_name, account_id"
    )]
    if len(source_ledgers) != 2 or len(target_ledgers) != 2:
        _fail("conflict", "此安装只支持在两本完整账之间恢复迁移。", "incompatible_backup_ledgers")
    source_owners = _restore_owner_map(source, source_ledgers)
    target_owners = _restore_owner_map(conn, target_ledgers)
    source_by_id = {item["ledger_id"]: item for item in source_ledgers}
    target_by_account = {item["account_id"]: item for item in target_ledgers}
    target_by_id = {item["ledger_id"]: item for item in target_ledgers}
    same_installation = (
        set(source_by_id) == set(target_by_id)
        and all(source_owners[ledger_id] == target_owners[ledger_id] for ledger_id in source_by_id)
        and all(source_by_id[ledger_id]["account_id"] == target_by_id[ledger_id]["account_id"] for ledger_id in source_by_id)
    )
    if same_installation:
        expected = [
            {"source_ledger_id": ledger_id, "target_account_id": source_by_id[ledger_id]["account_id"]}
            for ledger_id in sorted(source_by_id)
        ]
        if requested_bindings is not None and requested_bindings != expected:
            _fail("invalid_input", "同一安装的备份不能交换本机账本归属。", "invalid_ledger_bindings")
        return {
            "same_installation": True,
            "ledger_map": {ledger_id: ledger_id for ledger_id in source_by_id},
            "principal_map": {source_owners[key]: target_owners[key] for key in source_owners},
            "bindings": expected,
            "source_ledgers": source_ledgers,
            "target_ledgers": target_ledgers,
            "binding_summary": [
                {"source_ledger_id": item["ledger_id"], "target_account_id": item["account_id"],
                 "target_account_name": item["account_name"], "transaction_count": item["transaction_count"],
                 "balance_minor": item["balance_minor"]}
                for item in source_ledgers
            ],
        }
    if requested_bindings is None:
        return {
            "same_installation": False,
            "binding_required": True,
            "source_ledgers": source_ledgers,
            "target_ledgers": [{"account_id": item["account_id"], "account_name": item["account_name"]} for item in target_ledgers],
        }
    if not isinstance(requested_bindings, list) or len(requested_bindings) != 2:
        _fail("invalid_input", "必须为来源两本账各选择一个本机目标账户。", "invalid_ledger_bindings")
    normalized = []
    target_ledger_map = {}
    target_principal_map = {}
    target_account_ids = set()
    source_ledger_ids = set()
    for item in requested_bindings:
        if not isinstance(item, dict) or set(item) != {"source_ledger_id", "target_account_id"}:
            _fail("invalid_input", "账本绑定必须只包含 source_ledger_id 和 target_account_id。", "invalid_ledger_bindings")
        source_ledger_id = _uuid(item.get("source_ledger_id"), "source_ledger_id")
        target_account_id = _uuid(item.get("target_account_id"), "target_account_id")
        if source_ledger_id not in source_by_id or source_ledger_id in source_ledger_ids:
            _fail("invalid_input", "来源账本必须完整且不能重复。", "invalid_ledger_bindings")
        target = target_by_account.get(target_account_id)
        if target is None or target_account_id in target_account_ids:
            _fail("invalid_input", "本机目标账户必须有效且不能重复。", "invalid_ledger_bindings")
        source_ledger_ids.add(source_ledger_id)
        target_account_ids.add(target_account_id)
        target_ledger_map[source_ledger_id] = target["ledger_id"]
        target_principal_map[source_owners[source_ledger_id]] = target_owners[target["ledger_id"]]
        normalized.append({"source_ledger_id": source_ledger_id, "target_account_id": target_account_id})
    if source_ledger_ids != set(source_by_id) or target_account_ids != set(target_by_account):
        _fail("invalid_input", "必须将所有来源账本一对一映射到所有本机账户。", "invalid_ledger_bindings")
    normalized.sort(key=lambda item: item["source_ledger_id"])
    return {
        "same_installation": False,
        "binding_required": False,
        "ledger_map": target_ledger_map,
        "principal_map": target_principal_map,
        "bindings": normalized,
        "source_ledgers": source_ledgers,
        "target_ledgers": target_ledgers,
        "binding_summary": [
            {"source_ledger_id": item["source_ledger_id"], "target_account_id": item["target_account_id"],
             "target_account_name": target_by_account[item["target_account_id"]]["account_name"],
             "transaction_count": source_by_id[item["source_ledger_id"]]["transaction_count"],
             "balance_minor": source_by_id[item["source_ledger_id"]]["balance_minor"]}
            for item in normalized
        ],
    }


def _remap_identity_payload(
    value: object,
    ledger_map: dict[str, str],
    target_ledgers: dict[str, dict],
    parent_key: str | None = None,
) -> object:
    if isinstance(value, dict):
        result = {key: _remap_identity_payload(item, ledger_map, target_ledgers, key) for key, item in value.items()}
        if result.get("ledger_id") in ledger_map:
            source_ledger_id = result["ledger_id"]
            target = target_ledgers[ledger_map[source_ledger_id]]
            result["ledger_id"] = target["ledger_id"]
            if parent_key == "ledger" or (parent_key == "ledgers" and "transaction_count" in result):
                result.update({"account_id": target["account_id"], "account_name": target["account_name"]})
        return result
    if isinstance(value, list):
        return [_remap_identity_payload(item, ledger_map, target_ledgers, parent_key) for item in value]
    return value


def _preview_restore(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    backup_id = _uuid(params.get("backup_id"), "backup_id")
    source, manifest, database_sha256 = _read_backup(conn, backup_id)
    try:
        bindings = params.get("ledger_bindings")
        if ctx.get("source") == "mcp" and bindings is not None:
            _fail("forbidden", "跨设备账本绑定只能在本机网页完成。", "web_confirmation_required")
        state = _restore_binding_state(source, conn, bindings)
        if state.get("binding_required"):
            return {
                "binding_required": True,
                "backup_id": backup_id,
                "source_ledgers": state["source_ledgers"],
                "target_ledgers": state["target_ledgers"],
                "message": "请明确选择每本来源账对应的本机账户，再生成恢复预览。",
            }
        if state["same_installation"]:
            backup_principals = {row["principal_id"] for row in source.execute("SELECT principal_id FROM yuxu_principals")}
            if ctx["principal_id"] not in backup_principals:
                _fail("conflict", "备份不包含当前维护者身份。", "backup_identity_mismatch")
        now = _now()
        preview_id = str(uuid.uuid4())
        expires_at = _timestamp(now + timedelta(minutes=30))
        conn.execute(
            "INSERT INTO yuxu_restore_previews "
            "(preview_id, principal_id, ledger_id, backup_id, database_sha256, ledger_bindings_json, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (preview_id, ctx["principal_id"], ctx["ledger_id"], backup_id, database_sha256,
             json.dumps(state["bindings"], sort_keys=True), _timestamp(now), expires_at),
        )
        return {
            "preview_id": preview_id, "backup_id": backup_id, "format_version": manifest["format_version"],
            "created_at": manifest["created_at"], "ledgers": state["source_ledgers"],
            "ledger_bindings": state["binding_summary"], "sha256": database_sha256, "expires_at": expires_at,
        }
    finally:
        source.close()


def _restore_backup(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    if ctx.get("source") != "web" or params.get("confirmation") != "RESTORE":
        _fail("needs_clarification", "正式恢复只能在本机网页明确确认。", "restore_confirmation_required")
    preview_id = _uuid(params.get("preview_id"), "preview_id")
    preview = conn.execute(
        "SELECT * FROM yuxu_restore_previews WHERE preview_id = ? AND principal_id = ? AND ledger_id = ?",
        (preview_id, ctx["principal_id"], ctx["ledger_id"]),
    ).fetchone()
    if preview is None:
        _fail("not_found", "恢复预览不存在。", "restore_preview_not_found")
    if preview["consumed_at"]:
        _fail("conflict", "该恢复预览已使用。", "restore_preview_used")
    if preview["expires_at"] <= _timestamp():
        _fail("conflict", "恢复预览已过期，请重新预览。", "restore_preview_expired")
    source, manifest, database_sha256 = _read_backup(conn, preview["backup_id"])
    try:
        if database_sha256 != preview["database_sha256"]:
            _fail("conflict", "预览后备份文件发生变化，请重新预览。", "backup_changed_after_preview")
        requested_bindings = json.loads(preview["ledger_bindings_json"])
        binding_state = _restore_binding_state(source, conn, requested_bindings)
        if binding_state.get("binding_required"):
            _fail("conflict", "恢复预览缺少完整的本机账户映射，请重新预览。", "restore_binding_missing")
        if binding_state["same_installation"]:
            source_principals = {row["principal_id"] for row in source.execute("SELECT principal_id FROM yuxu_principals")}
            if ctx["principal_id"] not in source_principals:
                _fail("conflict", "备份不包含当前维护者身份。", "backup_identity_mismatch")
        pre_restore = _create_backup(conn)
        conn.execute("DELETE FROM yuxu_export_previews")
        conn.execute("DELETE FROM yuxu_export_files")
        conn.execute("DELETE FROM yuxu_report_artifacts")
        conn.execute("DELETE FROM yuxu_private_read_grants")
        conn.execute("DELETE FROM yuxu_local_review_links")
        conn.execute("DELETE FROM insight_merchant_refs")
        conn.execute("DELETE FROM maintenance_import_previews")
        conn.execute("DELETE FROM maintenance_reconciliation_previews")
        conn.execute("DELETE FROM ledger_change_previews")
        conn.execute("DELETE FROM ledger_conversation_references")
        conn.execute("DELETE FROM ledger_search_sessions")
        conn.execute("DELETE FROM yuxu_restore_previews WHERE preview_id <> ?", (preview_id,))
        tables = [row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ) if row["name"] not in _TRANSIENT_TABLES]
        conn.execute("PRAGMA defer_foreign_keys = ON")
        for table in tables:
            conn.execute(f'DELETE FROM "{table}"')
        target_ledgers = {
            row["ledger_id"]: dict(row)
            for row in conn.execute("SELECT ledger_id, account_id, account_name FROM yuxu_ledgers")
        }
        for table in tables:
            columns = [row["name"] for row in source.execute(f'PRAGMA table_info("{table}")')]
            if not columns:
                continue
            quoted_columns = ", ".join(f'"{column}"' for column in columns)
            source_rows = source.execute(f'SELECT {quoted_columns} FROM "{table}"')
            insert_sql = f'INSERT INTO "{table}" ({quoted_columns}) VALUES ({", ".join("?" for _ in columns)})'
            while batch := source_rows.fetchmany(1000):
                rows = []
                for source_row in batch:
                    values = dict(source_row)
                    source_ledger_id = values.get("ledger_id")
                    if source_ledger_id is not None:
                        if source_ledger_id not in binding_state["ledger_map"]:
                            _fail("invalid_input", "备份业务行引用了未映射的账本。", "backup_reference_mismatch")
                        values["ledger_id"] = binding_state["ledger_map"][source_ledger_id]
                    for column in ("principal_id", "actor_id", "created_by", "updated_by", "checked_by", "reversed_by"):
                        principal_id = values.get(column)
                        if principal_id is not None:
                            if principal_id not in binding_state["principal_map"]:
                                _fail("invalid_input", "备份业务行引用了未映射的本机主体。", "backup_identity_mapping_invalid")
                            values[column] = binding_state["principal_map"][principal_id]
                    for column, value in tuple(values.items()):
                        if column.endswith("_json") and value is not None and table not in ("yuxu_export_previews", "yuxu_restore_previews", "maintenance_import_previews"):
                            try:
                                payload = json.loads(value)
                            except (TypeError, json.JSONDecodeError):
                                _fail("invalid_input", "备份中的业务快照无法安全映射。", "backup_snapshot_invalid")
                            values[column] = json.dumps(
                                _remap_identity_payload(payload, binding_state["ledger_map"], target_ledgers),
                                ensure_ascii=False, sort_keys=True,
                            )
                    rows.append(tuple(values[column] for column in columns))
                conn.executemany(insert_sql, rows)
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            _fail("invalid_input", "恢复后的数据关联未通过外键校验，目标账簿未提交变更。", "backup_reference_mismatch")
        conn.execute(
            "DELETE FROM yuxu_operation_receipts WHERE principal_id = ? AND ledger_id = ? AND request_id = ?",
            (ctx["principal_id"], ctx["ledger_id"], ctx["request_id"]),
        )
        conn.execute("UPDATE yuxu_restore_previews SET consumed_at = ? WHERE preview_id = ?", (_timestamp(), preview_id))
        restored_ledgers = manifest["ledgers"] if binding_state["same_installation"] else _ledger_summaries(conn)
        return {
            "restored": True, "backup_id": preview["backup_id"], "pre_restore_backup_id": pre_restore["backup_id"],
            "ledgers": restored_ledgers,
            "ledger_bindings": binding_state["binding_summary"],
        }
    finally:
        source.close()


ACTIONS = {
    "list_export_presets": {
        "description": "List the caller's saved anonymized export field presets.",
        "inputSchema": _object({}), "scope": "own_read", "host_permission": "summary_read", "run": _list_export_presets,
    },
    "upsert_export_preset": {
        "description": "Create or update a reusable anonymized export field preset for the current ledger.",
        "inputSchema": _object({
            "preset_id": _STRING, "expected_version": {"type": "integer", "minimum": 1}, "name": _STRING,
            "hide_merchant": {"type": "boolean"}, "hide_note": {"type": "boolean"}, "hide_exact_date": {"type": "boolean"},
        }, ("name", "hide_merchant", "hide_note", "hide_exact_date")),
        "scope": "own_write", "host_permission": "write", "run": _upsert_export_preset,
    },
    "preview_ledger_export": {
        "description": "Preview the current ledger export scope, row count, and fields before writing a CSV or XLSX file.",
        "inputSchema": _object({
            "start_date": _DATE, "end_date": _DATE, "project_id": _STRING,
            "format": {"type": "string", "enum": ["csv", "xlsx"]}, "include_notes": {"type": "boolean"},
            "fields": {"type": "array", "items": {"type": "string", "enum": _EXPORT_FIELDS}, "uniqueItems": True},
            "preset_id": _STRING,
            "redaction_profile_id": _STRING,
        }, ("format", "include_notes")),
        "scope": "own_write", "host_permission": "export_summary", "run": _preview_export,
    },
    "confirm_ledger_export": {
        "description": "Write a CSV or XLSX export after confirming an unexpired preview in the current ledger.",
        "inputSchema": _object({"preview_id": _STRING}, ("preview_id",)),
        "scope": "own_write", "host_permission": "export_summary", "run": _confirm_export,
    },
    "get_ledger_export_preview": {
        "description": "Read an unexpired pending ledger export preview in the authenticated local Web session.",
        "inputSchema": _object({"preview_id": _STRING}, ("preview_id",)),
        "scope": "own_read", "sources": ["web"], "run": _get_ledger_export_preview,
    },
    "get_export_artifact": {
        "description": "Retrieve local export metadata and a signed-in Web delivery link. Set inline to true to display an authorized PNG in a compatible local MCP client.",
        "inputSchema": _object({"file_id": _STRING, "inline": {"type": "boolean"}}, ("file_id",)),
        "scope": "own_read", "host_permission": "export_artifact", "run": _get_export_artifact,
    },
    "create_backup": {
        "description": "Create a versioned local backup of ledger data, categories, plans, recurring rules, and receipts. Requires the separately granted local maintenance scope.",
        "inputSchema": _object({}), "scope": "own_write", "host_permission": "write", "requires_scope": "maintenance", "run": _create_backup_action,
    },
    "list_backups": {
        "description": "List versioned local backups and their ledger summaries. Requires the separately granted local maintenance scope.",
        "inputSchema": _object({}), "scope": "own_read", "host_permission": "summary_read", "requires_scope": "maintenance", "run": _list_backups,
    },
    "get_backup_schedule": {
        "description": "Read the local backup schedule, latest scheduled backup result, and latest recorded isolated backup drill. Requires the separately granted local maintenance scope.",
        "inputSchema": _object({}), "scope": "own_read", "host_permission": "summary_read",
        "requires_scope": "maintenance", "sources": ["web"], "run": _get_backup_schedule,
    },
    "set_backup_schedule": {
        "description": "Opt in or out of local scheduled backups and set the interval and retention count. Only scheduled backups created by this installation are eligible for pruning.",
        "inputSchema": _object({
            "enabled": {"type": "boolean"},
            "interval_days": {"type": "integer", "minimum": 1, "maximum": 365},
            "retention_count": {"type": "integer", "minimum": 1, "maximum": 90},
        }, ("enabled", "interval_days", "retention_count")),
        "scope": "own_write", "host_permission": "write", "requires_scope": "maintenance",
        "sources": ["web"], "run": _set_backup_schedule,
    },
    "preview_restore": {
        "description": "Validate a backup and preview its source ledgers. Cross-device restore requires the trusted local web owner to explicitly map each source ledger to a different current account before confirming.",
        "inputSchema": _object({
            "backup_id": _STRING,
            "ledger_bindings": {
                "type": "array", "minItems": 2, "maxItems": 2,
                "items": _object({"source_ledger_id": _STRING, "target_account_id": _STRING}, ("source_ledger_id", "target_account_id")),
            },
        }, ("backup_id",)),
        "scope": "own_write", "host_permission": "write", "requires_scope": "maintenance", "run": _preview_restore,
    },
    "restore_backup": {
        "description": "Restore a previously previewed backup after an explicit confirmation from the trusted local web session. Creates a pre-restore backup first.",
        "inputSchema": _object({"preview_id": _STRING, "confirmation": {"type": "string", "enum": ["RESTORE"]}}, ("preview_id", "confirmation")),
        "scope": "own_write", "host_permission": "write", "requires_scope": "maintenance", "sources": ["web"], "run": _restore_backup,
    },
}
