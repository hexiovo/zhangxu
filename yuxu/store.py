from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .contract import BusinessError

PASSWORD_ITERATIONS = 600_000
SESSION_TTL_SECONDS = 43_200
SCHEMA_VERSION = 12
HOST_PERMISSIONS = frozenset({'summary_read', 'detail_read', 'write', 'export_summary', 'export_artifact'})
DEFAULT_HOST_SCOPES = ("summary_read",)

PLATFORM_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS yuxu_principals (
    principal_id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_salt BLOB NOT NULL,
    password_hash BLOB NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS yuxu_ledgers (
    ledger_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL UNIQUE,
    account_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS yuxu_access (
    principal_id TEXT NOT NULL REFERENCES yuxu_principals(principal_id),
    ledger_id TEXT NOT NULL REFERENCES yuxu_ledgers(ledger_id),
    scope TEXT NOT NULL CHECK (scope IN ('own_read', 'own_write', 'admin_spending')),
    PRIMARY KEY (principal_id, ledger_id, scope)
);

CREATE TABLE IF NOT EXISTS yuxu_access_extensions (
    principal_id TEXT NOT NULL REFERENCES yuxu_principals(principal_id),
    ledger_id TEXT NOT NULL REFERENCES yuxu_ledgers(ledger_id),
    scope TEXT NOT NULL CHECK (scope IN ('admin_goal', 'maintenance')),
    PRIMARY KEY (principal_id, ledger_id, scope)
);

CREATE TABLE IF NOT EXISTS yuxu_api_tokens (
    token_hash TEXT PRIMARY KEY,
    token_id TEXT NOT NULL UNIQUE,
    principal_id TEXT NOT NULL REFERENCES yuxu_principals(principal_id),
    label TEXT NOT NULL,
    scopes_json TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS yuxu_sessions (
    session_hash TEXT PRIMARY KEY,
    csrf_token TEXT NOT NULL,
    principal_id TEXT NOT NULL REFERENCES yuxu_principals(principal_id),
    conversation_id TEXT NOT NULL,
    expires_at REAL NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS yuxu_operation_receipts (
    principal_id TEXT NOT NULL REFERENCES yuxu_principals(principal_id),
    ledger_id TEXT NOT NULL REFERENCES yuxu_ledgers(ledger_id),
    request_id TEXT NOT NULL,
    action_name TEXT NOT NULL,
    params_hash TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (principal_id, ledger_id, request_id)
);

CREATE TABLE IF NOT EXISTS yuxu_private_read_grants (
    principal_id TEXT NOT NULL REFERENCES yuxu_principals(principal_id),
    ledger_id TEXT NOT NULL REFERENCES yuxu_ledgers(ledger_id),
    token_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    fields_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    PRIMARY KEY (principal_id, ledger_id, token_id, transaction_id)
);

CREATE TABLE IF NOT EXISTS yuxu_local_review_links (
    review_ref_hash TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL REFERENCES yuxu_principals(principal_id),
    ledger_id TEXT NOT NULL REFERENCES yuxu_ledgers(ledger_id),
    target_kind TEXT NOT NULL CHECK (target_kind IN ('ledger', 'period', 'category', 'transaction')),
    target_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_yuxu_local_review_links_expiry
    ON yuxu_local_review_links(expires_at);
"""


def data_dir() -> Path:
    configured = os.environ.get("YUXU_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return (base / "Yuxu").resolve()


def runtime_dir() -> Path:
    configured = os.environ.get("YUXU_RUNTIME_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return (base / "Yuxu" / "runtime").resolve()


def database_path(directory: Path | None = None) -> Path:
    return (directory or data_dir()) / "yuxu.sqlite3"


def is_initialized(directory: Path | None = None) -> bool:
    path = database_path(directory)
    if not path.is_file():
        return False
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'yuxu_principals'"
        ).fetchone() is not None and conn.execute("SELECT 1 FROM yuxu_principals LIMIT 1").fetchone() is not None
    finally:
        conn.close()


def connect(directory: Path | None = None) -> sqlite3.Connection:
    path = database_path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    schema_version = conn.execute("PRAGMA user_version").fetchone()[0]
    if schema_version < SCHEMA_VERSION:
        migrate_schema(conn, schema_version)
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection, *, write: bool) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def install_schema(conn: sqlite3.Connection) -> None:
    from . import bookkeeping, files, insights, ledger, maintenance, movements, planning, recurring

    conn.executescript(PLATFORM_SCHEMA_SQL)
    for module in (ledger, planning, movements, recurring, insights, files, maintenance, bookkeeping):
        conn.executescript(module.SCHEMA_SQL)


def migrate_schema(conn: sqlite3.Connection, from_version: int | None = None) -> None:
    from . import files

    from_version = conn.execute("PRAGMA user_version").fetchone()[0] if from_version is None else from_version
    install_schema(conn)
    with transaction(conn, write=True):
        if from_version < 2:
            conn.execute(
                "INSERT OR IGNORE INTO yuxu_access_extensions(principal_id, ledger_id, scope) "
                "SELECT principal_id, ledger_id, 'admin_goal' FROM yuxu_access WHERE scope = 'admin_spending'"
            )
        if from_version < 3:
            columns = {row["name"] for row in conn.execute('PRAGMA table_info("yuxu_api_tokens")')}
            if "token_id" not in columns:
                conn.execute("ALTER TABLE yuxu_api_tokens ADD COLUMN token_id TEXT")
            if "scopes_json" not in columns:
                conn.execute(
                    "ALTER TABLE yuxu_api_tokens ADD COLUMN scopes_json TEXT NOT NULL "
                    "DEFAULT '[\"summary_read\",\"detail_read\",\"write\"]'"
                )
            if "version" not in columns:
                conn.execute("ALTER TABLE yuxu_api_tokens ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
            for row in conn.execute("SELECT token_hash FROM yuxu_api_tokens WHERE token_id IS NULL OR token_id = ''"):
                conn.execute("UPDATE yuxu_api_tokens SET token_id = ? WHERE token_hash = ?", (str(uuid.uuid4()), row["token_hash"]))
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS yuxu_api_tokens_token_id ON yuxu_api_tokens(token_id)")
        if from_version < 9:
            columns = {row["name"] for row in conn.execute('PRAGMA table_info("yuxu_restore_previews")')}
            if "ledger_bindings_json" not in columns:
                conn.execute(
                    "ALTER TABLE yuxu_restore_previews ADD COLUMN ledger_bindings_json TEXT NOT NULL DEFAULT '[]'"
                )
            expected = ["preview_id", "principal_id", "ledger_id", "backup_id", "database_sha256",
                        "ledger_bindings_json", "created_at", "expires_at", "consumed_at"]
            columns = [row["name"] for row in conn.execute('PRAGMA table_info("yuxu_restore_previews")')]
            if set(columns) != set(expected):
                raise RuntimeError("unsupported yuxu_restore_previews schema")
            conn.execute("ALTER TABLE yuxu_restore_previews RENAME TO yuxu_restore_previews_schema_v8")
            conn.execute(files.RESTORE_PREVIEWS_SCHEMA_SQL)
            conn.execute(
                "INSERT INTO yuxu_restore_previews "
                "(preview_id, principal_id, ledger_id, backup_id, database_sha256, ledger_bindings_json, created_at, expires_at, consumed_at) "
                "SELECT preview_id, principal_id, ledger_id, backup_id, database_sha256, ledger_bindings_json, created_at, expires_at, consumed_at "
                "FROM yuxu_restore_previews_schema_v8"
            )
            conn.execute("DROP TABLE yuxu_restore_previews_schema_v8")
        if from_version < 11:
            columns = {row["name"] for row in conn.execute('PRAGMA table_info("ledger_transaction_drafts")')}
            if "copied_from_transaction_id" not in columns:
                conn.execute("ALTER TABLE ledger_transaction_drafts ADD COLUMN copied_from_transaction_id TEXT")
        if from_version < 12:
            columns = {row["name"] for row in conn.execute('PRAGMA table_info("yuxu_export_files")')}
            if "shareable" not in columns:
                conn.execute("ALTER TABLE yuxu_export_files ADD COLUMN shareable INTEGER NOT NULL DEFAULT 0")
            if "expires_at" not in columns:
                conn.execute("ALTER TABLE yuxu_export_files ADD COLUMN expires_at TEXT")
            columns = {row["name"] for row in conn.execute('PRAGMA table_info("maintenance_reconciliation_previews")')}
            if "result_json" not in columns:
                conn.execute("ALTER TABLE maintenance_reconciliation_previews ADD COLUMN result_json TEXT")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS, dklen=32)


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT principal_id, username, password_salt, password_hash "
        "FROM yuxu_principals WHERE username = ? COLLATE NOCASE",
        (username,),
    ).fetchone()
    if row is None or not hmac.compare_digest(password_hash(password, row["password_salt"]), row["password_hash"]):
        raise BusinessError("forbidden", "用户名或密码不正确。", code="invalid_credentials")
    return row


def create_local_accounts(
    conn: sqlite3.Connection,
    accounts: list[dict],
    admin_username: str | None,
    maintenance_username: str | None = None,
    *,
    create_initial_tokens: bool = True,
) -> list[dict]:
    from .ledger import seed_categories

    migrate_schema(conn)
    if len(accounts) != 2:
        raise BusinessError("needs_clarification", "首次设置需要两个本机用户。", code="two_users_required")
    names = [account["username"].strip() for account in accounts]
    if not all(names) or names[0].casefold() == names[1].casefold():
        raise BusinessError("needs_clarification", "两个用户名必须非空且互不相同。", code="distinct_usernames_required")
    if not all(account["account_name"].strip() for account in accounts):
        raise BusinessError("needs_clarification", "两个账本显示名都必须填写。", code="account_names_required")
    admin_key = admin_username.strip().casefold() if admin_username else None
    if admin_key and admin_key not in {name.casefold() for name in names}:
        raise BusinessError("needs_clarification", "管理员必须是这两位本机用户之一。", code="invalid_admin")
    maintenance_key = maintenance_username.strip().casefold() if maintenance_username else None
    if maintenance_key and maintenance_key not in {name.casefold() for name in names}:
        raise BusinessError("needs_clarification", "维护者必须是这两位本机用户之一，并须独立选择。", code="invalid_maintenance_user")

    created = []
    ledger_ids = []
    principals_by_name = {}
    with transaction(conn, write=True):
        if conn.execute("SELECT 1 FROM yuxu_principals LIMIT 1").fetchone():
            raise BusinessError("conflict", "本机账号已注册，请登录。", code="already_initialized")
        for account, username in zip(accounts, names, strict=True):
            password = account["password"]
            if not password:
                raise BusinessError("needs_clarification", "本机密码不能为空。", code="weak_password")
            principal_id = str(uuid.uuid4())
            ledger_id = str(uuid.uuid4())
            account_id = str(uuid.uuid4())
            salt = secrets.token_bytes(16)
            conn.execute(
                "INSERT INTO yuxu_principals(principal_id, username, password_salt, password_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (principal_id, username, salt, password_hash(password, salt), utc_now()),
            )
            account_name = account["account_name"].strip()
            conn.execute(
                "INSERT INTO yuxu_ledgers(ledger_id, account_id, account_name, created_at) VALUES (?, ?, ?, ?)",
                (ledger_id, account_id, account_name, utc_now()),
            )
            conn.execute(
                "INSERT INTO yuxu_access(principal_id, ledger_id, scope) VALUES (?, ?, 'own_read')",
                (principal_id, ledger_id),
            )
            conn.execute(
                "INSERT INTO yuxu_access(principal_id, ledger_id, scope) VALUES (?, ?, 'own_write')",
                (principal_id, ledger_id),
            )
            registered = {"username": username, "account_id": account_id, "account_name": account_name}
            if create_initial_tokens:
                host_token = create_host_token(conn, principal_id, "initial-mcp", list(DEFAULT_HOST_SCOPES))
                registered.update(mcp_token=host_token["token"], host_token_id=host_token["token_id"], host_scopes=host_token["scopes"])
            seed_categories(conn, ledger_id)
            ledger_ids.append((principal_id, ledger_id))
            principals_by_name[username.casefold()] = principal_id
            created.append(registered)

        if admin_key:
            admin_principal = principals_by_name[admin_key]
            other_ledger = next(ledger_id for principal_id, ledger_id in ledger_ids if principal_id != admin_principal)
            conn.execute(
                "INSERT INTO yuxu_access(principal_id, ledger_id, scope) VALUES (?, ?, 'admin_spending')",
                (admin_principal, other_ledger),
            )
            conn.execute(
                "INSERT INTO yuxu_access_extensions(principal_id, ledger_id, scope) VALUES (?, ?, 'admin_goal')",
                (admin_principal, other_ledger),
            )
        if maintenance_key:
            maintenance_principal = principals_by_name[maintenance_key]
            own_ledger = next(ledger_id for principal_id, ledger_id in ledger_ids if principal_id == maintenance_principal)
            conn.execute(
                "INSERT INTO yuxu_access_extensions(principal_id, ledger_id, scope) VALUES (?, ?, 'maintenance')",
                (maintenance_principal, own_ledger),
            )
    return created


def issue_session(conn: sqlite3.Connection, principal_id: str) -> tuple[str, str, float]:
    raw_session = secrets.token_urlsafe(32)
    raw_csrf = secrets.token_urlsafe(32)
    conversation_id = str(uuid.uuid4())
    expires_at = time.time() + SESSION_TTL_SECONDS
    conn.execute(
        "INSERT INTO yuxu_sessions(session_hash, csrf_token, principal_id, conversation_id, expires_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (hash_secret(raw_session), raw_csrf, principal_id, conversation_id, expires_at, utc_now()),
    )
    return raw_session, raw_csrf, expires_at


def session_info(conn: sqlite3.Connection, raw_session: str) -> dict | None:
    session_hash = hash_secret(raw_session)
    row = conn.execute(
        "SELECT s.session_hash, s.csrf_token, s.principal_id, s.conversation_id, s.expires_at, "
        "p.username, l.ledger_id, l.account_id, l.account_name "
        "FROM yuxu_sessions s "
        "JOIN yuxu_principals p ON p.principal_id = s.principal_id "
        "JOIN yuxu_access a ON a.principal_id = p.principal_id AND a.scope = 'own_read' "
        "JOIN yuxu_ledgers l ON l.ledger_id = a.ledger_id "
        "WHERE s.session_hash = ? ORDER BY l.created_at LIMIT 1",
        (session_hash,),
    ).fetchone()
    if row is None:
        return None
    if row["expires_at"] <= time.time():
        conn.execute("DELETE FROM yuxu_sessions WHERE session_hash = ?", (session_hash,))
        return None
    return dict(row)


def csrf_matches(conn: sqlite3.Connection, raw_session: str, csrf_token: str) -> bool:
    row = conn.execute(
        "SELECT csrf_token, expires_at FROM yuxu_sessions WHERE session_hash = ?",
        (hash_secret(raw_session),),
    ).fetchone()
    return bool(row and row["expires_at"] > time.time() and hmac.compare_digest(csrf_token, row["csrf_token"]))


def accounts_for_principal(conn: sqlite3.Connection, principal_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT l.account_id, l.account_name, a.scope FROM yuxu_access a "
        "JOIN yuxu_ledgers l ON l.ledger_id = a.ledger_id "
        "WHERE a.principal_id = ? AND a.scope IN ('own_read', 'admin_spending') "
        "UNION ALL "
        "SELECT l.account_id, l.account_name, x.scope FROM yuxu_access_extensions x "
        "JOIN yuxu_ledgers l ON l.ledger_id = x.ledger_id "
        "WHERE x.principal_id = ? AND x.scope = 'admin_goal' "
        "ORDER BY account_name, scope",
        (principal_id, principal_id),
    ).fetchall()
    return [dict(row) for row in rows]


def verified_access_accounts(conn: sqlite3.Connection, principal_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT l.account_id, l.account_name, a.scope FROM yuxu_access a "
        "JOIN yuxu_ledgers l ON l.ledger_id = a.ledger_id "
        "WHERE a.principal_id = ? "
        "UNION ALL "
        "SELECT l.account_id, l.account_name, x.scope FROM yuxu_access_extensions x "
        "JOIN yuxu_ledgers l ON l.ledger_id = x.ledger_id "
        "WHERE x.principal_id = ? "
        "ORDER BY account_name, account_id, scope",
        (principal_id, principal_id),
    ).fetchall()
    accounts = {}
    for row in rows:
        account = accounts.setdefault(row["account_id"], {
            "account_id": row["account_id"],
            "account_name": row["account_name"],
            "scopes": [],
        })
        account["scopes"].append(row["scope"])
    return list(accounts.values())


def account_access_policy(conn: sqlite3.Connection, principal_id: str, ledger_id: str) -> dict:
    own = conn.execute(
        "SELECT account_name FROM yuxu_ledgers WHERE ledger_id = ?", (ledger_id,)
    ).fetchone()
    inbound = conn.execute(
        "SELECT p.username AS display_name, a.scope FROM yuxu_access a "
        "JOIN yuxu_principals p ON p.principal_id = a.principal_id "
        "WHERE a.ledger_id = ? AND a.principal_id <> ? AND a.scope = 'admin_spending' "
        "UNION ALL "
        "SELECT p.username AS display_name, x.scope FROM yuxu_access_extensions x "
        "JOIN yuxu_principals p ON p.principal_id = x.principal_id "
        "WHERE x.ledger_id = ? AND x.principal_id <> ? AND x.scope = 'admin_goal' "
        "ORDER BY display_name, scope",
        (ledger_id, principal_id, ledger_id, principal_id),
    ).fetchall()
    outbound = conn.execute(
        "SELECT l.account_name AS display_name, a.scope FROM yuxu_access a "
        "JOIN yuxu_ledgers l ON l.ledger_id = a.ledger_id "
        "WHERE a.principal_id = ? AND a.ledger_id <> ? AND a.scope = 'admin_spending' "
        "UNION ALL "
        "SELECT l.account_name AS display_name, x.scope FROM yuxu_access_extensions x "
        "JOIN yuxu_ledgers l ON l.ledger_id = x.ledger_id "
        "WHERE x.principal_id = ? AND x.ledger_id <> ? AND x.scope = 'admin_goal' "
        "ORDER BY display_name, scope",
        (principal_id, ledger_id, principal_id, ledger_id),
    ).fetchall()

    def entries(rows: list[sqlite3.Row]) -> list[dict]:
        values: dict[str, list[str]] = {}
        for row in rows:
            values.setdefault(row["display_name"], []).append(row["scope"])
        return [{"display_name": name, "scopes": scopes} for name, scopes in values.items()]

    return {
        "account": {"display_name": own["account_name"], "scopes": sorted(access_scopes(conn, principal_id, ledger_id))},
        "incoming": entries(inbound),
        "outgoing": entries(outbound),
    }


def revoke_session(conn: sqlite3.Connection, raw_session: str) -> None:
    conn.execute("DELETE FROM yuxu_sessions WHERE session_hash = ?", (hash_secret(raw_session),))


def resolve_api_token(conn: sqlite3.Connection, raw_token: str) -> dict | None:
    row = conn.execute(
        "SELECT t.token_id, t.label, t.scopes_json, t.version AS token_version, "
        "t.principal_id, p.username, l.ledger_id, l.account_id, l.account_name "
        "FROM yuxu_api_tokens t "
        "JOIN yuxu_principals p ON p.principal_id = t.principal_id "
        "JOIN yuxu_access a ON a.principal_id = p.principal_id AND a.scope = 'own_read' "
        "JOIN yuxu_ledgers l ON l.ledger_id = a.ledger_id "
        "WHERE t.token_hash = ? AND t.revoked_at IS NULL ORDER BY l.created_at LIMIT 1",
        (hash_secret(raw_token),),
    ).fetchone()
    if row is None:
        return None
    actor = dict(row)
    actor["host_scopes"] = json.loads(actor.pop("scopes_json"))
    return actor


def create_host_token(conn: sqlite3.Connection, principal_id: str, label: str, scopes: list[str]) -> dict:
    raw_token = secrets.token_urlsafe(32)
    token_id = str(uuid.uuid4())
    created_at = utc_now()
    conn.execute(
        "INSERT INTO yuxu_api_tokens(token_hash, token_id, principal_id, label, scopes_json, version, created_at) "
        "VALUES (?, ?, ?, ?, ?, 1, ?)",
        (hash_secret(raw_token), token_id, principal_id, label, json.dumps(scopes), created_at),
    )
    return {
        "token_id": token_id, "label": label, "scopes": scopes, "version": 1,
        "created_at": created_at, "token": raw_token,
    }


def rotate_host_token(conn: sqlite3.Connection, principal_id: str, token_id: str) -> dict:
    row = conn.execute(
        "SELECT token_id, label, scopes_json, version FROM yuxu_api_tokens "
        "WHERE token_id = ? AND principal_id = ? AND revoked_at IS NULL",
        (token_id, principal_id),
    ).fetchone()
    if row is None:
        raise BusinessError("not_found", "MCP 宿主令牌不存在或已撤销。", code="host_token_not_found")
    now = utc_now()
    raw_token = secrets.token_urlsafe(32)
    new_token_id = str(uuid.uuid4())
    scopes = json.loads(row["scopes_json"])
    conn.execute(
        "UPDATE yuxu_api_tokens SET revoked_at = ?, version = version + 1 "
        "WHERE token_id = ? AND principal_id = ? AND revoked_at IS NULL",
        (now, row["token_id"], principal_id),
    )
    conn.execute(
        "INSERT INTO yuxu_api_tokens(token_hash, token_id, principal_id, label, scopes_json, version, created_at) "
        "VALUES (?, ?, ?, ?, ?, 1, ?)",
        (hash_secret(raw_token), new_token_id, principal_id, row["label"], row["scopes_json"], now),
    )
    return {
        "token_id": new_token_id, "replaced_token_id": row["token_id"], "label": row["label"],
        "scopes": scopes, "version": 1, "created_at": now, "token": raw_token,
    }


def access_scopes(conn: sqlite3.Connection, principal_id: str, ledger_id: str) -> set[str]:
    rows = conn.execute(
        "SELECT scope FROM yuxu_access WHERE principal_id = ? AND ledger_id = ?",
        (principal_id, ledger_id),
    ).fetchall()
    extended = conn.execute(
        "SELECT scope FROM yuxu_access_extensions WHERE principal_id = ? AND ledger_id = ?",
        (principal_id, ledger_id),
    ).fetchall()
    return {row["scope"] for row in rows + extended}


def grant_maintenance(conn: sqlite3.Connection, username: str) -> dict:
    username = username.strip()
    principal = conn.execute(
        "SELECT principal_id, username FROM yuxu_principals WHERE username = ? COLLATE NOCASE",
        (username,),
    ).fetchone()
    if principal is None:
        raise BusinessError("not_found", "本机用户名不存在。", code="maintenance_user_not_found")
    own_ledger = conn.execute(
        "SELECT ledger_id FROM yuxu_access WHERE principal_id = ? AND scope = 'own_read' ORDER BY ledger_id LIMIT 1",
        (principal["principal_id"],),
    ).fetchone()
    if own_ledger is None:
        raise BusinessError("forbidden", "维护者必须是本机账本用户。", code="maintenance_user_not_local")
    with transaction(conn, write=True):
        conn.execute("DELETE FROM yuxu_access_extensions WHERE scope = 'maintenance'")
        conn.execute(
            "INSERT INTO yuxu_access_extensions(principal_id, ledger_id, scope) VALUES (?, ?, 'maintenance')",
            (principal["principal_id"], own_ledger["ledger_id"]),
        )
    return {"username": principal["username"], "scope": "maintenance"}


def ledger_by_account_id(conn: sqlite3.Connection, account_id: str) -> dict | None:
    row = conn.execute(
        "SELECT ledger_id, account_id, account_name FROM yuxu_ledgers WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    return dict(row) if row else None


def receipt(conn: sqlite3.Connection, principal_id: str, ledger_id: str, request_id: str) -> dict | None:
    row = conn.execute(
        "SELECT action_name, params_hash, result_json, created_at FROM yuxu_operation_receipts "
        "WHERE principal_id = ? AND ledger_id = ? AND request_id = ?",
        (principal_id, ledger_id, request_id),
    ).fetchone()
    if row is None:
        return None
    return {**dict(row), "result": json.loads(row["result_json"])}


def save_receipt(
    conn: sqlite3.Connection,
    principal_id: str,
    ledger_id: str,
    request_id: str,
    action_name: str,
    params_hash: str,
    result: dict,
) -> None:
    conn.execute(
        "INSERT INTO yuxu_operation_receipts "
        "(principal_id, ledger_id, request_id, action_name, params_hash, result_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (principal_id, ledger_id, request_id, action_name, params_hash, json.dumps(result, ensure_ascii=False, sort_keys=True), utc_now()),
    )
