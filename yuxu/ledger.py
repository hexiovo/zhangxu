from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import sqlite3
import unicodedata
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, DivisionByZero, InvalidOperation

from .contract import BusinessError


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ledger_categories (
    ledger_id TEXT NOT NULL,
    category_id TEXT NOT NULL,
    parent_id TEXT,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    PRIMARY KEY (ledger_id, category_id),
    FOREIGN KEY (ledger_id, parent_id) REFERENCES ledger_categories(ledger_id, category_id)
);

CREATE TABLE IF NOT EXISTS ledger_tags (
    ledger_id TEXT NOT NULL,
    tag_id TEXT NOT NULL,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    PRIMARY KEY (ledger_id, tag_id),
    UNIQUE (ledger_id, name_key)
);

CREATE TABLE IF NOT EXISTS ledger_transactions (
    ledger_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    occurred_on TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('income', 'expense')),
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
    estimated INTEGER NOT NULL DEFAULT 0 CHECK (estimated IN (0, 1)),
    confirmation_status TEXT NOT NULL DEFAULT 'confirmed' CHECK (confirmation_status IN ('confirmed', 'pending')),
    classification_status TEXT NOT NULL DEFAULT 'unclassified' CHECK (classification_status IN ('classified', 'unclassified')),
    category_id TEXT,
    merchant TEXT,
    note TEXT,
    created_by TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    voided_at TEXT,
    void_reason TEXT,
    PRIMARY KEY (ledger_id, transaction_id),
    FOREIGN KEY (ledger_id, category_id) REFERENCES ledger_categories(ledger_id, category_id)
);

CREATE TABLE IF NOT EXISTS ledger_transaction_tags (
    ledger_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    tag_id TEXT NOT NULL,
    PRIMARY KEY (ledger_id, transaction_id, tag_id),
    FOREIGN KEY (ledger_id, transaction_id) REFERENCES ledger_transactions(ledger_id, transaction_id),
    FOREIGN KEY (ledger_id, tag_id) REFERENCES ledger_tags(ledger_id, tag_id)
);

CREATE TABLE IF NOT EXISTS ledger_transaction_revisions (
    ledger_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    action TEXT NOT NULL,
    request_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, revision_id),
    UNIQUE (ledger_id, transaction_id, version),
    FOREIGN KEY (ledger_id, transaction_id) REFERENCES ledger_transactions(ledger_id, transaction_id)
);

CREATE TABLE IF NOT EXISTS ledger_operation_effects (
    ledger_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    action TEXT NOT NULL,
    effects_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    undone_by_request_id TEXT,
    PRIMARY KEY (ledger_id, actor_id, request_id)
);

CREATE TABLE IF NOT EXISTS ledger_conversation_references (
    ledger_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    transaction_ids_json TEXT NOT NULL,
    request_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, principal_id, conversation_id)
);

CREATE INDEX IF NOT EXISTS idx_ledger_transactions_period
    ON ledger_transactions(ledger_id, occurred_on, created_at, transaction_id);
CREATE INDEX IF NOT EXISTS idx_ledger_transactions_category
    ON ledger_transactions(ledger_id, category_id, occurred_on);
CREATE INDEX IF NOT EXISTS idx_ledger_transaction_tags_tag
    ON ledger_transaction_tags(ledger_id, tag_id, transaction_id);
CREATE INDEX IF NOT EXISTS idx_ledger_revisions_transaction
    ON ledger_transaction_revisions(ledger_id, transaction_id, version);

CREATE TABLE IF NOT EXISTS ledger_transaction_dates (
    ledger_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    charged_on TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, transaction_id),
    FOREIGN KEY (ledger_id, transaction_id) REFERENCES ledger_transactions(ledger_id, transaction_id)
);

CREATE TABLE IF NOT EXISTS ledger_transaction_splits (
    ledger_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    split_id TEXT NOT NULL,
    category_id TEXT NOT NULL,
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
    position INTEGER NOT NULL,
    PRIMARY KEY (ledger_id, transaction_id, split_id),
    FOREIGN KEY (ledger_id, transaction_id) REFERENCES ledger_transactions(ledger_id, transaction_id),
    FOREIGN KEY (ledger_id, category_id) REFERENCES ledger_categories(ledger_id, category_id)
);

CREATE TABLE IF NOT EXISTS ledger_transaction_templates (
    ledger_id TEXT NOT NULL,
    template_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    defaults_json TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, template_id),
    UNIQUE (ledger_id, principal_id, name_key)
);

CREATE TABLE IF NOT EXISTS ledger_transaction_drafts (
    ledger_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    draft_id TEXT NOT NULL,
    fields_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'committed')),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    committed_request_id TEXT, copied_from_transaction_id TEXT,
    PRIMARY KEY (ledger_id, principal_id, draft_id)
);

CREATE TABLE IF NOT EXISTS ledger_merchant_aliases (
    ledger_id TEXT NOT NULL,
    alias_id TEXT NOT NULL,
    alias_name TEXT NOT NULL,
    alias_key TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    canonical_key TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, alias_id),
    UNIQUE (ledger_id, alias_key)
);

CREATE TABLE IF NOT EXISTS ledger_transaction_merge_links (
    ledger_id TEXT NOT NULL,
    source_transaction_id TEXT NOT NULL,
    target_transaction_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, source_transaction_id),
    FOREIGN KEY (ledger_id, source_transaction_id) REFERENCES ledger_transactions(ledger_id, transaction_id),
    FOREIGN KEY (ledger_id, target_transaction_id) REFERENCES ledger_transactions(ledger_id, transaction_id)
);

CREATE TABLE IF NOT EXISTS ledger_classification_rules (
    ledger_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    name TEXT NOT NULL,
    conditions_json TEXT NOT NULL,
    category_id TEXT NOT NULL,
    priority INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, principal_id, rule_id)
);

CREATE TABLE IF NOT EXISTS ledger_transaction_rule_matches (
    ledger_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    PRIMARY KEY (ledger_id, transaction_id),
    FOREIGN KEY (ledger_id, transaction_id) REFERENCES ledger_transactions(ledger_id, transaction_id)
);

CREATE TABLE IF NOT EXISTS ledger_change_previews (
    ledger_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    preview_id TEXT NOT NULL,
    action TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_by_request_id TEXT,
    PRIMARY KEY (ledger_id, principal_id, preview_id)
);

CREATE TABLE IF NOT EXISTS ledger_saved_filters (
    ledger_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    filter_id TEXT NOT NULL,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    filters_json TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, principal_id, filter_id),
    UNIQUE (ledger_id, principal_id, name_key)
);

CREATE TABLE IF NOT EXISTS ledger_search_sessions (
    ledger_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    filters_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, principal_id, conversation_id)
);

CREATE INDEX IF NOT EXISTS idx_ledger_drafts_status
    ON ledger_transaction_drafts(ledger_id, principal_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_ledger_aliases_canonical
    ON ledger_merchant_aliases(ledger_id, canonical_key);
CREATE INDEX IF NOT EXISTS idx_ledger_rules_priority
    ON ledger_classification_rules(ledger_id, principal_id, active, priority DESC);
CREATE INDEX IF NOT EXISTS idx_ledger_splits_category
    ON ledger_transaction_splits(ledger_id, category_id, transaction_id);
"""


_CATEGORY_SEEDS = (
    ("income", "收入", None),
    ("income_salary", "工资", "income"),
    ("income_other", "其他收入", "income"),
    ("food", "食宿", None),
    ("food_dining", "餐饮", "food"),
    ("food_lodging", "住宿", "food"),
    ("transport", "交通", None),
    ("transport_metro", "地铁", "transport"),
    ("transport_bus", "公交", "transport"),
    ("transport_taxi", "打车", "transport"),
    ("home", "家居", None),
    ("home_daily", "日用品", "home"),
    ("shopping", "购物", None),
    ("shopping_clothing", "服饰", "shopping"),
    ("medical", "医疗", None),
    ("medical_care", "诊疗", "medical"),
    ("entertainment", "娱乐", None),
    ("entertainment_leisure", "休闲", "entertainment"),
    ("education", "教育", None),
    ("education_learning", "学习", "education"),
    ("other", "其他", None),
    ("other_misc", "其他支出", "other"),
)


def seed_categories(conn: sqlite3.Connection, ledger_id: str) -> None:
    paths: dict[str, str] = {}
    rows = []
    for category_id, name, parent_id in _CATEGORY_SEEDS:
        path = f"{paths[parent_id]}／{name}" if parent_id else name
        paths[category_id] = path
        rows.append((ledger_id, category_id, parent_id, name, path, 1))
    conn.executemany(
        "INSERT OR IGNORE INTO ledger_categories(ledger_id, category_id, parent_id, name, path, active) VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )


def _fail(status: str, message: str, details: dict | None = None) -> None:
    raise BusinessError(status, message, details)


def _assert_period_open(conn: sqlite3.Connection, ctx: dict, *business_dates: str, action_name: str) -> None:
    from .planning import assert_period_open

    for business_date in dict.fromkeys(business_dates):
        assert_period_open(conn, ctx, business_date, action_name=action_name)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _new_id() -> str:
    return str(uuid.uuid4())


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail("invalid_input", f"{field} must be a non-empty string", {"field": field})
    return value.strip()


def _date_value(value: object, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        _fail("invalid_input", f"{field} must use YYYY-MM-DD", {"field": field})
    try:
        date.fromisoformat(value)
    except ValueError:
        _fail("invalid_input", f"{field} is not a valid date", {"field": field})
    return value


def _amount(value: object, field: str = "amount_minor") -> int:
    if type(value) is not int or value <= 0:
        _fail("needs_clarification", "A positive amount in integer fen is required", {"field": field})
    return value


def _require_scope(ctx: dict, own_only: bool = False, write: bool = False) -> None:
    if own_only and ctx["cross_account"]:
        _fail("forbidden", "This action is available only in the caller's own ledger")
    if write and ctx["cross_account"]:
        _fail("forbidden", "Cross-account writes are not allowed")
    if write and not ctx["request_id"]:
        _fail("invalid_input", "A request_id is required for writes")


def _ledger_info(ctx: dict) -> dict:
    return {
        "ledger_id": ctx["ledger_id"],
        "account_id": ctx["account_id"],
        "account_name": ctx["account_name"],
    }


def _category_path(conn: sqlite3.Connection, ledger_id: str, category_id: str | None) -> str | None:
    if category_id is None:
        return None
    row = conn.execute(
        "SELECT path FROM ledger_categories WHERE ledger_id = ? AND category_id = ?",
        (ledger_id, category_id),
    ).fetchone()
    if row is None:
        _fail("not_found", "Category not found in this ledger", {"category_id": category_id})
    return row["path"]


def _tag_key(name: str) -> str:
    return unicodedata.normalize("NFKC", name.strip()).casefold()


def _merchant_key(name: str) -> str:
    return unicodedata.normalize("NFKC", name.strip()).casefold()


def _canonical_merchant(conn: sqlite3.Connection, ledger_id: str, name: str) -> tuple[str, str]:
    key = _merchant_key(name)
    row = conn.execute(
        "SELECT canonical_key, canonical_name FROM ledger_merchant_aliases WHERE ledger_id = ? AND alias_key = ?",
        (ledger_id, key),
    ).fetchone()
    return (row["canonical_key"], row["canonical_name"]) if row else (key, name.strip())


def _charged_on(conn: sqlite3.Connection, ledger_id: str, transaction_id: str) -> str | None:
    row = conn.execute(
        "SELECT charged_on FROM ledger_transaction_dates WHERE ledger_id = ? AND transaction_id = ?",
        (ledger_id, transaction_id),
    ).fetchone()
    return row["charged_on"] if row else None


def _set_charged_on(conn: sqlite3.Connection, ledger_id: str, transaction_id: str, value: str | None) -> None:
    if value is None:
        conn.execute(
            "DELETE FROM ledger_transaction_dates WHERE ledger_id = ? AND transaction_id = ?",
            (ledger_id, transaction_id),
        )
        return
    conn.execute(
        "INSERT INTO ledger_transaction_dates(ledger_id, transaction_id, charged_on, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(ledger_id, transaction_id) DO UPDATE SET charged_on = excluded.charged_on, updated_at = excluded.updated_at",
        (ledger_id, transaction_id, value, _now()),
    )


def _splits_for(conn: sqlite3.Connection, ledger_id: str, transaction_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT s.split_id, s.category_id, c.path AS category_path, s.amount_minor, s.position "
        "FROM ledger_transaction_splits AS s LEFT JOIN ledger_categories AS c "
        "ON c.ledger_id = s.ledger_id AND c.category_id = s.category_id "
        "WHERE s.ledger_id = ? AND s.transaction_id = ? ORDER BY s.position, s.split_id",
        (ledger_id, transaction_id),
    ).fetchall()
    return [
        {"split_id": row["split_id"], "category_id": row["category_id"], "category_path": row["category_path"], "amount_minor": row["amount_minor"], "position": row["position"]}
        for row in rows
    ]


def _set_splits(conn: sqlite3.Connection, ledger_id: str, transaction_id: str, splits: list[dict]) -> None:
    conn.execute(
        "DELETE FROM ledger_transaction_splits WHERE ledger_id = ? AND transaction_id = ?",
        (ledger_id, transaction_id),
    )
    for position, split in enumerate(splits):
        conn.execute(
            "INSERT INTO ledger_transaction_splits(ledger_id, transaction_id, split_id, category_id, amount_minor, position) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ledger_id, transaction_id, split["split_id"], split["category_id"], split["amount_minor"], position),
        )


def _merchant_matches(conn: sqlite3.Connection, ledger_id: str, actual: str | None, expected: str) -> bool:
    if not actual:
        return False
    return _canonical_merchant(conn, ledger_id, actual)[0] == _canonical_merchant(conn, ledger_id, expected)[0]


def _rule_matches(conn: sqlite3.Connection, ledger_id: str, conditions: dict, record: dict) -> bool:
    if "merchant" in conditions and not _merchant_matches(conn, ledger_id, record["merchant"], conditions["merchant"]):
        return False
    if "keyword" in conditions:
        text = conditions["keyword"].casefold()
        if text not in (record["merchant"] or "").casefold() and text not in (record["note"] or "").casefold():
            return False
    if "amount_min_minor" in conditions and record["amount_minor"] < conditions["amount_min_minor"]:
        return False
    if "amount_max_minor" in conditions and record["amount_minor"] > conditions["amount_max_minor"]:
        return False
    return True


def _matching_rule(conn: sqlite3.Connection, ctx: dict, record: dict) -> tuple[str, str] | None:
    rows = conn.execute(
        "SELECT rule_id, conditions_json, category_id FROM ledger_classification_rules "
        "WHERE ledger_id = ? AND principal_id = ? AND active = 1 ORDER BY priority DESC, created_at, rule_id",
        (ctx["ledger_id"], ctx["principal_id"]),
    ).fetchall()
    for row in rows:
        conditions = json.loads(row["conditions_json"])
        if _rule_matches(conn, ctx["ledger_id"], conditions, record):
            _category_path(conn, ctx["ledger_id"], row["category_id"])
            return row["category_id"], row["rule_id"]
    return None


def _tag_names(value: object, field: str = "tags") -> list[str]:
    if not isinstance(value, list):
        _fail("invalid_input", f"{field} must be an array of tag names", {"field": field})
    names = []
    seen = set()
    for item in value:
        name = _required_text(item, field)
        key = _tag_key(name)
        if key not in seen:
            names.append(name)
            seen.add(key)
    return names


def _ensure_tag(conn: sqlite3.Connection, ledger_id: str, name: str) -> tuple[dict, bool]:
    key = _tag_key(name)
    row = conn.execute(
        "SELECT tag_id, name FROM ledger_tags WHERE ledger_id = ? AND name_key = ?",
        (ledger_id, key),
    ).fetchone()
    if row is not None:
        return dict(row), False
    tag_id = _new_id()
    conn.execute(
        "INSERT INTO ledger_tags(ledger_id, tag_id, name, name_key) VALUES (?, ?, ?, ?)",
        (ledger_id, tag_id, name, key),
    )
    return {"tag_id": tag_id, "name": name}, True


def _set_tags(conn: sqlite3.Connection, ledger_id: str, transaction_id: str, names: list[str]) -> None:
    conn.execute(
        "DELETE FROM ledger_transaction_tags WHERE ledger_id = ? AND transaction_id = ?",
        (ledger_id, transaction_id),
    )
    for name in names:
        tag, _ = _ensure_tag(conn, ledger_id, name)
        conn.execute(
            "INSERT OR IGNORE INTO ledger_transaction_tags(ledger_id, transaction_id, tag_id) VALUES (?, ?, ?)",
            (ledger_id, transaction_id, tag["tag_id"]),
        )


def _tags_for(conn: sqlite3.Connection, ledger_id: str, transaction_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT tag.name FROM ledger_transaction_tags AS link "
        "JOIN ledger_tags AS tag ON tag.ledger_id = link.ledger_id AND tag.tag_id = link.tag_id "
        "WHERE link.ledger_id = ? AND link.transaction_id = ? ORDER BY tag.name_key",
        (ledger_id, transaction_id),
    ).fetchall()
    return [row["name"] for row in rows]


def _get_transaction(conn: sqlite3.Connection, ledger_id: str, transaction_id: str) -> sqlite3.Row:
    transaction_id = _required_text(transaction_id, "transaction_id")
    row = conn.execute(
        "SELECT * FROM ledger_transactions WHERE ledger_id = ? AND transaction_id = ?",
        (ledger_id, transaction_id),
    ).fetchone()
    if row is None:
        _fail("not_found", "Transaction not found in this ledger", {"transaction_id": transaction_id})
    return row


def _transaction_payload(conn: sqlite3.Connection, ledger_id: str, transaction_id: str) -> dict:
    row = _get_transaction(conn, ledger_id, transaction_id)
    merchant_canonical = _canonical_merchant(conn, ledger_id, row["merchant"])[1] if row["merchant"] else None
    merge = conn.execute(
        "SELECT target_transaction_id FROM ledger_transaction_merge_links WHERE ledger_id = ? AND source_transaction_id = ?",
        (ledger_id, transaction_id),
    ).fetchone()
    rule = conn.execute(
        "SELECT rule_id FROM ledger_transaction_rule_matches WHERE ledger_id = ? AND transaction_id = ?",
        (ledger_id, transaction_id),
    ).fetchone()
    return {
        "transaction_id": row["transaction_id"],
        "version": row["version"],
        "occurred_on": row["occurred_on"],
        "kind": row["kind"],
        "amount_minor": row["amount_minor"],
        "estimated": bool(row["estimated"]),
        "confirmation_status": row["confirmation_status"],
        "classification_status": row["classification_status"],
        "category_id": row["category_id"],
        "category_path": _category_path(conn, ledger_id, row["category_id"]),
        "tags": _tags_for(conn, ledger_id, transaction_id),
        "merchant": row["merchant"],
        "merchant_canonical": merchant_canonical,
        "note": row["note"],
        "charged_on": _charged_on(conn, ledger_id, transaction_id),
        "splits": _splits_for(conn, ledger_id, transaction_id),
        "classification_rule_id": rule["rule_id"] if rule else None,
        "merged_into_transaction_id": merge["target_transaction_id"] if merge else None,
        "voided": row["voided_at"] is not None,
        "voided_at": row["voided_at"],
        "void_reason": row["void_reason"],
    }


def _snapshot(conn: sqlite3.Connection, ledger_id: str, transaction_id: str) -> dict:
    row = _get_transaction(conn, ledger_id, transaction_id)
    rule = conn.execute(
        "SELECT rule_id FROM ledger_transaction_rule_matches WHERE ledger_id = ? AND transaction_id = ?",
        (ledger_id, transaction_id),
    ).fetchone()
    return {
        "occurred_on": row["occurred_on"],
        "kind": row["kind"],
        "amount_minor": row["amount_minor"],
        "estimated": bool(row["estimated"]),
        "confirmation_status": row["confirmation_status"],
        "classification_status": row["classification_status"],
        "category_id": row["category_id"],
        "merchant": row["merchant"],
        "note": row["note"],
        "voided_at": row["voided_at"],
        "void_reason": row["void_reason"],
        "tags": _tags_for(conn, ledger_id, transaction_id),
        "charged_on": _charged_on(conn, ledger_id, transaction_id),
        "splits": _splits_for(conn, ledger_id, transaction_id),
        "classification_rule_id": rule["rule_id"] if rule else None,
    }


def _record_revision(
    conn: sqlite3.Connection,
    ctx: dict,
    transaction_id: str,
    version: int,
    action: str,
    before: dict | None,
    after: dict,
) -> str:
    revision_id = _new_id()
    conn.execute(
        "INSERT INTO ledger_transaction_revisions"
        "(ledger_id, revision_id, transaction_id, version, action, request_id, actor_id, before_json, after_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ctx["ledger_id"],
            revision_id,
            transaction_id,
            version,
            action,
            ctx["request_id"],
            ctx["principal_id"],
            json.dumps(before, ensure_ascii=False, sort_keys=True) if before is not None else None,
            json.dumps(after, ensure_ascii=False, sort_keys=True),
            _now(),
        ),
    )
    return revision_id


def _record_operation(conn: sqlite3.Connection, ctx: dict, action: str, effects: list[dict]) -> None:
    conn.execute(
        "INSERT INTO ledger_operation_effects"
        "(ledger_id, actor_id, request_id, action, effects_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            ctx["ledger_id"],
            ctx["principal_id"],
            ctx["request_id"],
            action,
            json.dumps(effects, ensure_ascii=False, sort_keys=True),
            _now(),
        ),
    )


def _record_reference(conn: sqlite3.Connection, ctx: dict, transaction_ids: list[str]) -> None:
    conversation_id = ctx["conversation_id"]
    if not conversation_id:
        return
    conn.execute(
        "INSERT INTO ledger_conversation_references"
        "(ledger_id, principal_id, conversation_id, transaction_ids_json, request_id, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(ledger_id, principal_id, conversation_id) DO UPDATE SET "
        "transaction_ids_json = excluded.transaction_ids_json, request_id = excluded.request_id, updated_at = excluded.updated_at",
        (
            ctx["ledger_id"],
            ctx["principal_id"],
            conversation_id,
            json.dumps(transaction_ids),
            ctx["request_id"],
            _now(),
        ),
    )


def _clean_optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        _fail("invalid_input", f"{field} must be text or null", {"field": field})
    value = value.strip()
    return value or None


def _validate_category(conn: sqlite3.Connection, ctx: dict, category_id: object) -> str | None:
    if category_id is None:
        return None
    if not isinstance(category_id, str) or not category_id.strip():
        _fail("invalid_input", "category_id must be a category ID or null", {"field": "category_id"})
    row = conn.execute(
        "SELECT active FROM ledger_categories WHERE ledger_id = ? AND category_id = ?",
        (ctx["ledger_id"], category_id),
    ).fetchone()
    if row is None or not row["active"]:
        _fail("not_found", "Category not found or inactive in this ledger", {"category_id": category_id})
    return category_id


def _record_input(conn: sqlite3.Connection, ctx: dict, item: dict) -> dict:
    if not isinstance(item, dict):
        _fail("needs_clarification", "Each transaction needs a kind and amount")
    item = dict(item)
    template_id = item.pop("template_id", None)
    if template_id is not None:
        row = conn.execute(
            "SELECT defaults_json FROM ledger_transaction_templates WHERE ledger_id = ? AND principal_id = ? AND template_id = ?",
            (ctx["ledger_id"], ctx["principal_id"], _required_text(template_id, "template_id")),
        ).fetchone()
        if row is None:
            _fail("not_found", "Transaction template not found", {"template_id": template_id})
        item = {**json.loads(row["defaults_json"]), **item}
    unknown = set(item) - {"kind", "amount_minor", "occurred_on", "charged_on", "estimated", "confirmation_status", "category_id", "tags", "merchant", "note"}
    if unknown:
        _fail("invalid_input", "Unknown transaction field", {"fields": sorted(unknown)})
    kind = item.get("kind")
    if kind not in ("income", "expense"):
        _fail("needs_clarification", "Transaction direction must be income or expense", {"field": "kind"})
    amount_minor = _amount(item.get("amount_minor"))
    occurred_on = _date_value(item.get("occurred_on", ctx["today"]), "occurred_on")
    estimated = item.get("estimated", False)
    if not isinstance(estimated, bool):
        _fail("invalid_input", "estimated must be boolean", {"field": "estimated"})
    confirmation_status = item.get("confirmation_status", "confirmed")
    if confirmation_status not in ("confirmed", "pending"):
        _fail("invalid_input", "confirmation_status must be confirmed or pending")
    category_id = _validate_category(conn, ctx, item.get("category_id"))
    tags = _tag_names(item.get("tags", []))
    record = {
        "occurred_on": occurred_on,
        "charged_on": _date_value(item["charged_on"], "charged_on") if "charged_on" in item and item["charged_on"] is not None else None,
        "kind": kind,
        "amount_minor": amount_minor,
        "estimated": estimated,
        "confirmation_status": confirmation_status,
        "classification_status": "classified" if category_id else "unclassified",
        "category_id": category_id,
        "tags": tags,
        "merchant": _clean_optional_text(item.get("merchant"), "merchant"),
        "note": _clean_optional_text(item.get("note"), "note"),
    }
    if category_id is None:
        match = _matching_rule(conn, ctx, record)
        if match:
            record["category_id"], record["classification_rule_id"] = match
            record["classification_status"] = "classified"
    return record


def _insert_transaction(conn: sqlite3.Connection, ctx: dict, record: dict, transaction_id: str) -> str:
    now = _now()
    conn.execute(
        "INSERT INTO ledger_transactions"
        "(ledger_id, transaction_id, occurred_on, kind, amount_minor, estimated, confirmation_status, classification_status, "
        "category_id, merchant, note, created_by, source, created_at, updated_at, version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
        (
            ctx["ledger_id"],
            transaction_id,
            record["occurred_on"],
            record["kind"],
            record["amount_minor"],
            int(record["estimated"]),
            record["confirmation_status"],
            record["classification_status"],
            record["category_id"],
            record["merchant"],
            record["note"],
            ctx["principal_id"],
            ctx["source"],
            now,
            now,
        ),
    )
    _set_tags(conn, ctx["ledger_id"], transaction_id, record["tags"])
    _set_charged_on(conn, ctx["ledger_id"], transaction_id, record["charged_on"])
    if record.get("classification_rule_id"):
        conn.execute(
            "INSERT INTO ledger_transaction_rule_matches(ledger_id, transaction_id, rule_id) VALUES (?, ?, ?)",
            (ctx["ledger_id"], transaction_id, record["classification_rule_id"]),
        )
    return transaction_id


def _duplicate_candidates(conn: sqlite3.Connection, ctx: dict, record: dict) -> list[dict]:
    rows = conn.execute(
        "SELECT t.transaction_id, t.occurred_on, t.kind, t.amount_minor, t.category_id, c.path AS category_path "
        "FROM ledger_transactions AS t LEFT JOIN ledger_categories AS c "
        "ON c.ledger_id = t.ledger_id AND c.category_id = t.category_id "
        "WHERE t.ledger_id = ? AND t.occurred_on = ? AND t.kind = ? AND t.amount_minor = ? "
        "AND t.category_id IS ? AND t.voided_at IS NULL "
        "ORDER BY t.created_at DESC, t.transaction_id DESC LIMIT 3",
        (ctx["ledger_id"], record["occurred_on"], record["kind"], record["amount_minor"], record["category_id"]),
    ).fetchall()
    return [
        {
            "transaction_id": row["transaction_id"],
            "occurred_on": row["occurred_on"],
            "kind": row["kind"],
            "amount_minor": row["amount_minor"],
            "category_id": row["category_id"],
            "category_path": row["category_path"],
        }
        for row in rows
    ]


def _add_transactions(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, write=True)
    items = params.get("transactions")
    if not isinstance(items, list) or not items:
        _fail("needs_clarification", "At least one complete transaction is required")
    if len(items) > 100:
        _fail("invalid_input", "A single batch can contain at most 100 transactions")
    records = [_record_input(conn, ctx, item) for item in items]
    _assert_period_open(conn, ctx, *(value for record in records for value in (record["occurred_on"], record["charged_on"]) if value),
                        action_name="add_transactions")
    transaction_ids = [_new_id() for _ in records]
    warnings = []
    for index, (transaction_id, record) in enumerate(zip(transaction_ids, records)):
        candidates = _duplicate_candidates(conn, ctx, record)
        for other_index, (other_id, other) in enumerate(zip(transaction_ids, records)):
            if other_index == index:
                continue
            if (other["occurred_on"], other["kind"], other["amount_minor"], other["category_id"]) == (
                record["occurred_on"], record["kind"], record["amount_minor"], record["category_id"]
            ):
                candidates.append(
                    {
                        "transaction_id": other_id,
                        "occurred_on": other["occurred_on"],
                        "kind": other["kind"],
                        "amount_minor": other["amount_minor"],
                        "category_id": other["category_id"],
                        "category_path": _category_path(conn, ctx["ledger_id"], other["category_id"]),
                    }
                )
        if candidates:
            warnings.append(
                {
                    "code": "possible_duplicate",
                    "transaction_id": transaction_id,
                    "message": "A similar transaction exists; this entry was recorded separately.",
                    "candidates": candidates[:3],
                }
            )
    effects = []
    for record, transaction_id in zip(records, transaction_ids):
        _insert_transaction(conn, ctx, record, transaction_id)
        snapshot = _snapshot(conn, ctx["ledger_id"], transaction_id)
        _record_revision(conn, ctx, transaction_id, 1, "create", None, snapshot)
        effects.append({"transaction_id": transaction_id, "before": None, "after": snapshot, "after_version": 1})
    _record_operation(conn, ctx, "add_transactions", effects)
    _record_reference(conn, ctx, transaction_ids)
    transactions = [_transaction_payload(conn, ctx["ledger_id"], transaction_id) for transaction_id in transaction_ids]
    return {"transactions": transactions, "transaction_ids": transaction_ids, "warnings": warnings, "ledger": _ledger_info(ctx)}


def _get_transaction_action(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, own_only=True)
    transaction = _transaction_payload(conn, ctx["ledger_id"], params.get("transaction_id"))
    return {"transaction": transaction, "ledger": _ledger_info(ctx)}


def _check_version(row: sqlite3.Row, expected_version: object) -> int:
    if type(expected_version) is not int or expected_version < 1:
        _fail("invalid_input", "expected_version must be a positive integer")
    if row["version"] != expected_version:
        _fail(
            "conflict",
            "The transaction changed after it was read",
            {"transaction_id": row["transaction_id"], "expected_version": expected_version, "actual_version": row["version"]},
        )
    if row["voided_at"] is not None:
        _fail("conflict", "A voided transaction cannot be changed", {"transaction_id": row["transaction_id"]})
    return row["version"] + 1


def _apply_patch(
    conn: sqlite3.Connection,
    ctx: dict,
    transaction_id: str,
    patch: dict,
    action: str,
    operation_effects: list[dict] | None = None,
) -> tuple[dict, str]:
    row = _get_transaction(conn, ctx["ledger_id"], transaction_id)
    new_version = _check_version(row, patch["expected_version"])
    changes = patch["patch"]
    if not isinstance(changes, dict) or not changes:
        _fail("needs_clarification", "At least one transaction field must be supplied")
    unknown = set(changes) - {"occurred_on", "charged_on", "kind", "amount_minor", "estimated", "confirmation_status", "category_id", "tags", "merchant", "note"}
    if unknown:
        _fail("invalid_input", "Unknown transaction patch field", {"fields": sorted(unknown)})
    before = _snapshot(conn, ctx["ledger_id"], transaction_id)
    if before["splits"] and ("amount_minor" in changes or "category_id" in changes):
        _fail("needs_clarification", "Update the split allocation with set_transaction_splits before changing its amount or category")
    values = {}
    if "occurred_on" in changes:
        values["occurred_on"] = _date_value(changes["occurred_on"], "occurred_on")
    charged_on = _date_value(changes["charged_on"], "charged_on") if changes.get("charged_on") is not None else None
    if "kind" in changes:
        if changes["kind"] not in ("income", "expense"):
            _fail("invalid_input", "kind must be income or expense")
        values["kind"] = changes["kind"]
    if "amount_minor" in changes:
        values["amount_minor"] = _amount(changes["amount_minor"])
    if "estimated" in changes:
        if not isinstance(changes["estimated"], bool):
            _fail("invalid_input", "estimated must be boolean")
        values["estimated"] = int(changes["estimated"])
    if "confirmation_status" in changes:
        status = changes["confirmation_status"]
        if status not in ("confirmed", "pending"):
            _fail("invalid_input", "confirmation_status must be confirmed or pending")
        values["confirmation_status"] = status
    if "category_id" in changes:
        category_id = _validate_category(conn, ctx, changes["category_id"])
        values["category_id"] = category_id
        values["classification_status"] = "classified" if category_id else "unclassified"
    if "merchant" in changes:
        values["merchant"] = _clean_optional_text(changes["merchant"], "merchant")
    if "note" in changes:
        values["note"] = _clean_optional_text(changes["note"], "note")
    _assert_period_open(conn, ctx, row["occurred_on"], values.get("occurred_on", row["occurred_on"]), action_name=action)
    set_fields = [f"{field} = ?" for field in values]
    update = conn.execute(
        "UPDATE ledger_transactions SET " + ", ".join(set_fields + ["version = ?", "updated_at = ?"]) +
        " WHERE ledger_id = ? AND transaction_id = ? AND version = ? AND voided_at IS NULL",
        list(values.values()) + [new_version, _now(), ctx["ledger_id"], transaction_id, patch["expected_version"]],
    )
    if update.rowcount != 1:
        current = _get_transaction(conn, ctx["ledger_id"], transaction_id)
        _fail(
            "conflict",
            "The transaction changed after it was read",
            {"transaction_id": transaction_id, "expected_version": patch["expected_version"], "actual_version": current["version"]},
        )
    if "charged_on" in changes:
        _set_charged_on(conn, ctx["ledger_id"], transaction_id, charged_on)
    if "tags" in changes:
        _set_tags(conn, ctx["ledger_id"], transaction_id, _tag_names(changes["tags"]))
    if "category_id" in changes:
        conn.execute(
            "DELETE FROM ledger_transaction_rule_matches WHERE ledger_id = ? AND transaction_id = ?",
            (ctx["ledger_id"], transaction_id),
        )
    after = _snapshot(conn, ctx["ledger_id"], transaction_id)
    revision_id = _record_revision(conn, ctx, transaction_id, new_version, action, before, after)
    effect = {"transaction_id": transaction_id, "before": before, "after": after, "after_version": new_version}
    if operation_effects is None:
        _record_operation(conn, ctx, action, [effect])
    else:
        operation_effects.append(effect)
    _record_reference(conn, ctx, [transaction_id])
    return {"transaction": _transaction_payload(conn, ctx["ledger_id"], transaction_id), "revision_id": revision_id, "version": new_version}, transaction_id


def _update_transaction(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, write=True)
    result, _ = _apply_patch(
        conn,
        ctx,
        params.get("transaction_id"),
        {"expected_version": params.get("expected_version"), "patch": params.get("patch")},
        "update_transaction",
    )
    result["ledger"] = _ledger_info(ctx)
    return result


def _void_transaction(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, write=True)
    transaction_id = _required_text(params.get("transaction_id"), "transaction_id")
    row = _get_transaction(conn, ctx["ledger_id"], transaction_id)
    version = _check_version(row, params.get("expected_version"))
    before = _snapshot(conn, ctx["ledger_id"], transaction_id)
    reason = _clean_optional_text(params.get("reason"), "reason")
    _assert_period_open(conn, ctx, row["occurred_on"], action_name="void_transaction")
    now = _now()
    update = conn.execute(
        "UPDATE ledger_transactions SET voided_at = ?, void_reason = ?, version = ?, updated_at = ? "
        "WHERE ledger_id = ? AND transaction_id = ? AND version = ? AND voided_at IS NULL",
        (now, reason, version, now, ctx["ledger_id"], transaction_id, params["expected_version"]),
    )
    if update.rowcount != 1:
        current = _get_transaction(conn, ctx["ledger_id"], transaction_id)
        _fail("conflict", "The transaction changed after it was read", {"transaction_id": transaction_id, "actual_version": current["version"]})
    after = _snapshot(conn, ctx["ledger_id"], transaction_id)
    revision_id = _record_revision(conn, ctx, transaction_id, version, "void_transaction", before, after)
    _record_operation(
        conn,
        ctx,
        "void_transaction",
        [{"transaction_id": transaction_id, "before": before, "after": after, "after_version": version}],
    )
    _record_reference(conn, ctx, [transaction_id])
    return {"transaction_id": transaction_id, "version": version, "voided": True, "revision_id": revision_id, "ledger": _ledger_info(ctx)}


def _undo_operation(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, write=True)
    target_request_id = _required_text(params.get("target_request_id"), "target_request_id")
    operation = conn.execute(
        "SELECT * FROM ledger_operation_effects WHERE ledger_id = ? AND actor_id = ? AND request_id = ?",
        (ctx["ledger_id"], ctx["principal_id"], target_request_id),
    ).fetchone()
    if operation is None:
        from . import movements

        result = movements.undo_movement(conn, ctx, target_request_id)
        if result is not None:
            return result
        _fail("not_found", "The original write request was not found in this ledger", {"target_request_id": target_request_id})
    if operation["undone_by_request_id"] is not None:
        _fail("conflict", "This operation has already been undone", {"target_request_id": target_request_id})
    effects = json.loads(operation["effects_json"])
    plans = []
    for effect in effects:
        row = _get_transaction(conn, ctx["ledger_id"], effect["transaction_id"])
        if row["version"] != effect["after_version"]:
            _fail(
                "conflict",
                "A transaction from this operation changed and cannot be safely undone",
                {"transaction_id": row["transaction_id"], "expected_version": effect["after_version"], "actual_version": row["version"]},
            )
        _assert_period_open(conn, ctx, row["occurred_on"], effect["before"]["occurred_on"] if effect["before"] else row["occurred_on"], action_name="undo_operation")
        plans.append((effect, _snapshot(conn, ctx["ledger_id"], row["transaction_id"]), row["version"], row["version"] + 1))
    result_effects = []
    transaction_ids = []
    for effect, current, expected_version, next_version in plans:
        transaction_id = effect["transaction_id"]
        before = effect["before"]
        if before is None:
            after = dict(current)
            after["voided_at"] = _now()
            after["void_reason"] = f"Undone via {ctx['request_id']}"
            action = "undo_create"
        else:
            after = before
            action = "undo"
        update = conn.execute(
            "UPDATE ledger_transactions SET occurred_on = ?, kind = ?, amount_minor = ?, estimated = ?, confirmation_status = ?, "
            "classification_status = ?, category_id = ?, merchant = ?, note = ?, voided_at = ?, void_reason = ?, version = ?, updated_at = ? "
            "WHERE ledger_id = ? AND transaction_id = ? AND version = ?",
            (
                after["occurred_on"],
                after["kind"],
                after["amount_minor"],
                int(after["estimated"]),
                after["confirmation_status"],
                after["classification_status"],
                after["category_id"],
                after["merchant"],
                after["note"],
                after["voided_at"],
                after["void_reason"],
                next_version,
                _now(),
                ctx["ledger_id"],
                transaction_id,
                expected_version,
            ),
        )
        if update.rowcount != 1:
            current_row = _get_transaction(conn, ctx["ledger_id"], transaction_id)
            _fail("conflict", "A transaction changed before undo could be applied", {"transaction_id": transaction_id, "actual_version": current_row["version"]})
        _set_tags(conn, ctx["ledger_id"], transaction_id, after["tags"])
        _set_charged_on(conn, ctx["ledger_id"], transaction_id, after.get("charged_on"))
        _set_splits(conn, ctx["ledger_id"], transaction_id, after.get("splits", []))
        conn.execute(
            "DELETE FROM ledger_transaction_rule_matches WHERE ledger_id = ? AND transaction_id = ?",
            (ctx["ledger_id"], transaction_id),
        )
        if after.get("classification_rule_id"):
            conn.execute(
                "INSERT INTO ledger_transaction_rule_matches(ledger_id, transaction_id, rule_id) VALUES (?, ?, ?)",
                (ctx["ledger_id"], transaction_id, after["classification_rule_id"]),
            )
        current_snapshot = current
        restored = _snapshot(conn, ctx["ledger_id"], transaction_id)
        revision_id = _record_revision(conn, ctx, transaction_id, next_version, action, current_snapshot, restored)
        result_effects.append({"transaction_id": transaction_id, "version": next_version, "action": action, "revision_id": revision_id})
        transaction_ids.append(transaction_id)
    conn.execute(
        "UPDATE ledger_operation_effects SET undone_by_request_id = ? WHERE ledger_id = ? AND actor_id = ? AND request_id = ?",
        (ctx["request_id"], ctx["ledger_id"], ctx["principal_id"], target_request_id),
    )
    from . import movements

    movement_result = movements.undo_movement(conn, ctx, target_request_id, include_payments=False)
    if movement_result:
        result_effects.extend(movement_result["effects"])
    _record_reference(conn, ctx, transaction_ids)
    return {"target_request_id": target_request_id, "effects": result_effects, "ledger": _ledger_info(ctx)}


def _resolve_reference(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, own_only=True)
    conversation_id = ctx["conversation_id"]
    if not conversation_id:
        _fail("needs_clarification", "A trusted conversation ID is required to resolve a recent reference")
    reference = params.get("reference")
    if reference not in ("last_transaction", "last_operation"):
        _fail("invalid_input", "reference must be last_transaction or last_operation")
    row = conn.execute(
        "SELECT transaction_ids_json, request_id FROM ledger_conversation_references "
        "WHERE ledger_id = ? AND principal_id = ? AND conversation_id = ?",
        (ctx["ledger_id"], ctx["principal_id"], conversation_id),
    ).fetchone()
    if row is None:
        return {"status": "not_found", "transactions": [], "candidates": [], "ledger": _ledger_info(ctx)}
    transaction_ids = json.loads(row["transaction_ids_json"])
    if reference == "last_transaction" and len(transaction_ids) != 1:
        candidates = [_transaction_payload(conn, ctx["ledger_id"], transaction_id) for transaction_id in transaction_ids[:5]]
        return {"status": "ambiguous", "transactions": [], "candidates": candidates, "target_request_id": row["request_id"], "ledger": _ledger_info(ctx)}
    transactions = [_transaction_payload(conn, ctx["ledger_id"], transaction_id) for transaction_id in transaction_ids]
    return {"status": "resolved", "transactions": transactions, "candidates": [], "target_request_id": row["request_id"], "ledger": _ledger_info(ctx)}


def _create_category_or_tag(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, write=True)
    item_type = params.get("type")
    if item_type not in ("category", "tag"):
        _fail("invalid_input", "type must be category or tag", {"field": "type"})
    name = _required_text(params.get("name"), "name")
    if not name.isprintable():
        _fail("invalid_input", "name must contain visible text", {"field": "name"})
    ledger_id = ctx["ledger_id"]
    if item_type == "tag":
        if "parent_category_id" in params:
            _fail("invalid_input", "Tags have no parent category", {"field": "parent_category_id"})
        tag, created = _ensure_tag(conn, ledger_id, name)
        return {"type": "tag", "created": created, "tag": tag, "ledger": _ledger_info(ctx)}

    if "／" in name:
        _fail("invalid_input", "Provide a category name and pass its parent separately", {"field": "name"})
    parent_id = None
    path = name
    if "parent_category_id" in params:
        parent_id = _required_text(params["parent_category_id"], "parent_category_id")
        parent = conn.execute(
            "SELECT parent_id, path FROM ledger_categories WHERE ledger_id = ? AND category_id = ? AND active = 1",
            (ledger_id, parent_id),
        ).fetchone()
        if parent is None:
            _fail("not_found", "Parent category not found or inactive in this ledger", {"parent_category_id": parent_id})
        if parent["parent_id"] is not None:
            _fail("invalid_input", "A subcategory must have a top-level parent", {"parent_category_id": parent_id})
        path = f"{parent['path']}／{name}"

    siblings = conn.execute(
        "SELECT category_id, parent_id, name, path, active FROM ledger_categories WHERE ledger_id = ? AND parent_id IS ?",
        (ledger_id, parent_id),
    )
    key = _tag_key(name)
    existing = next((row for row in siblings if _tag_key(row["name"]) == key), None)
    if existing is not None:
        if not existing["active"]:
            _fail("conflict", "This category name belongs to an inactive category", {"category_id": existing["category_id"]})
        category = {**dict(existing), "active": True}
        return {"type": "category", "created": False, "category": category, "ledger": _ledger_info(ctx)}

    category_id = _new_id()
    conn.execute(
        "INSERT INTO ledger_categories(ledger_id, category_id, parent_id, name, path, active) VALUES (?, ?, ?, ?, ?, 1)",
        (ledger_id, category_id, parent_id, name, path),
    )
    category = {"category_id": category_id, "parent_id": parent_id, "name": name, "path": path, "active": True}
    return {"type": "category", "created": True, "category": category, "ledger": _ledger_info(ctx)}


def _list_categories(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, own_only=True)
    include_inactive = params.get("include_inactive", False)
    if not isinstance(include_inactive, bool):
        _fail("invalid_input", "include_inactive must be boolean")
    clause = "" if include_inactive else " AND active = 1"
    rows = conn.execute(
        "SELECT category_id, parent_id, name, path, active FROM ledger_categories WHERE ledger_id = ?" + clause + " ORDER BY path",
        (ctx["ledger_id"],),
    ).fetchall()
    return {
        "categories": [
            {"category_id": row["category_id"], "parent_id": row["parent_id"], "name": row["name"], "path": row["path"], "active": bool(row["active"])}
            for row in rows
        ],
        "ledger": _ledger_info(ctx),
    }


def _list_tags(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, own_only=True)
    limit = params.get("limit", 100)
    if type(limit) is not int or not 1 <= limit <= 100:
        _fail("invalid_input", "limit must be from 1 to 100")
    query = _clean_optional_text(params.get("query"), "query")
    sql = "SELECT tag.tag_id, tag.name, COUNT(link.transaction_id) AS usage_count FROM ledger_tags AS tag "
    sql += "LEFT JOIN ledger_transaction_tags AS link ON link.ledger_id = tag.ledger_id AND link.tag_id = tag.tag_id WHERE tag.ledger_id = ?"
    values = [ctx["ledger_id"]]
    if query:
        sql += " AND tag.name_key LIKE ?"
        values.append("%" + _tag_key(query) + "%")
    sql += " GROUP BY tag.tag_id, tag.name, tag.name_key ORDER BY tag.name_key LIMIT ?"
    values.append(limit)
    rows = conn.execute(sql, values).fetchall()
    return {"tags": [{"tag_id": row["tag_id"], "name": row["name"], "usage_count": row["usage_count"]} for row in rows], "ledger": _ledger_info(ctx)}


def _normalize_filters(filters: object, allowed: set[str]) -> dict:
    if not isinstance(filters, dict):
        _fail("invalid_input", "filters must be an object")
    unknown = set(filters) - allowed
    if unknown:
        _fail("invalid_input", "Unknown filter field", {"fields": sorted(unknown)})
    return filters


_SEARCH_FILTERS = {
    "start_date", "end_date", "kind", "category_id", "tag", "keyword", "merchant",
    "note_keyword", "amount_min_minor", "amount_max_minor", "project_id",
    "exclude_category_id", "exclude_tag", "exclude_keyword", "exclude_merchant",
}
_REPORT_FILTERS = {
    "kind", "category_id", "tag", "keyword", "merchant", "note_keyword", "amount_min_minor", "amount_max_minor", "project_id",
    "exclude_category_id", "exclude_tag", "exclude_keyword", "exclude_merchant",
}
_CROSS_ACCOUNT_PRIVATE_FILTERS = {"tag", "keyword", "merchant", "note_keyword", "exclude_tag", "exclude_keyword", "exclude_merchant"}
_DATE_BASES = {"occurred_on", "charged_on"}


def _merchant_filter_names(conn: sqlite3.Connection, ledger_id: str, query: str) -> list[str]:
    key = _merchant_key(query)
    alias = conn.execute(
        "SELECT canonical_key FROM ledger_merchant_aliases WHERE ledger_id = ? AND alias_key = ?",
        (ledger_id, key),
    ).fetchone()
    canonical_key = alias["canonical_key"] if alias else key
    rows = conn.execute(
        "SELECT alias_name, canonical_name FROM ledger_merchant_aliases WHERE ledger_id = ? AND canonical_key = ?",
        (ledger_id, canonical_key),
    ).fetchall()
    names = {query.casefold()}
    for row in rows:
        names.add(row["alias_name"].casefold())
        names.add(row["canonical_name"].casefold())
    return sorted(names)


def _filter_sql(
    conn: sqlite3.Connection,
    ctx: dict,
    filters_value: object,
    *,
    report: bool = False,
    start_date: str | None = None,
    end_date: str | None = None,
    include_voided: bool = False,
    date_basis: str = "occurred_on",
) -> tuple[str, list]:
    filters = _normalize_filters(filters_value, _REPORT_FILTERS if report else _SEARCH_FILTERS)
    if date_basis not in _DATE_BASES:
        _fail("invalid_input", "date_basis must be occurred_on or charged_on")
    cross_account = ctx["cross_account"]
    if cross_account and any(filters.get(key) not in (None, "", []) for key in _CROSS_ACCOUNT_PRIVATE_FILTERS):
        _fail("forbidden", "Cross-account reports cannot filter on tags, merchant, or private notes")
    clauses = ["t.ledger_id = ?"]
    values: list = [ctx["ledger_id"]]
    date_column = "t.occurred_on" if date_basis == "occurred_on" else (
        "COALESCE((SELECT ledger_date.charged_on FROM ledger_transaction_dates AS ledger_date "
        "WHERE ledger_date.ledger_id = t.ledger_id AND ledger_date.transaction_id = t.transaction_id), t.occurred_on)"
    )
    if not include_voided:
        clauses.append("t.voided_at IS NULL")
    clauses.append(
        "NOT EXISTS (SELECT 1 FROM ledger_transaction_merge_links AS merged "
        "WHERE merged.ledger_id = t.ledger_id AND merged.source_transaction_id = t.transaction_id)"
    )
    if start_date is not None:
        clauses.append(date_column + " >= ?")
        values.append(start_date)
    if end_date is not None:
        clauses.append(date_column + " <= ?")
        values.append(end_date)
    for field, operator in (("start_date", ">="), ("end_date", "<=")):
        if field in filters:
            value = _date_value(filters[field], field)
            clauses.append(date_column + f" {operator} ?")
            values.append(value)
    kind = filters.get("kind")
    if cross_account:
        if kind not in (None, "expense"):
            _fail("forbidden", "Cross-account access is limited to spending")
        clauses.append("t.kind = 'expense'")
    elif kind is not None:
        if kind not in ("income", "expense"):
            _fail("invalid_input", "kind must be income or expense")
        clauses.append("t.kind = ?")
        values.append(kind)
    category_id = filters.get("category_id")
    if category_id is not None:
        category_id = _required_text(category_id, "category_id")
        _category_path(conn, ctx["ledger_id"], category_id)
        clauses.append(
            "EXISTS (SELECT 1 FROM ledger_categories AS selected WHERE selected.ledger_id = t.ledger_id AND selected.category_id = ? "
            "AND (t.category_id = selected.category_id OR EXISTS (SELECT 1 FROM ledger_categories AS child "
            "WHERE child.ledger_id = t.ledger_id AND child.category_id = t.category_id AND child.parent_id = selected.category_id) "
            "OR EXISTS (SELECT 1 FROM ledger_transaction_splits AS split JOIN ledger_categories AS split_category "
            "ON split_category.ledger_id = split.ledger_id AND split_category.category_id = split.category_id "
            "WHERE split.ledger_id = t.ledger_id AND split.transaction_id = t.transaction_id "
            "AND (split_category.category_id = selected.category_id OR split_category.parent_id = selected.category_id))))"
        )
        values.append(category_id)
    exclude_category_id = filters.get("exclude_category_id")
    if exclude_category_id is not None:
        exclude_category_id = _required_text(exclude_category_id, "exclude_category_id")
        _category_path(conn, ctx["ledger_id"], exclude_category_id)
        clauses.append(
            "NOT EXISTS (SELECT 1 FROM ledger_categories AS selected WHERE selected.ledger_id = t.ledger_id AND selected.category_id = ? "
            "AND (t.category_id = selected.category_id OR EXISTS (SELECT 1 FROM ledger_categories AS child "
            "WHERE child.ledger_id = t.ledger_id AND child.category_id = t.category_id AND child.parent_id = selected.category_id) "
            "OR EXISTS (SELECT 1 FROM ledger_transaction_splits AS split JOIN ledger_categories AS split_category "
            "ON split_category.ledger_id = split.ledger_id AND split_category.category_id = split.category_id "
            "WHERE split.ledger_id = t.ledger_id AND split.transaction_id = t.transaction_id "
            "AND (split_category.category_id = selected.category_id OR split_category.parent_id = selected.category_id))))"
        )
        values.append(exclude_category_id)
    if filters.get("tag") is not None:
        tag_key = _tag_key(_required_text(filters["tag"], "tag"))
        clauses.append(
            "EXISTS (SELECT 1 FROM ledger_transaction_tags AS link JOIN ledger_tags AS tag "
            "ON tag.ledger_id = link.ledger_id AND tag.tag_id = link.tag_id "
            "WHERE link.ledger_id = t.ledger_id AND link.transaction_id = t.transaction_id AND tag.name_key = ?)"
        )
        values.append(tag_key)
    if filters.get("exclude_tag") is not None:
        tag_key = _tag_key(_required_text(filters["exclude_tag"], "exclude_tag"))
        clauses.append(
            "NOT EXISTS (SELECT 1 FROM ledger_transaction_tags AS link JOIN ledger_tags AS tag "
            "ON tag.ledger_id = link.ledger_id AND tag.tag_id = link.tag_id "
            "WHERE link.ledger_id = t.ledger_id AND link.transaction_id = t.transaction_id AND tag.name_key = ?)"
        )
        values.append(tag_key)
    for field in ("keyword", "merchant", "note_keyword"):
        if filters.get(field) is not None:
            text = _required_text(filters[field], field).casefold()
            if field == "keyword":
                clauses.append("(lower(coalesce(t.merchant, '')) LIKE ? OR lower(coalesce(t.note, '')) LIKE ?)")
                values.extend(("%" + text + "%", "%" + text + "%"))
            elif field == "merchant":
                names = _merchant_filter_names(conn, ctx["ledger_id"], filters[field])
                placeholders = ",".join("?" for _ in names)
                clauses.append("(lower(coalesce(t.merchant, '')) LIKE ? OR lower(coalesce(t.merchant, '')) IN (" + placeholders + "))")
                values.extend(["%" + text + "%", *names])
            else:
                clauses.append("lower(coalesce(t.note, '')) LIKE ?")
                values.append("%" + text + "%")
    if filters.get("exclude_keyword") is not None:
        text = _required_text(filters["exclude_keyword"], "exclude_keyword").casefold()
        clauses.append("NOT (lower(coalesce(t.merchant, '')) LIKE ? OR lower(coalesce(t.note, '')) LIKE ?)")
        values.extend(("%" + text + "%", "%" + text + "%"))
    if filters.get("exclude_merchant") is not None:
        text = _required_text(filters["exclude_merchant"], "exclude_merchant").casefold()
        clauses.append("lower(coalesce(t.merchant, '')) NOT LIKE ?")
        values.append("%" + text + "%")
    for field, operator in (("amount_min_minor", ">="), ("amount_max_minor", "<=")):
        if filters.get(field) is not None:
            value = filters[field]
            if type(value) is not int or value < 0:
                _fail("invalid_input", f"{field} must be a non-negative integer")
            clauses.append(f"t.amount_minor {operator} ?")
            values.append(value)
    if filters.get("project_id") is not None:
        project_id = _required_text(filters["project_id"], "project_id")
        clauses.append(
            "EXISTS (SELECT 1 FROM project_entries AS project_entry "
            "WHERE project_entry.ledger_id = t.ledger_id AND project_entry.transaction_id = t.transaction_id AND project_entry.project_id = ?)"
        )
        values.append(project_id)
    return " AND ".join(clauses), values


def _cursor_hash(filters: dict, ctx: dict, date_basis: str) -> str:
    packed = json.dumps(
        {"filters": filters, "date_basis": date_basis, "ledger_id": ctx["ledger_id"], "principal_id": ctx["principal_id"]},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(packed).hexdigest()


def _encode_cursor(row: sqlite3.Row, filters: dict, ctx: dict, date_basis: str) -> str:
    payload = {
        "date": row["sort_date"],
        "created_at": row["created_at"],
        "transaction_id": row["transaction_id"],
        "filter_hash": _cursor_hash(filters, ctx, date_basis),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: object, filters: dict, ctx: dict, date_basis: str) -> dict:
    if not isinstance(cursor, str) or not cursor:
        _fail("invalid_input", "page.after must be an opaque cursor")
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error):
        _fail("invalid_input", "page.after is not a valid cursor")
    if not isinstance(payload, dict) or payload.get("filter_hash") != _cursor_hash(filters, ctx, date_basis):
        _fail("conflict", "The page cursor belongs to a different filter set")
    _date_value(payload.get("date"), "page.after")
    _required_text(payload.get("created_at"), "page.after")
    _required_text(payload.get("transaction_id"), "page.after")
    return payload


def _search_transactions(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, own_only=True)
    filters = _normalize_filters(params.get("filters", {}), _SEARCH_FILTERS)
    date_basis = params.get("date_basis", "occurred_on")
    if date_basis not in _DATE_BASES:
        _fail("invalid_input", "date_basis must be occurred_on or charged_on")
    clause, values = _filter_sql(conn, ctx, filters, date_basis=date_basis)
    totals = conn.execute(
        "SELECT COUNT(*) AS total_count, "
        "COALESCE(SUM(CASE WHEN t.kind = 'income' THEN t.amount_minor ELSE 0 END), 0) AS total_income_minor, "
        "COALESCE(SUM(CASE WHEN t.kind = 'expense' THEN t.amount_minor ELSE 0 END), 0) AS total_expense_minor "
        "FROM ledger_transactions AS t WHERE " + clause,
        values,
    ).fetchone()
    page = params.get("page", {})
    if not isinstance(page, dict):
        _fail("invalid_input", "page must be an object")
    limit = page.get("limit", 20)
    if type(limit) is not int or not 1 <= limit <= 100:
        _fail("invalid_input", "page.limit must be from 1 to 100")
    cursor = None
    if page.get("after"):
        cursor = _decode_cursor(page["after"], filters, ctx, date_basis)
    page_clause = clause
    page_values = list(values)
    date_column = "t.occurred_on" if date_basis == "occurred_on" else (
        "COALESCE((SELECT ledger_date.charged_on FROM ledger_transaction_dates AS ledger_date "
        "WHERE ledger_date.ledger_id = t.ledger_id AND ledger_date.transaction_id = t.transaction_id), t.occurred_on)"
    )
    if cursor:
        page_clause += f" AND ({date_column} < ? OR ({date_column} = ? AND t.created_at < ?) OR "
        page_clause += f"({date_column} = ? AND t.created_at = ? AND t.transaction_id < ?))"
        page_values.extend(
            (
                cursor["date"],
                cursor["date"],
                cursor["created_at"],
                cursor["date"],
                cursor["created_at"],
                cursor["transaction_id"],
            )
        )
    rows = conn.execute(
        "SELECT t.*, " + date_column + " AS sort_date FROM ledger_transactions AS t WHERE " + page_clause +
        " ORDER BY sort_date DESC, t.created_at DESC, t.transaction_id DESC LIMIT ?",
        page_values + [limit + 1],
    ).fetchall()
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    next_cursor = _encode_cursor(page_rows[-1], filters, ctx, date_basis) if has_more and page_rows else None
    transactions = [_transaction_payload(conn, ctx["ledger_id"], row["transaction_id"]) for row in page_rows]
    if ctx["conversation_id"]:
        conn.execute(
            "INSERT INTO ledger_search_sessions(ledger_id, principal_id, conversation_id, filters_json, updated_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(ledger_id, principal_id, conversation_id) DO UPDATE SET "
            "filters_json = excluded.filters_json, updated_at = excluded.updated_at",
            (
                ctx["ledger_id"], ctx["principal_id"], ctx["conversation_id"],
                json.dumps({"filters": filters, "date_basis": date_basis}, ensure_ascii=False, sort_keys=True), _now(),
            ),
        )
    return {
        "filters": filters,
        "date_basis": date_basis,
        "total_count": totals["total_count"],
        "total_income_minor": totals["total_income_minor"],
        "total_expense_minor": totals["total_expense_minor"],
        "total_amount_minor": totals["total_income_minor"] + totals["total_expense_minor"],
        "transactions": transactions,
        "next_cursor": next_cursor,
        "ledger": _ledger_info(ctx),
    }


def _apply_classification(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, write=True)
    transaction_id = _required_text(params.get("transaction_id"), "transaction_id")
    row = _get_transaction(conn, ctx["ledger_id"], transaction_id)
    new_version = _check_version(row, params.get("expected_version"))
    _assert_period_open(conn, ctx, row["occurred_on"], action_name="apply_classification")
    before = _snapshot(conn, ctx["ledger_id"], transaction_id)
    if before["splits"]:
        _fail("needs_clarification", "Set categories on each split with set_transaction_splits")
    category_id = _validate_category(conn, ctx, params.get("category_id"))
    update = conn.execute(
        "UPDATE ledger_transactions SET category_id = ?, classification_status = ?, version = ?, updated_at = ? "
        "WHERE ledger_id = ? AND transaction_id = ? AND version = ? AND voided_at IS NULL",
        (category_id, "classified" if category_id else "unclassified", new_version, _now(), ctx["ledger_id"], transaction_id, params["expected_version"]),
    )
    if update.rowcount != 1:
        current = _get_transaction(conn, ctx["ledger_id"], transaction_id)
        _fail("conflict", "The transaction changed after it was read", {"transaction_id": transaction_id, "actual_version": current["version"]})
    conn.execute(
        "DELETE FROM ledger_transaction_rule_matches WHERE ledger_id = ? AND transaction_id = ?",
        (ctx["ledger_id"], transaction_id),
    )
    names = list(before["tags"])
    remove = {_tag_key(name) for name in _tag_names(params.get("tags_remove", []), "tags_remove")}
    names = [name for name in names if _tag_key(name) not in remove]
    seen = {_tag_key(name) for name in names}
    for name in _tag_names(params.get("tags_add", []), "tags_add"):
        if _tag_key(name) not in seen:
            names.append(name)
            seen.add(_tag_key(name))
    _set_tags(conn, ctx["ledger_id"], transaction_id, names)
    after = _snapshot(conn, ctx["ledger_id"], transaction_id)
    revision_id = _record_revision(conn, ctx, transaction_id, new_version, "apply_classification", before, after)
    _record_operation(
        conn,
        ctx,
        "apply_classification",
        [{"transaction_id": transaction_id, "before": before, "after": after, "after_version": new_version}],
    )
    _record_reference(conn, ctx, [transaction_id])
    return {"transaction": _transaction_payload(conn, ctx["ledger_id"], transaction_id), "revision_id": revision_id, "version": new_version, "ledger": _ledger_info(ctx)}


def _period_values(params: dict) -> tuple[str, str]:
    start_date = _date_value(params.get("start_date"), "start_date")
    end_date = _date_value(params.get("end_date"), "end_date")
    if start_date > end_date:
        _fail("invalid_input", "start_date must be on or before end_date")
    return start_date, end_date


def _aggregate_period(
    conn: sqlite3.Connection,
    ctx: dict,
    start_date: str,
    end_date: str,
    filters: dict,
    date_basis: str = "occurred_on",
) -> dict:
    clause, values = _filter_sql(conn, ctx, filters, report=True, start_date=start_date, end_date=end_date, date_basis=date_basis)
    row = conn.execute(
        "SELECT COUNT(*) AS record_count, "
        "COALESCE(SUM(CASE WHEN t.confirmation_status = 'confirmed' AND t.kind = 'income' THEN t.amount_minor ELSE 0 END), 0) AS income_minor, "
        "COALESCE(SUM(CASE WHEN t.confirmation_status = 'confirmed' AND t.kind = 'expense' THEN t.amount_minor ELSE 0 END), 0) AS expense_minor, "
        "COALESCE(SUM(CASE WHEN t.confirmation_status = 'pending' AND t.kind = 'income' THEN t.amount_minor ELSE 0 END), 0) AS pending_income_minor, "
        "COALESCE(SUM(CASE WHEN t.confirmation_status = 'pending' AND t.kind = 'expense' THEN t.amount_minor ELSE 0 END), 0) AS pending_expense_minor, "
        "SUM(CASE WHEN t.estimated = 1 THEN 1 ELSE 0 END) AS estimated_count, "
        "COALESCE(SUM(CASE WHEN t.estimated = 1 THEN t.amount_minor ELSE 0 END), 0) AS estimated_minor "
        "FROM ledger_transactions AS t WHERE " + clause,
        values,
    ).fetchone()
    result = {
        "period": {"start_date": start_date, "end_date": end_date},
        "date_basis": date_basis,
        "expense_minor": row["expense_minor"],
        "record_count": row["record_count"],
        "empty": row["record_count"] == 0,
        "estimated_count": row["estimated_count"] or 0,
        "estimated_minor": row["estimated_minor"],
        "pending_count": 0,
        "pending_minor": row["pending_income_minor"] + row["pending_expense_minor"],
        "pending_expense_minor": row["pending_expense_minor"],
    }
    if not ctx["cross_account"]:
        result["pending_income_minor"] = row["pending_income_minor"]
    result["pending_count"] = conn.execute(
        "SELECT COUNT(*) AS count FROM ledger_transactions AS t WHERE " + clause + " AND t.confirmation_status = 'pending'",
        values,
    ).fetchone()["count"]
    if not ctx["cross_account"]:
        result["income_minor"] = row["income_minor"]
        result["net_minor"] = row["income_minor"] - row["expense_minor"]
    return result


def _get_period_summary(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    start_date, end_date = _period_values(params)
    filters = _normalize_filters(params.get("filters", {}), _REPORT_FILTERS)
    result = _aggregate_period(conn, ctx, start_date, end_date, filters, params.get("date_basis", "occurred_on"))
    result["filters"] = filters
    result["ledger"] = _ledger_info(ctx)
    return result


def _get_category_breakdown(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    start_date, end_date = _period_values(params)
    filters = _normalize_filters(params.get("filters", {}), _REPORT_FILTERS)
    date_basis = params.get("date_basis", "occurred_on")
    if date_basis not in _DATE_BASES:
        _fail("invalid_input", "date_basis must be occurred_on or charged_on")
    if filters.get("kind") not in (None, "expense"):
        _fail("invalid_input", "Category breakdowns are limited to expenses")
    report_filters = dict(filters)
    report_filters["kind"] = "expense"
    clause, values = _filter_sql(conn, ctx, report_filters, report=True, start_date=start_date, end_date=end_date, date_basis=date_basis)
    rows = conn.execute(
        "WITH eligible AS (SELECT t.* FROM ledger_transactions AS t WHERE " + clause + "), "
        "allocations AS ("
        "SELECT eligible.ledger_id, eligible.transaction_id, split.category_id, split.amount_minor, eligible.confirmation_status, eligible.estimated "
        "FROM eligible JOIN ledger_transaction_splits AS split ON split.ledger_id = eligible.ledger_id AND split.transaction_id = eligible.transaction_id "
        "UNION ALL "
        "SELECT eligible.ledger_id, eligible.transaction_id, eligible.category_id, eligible.amount_minor, eligible.confirmation_status, eligible.estimated "
        "FROM eligible WHERE NOT EXISTS (SELECT 1 FROM ledger_transaction_splits AS split "
        "WHERE split.ledger_id = eligible.ledger_id AND split.transaction_id = eligible.transaction_id)) "
        "SELECT allocations.category_id, COALESCE(category.path, '待归类') AS path, COUNT(*) AS record_count, "
        "COALESCE(SUM(CASE WHEN allocations.confirmation_status = 'confirmed' THEN allocations.amount_minor ELSE 0 END), 0) AS expense_minor, "
        "COALESCE(SUM(CASE WHEN allocations.confirmation_status = 'pending' THEN allocations.amount_minor ELSE 0 END), 0) AS pending_minor, "
        "SUM(CASE WHEN allocations.confirmation_status = 'pending' THEN 1 ELSE 0 END) AS pending_count, "
        "SUM(CASE WHEN allocations.estimated = 1 THEN 1 ELSE 0 END) AS estimated_count "
        "FROM allocations LEFT JOIN ledger_categories AS category "
        "ON category.ledger_id = allocations.ledger_id AND category.category_id = allocations.category_id "
        "GROUP BY allocations.category_id, category.path ORDER BY expense_minor DESC, path",
        values,
    ).fetchall()
    if report_filters.get("category_id"):
        selected_path = _category_path(conn, ctx["ledger_id"], report_filters["category_id"])
        rows = [row for row in rows if row["path"] == selected_path or row["path"].startswith(selected_path + "／")]
    total = sum(row["expense_minor"] for row in rows)
    items = [
        {
            "category_id": row["category_id"],
            "path": row["path"],
            "expense_minor": row["expense_minor"],
            "pending_minor": row["pending_minor"],
            "pending_count": row["pending_count"] or 0,
            "record_count": row["record_count"],
            "estimated_count": row["estimated_count"] or 0,
            "share": round(row["expense_minor"] * 100 / total, 2) if total else 0,
        }
        for row in rows
    ]
    return {
        "period": {"start_date": start_date, "end_date": end_date},
        "date_basis": date_basis,
        "items": items,
        "expense_minor": total,
        "record_count": sum(row["record_count"] for row in rows),
        "empty": not rows,
        "filters": report_filters,
        "ledger": _ledger_info(ctx),
    }


def _get_spending_comparison(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    filters = _normalize_filters(params.get("filters", {}), _REPORT_FILTERS)
    date_basis = params.get("date_basis", "occurred_on")
    if date_basis not in _DATE_BASES:
        _fail("invalid_input", "date_basis must be occurred_on or charged_on")
    if filters.get("kind") not in (None, "expense"):
        _fail("invalid_input", "Spending comparisons are limited to expenses")
    periods = params.get("periods")
    if not isinstance(periods, list) or len(periods) < 2:
        _fail("needs_clarification", "At least two explicit periods are required for comparison")
    results = []
    report_filters = dict(filters)
    report_filters["kind"] = "expense"
    for period in periods:
        if not isinstance(period, dict):
            _fail("invalid_input", "Each period must be an object")
        start_date, end_date = _period_values(period)
        stats = _aggregate_period(conn, ctx, start_date, end_date, report_filters, date_basis)
        results.append(
            {
                "start_date": start_date,
                "end_date": end_date,
                "label": _clean_optional_text(period.get("label"), "label") or f"{start_date}–{end_date}",
                "expense_minor": stats["expense_minor"],
                "record_count": stats["record_count"],
                "empty": stats["empty"],
                "estimated_count": stats["estimated_count"],
                "pending_count": stats["pending_count"],
                "pending_minor": stats["pending_expense_minor"],
                "date_basis": date_basis,
            }
        )
    return {"periods": results, "filters": report_filters, "ledger": _ledger_info(ctx)}


def _evaluate_amount_expression(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, own_only=True)
    expression = _required_text(params.get("expression"), "expression")
    normalized = expression.translate(str.maketrans({"＋": "+", "－": "-", "−": "-", "×": "*", "÷": "/", "（": "(", "）": ")"}))
    compact = re.sub(r"\s+", "", normalized)
    tokens = re.findall(r"\d+(?:\.\d{1,2})?|[()+\-*/]", compact)
    if not tokens or "".join(tokens) != compact:
        _fail("invalid_input", "expression contains unsupported characters or number formats")
    index = 0

    def factor() -> Decimal:
        nonlocal index
        if index >= len(tokens):
            _fail("invalid_input", "expression ends before an amount")
        token = tokens[index]
        if token in ("+", "-"):
            index += 1
            value = factor()
            return -value if token == "-" else value
        if token == "(":
            index += 1
            value = total()
            if index >= len(tokens) or tokens[index] != ")":
                _fail("invalid_input", "expression has an unmatched parenthesis")
            index += 1
            return value
        if token == ")":
            _fail("invalid_input", "expression has an unmatched parenthesis")
        index += 1
        try:
            return Decimal(token)
        except InvalidOperation:
            _fail("invalid_input", "expression contains an invalid number")

    def product() -> Decimal:
        nonlocal index
        value = factor()
        while index < len(tokens) and tokens[index] in ("*", "/"):
            operator = tokens[index]
            index += 1
            right = factor()
            if operator == "/" and right == 0:
                _fail("invalid_input", "expression cannot divide by zero")
            value = value * right if operator == "*" else value / right
        return value

    def total() -> Decimal:
        nonlocal index
        value = product()
        while index < len(tokens) and tokens[index] in ("+", "-"):
            operator = tokens[index]
            index += 1
            right = product()
            value = value + right if operator == "+" else value - right
        return value

    result = total()
    if index != len(tokens):
        _fail("invalid_input", "expression has an unexpected operator or parenthesis")
    try:
        amount = result.quantize(Decimal("0.01"))
    except (InvalidOperation, DivisionByZero):
        _fail("invalid_input", "expression result is outside the supported amount range")
    if result != amount:
        _fail("invalid_input", "expression does not resolve to an exact fen amount")
    if amount <= 0:
        _fail("needs_clarification", "A positive amount is required")
    amount_minor = int(amount * 100)
    return {
        "expression": expression,
        "normalized_expression": compact,
        "amount_minor": amount_minor,
        "amount_yuan": format(amount, ".2f"),
        "ledger": _ledger_info(ctx),
    }


def _template_defaults(conn: sqlite3.Connection, ctx: dict, value: object) -> dict:
    if not isinstance(value, dict):
        _fail("invalid_input", "defaults must be an object")
    allowed = {"kind", "category_id", "tags", "merchant", "note"}
    unknown = set(value) - allowed
    if unknown:
        _fail("invalid_input", "Unknown template default field", {"fields": sorted(unknown)})
    if value.get("kind") not in ("income", "expense"):
        _fail("needs_clarification", "A template must set income or expense")
    return {
        "kind": value["kind"],
        "category_id": _validate_category(conn, ctx, value.get("category_id")),
        "tags": _tag_names(value.get("tags", [])),
        "merchant": _clean_optional_text(value.get("merchant"), "merchant"),
        "note": _clean_optional_text(value.get("note"), "note"),
    }


def _upsert_transaction_template(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, write=True)
    name = _required_text(params.get("name"), "name")
    name_key = _tag_key(name)
    defaults = _template_defaults(conn, ctx, params.get("defaults"))
    template_id = params.get("template_id")
    expected_version = params.get("expected_version")
    if template_id is None:
        if expected_version is not None:
            _fail("invalid_input", "expected_version requires template_id")
        conflict = conn.execute(
            "SELECT template_id FROM ledger_transaction_templates WHERE ledger_id = ? AND principal_id = ? AND name_key = ?",
            (ctx["ledger_id"], ctx["principal_id"], name_key),
        ).fetchone()
        if conflict:
            _fail("conflict", "A template with this name already exists", {"template_id": conflict["template_id"]})
        template_id = _new_id()
        version = 1
        now = _now()
        conn.execute(
            "INSERT INTO ledger_transaction_templates(ledger_id, template_id, principal_id, name, name_key, defaults_json, version, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ctx["ledger_id"], template_id, ctx["principal_id"], name, name_key, json.dumps(defaults, ensure_ascii=False, sort_keys=True), version, now, now),
        )
    else:
        template_id = _required_text(template_id, "template_id")
        row = conn.execute(
            "SELECT * FROM ledger_transaction_templates WHERE ledger_id = ? AND principal_id = ? AND template_id = ?",
            (ctx["ledger_id"], ctx["principal_id"], template_id),
        ).fetchone()
        if row is None:
            _fail("not_found", "Transaction template not found", {"template_id": template_id})
        if type(expected_version) is not int or row["version"] != expected_version:
            _fail("conflict", "The template changed after it was read", {"template_id": template_id, "actual_version": row["version"]})
        conflict = conn.execute(
            "SELECT template_id FROM ledger_transaction_templates WHERE ledger_id = ? AND principal_id = ? AND name_key = ? AND template_id <> ?",
            (ctx["ledger_id"], ctx["principal_id"], name_key, template_id),
        ).fetchone()
        if conflict:
            _fail("conflict", "A template with this name already exists", {"template_id": conflict["template_id"]})
        version = row["version"] + 1
        conn.execute(
            "UPDATE ledger_transaction_templates SET name = ?, name_key = ?, defaults_json = ?, version = ?, updated_at = ? "
            "WHERE ledger_id = ? AND principal_id = ? AND template_id = ? AND version = ?",
            (name, name_key, json.dumps(defaults, ensure_ascii=False, sort_keys=True), version, _now(), ctx["ledger_id"], ctx["principal_id"], template_id, expected_version),
        )
    return {"template": {"template_id": template_id, "name": name, "defaults": defaults, "version": version}, "ledger": _ledger_info(ctx)}


def _list_transaction_templates(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, own_only=True)
    query = _clean_optional_text(params.get("query"), "query")
    sql = "SELECT template_id, name, defaults_json, version FROM ledger_transaction_templates WHERE ledger_id = ? AND principal_id = ?"
    values = [ctx["ledger_id"], ctx["principal_id"]]
    if query:
        sql += " AND name_key LIKE ?"
        values.append("%" + _tag_key(query) + "%")
    sql += " ORDER BY name_key, template_id"
    rows = conn.execute(sql, values).fetchall()
    return {
        "templates": [{"template_id": row["template_id"], "name": row["name"], "defaults": json.loads(row["defaults_json"]), "version": row["version"]} for row in rows],
        "ledger": _ledger_info(ctx),
    }


_DRAFT_FIELDS = {"template_id", "occurred_on", "charged_on", "kind", "amount_minor", "estimated", "confirmation_status", "category_id", "tags", "merchant", "note"}


def _validate_draft_fields(conn: sqlite3.Connection, ctx: dict, fields: object) -> dict:
    if not isinstance(fields, dict):
        _fail("invalid_input", "fields must be an object")
    unknown = set(fields) - _DRAFT_FIELDS
    if unknown:
        _fail("invalid_input", "Unknown draft field", {"fields": sorted(unknown)})
    validated = dict(fields)
    if "template_id" in validated:
        validated["template_id"] = _required_text(validated["template_id"], "template_id")
        if conn.execute(
            "SELECT 1 FROM ledger_transaction_templates WHERE ledger_id = ? AND principal_id = ? AND template_id = ?",
            (ctx["ledger_id"], ctx["principal_id"], validated["template_id"]),
        ).fetchone() is None:
            _fail("not_found", "Transaction template not found", {"template_id": validated["template_id"]})
    if "occurred_on" in validated:
        validated["occurred_on"] = _date_value(validated["occurred_on"], "occurred_on")
    if "charged_on" in validated and validated["charged_on"] is not None:
        validated["charged_on"] = _date_value(validated["charged_on"], "charged_on")
    if "kind" in validated and validated["kind"] not in ("income", "expense"):
        _fail("invalid_input", "kind must be income or expense")
    if "amount_minor" in validated:
        validated["amount_minor"] = _amount(validated["amount_minor"])
    if "estimated" in validated and not isinstance(validated["estimated"], bool):
        _fail("invalid_input", "estimated must be boolean")
    if "confirmation_status" in validated and validated["confirmation_status"] not in ("confirmed", "pending"):
        _fail("invalid_input", "confirmation_status must be confirmed or pending")
    if "category_id" in validated:
        validated["category_id"] = _validate_category(conn, ctx, validated["category_id"])
    if "tags" in validated:
        validated["tags"] = _tag_names(validated["tags"])
    for field in ("merchant", "note"):
        if field in validated:
            validated[field] = _clean_optional_text(validated[field], field)
    return validated


def _upsert_transaction_draft(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, write=True)
    patch = _validate_draft_fields(conn, ctx, params.get("fields"))
    draft_id = params.get("draft_id")
    if draft_id is None:
        if params.get("expected_version") is not None:
            _fail("invalid_input", "expected_version requires draft_id")
        draft_id = _new_id()
        fields = patch
        version = 1
        now = _now()
        conn.execute(
            "INSERT INTO ledger_transaction_drafts(ledger_id, principal_id, draft_id, fields_json, status, version, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'open', ?, ?, ?)",
            (ctx["ledger_id"], ctx["principal_id"], draft_id, json.dumps(fields, ensure_ascii=False, sort_keys=True), version, now, now),
        )
    else:
        draft_id = _required_text(draft_id, "draft_id")
        row = conn.execute(
            "SELECT * FROM ledger_transaction_drafts WHERE ledger_id = ? AND principal_id = ? AND draft_id = ?",
            (ctx["ledger_id"], ctx["principal_id"], draft_id),
        ).fetchone()
        if row is None:
            _fail("not_found", "Transaction draft not found", {"draft_id": draft_id})
        if row["status"] != "open":
            _fail("conflict", "A committed draft cannot be changed", {"draft_id": draft_id})
        expected_version = params.get("expected_version")
        if type(expected_version) is not int or row["version"] != expected_version:
            _fail("conflict", "The draft changed after it was read", {"draft_id": draft_id, "actual_version": row["version"]})
        fields = {**json.loads(row["fields_json"]), **patch}
        version = row["version"] + 1
        conn.execute(
            "UPDATE ledger_transaction_drafts SET fields_json = ?, version = ?, updated_at = ? "
            "WHERE ledger_id = ? AND principal_id = ? AND draft_id = ? AND version = ?",
            (json.dumps(fields, ensure_ascii=False, sort_keys=True), version, _now(), ctx["ledger_id"], ctx["principal_id"], draft_id, expected_version),
        )
    return {"draft": {"draft_id": draft_id, "fields": fields, "status": "open", "version": version}, "ledger": _ledger_info(ctx)}


def _list_transaction_drafts(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, own_only=True)
    status = params.get("status", "open")
    if status not in ("open", "committed", "all"):
        _fail("invalid_input", "status must be open, committed, or all")
    sql = "SELECT draft_id, fields_json, status, version, created_at, updated_at, committed_request_id FROM ledger_transaction_drafts WHERE ledger_id = ? AND principal_id = ?"
    values = [ctx["ledger_id"], ctx["principal_id"]]
    if status != "all":
        sql += " AND status = ?"
        values.append(status)
    sql += " ORDER BY updated_at DESC, draft_id"
    rows = conn.execute(sql, values).fetchall()
    return {
        "drafts": [
            {"draft_id": row["draft_id"], "fields": json.loads(row["fields_json"]), "status": row["status"], "version": row["version"], "created_at": row["created_at"], "updated_at": row["updated_at"], "committed_request_id": row["committed_request_id"]}
            for row in rows
        ],
        "ledger": _ledger_info(ctx),
    }


def _copy_transaction_to_draft(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, write=True)
    transaction_id = _required_text(params.get("transaction_id"), "transaction_id")
    transaction = _get_transaction(conn, ctx["ledger_id"], transaction_id)
    if ctx["source"] != "web" and not ctx.get("copy_merchant_authorized"):
        _fail("forbidden", "Copying a merchant requires the existing one-time private-field authorization")
    fields = _validate_draft_fields(conn, ctx, {
        "kind": transaction["kind"],
        "category_id": transaction["category_id"],
        "tags": _tags_for(conn, ctx["ledger_id"], transaction_id),
        "merchant": transaction["merchant"],
    })
    draft_id = _new_id()
    now = _now()
    conn.execute(
        "INSERT INTO ledger_transaction_drafts(ledger_id, principal_id, draft_id, fields_json, status, version, created_at, updated_at, copied_from_transaction_id) "
        "VALUES (?, ?, ?, ?, 'open', 1, ?, ?, ?)",
        (ctx["ledger_id"], ctx["principal_id"], draft_id, json.dumps(fields, ensure_ascii=False, sort_keys=True), now, now, transaction_id),
    )
    return {"draft": {"draft_id": draft_id, "fields": fields, "status": "open", "version": 1}, "ledger": _ledger_info(ctx)}


def _commit_transaction_draft(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, write=True)
    draft_id = _required_text(params.get("draft_id"), "draft_id")
    row = conn.execute(
        "SELECT * FROM ledger_transaction_drafts WHERE ledger_id = ? AND principal_id = ? AND draft_id = ?",
        (ctx["ledger_id"], ctx["principal_id"], draft_id),
    ).fetchone()
    if row is None:
        _fail("not_found", "Transaction draft not found", {"draft_id": draft_id})
    expected_version = params.get("expected_version")
    if row["status"] != "open" or type(expected_version) is not int or row["version"] != expected_version:
        _fail("conflict", "The draft changed or was already committed", {"draft_id": draft_id, "actual_version": row["version"]})
    fields = json.loads(row["fields_json"])
    if row["copied_from_transaction_id"] is not None:
        missing = {"amount_minor", "occurred_on"} - set(fields)
        if missing:
            _fail("needs_clarification", "A copied transaction draft needs a new amount and occurrence date before commit", {"fields": sorted(missing)})
    result = _add_transactions(conn, ctx, {"transactions": [fields]})
    version = row["version"] + 1
    conn.execute(
        "UPDATE ledger_transaction_drafts SET status = 'committed', committed_request_id = ?, version = ?, updated_at = ? "
        "WHERE ledger_id = ? AND principal_id = ? AND draft_id = ? AND version = ? AND status = 'open'",
        (ctx["request_id"], version, _now(), ctx["ledger_id"], ctx["principal_id"], draft_id, expected_version),
    )
    return {"draft_id": draft_id, "draft_version": version, **result}


def _list_estimated_transactions(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, own_only=True)
    if params.get("start_date") is None and params.get("end_date") is None:
        today = date.fromisoformat(ctx["today"])
        start = f"{today.year:04d}-{today.month:02d}-01"
        next_month = date(today.year + (today.month == 12), 1 if today.month == 12 else today.month + 1, 1)
        end = (next_month - timedelta(days=1)).isoformat()
    elif params.get("start_date") is not None and params.get("end_date") is not None:
        start, end = _period_values(params)
    else:
        _fail("invalid_input", "start_date and end_date must be supplied together")
    limit = params.get("limit", 100)
    if type(limit) is not int or not 1 <= limit <= 100:
        _fail("invalid_input", "limit must be from 1 to 100")
    rows = conn.execute(
        "SELECT transaction_id FROM ledger_transactions AS t WHERE t.ledger_id = ? AND t.estimated = 1 AND t.voided_at IS NULL "
        "AND t.occurred_on BETWEEN ? AND ? AND NOT EXISTS (SELECT 1 FROM ledger_transaction_merge_links AS merged "
        "WHERE merged.ledger_id = t.ledger_id AND merged.source_transaction_id = t.transaction_id) "
        "ORDER BY t.occurred_on, t.created_at, t.transaction_id LIMIT ?",
        (ctx["ledger_id"], start, end, limit),
    ).fetchall()
    return {"period": {"start_date": start, "end_date": end}, "transactions": [_transaction_payload(conn, ctx["ledger_id"], row["transaction_id"]) for row in rows], "ledger": _ledger_info(ctx)}


def _confirm_estimated_transaction(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, write=True)
    patch = {"estimated": False, "confirmation_status": "confirmed", "amount_minor": params.get("amount_minor")}
    for field in ("occurred_on", "charged_on"):
        if field in params:
            patch[field] = params[field]
    result, _ = _apply_patch(
        conn,
        ctx,
        params.get("transaction_id"),
        {"expected_version": params.get("expected_version"), "patch": patch},
        "confirm_estimated_transaction",
    )
    result["ledger"] = _ledger_info(ctx)
    return result


def _set_transaction_splits(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, write=True)
    transaction_id = _required_text(params.get("transaction_id"), "transaction_id")
    row = _get_transaction(conn, ctx["ledger_id"], transaction_id)
    version = _check_version(row, params.get("expected_version"))
    _assert_period_open(conn, ctx, row["occurred_on"], action_name="set_transaction_splits")
    splits_value = params.get("splits")
    if not isinstance(splits_value, list) or len(splits_value) < 2:
        _fail("needs_clarification", "At least two category allocations are required")
    splits = []
    for split in splits_value:
        if not isinstance(split, dict) or set(split) != {"category_id", "amount_minor"}:
            _fail("invalid_input", "Each split needs category_id and amount_minor")
        category_id = _required_text(split["category_id"], "category_id")
        splits.append({"split_id": _new_id(), "category_id": _validate_category(conn, ctx, category_id), "amount_minor": _amount(split["amount_minor"])})
    if sum(split["amount_minor"] for split in splits) != row["amount_minor"]:
        _fail("needs_clarification", "Split amounts must sum to the transaction amount", {"amount_minor": row["amount_minor"]})
    before = _snapshot(conn, ctx["ledger_id"], transaction_id)
    conn.execute(
        "UPDATE ledger_transactions SET category_id = NULL, classification_status = 'unclassified', version = ?, updated_at = ? "
        "WHERE ledger_id = ? AND transaction_id = ? AND version = ? AND voided_at IS NULL",
        (version, _now(), ctx["ledger_id"], transaction_id, params["expected_version"]),
    )
    _set_splits(conn, ctx["ledger_id"], transaction_id, splits)
    conn.execute("DELETE FROM ledger_transaction_rule_matches WHERE ledger_id = ? AND transaction_id = ?", (ctx["ledger_id"], transaction_id))
    after = _snapshot(conn, ctx["ledger_id"], transaction_id)
    revision_id = _record_revision(conn, ctx, transaction_id, version, "set_transaction_splits", before, after)
    _record_operation(conn, ctx, "set_transaction_splits", [{"transaction_id": transaction_id, "before": before, "after": after, "after_version": version}])
    _record_reference(conn, ctx, [transaction_id])
    return {"transaction": _transaction_payload(conn, ctx["ledger_id"], transaction_id), "revision_id": revision_id, "version": version, "ledger": _ledger_info(ctx)}


def _upsert_merchant_alias(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, write=True)
    alias_name = _required_text(params.get("alias_name"), "alias_name")
    canonical_name = _required_text(params.get("canonical_name"), "canonical_name")
    alias_key = _merchant_key(alias_name)
    canonical_key = _merchant_key(canonical_name)
    canonical = conn.execute(
        "SELECT canonical_key, canonical_name FROM ledger_merchant_aliases WHERE ledger_id = ? AND alias_key = ?",
        (ctx["ledger_id"], canonical_key),
    ).fetchone()
    if canonical:
        canonical_key = canonical["canonical_key"]
        canonical_name = canonical["canonical_name"]
    if alias_key == canonical_key:
        _fail("invalid_input", "An alias and canonical merchant must have different names")
    row = conn.execute(
        "SELECT alias_id FROM ledger_merchant_aliases WHERE ledger_id = ? AND alias_key = ?",
        (ctx["ledger_id"], alias_key),
    ).fetchone()
    now = _now()
    if row:
        conn.execute(
            "UPDATE ledger_merchant_aliases SET alias_name = ?, canonical_name = ?, canonical_key = ?, updated_at = ? WHERE ledger_id = ? AND alias_id = ?",
            (alias_name, canonical_name, canonical_key, now, ctx["ledger_id"], row["alias_id"]),
        )
        alias_id = row["alias_id"]
    else:
        alias_id = _new_id()
        conn.execute(
            "INSERT INTO ledger_merchant_aliases(ledger_id, alias_id, alias_name, alias_key, canonical_name, canonical_key, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ctx["ledger_id"], alias_id, alias_name, alias_key, canonical_name, canonical_key, ctx["principal_id"], now, now),
        )
    return {"alias": {"alias_id": alias_id, "alias_name": alias_name, "canonical_name": canonical_name}, "ledger": _ledger_info(ctx)}


def _list_merchant_aliases(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, own_only=True)
    query = _clean_optional_text(params.get("query"), "query")
    sql = "SELECT alias_id, alias_name, canonical_name FROM ledger_merchant_aliases WHERE ledger_id = ?"
    values = [ctx["ledger_id"]]
    if query:
        key = _merchant_key(query)
        sql += " AND (alias_key LIKE ? OR canonical_key LIKE ?)"
        values.extend(("%" + key + "%", "%" + key + "%"))
    sql += " ORDER BY canonical_key, alias_key"
    rows = conn.execute(sql, values).fetchall()
    return {"aliases": [{"alias_id": row["alias_id"], "alias_name": row["alias_name"], "canonical_name": row["canonical_name"]} for row in rows], "ledger": _ledger_info(ctx)}


def _validated_rule_conditions(value: object) -> dict:
    if not isinstance(value, dict):
        _fail("invalid_input", "conditions must be an object")
    allowed = {"merchant", "keyword", "amount_min_minor", "amount_max_minor"}
    unknown = set(value) - allowed
    if unknown:
        _fail("invalid_input", "Unknown classification condition", {"fields": sorted(unknown)})
    conditions = {}
    for field in ("merchant", "keyword"):
        if field in value:
            conditions[field] = _required_text(value[field], field)
    for field in ("amount_min_minor", "amount_max_minor"):
        if field in value:
            if type(value[field]) is not int or value[field] < 0:
                _fail("invalid_input", f"{field} must be a non-negative integer")
            conditions[field] = value[field]
    if not conditions:
        _fail("needs_clarification", "At least one merchant, keyword, or amount condition is required")
    if "amount_min_minor" in conditions and "amount_max_minor" in conditions and conditions["amount_min_minor"] > conditions["amount_max_minor"]:
        _fail("invalid_input", "amount_min_minor cannot exceed amount_max_minor")
    return conditions


def _upsert_classification_rule(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, write=True)
    name = _required_text(params.get("name"), "name")
    conditions = _validated_rule_conditions(params.get("conditions"))
    category_id = _validate_category(conn, ctx, params.get("category_id"))
    if category_id is None:
        _fail("needs_clarification", "A classification rule needs an active category")
    priority = params.get("priority", 0)
    if type(priority) is not int:
        _fail("invalid_input", "priority must be an integer")
    active = params.get("active", True)
    if not isinstance(active, bool):
        _fail("invalid_input", "active must be boolean")
    rule_id = params.get("rule_id")
    expected_version = params.get("expected_version")
    now = _now()
    if rule_id is None:
        if expected_version is not None:
            _fail("invalid_input", "expected_version requires rule_id")
        rule_id = _new_id()
        version = 1
        conn.execute(
            "INSERT INTO ledger_classification_rules(ledger_id, principal_id, rule_id, name, conditions_json, category_id, priority, active, version, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ctx["ledger_id"], ctx["principal_id"], rule_id, name, json.dumps(conditions, ensure_ascii=False, sort_keys=True), category_id, priority, int(active), version, now, now),
        )
    else:
        rule_id = _required_text(rule_id, "rule_id")
        row = conn.execute(
            "SELECT version FROM ledger_classification_rules WHERE ledger_id = ? AND principal_id = ? AND rule_id = ?",
            (ctx["ledger_id"], ctx["principal_id"], rule_id),
        ).fetchone()
        if row is None:
            _fail("not_found", "Classification rule not found", {"rule_id": rule_id})
        if type(expected_version) is not int or row["version"] != expected_version:
            _fail("conflict", "The classification rule changed after it was read", {"rule_id": rule_id, "actual_version": row["version"]})
        version = row["version"] + 1
        conn.execute(
            "UPDATE ledger_classification_rules SET name = ?, conditions_json = ?, category_id = ?, priority = ?, active = ?, version = ?, updated_at = ? "
            "WHERE ledger_id = ? AND principal_id = ? AND rule_id = ? AND version = ?",
            (name, json.dumps(conditions, ensure_ascii=False, sort_keys=True), category_id, priority, int(active), version, now, ctx["ledger_id"], ctx["principal_id"], rule_id, expected_version),
        )
    return {"rule": {"rule_id": rule_id, "name": name, "conditions": conditions, "category_id": category_id, "priority": priority, "active": active, "version": version}, "ledger": _ledger_info(ctx)}


def _list_classification_rules(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, own_only=True)
    rows = conn.execute(
        "SELECT rule_id, name, conditions_json, category_id, priority, active, version FROM ledger_classification_rules "
        "WHERE ledger_id = ? AND principal_id = ? ORDER BY priority DESC, created_at, rule_id",
        (ctx["ledger_id"], ctx["principal_id"]),
    ).fetchall()
    return {
        "rules": [{"rule_id": row["rule_id"], "name": row["name"], "conditions": json.loads(row["conditions_json"]), "category_id": row["category_id"], "category_path": _category_path(conn, ctx["ledger_id"], row["category_id"]), "priority": row["priority"], "active": bool(row["active"]), "version": row["version"]} for row in rows],
        "ledger": _ledger_info(ctx),
    }


def _preview_classification_rule(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, own_only=True)
    conditions = _validated_rule_conditions(params.get("conditions"))
    category_id = _validate_category(conn, ctx, params.get("category_id"))
    if category_id is None:
        _fail("needs_clarification", "A preview needs an active category")
    limit = params.get("limit", 20)
    if type(limit) is not int or not 1 <= limit <= 100:
        _fail("invalid_input", "limit must be from 1 to 100")
    rows = conn.execute(
        "SELECT t.transaction_id, t.occurred_on, t.kind, t.amount_minor, t.merchant, t.note, t.category_id "
        "FROM ledger_transactions AS t WHERE t.ledger_id = ? AND t.voided_at IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM ledger_transaction_merge_links AS merged WHERE merged.ledger_id = t.ledger_id AND merged.source_transaction_id = t.transaction_id) "
        "ORDER BY t.occurred_on DESC, t.created_at DESC",
        (ctx["ledger_id"],),
    ).fetchall()
    matches = []
    for row in rows:
        record = {"merchant": row["merchant"], "note": row["note"], "amount_minor": row["amount_minor"]}
        if _rule_matches(conn, ctx["ledger_id"], conditions, record):
            matches.append({"transaction_id": row["transaction_id"], "occurred_on": row["occurred_on"], "kind": row["kind"], "amount_minor": row["amount_minor"], "current_category_id": row["category_id"], "new_category_id": category_id})
    return {"conditions": conditions, "category_id": category_id, "category_path": _category_path(conn, ctx["ledger_id"], category_id), "match_count": len(matches), "matches": matches[:limit], "ledger": _ledger_info(ctx)}


def _deactivate_classification_rule(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, write=True)
    rule_id = _required_text(params.get("rule_id"), "rule_id")
    row = conn.execute(
        "SELECT version FROM ledger_classification_rules WHERE ledger_id = ? AND principal_id = ? AND rule_id = ?",
        (ctx["ledger_id"], ctx["principal_id"], rule_id),
    ).fetchone()
    if row is None:
        _fail("not_found", "Classification rule not found", {"rule_id": rule_id})
    expected_version = params.get("expected_version")
    if type(expected_version) is not int or row["version"] != expected_version:
        _fail("conflict", "The classification rule changed after it was read", {"rule_id": rule_id, "actual_version": row["version"]})
    version = row["version"] + 1
    conn.execute(
        "UPDATE ledger_classification_rules SET active = 0, version = ?, updated_at = ? WHERE ledger_id = ? AND principal_id = ? AND rule_id = ? AND version = ?",
        (version, _now(), ctx["ledger_id"], ctx["principal_id"], rule_id, expected_version),
    )
    return {"rule_id": rule_id, "active": False, "version": version, "ledger": _ledger_info(ctx)}


def _create_change_preview(conn: sqlite3.Connection, ctx: dict, action: str, payload: dict) -> str:
    preview_id = _new_id()
    packed = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(packed.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO ledger_change_previews(ledger_id, principal_id, preview_id, action, payload_json, fingerprint, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (ctx["ledger_id"], ctx["principal_id"], preview_id, action, packed, fingerprint, _now(), (now + timedelta(minutes=15)).isoformat(timespec="seconds").replace("+00:00", "Z")),
    )
    return preview_id


def _load_change_preview(conn: sqlite3.Connection, ctx: dict, preview_id: object, action: str) -> dict:
    preview_id = _required_text(preview_id, "preview_id")
    row = conn.execute(
        "SELECT payload_json, fingerprint, expires_at, consumed_by_request_id FROM ledger_change_previews "
        "WHERE ledger_id = ? AND principal_id = ? AND preview_id = ? AND action = ?",
        (ctx["ledger_id"], ctx["principal_id"], preview_id, action),
    ).fetchone()
    if row is None:
        _fail("not_found", "Change preview not found", {"preview_id": preview_id})
    if row["consumed_by_request_id"] is not None:
        _fail("conflict", "This preview has already been applied", {"preview_id": preview_id})
    if row["expires_at"] <= _now():
        _fail("conflict", "This preview has expired; create a new preview", {"preview_id": preview_id})
    payload = json.loads(row["payload_json"])
    fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if fingerprint != row["fingerprint"]:
        _fail("conflict", "The stored preview is invalid", {"preview_id": preview_id})
    conn.execute(
        "UPDATE ledger_change_previews SET consumed_by_request_id = ? WHERE ledger_id = ? AND principal_id = ? AND preview_id = ? AND consumed_by_request_id IS NULL",
        (ctx["request_id"], ctx["ledger_id"], ctx["principal_id"], preview_id),
    )
    return payload


def _category_change_targets(conn: sqlite3.Connection, ledger_id: str, category_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT DISTINCT t.transaction_id, t.version, t.occurred_on, t.kind, t.amount_minor "
        "FROM ledger_transactions AS t LEFT JOIN ledger_transaction_splits AS split "
        "ON split.ledger_id = t.ledger_id AND split.transaction_id = t.transaction_id "
        "WHERE t.ledger_id = ? AND t.voided_at IS NULL AND (t.category_id = ? OR split.category_id = ?) "
        "AND NOT EXISTS (SELECT 1 FROM ledger_transaction_merge_links AS merged WHERE merged.ledger_id = t.ledger_id AND merged.source_transaction_id = t.transaction_id) "
        "ORDER BY t.occurred_on, t.transaction_id",
        (ledger_id, category_id, category_id),
    ).fetchall()
    return [{"transaction_id": row["transaction_id"], "version": row["version"], "occurred_on": row["occurred_on"], "kind": row["kind"], "amount_minor": row["amount_minor"]} for row in rows]


def _preview_category_change(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, own_only=True, write=True)
    category_id = _required_text(params.get("category_id"), "category_id")
    source = conn.execute(
        "SELECT category_id, path, active FROM ledger_categories WHERE ledger_id = ? AND category_id = ?",
        (ctx["ledger_id"], category_id),
    ).fetchone()
    if source is None or not source["active"]:
        _fail("not_found", "Active source category not found", {"category_id": category_id})
    target_category_id = params.get("target_category_id")
    if target_category_id is not None:
        target_category_id = _required_text(target_category_id, "target_category_id")
        if target_category_id == category_id:
            _fail("invalid_input", "A category cannot be merged into itself")
        target = conn.execute(
            "SELECT category_id, path, active FROM ledger_categories WHERE ledger_id = ? AND category_id = ?",
            (ctx["ledger_id"], target_category_id),
        ).fetchone()
        if target is None or not target["active"]:
            _fail("not_found", "Active target category not found", {"target_category_id": target_category_id})
        child = conn.execute(
            "SELECT category_id FROM ledger_categories WHERE ledger_id = ? AND parent_id = ? LIMIT 1",
            (ctx["ledger_id"], category_id),
        ).fetchone()
        if child:
            _fail("needs_clarification", "Merge leaf categories one at a time", {"child_category_id": child["category_id"]})
    transactions = _category_change_targets(conn, ctx["ledger_id"], category_id)
    if target_category_id is not None:
        _assert_period_open(conn, ctx, *(item["occurred_on"] for item in transactions), action_name="preview_category_change")
    rules = conn.execute(
        "SELECT principal_id, rule_id, version FROM ledger_classification_rules WHERE ledger_id = ? AND category_id = ? ORDER BY principal_id, rule_id",
        (ctx["ledger_id"], category_id),
    ).fetchall()
    payload = {
        "category_id": category_id,
        "target_category_id": target_category_id,
        "transactions": transactions,
        "rules": [{"principal_id": row["principal_id"], "rule_id": row["rule_id"], "version": row["version"]} for row in rules],
    }
    preview_id = _create_change_preview(conn, ctx, "category_change", payload)
    return {
        "preview_id": preview_id,
        "category_id": category_id,
        "category_path": source["path"],
        "target_category_id": target_category_id,
        "target_category_path": _category_path(conn, ctx["ledger_id"], target_category_id),
        "transaction_count": len(transactions),
        "rule_count": len(rules),
        "transactions": transactions[:100],
        "ledger": _ledger_info(ctx),
    }


def _apply_category_change(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, own_only=True, write=True)
    payload = _load_change_preview(conn, ctx, params.get("preview_id"), "category_change")
    category_id = payload["category_id"]
    target_category_id = payload["target_category_id"]
    source = conn.execute(
        "SELECT active FROM ledger_categories WHERE ledger_id = ? AND category_id = ?",
        (ctx["ledger_id"], category_id),
    ).fetchone()
    if source is None or not source["active"]:
        _fail("conflict", "The source category changed after preview")
    current_transactions = _category_change_targets(conn, ctx["ledger_id"], category_id)
    if current_transactions != payload["transactions"]:
        _fail("conflict", "Transactions using the category changed after preview; create a new preview")
    current_rules = conn.execute(
        "SELECT principal_id, rule_id, version FROM ledger_classification_rules WHERE ledger_id = ? AND category_id = ? ORDER BY principal_id, rule_id",
        (ctx["ledger_id"], category_id),
    ).fetchall()
    rules = [{"principal_id": row["principal_id"], "rule_id": row["rule_id"], "version": row["version"]} for row in current_rules]
    if rules != payload["rules"]:
        _fail("conflict", "Classification rules changed after preview; create a new preview")
    if target_category_id is not None:
        target = conn.execute(
            "SELECT active FROM ledger_categories WHERE ledger_id = ? AND category_id = ?",
            (ctx["ledger_id"], target_category_id),
        ).fetchone()
        if target is None or not target["active"]:
            _fail("conflict", "The target category is no longer active")
        for item in current_transactions:
            _assert_period_open(conn, ctx, item["occurred_on"], action_name="apply_category_change")
            transaction_id = item["transaction_id"]
            before = _snapshot(conn, ctx["ledger_id"], transaction_id)
            version = item["version"] + 1
            conn.execute(
                "UPDATE ledger_transactions SET category_id = CASE WHEN category_id = ? THEN ? ELSE category_id END, "
                "classification_status = CASE WHEN category_id = ? THEN 'classified' ELSE classification_status END, version = ?, updated_at = ? "
                "WHERE ledger_id = ? AND transaction_id = ? AND version = ? AND voided_at IS NULL",
                (category_id, target_category_id, category_id, version, _now(), ctx["ledger_id"], transaction_id, item["version"]),
            )
            conn.execute(
                "UPDATE ledger_transaction_splits SET category_id = ? WHERE ledger_id = ? AND transaction_id = ? AND category_id = ?",
                (target_category_id, ctx["ledger_id"], transaction_id, category_id),
            )
            after = _snapshot(conn, ctx["ledger_id"], transaction_id)
            _record_revision(conn, ctx, transaction_id, version, "apply_category_change", before, after)
        for rule in rules:
            conn.execute(
                "UPDATE ledger_classification_rules SET category_id = ?, version = version + 1, updated_at = ? "
                "WHERE ledger_id = ? AND principal_id = ? AND rule_id = ? AND version = ?",
                (target_category_id, _now(), ctx["ledger_id"], rule["principal_id"], rule["rule_id"], rule["version"]),
            )
    else:
        for rule in rules:
            conn.execute(
                "UPDATE ledger_classification_rules SET active = 0, version = version + 1, updated_at = ? "
                "WHERE ledger_id = ? AND principal_id = ? AND rule_id = ? AND version = ?",
                (_now(), ctx["ledger_id"], rule["principal_id"], rule["rule_id"], rule["version"]),
            )
    conn.execute(
        "UPDATE ledger_categories SET active = 0 WHERE ledger_id = ? AND category_id = ? AND active = 1",
        (ctx["ledger_id"], category_id),
    )
    return {"category_id": category_id, "merged_into_category_id": target_category_id, "transactions_changed": len(current_transactions) if target_category_id else 0, "rules_changed": len(rules), "active": False, "ledger": _ledger_info(ctx)}


def _tag_merge_targets(conn: sqlite3.Connection, ledger_id: str, source_tag_ids: list[str]) -> list[dict]:
    if not source_tag_ids:
        return []
    rows = conn.execute(
        "SELECT DISTINCT t.transaction_id, t.version, t.occurred_on FROM ledger_transactions AS t "
        "JOIN ledger_transaction_tags AS link ON link.ledger_id = t.ledger_id AND link.transaction_id = t.transaction_id "
        "WHERE t.ledger_id = ? AND link.tag_id IN (" + ",".join("?" for _ in source_tag_ids) + ") "
        "AND t.voided_at IS NULL AND NOT EXISTS (SELECT 1 FROM ledger_transaction_merge_links AS merged "
        "WHERE merged.ledger_id = t.ledger_id AND merged.source_transaction_id = t.transaction_id) "
        "ORDER BY t.occurred_on, t.transaction_id",
        [ledger_id, *source_tag_ids],
    ).fetchall()
    return [{"transaction_id": row["transaction_id"], "version": row["version"], "occurred_on": row["occurred_on"]} for row in rows]


def _unused_tag_ids(conn: sqlite3.Connection, ledger_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT tag.tag_id FROM ledger_tags AS tag LEFT JOIN ledger_transaction_tags AS link "
        "ON link.ledger_id = tag.ledger_id AND link.tag_id = tag.tag_id "
        "WHERE tag.ledger_id = ? GROUP BY tag.tag_id HAVING COUNT(link.transaction_id) = 0 ORDER BY tag.tag_id",
        (ledger_id,),
    ).fetchall()
    return [row["tag_id"] for row in rows]


def _preview_tag_merge(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, own_only=True, write=True)
    source_names = _tag_names(params.get("source_tags", []), "source_tags")
    target_name = _clean_optional_text(params.get("target_tag"), "target_tag")
    if source_names and not target_name:
        _fail("needs_clarification", "A target tag is required when merging source tags")
    source_rows = []
    target_key = _tag_key(target_name) if target_name else None
    for name in source_names:
        key = _tag_key(name)
        if key == target_key:
            continue
        row = conn.execute(
            "SELECT tag_id, name, name_key FROM ledger_tags WHERE ledger_id = ? AND name_key = ?",
            (ctx["ledger_id"], key),
        ).fetchone()
        if row is None:
            _fail("not_found", "Source tag not found", {"tag": name})
        source_rows.append({"tag_id": row["tag_id"], "name": row["name"], "name_key": row["name_key"]})
    if target_key:
        target_row = conn.execute(
            "SELECT tag_id, name FROM ledger_tags WHERE ledger_id = ? AND name_key = ?",
            (ctx["ledger_id"], target_key),
        ).fetchone()
    else:
        target_row = None
    source_ids = [row["tag_id"] for row in source_rows]
    transactions = _tag_merge_targets(conn, ctx["ledger_id"], source_ids)
    _assert_period_open(conn, ctx, *(item["occurred_on"] for item in transactions), action_name="preview_tag_merge")
    unused = [tag_id for tag_id in _unused_tag_ids(conn, ctx["ledger_id"]) if tag_id not in source_ids and (target_row is None or tag_id != target_row["tag_id"])]
    if not source_ids and not unused:
        _fail("not_found", "No unused tags or source tags were found")
    payload = {
        "source_tags": source_rows,
        "target_tag": target_name,
        "target_tag_id": target_row["tag_id"] if target_row else None,
        "transactions": transactions,
        "unused_tag_ids": unused,
    }
    preview_id = _create_change_preview(conn, ctx, "tag_merge", payload)
    return {
        "preview_id": preview_id,
        "source_tags": source_rows,
        "target_tag": target_name,
        "target_exists": target_row is not None,
        "transaction_count": len(transactions),
        "unused_tag_count": len(unused),
        "transactions": transactions[:100],
        "ledger": _ledger_info(ctx),
    }


def _apply_tag_merge(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, own_only=True, write=True)
    payload = _load_change_preview(conn, ctx, params.get("preview_id"), "tag_merge")
    source_tags = payload["source_tags"]
    source_ids = [row["tag_id"] for row in source_tags]
    current_transactions = _tag_merge_targets(conn, ctx["ledger_id"], source_ids)
    current_unused = [tag_id for tag_id in _unused_tag_ids(conn, ctx["ledger_id"]) if tag_id not in source_ids and tag_id != payload["target_tag_id"]]
    if current_transactions != payload["transactions"] or current_unused != payload["unused_tag_ids"]:
        _fail("conflict", "Tags or tagged transactions changed after preview; create a new preview")
    current_sources = []
    for source in source_tags:
        row = conn.execute("SELECT tag_id, name, name_key FROM ledger_tags WHERE ledger_id = ? AND tag_id = ?", (ctx["ledger_id"], source["tag_id"])).fetchone()
        if row is None or row["name_key"] != source["name_key"]:
            _fail("conflict", "A source tag changed after preview")
        current_sources.append(dict(row))
    target_name = payload["target_tag"]
    target_id = payload["target_tag_id"]
    if target_name:
        target_key = _tag_key(target_name)
        target = conn.execute("SELECT tag_id FROM ledger_tags WHERE ledger_id = ? AND name_key = ?", (ctx["ledger_id"], target_key)).fetchone()
        if target_id and (target is None or target["tag_id"] != target_id):
            _fail("conflict", "The target tag changed after preview")
        if not target_id and target is not None:
            _fail("conflict", "The target tag was created after preview")
        if not target_id and source_ids:
            target_id = _new_id()
            conn.execute(
                "INSERT INTO ledger_tags(ledger_id, tag_id, name, name_key) VALUES (?, ?, ?, ?)",
                (ctx["ledger_id"], target_id, target_name, target_key),
            )
    effects = []
    for item in current_transactions:
        transaction_id = item["transaction_id"]
        before = _snapshot(conn, ctx["ledger_id"], transaction_id)
        keys = {_tag_key(name) for name in (row["name"] for row in current_sources)}
        tags = []
        for name in before["tags"]:
            key = _tag_key(name)
            if key in keys:
                if target_name and _tag_key(target_name) not in {_tag_key(existing) for existing in tags}:
                    tags.append(target_name)
            elif key not in {_tag_key(existing) for existing in tags}:
                tags.append(name)
        if target_name and any(_tag_key(name) in keys for name in before["tags"]) and _tag_key(target_name) not in {_tag_key(name) for name in tags}:
            tags.append(target_name)
        result, _ = _apply_patch(
            conn, ctx, transaction_id,
            {"expected_version": item["version"], "patch": {"tags": tags}},
            "apply_tag_merge", effects,
        )
    for source in current_sources:
        if source["tag_id"] != target_id:
            conn.execute("DELETE FROM ledger_tags WHERE ledger_id = ? AND tag_id = ?", (ctx["ledger_id"], source["tag_id"]))
    for unused_id in payload["unused_tag_ids"]:
        conn.execute("DELETE FROM ledger_tags WHERE ledger_id = ? AND tag_id = ?", (ctx["ledger_id"], unused_id))
    if effects:
        _record_operation(conn, ctx, "apply_tag_merge", effects)
    return {"target_tag": target_name, "transactions_changed": len(effects), "unused_tags_removed": len(payload["unused_tag_ids"]), "ledger": _ledger_info(ctx)}


def _preview_transaction_bulk_change(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, own_only=True, write=True)
    filters = _normalize_filters(params.get("filters", {}), _SEARCH_FILTERS)
    changes = params.get("changes")
    if not isinstance(changes, dict):
        _fail("invalid_input", "changes must be an object")
    allowed = {"category_id", "tags_add", "tags_remove", "estimated", "confirmation_status"}
    unknown = set(changes) - allowed
    if unknown:
        _fail("invalid_input", "Unknown bulk change field", {"fields": sorted(unknown)})
    if not changes:
        _fail("needs_clarification", "At least one bulk change is required")
    normalized = {}
    if "category_id" in changes:
        normalized["category_id"] = _validate_category(conn, ctx, changes["category_id"])
    for field in ("tags_add", "tags_remove"):
        if field in changes:
            normalized[field] = _tag_names(changes[field], field)
    if "estimated" in changes:
        if not isinstance(changes["estimated"], bool):
            _fail("invalid_input", "estimated must be boolean")
        normalized["estimated"] = changes["estimated"]
    if "confirmation_status" in changes:
        if changes["confirmation_status"] not in ("confirmed", "pending"):
            _fail("invalid_input", "confirmation_status must be confirmed or pending")
        normalized["confirmation_status"] = changes["confirmation_status"]
    transaction_ids = params.get("transaction_ids")
    clause, values = _filter_sql(conn, ctx, filters)
    rows = conn.execute(
        "SELECT t.* FROM ledger_transactions AS t WHERE " + clause + " ORDER BY t.occurred_on, t.transaction_id LIMIT 101",
        values,
    ).fetchall()
    if transaction_ids is not None:
        if not isinstance(transaction_ids, list) or not transaction_ids or len(transaction_ids) > 100:
            _fail("invalid_input", "transaction_ids must contain from 1 to 100 IDs")
        ids = [_required_text(value, "transaction_ids") for value in transaction_ids]
        if len(set(ids)) != len(ids):
            _fail("invalid_input", "transaction_ids cannot contain duplicates")
        selected = {row["transaction_id"]: row for row in rows}
        if any(transaction_id not in selected for transaction_id in ids):
            _fail("conflict", "One or more selected transactions do not match the filters")
        rows = [selected[transaction_id] for transaction_id in ids]
    elif not filters:
        _fail("needs_clarification", "Select transactions or add a filter before previewing a bulk change")
    if len(rows) > 100:
        _fail("needs_clarification", "The bulk preview is limited to 100 transactions; narrow the filters")
    if not rows:
        _fail("not_found", "No transactions match the bulk change")
    preview_items = []
    visible = []
    for row in rows:
        transaction_id = row["transaction_id"]
        before = _snapshot(conn, ctx["ledger_id"], transaction_id)
        if before["splits"] and "category_id" in normalized:
            _fail("needs_clarification", "Set categories on each split before bulk-changing a split transaction", {"transaction_id": transaction_id})
        _assert_period_open(conn, ctx, row["occurred_on"], action_name="preview_transaction_bulk_change")
        patch = {key: value for key, value in normalized.items() if key not in ("tags_add", "tags_remove")}
        if "tags_add" in normalized or "tags_remove" in normalized:
            remove = {_tag_key(name) for name in normalized.get("tags_remove", [])}
            tags = [name for name in before["tags"] if _tag_key(name) not in remove]
            seen = {_tag_key(name) for name in tags}
            for name in normalized.get("tags_add", []):
                if _tag_key(name) not in seen:
                    tags.append(name)
                    seen.add(_tag_key(name))
            patch["tags"] = tags
        preview_items.append({"transaction_id": transaction_id, "expected_version": row["version"], "occurred_on": row["occurred_on"], "patch": patch})
        visible.append({"before": _transaction_payload(conn, ctx["ledger_id"], transaction_id), "changes": patch})
    preview_id = _create_change_preview(conn, ctx, "transaction_bulk_change", {"items": preview_items})
    return {"preview_id": preview_id, "count": len(preview_items), "items": visible, "ledger": _ledger_info(ctx)}


def _apply_transaction_bulk_change(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, own_only=True, write=True)
    payload = _load_change_preview(conn, ctx, params.get("preview_id"), "transaction_bulk_change")
    effects = []
    results = []
    for item in payload["items"]:
        result, transaction_id = _apply_patch(
            conn,
            ctx,
            item["transaction_id"],
            {"expected_version": item["expected_version"], "patch": item["patch"]},
            "apply_transaction_bulk_change",
            effects,
        )
        results.append({"transaction_id": transaction_id, "version": result["version"]})
    _record_operation(conn, ctx, "apply_transaction_bulk_change", effects)
    return {"transactions": results, "count": len(results), "ledger": _ledger_info(ctx)}


def _project_for_transaction(conn: sqlite3.Connection, ledger_id: str, transaction_id: str) -> str | None:
    row = conn.execute(
        "SELECT project_id FROM project_entries WHERE ledger_id = ? AND transaction_id = ?",
        (ledger_id, transaction_id),
    ).fetchone()
    return row["project_id"] if row else None


def _transaction_completeness(transaction: dict) -> int:
    return sum(bool(transaction[field]) for field in ("category_id", "merchant", "note", "charged_on", "tags", "splits"))


def _preview_duplicate_merge(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, own_only=True, write=True)
    source_id = _required_text(params.get("source_transaction_id"), "source_transaction_id")
    target_id = _required_text(params.get("target_transaction_id"), "target_transaction_id")
    if source_id == target_id:
        _fail("invalid_input", "Source and target transactions must differ")
    source_row = _get_transaction(conn, ctx["ledger_id"], source_id)
    target_row = _get_transaction(conn, ctx["ledger_id"], target_id)
    source_version = params.get("source_expected_version")
    target_version = params.get("target_expected_version")
    if type(source_version) is not int or source_row["version"] != source_version or type(target_version) is not int or target_row["version"] != target_version:
        _fail("conflict", "A transaction changed after it was read", {"source_version": source_row["version"], "target_version": target_row["version"]})
    if source_row["voided_at"] is not None or target_row["voided_at"] is not None:
        _fail("conflict", "Voided transactions cannot be merged")
    if source_row["occurred_on"] != target_row["occurred_on"] or source_row["kind"] != target_row["kind"] or source_row["amount_minor"] != target_row["amount_minor"]:
        _fail("needs_clarification", "Only transactions with the same date, direction, and amount can be merged")
    _assert_period_open(conn, ctx, source_row["occurred_on"], target_row["occurred_on"], action_name="preview_duplicate_merge")
    if conn.execute(
        "SELECT 1 FROM ledger_transaction_merge_links WHERE ledger_id = ? AND (source_transaction_id IN (?, ?) OR target_transaction_id IN (?, ?))",
        (ctx["ledger_id"], source_id, target_id, source_id, target_id),
    ).fetchone():
        _fail("conflict", "A transaction already belongs to a merge")
    source = _transaction_payload(conn, ctx["ledger_id"], source_id)
    target = _transaction_payload(conn, ctx["ledger_id"], target_id)
    source_project = _project_for_transaction(conn, ctx["ledger_id"], source_id)
    target_project = _project_for_transaction(conn, ctx["ledger_id"], target_id)
    if source_project and target_project and source_project != target_project:
        _fail("needs_clarification", "The transactions belong to different projects and cannot be merged automatically", {"source_project_id": source_project, "target_project_id": target_project})
    from . import movements

    movement_links = movements.preview_transaction_links(conn, ctx["ledger_id"], source_id, target_id)
    if not movement_links["can_repoint"]:
        return {
            "can_merge": False,
            "conflicts": movement_links["conflicts"],
            "source": source,
            "target": target,
            "recommended_keep_transaction_id": target_id if _transaction_completeness(target) >= _transaction_completeness(source) else source_id,
            "ledger": _ledger_info(ctx),
        }
    source_splits = source["splits"]
    target_splits = target["splits"]
    if source_splits and target_splits and [(split["category_id"], split["amount_minor"]) for split in source_splits] != [(split["category_id"], split["amount_minor"]) for split in target_splits]:
        _fail("needs_clarification", "The transactions have different split allocations")
    payload = {
        "source_transaction_id": source_id,
        "target_transaction_id": target_id,
        "source_expected_version": source_version,
        "target_expected_version": target_version,
        "source_project_id": source_project,
        "target_project_id": target_project,
        "movement_links": movement_links["moved_links"],
    }
    preview_id = _create_change_preview(conn, ctx, "duplicate_merge", payload)
    return {
        "preview_id": preview_id,
        "source": source,
        "target": target,
        "recommended_keep_transaction_id": target_id if _transaction_completeness(target) >= _transaction_completeness(source) else source_id,
        "project_id": target_project or source_project,
        "movement_links": movement_links["moved_links"],
        "ledger": _ledger_info(ctx),
    }


def _merge_duplicate_transactions(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, own_only=True, write=True)
    payload = _load_change_preview(conn, ctx, params.get("preview_id"), "duplicate_merge")
    source_id = payload["source_transaction_id"]
    target_id = payload["target_transaction_id"]
    source_row = _get_transaction(conn, ctx["ledger_id"], source_id)
    target_row = _get_transaction(conn, ctx["ledger_id"], target_id)
    if source_row["version"] != payload["source_expected_version"] or target_row["version"] != payload["target_expected_version"]:
        _fail("conflict", "A transaction changed after preview; create a new preview")
    if _project_for_transaction(conn, ctx["ledger_id"], source_id) != payload["source_project_id"] or _project_for_transaction(conn, ctx["ledger_id"], target_id) != payload["target_project_id"]:
        _fail("conflict", "Project links changed after preview; create a new preview")
    if source_row["occurred_on"] != target_row["occurred_on"] or source_row["kind"] != target_row["kind"] or source_row["amount_minor"] != target_row["amount_minor"]:
        _fail("conflict", "Transactions no longer match the preview")
    source = _snapshot(conn, ctx["ledger_id"], source_id)
    target = _snapshot(conn, ctx["ledger_id"], target_id)
    _assert_period_open(conn, ctx, source["occurred_on"], target["occurred_on"], action_name="merge_duplicate_transactions")
    from . import movements

    movement_link_result = movements.repoint_transaction_links(conn, ctx["ledger_id"], source_id, target_id)
    if movement_link_result["moved_links"] != payload["movement_links"]:
        _fail("conflict", "Movement links changed after preview; create a new preview")
    target_tags = list(target["tags"])
    target_keys = {_tag_key(name) for name in target_tags}
    for name in source["tags"]:
        if _tag_key(name) not in target_keys:
            target_tags.append(name)
            target_keys.add(_tag_key(name))
    target_after = dict(target)
    target_after["merchant"] = target["merchant"] or source["merchant"]
    target_after["note"] = target["note"] or source["note"]
    target_after["category_id"] = target["category_id"] or source["category_id"]
    target_after["classification_status"] = "classified" if target_after["category_id"] else "unclassified"
    target_after["estimated"] = target["estimated"] and source["estimated"]
    target_after["confirmation_status"] = "confirmed" if "confirmed" in (target["confirmation_status"], source["confirmation_status"]) else "pending"
    target_after["charged_on"] = target["charged_on"] or source["charged_on"]
    target_after["tags"] = target_tags
    target_after["splits"] = target["splits"] or source["splits"]
    target_version = target_row["version"] + 1
    conn.execute(
        "UPDATE ledger_transactions SET estimated = ?, confirmation_status = ?, classification_status = ?, category_id = ?, merchant = ?, note = ?, version = ?, updated_at = ? "
        "WHERE ledger_id = ? AND transaction_id = ? AND version = ? AND voided_at IS NULL",
        (int(target_after["estimated"]), target_after["confirmation_status"], target_after["classification_status"], target_after["category_id"], target_after["merchant"], target_after["note"], target_version, _now(), ctx["ledger_id"], target_id, target_row["version"]),
    )
    _set_tags(conn, ctx["ledger_id"], target_id, target_tags)
    _set_charged_on(conn, ctx["ledger_id"], target_id, target_after["charged_on"])
    if not target["splits"] and source["splits"]:
        _set_splits(conn, ctx["ledger_id"], target_id, source["splits"])
    if not target["classification_rule_id"] and source["classification_rule_id"]:
        conn.execute(
            "INSERT INTO ledger_transaction_rule_matches(ledger_id, transaction_id, rule_id) VALUES (?, ?, ?)",
            (ctx["ledger_id"], target_id, source["classification_rule_id"]),
        )
    target_revision = _record_revision(conn, ctx, target_id, target_version, "merge_duplicate_transactions", target, _snapshot(conn, ctx["ledger_id"], target_id))
    source_version = source_row["version"] + 1
    now = _now()
    conn.execute(
        "UPDATE ledger_transactions SET voided_at = ?, void_reason = ?, version = ?, updated_at = ? "
        "WHERE ledger_id = ? AND transaction_id = ? AND version = ? AND voided_at IS NULL",
        (now, "Merged into " + target_id, source_version, now, ctx["ledger_id"], source_id, source_row["version"]),
    )
    source_after = _snapshot(conn, ctx["ledger_id"], source_id)
    source_revision = _record_revision(conn, ctx, source_id, source_version, "merge_duplicate_source", source, source_after)
    conn.execute(
        "INSERT INTO ledger_transaction_merge_links(ledger_id, source_transaction_id, target_transaction_id, request_id, actor_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (ctx["ledger_id"], source_id, target_id, ctx["request_id"], ctx["principal_id"], now),
    )
    if payload["source_project_id"] and not payload["target_project_id"]:
        conn.execute("UPDATE project_entries SET transaction_id = ? WHERE ledger_id = ? AND transaction_id = ?", (target_id, ctx["ledger_id"], source_id))
    elif payload["source_project_id"] and payload["target_project_id"] == payload["source_project_id"]:
        conn.execute("DELETE FROM project_entries WHERE ledger_id = ? AND transaction_id = ?", (ctx["ledger_id"], source_id))
    _record_reference(conn, ctx, [target_id, source_id])
    return {"target_transaction_id": target_id, "merged_transaction_id": source_id, "target_version": target_version, "source_version": source_version, "revision_ids": [target_revision, source_revision], "movement_links": movement_link_result["moved_links"], "undoable": False, "ledger": _ledger_info(ctx)}


def _list_transaction_revisions(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, own_only=True)
    transaction_id = _required_text(params.get("transaction_id"), "transaction_id")
    _get_transaction(conn, ctx["ledger_id"], transaction_id)
    limit = params.get("limit", 50)
    if type(limit) is not int or not 1 <= limit <= 100:
        _fail("invalid_input", "limit must be from 1 to 100")
    rows = conn.execute(
        "SELECT revision_id, version, action, request_id, actor_id, before_json, after_json, created_at "
        "FROM ledger_transaction_revisions WHERE ledger_id = ? AND transaction_id = ? ORDER BY version DESC LIMIT ?",
        (ctx["ledger_id"], transaction_id, limit),
    ).fetchall()
    return {
        "transaction_id": transaction_id,
        "revisions": [
            {"revision_id": row["revision_id"], "version": row["version"], "action": row["action"], "request_id": row["request_id"], "actor_id": row["actor_id"], "before": json.loads(row["before_json"]) if row["before_json"] else None, "after": json.loads(row["after_json"]), "created_at": row["created_at"]}
            for row in rows
        ],
        "ledger": _ledger_info(ctx),
    }


def _validated_saved_filters(value: object) -> dict:
    filters = _normalize_filters(value, _SEARCH_FILTERS)
    for field in ("start_date", "end_date"):
        if field in filters:
            _date_value(filters[field], field)
    if filters.get("kind") not in (None, "income", "expense"):
        _fail("invalid_input", "kind must be income or expense")
    for field in ("amount_min_minor", "amount_max_minor"):
        if field in filters and (type(filters[field]) is not int or filters[field] < 0):
            _fail("invalid_input", f"{field} must be a non-negative integer")
    for field in ("category_id", "exclude_category_id"):
        if filters.get(field) is not None:
            _required_text(filters[field], field)
    for field in ("tag", "exclude_tag", "keyword", "exclude_keyword", "merchant", "exclude_merchant", "note_keyword", "project_id"):
        if filters.get(field) is not None:
            _required_text(filters[field], field)
    return filters


def _upsert_saved_transaction_filter(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, write=True)
    name = _required_text(params.get("name"), "name")
    name_key = _tag_key(name)
    filters = _validated_saved_filters(params.get("filters"))
    date_basis = params.get("date_basis", "occurred_on")
    if date_basis not in _DATE_BASES:
        _fail("invalid_input", "date_basis must be occurred_on or charged_on")
    saved = {"filters": filters, "date_basis": date_basis}
    filter_id = params.get("filter_id")
    expected_version = params.get("expected_version")
    if filter_id is None:
        if expected_version is not None:
            _fail("invalid_input", "expected_version requires filter_id")
        conflict = conn.execute(
            "SELECT filter_id FROM ledger_saved_filters WHERE ledger_id = ? AND principal_id = ? AND name_key = ?",
            (ctx["ledger_id"], ctx["principal_id"], name_key),
        ).fetchone()
        if conflict:
            _fail("conflict", "A saved filter with this name already exists", {"filter_id": conflict["filter_id"]})
        filter_id = _new_id()
        version = 1
        now = _now()
        conn.execute(
            "INSERT INTO ledger_saved_filters(ledger_id, principal_id, filter_id, name, name_key, filters_json, version, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ctx["ledger_id"], ctx["principal_id"], filter_id, name, name_key, json.dumps(saved, ensure_ascii=False, sort_keys=True), version, now, now),
        )
    else:
        filter_id = _required_text(filter_id, "filter_id")
        row = conn.execute(
            "SELECT version FROM ledger_saved_filters WHERE ledger_id = ? AND principal_id = ? AND filter_id = ?",
            (ctx["ledger_id"], ctx["principal_id"], filter_id),
        ).fetchone()
        if row is None:
            _fail("not_found", "Saved filter not found", {"filter_id": filter_id})
        if type(expected_version) is not int or row["version"] != expected_version:
            _fail("conflict", "The saved filter changed after it was read", {"filter_id": filter_id, "actual_version": row["version"]})
        conflict = conn.execute(
            "SELECT filter_id FROM ledger_saved_filters WHERE ledger_id = ? AND principal_id = ? AND name_key = ? AND filter_id <> ?",
            (ctx["ledger_id"], ctx["principal_id"], name_key, filter_id),
        ).fetchone()
        if conflict:
            _fail("conflict", "A saved filter with this name already exists", {"filter_id": conflict["filter_id"]})
        version = row["version"] + 1
        conn.execute(
            "UPDATE ledger_saved_filters SET name = ?, name_key = ?, filters_json = ?, version = ?, updated_at = ? "
            "WHERE ledger_id = ? AND principal_id = ? AND filter_id = ? AND version = ?",
            (name, name_key, json.dumps(saved, ensure_ascii=False, sort_keys=True), version, _now(), ctx["ledger_id"], ctx["principal_id"], filter_id, expected_version),
        )
    return {"saved_filter": {"filter_id": filter_id, "name": name, **saved, "version": version}, "ledger": _ledger_info(ctx)}


def _list_saved_transaction_filters(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, own_only=True)
    rows = conn.execute(
        "SELECT filter_id, name, filters_json, version, updated_at FROM ledger_saved_filters WHERE ledger_id = ? AND principal_id = ? ORDER BY name_key",
        (ctx["ledger_id"], ctx["principal_id"]),
    ).fetchall()
    return {
        "saved_filters": [{"filter_id": row["filter_id"], "name": row["name"], **json.loads(row["filters_json"]), "version": row["version"], "updated_at": row["updated_at"]} for row in rows],
        "ledger": _ledger_info(ctx),
    }


def _run_saved_transaction_filter(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, own_only=True)
    filter_id = _required_text(params.get("filter_id"), "filter_id")
    row = conn.execute(
        "SELECT filters_json FROM ledger_saved_filters WHERE ledger_id = ? AND principal_id = ? AND filter_id = ?",
        (ctx["ledger_id"], ctx["principal_id"], filter_id),
    ).fetchone()
    if row is None:
        _fail("not_found", "Saved filter not found", {"filter_id": filter_id})
    saved = json.loads(row["filters_json"])
    return _search_transactions(conn, ctx, {"filters": saved["filters"], "date_basis": saved["date_basis"], "page": params.get("page", {})})


def _continue_transaction_search(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, own_only=True)
    if not ctx["conversation_id"]:
        _fail("needs_clarification", "A trusted conversation ID is required to continue the last search")
    row = conn.execute(
        "SELECT filters_json FROM ledger_search_sessions WHERE ledger_id = ? AND principal_id = ? AND conversation_id = ?",
        (ctx["ledger_id"], ctx["principal_id"], ctx["conversation_id"]),
    ).fetchone()
    if row is None:
        _fail("not_found", "There is no previous search in this conversation")
    previous = json.loads(row["filters_json"])
    incoming = _validated_saved_filters(params.get("filters", {}))
    mode = params.get("mode", "merge")
    if mode not in ("merge", "replace"):
        _fail("invalid_input", "mode must be merge or replace")
    filters = {**previous["filters"], **incoming} if mode == "merge" else incoming
    date_basis = params.get("date_basis", previous["date_basis"] if mode == "merge" else "occurred_on")
    if date_basis not in _DATE_BASES:
        _fail("invalid_input", "date_basis must be occurred_on or charged_on")
    return _search_transactions(conn, ctx, {"filters": filters, "date_basis": date_basis, "page": params.get("page", {})})


def _month_bounds(month: object, today_value: str) -> tuple[str, str, str]:
    if month is None:
        today = date.fromisoformat(today_value)
        year, month_number = today.year, today.month
    else:
        month = _required_text(month, "month")
        if not re.fullmatch(r"\d{4}-\d{2}", month):
            _fail("invalid_input", "month must use YYYY-MM")
        year, month_number = (int(part) for part in month.split("-"))
        if not 1 <= month_number <= 12:
            _fail("invalid_input", "month must use a valid calendar month")
    start = date(year, month_number, 1)
    next_month = date(year + (month_number == 12), 1 if month_number == 12 else month_number + 1, 1)
    return f"{year:04d}-{month_number:02d}", start.isoformat(), (next_month - timedelta(days=1)).isoformat()


def _get_month_end_checklist(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, own_only=True)
    month, start, end = _month_bounds(params.get("month"), ctx["today"])
    limit = params.get("limit", 100)
    if type(limit) is not int or not 1 <= limit <= 100:
        _fail("invalid_input", "limit must be from 1 to 100")
    drafts = conn.execute(
        "SELECT draft_id, fields_json, version, updated_at FROM ledger_transaction_drafts WHERE ledger_id = ? AND principal_id = ? AND status = 'open' ORDER BY updated_at DESC",
        (ctx["ledger_id"], ctx["principal_id"]),
    ).fetchall()
    open_drafts = []
    for row in drafts:
        fields = json.loads(row["fields_json"])
        if fields.get("occurred_on") is None or start <= fields["occurred_on"] <= end:
            open_drafts.append({"draft_id": row["draft_id"], "fields": fields, "version": row["version"], "updated_at": row["updated_at"]})
    base = "FROM ledger_transactions AS t WHERE t.ledger_id = ? AND t.voided_at IS NULL AND t.occurred_on BETWEEN ? AND ? "
    base += "AND NOT EXISTS (SELECT 1 FROM ledger_transaction_merge_links AS merged WHERE merged.ledger_id = t.ledger_id AND merged.source_transaction_id = t.transaction_id) "
    estimated_ids = conn.execute("SELECT t.transaction_id " + base + " AND t.estimated = 1 ORDER BY t.occurred_on, t.transaction_id LIMIT ?", (ctx["ledger_id"], start, end, limit)).fetchall()
    unclassified_ids = conn.execute(
        "SELECT t.transaction_id " + base + " AND t.classification_status = 'unclassified' "
        "AND NOT EXISTS (SELECT 1 FROM ledger_transaction_splits AS split WHERE split.ledger_id = t.ledger_id AND split.transaction_id = t.transaction_id) "
        "ORDER BY t.occurred_on, t.transaction_id LIMIT ?",
        (ctx["ledger_id"], start, end, limit),
    ).fetchall()
    pending_ids = conn.execute(
        "SELECT t.transaction_id FROM ledger_transactions AS t WHERE t.ledger_id = ? AND t.voided_at IS NULL AND t.confirmation_status = 'pending' "
        "AND COALESCE((SELECT d.charged_on FROM ledger_transaction_dates AS d WHERE d.ledger_id = t.ledger_id AND d.transaction_id = t.transaction_id), t.occurred_on) BETWEEN ? AND ? "
        "AND NOT EXISTS (SELECT 1 FROM ledger_transaction_merge_links AS merged WHERE merged.ledger_id = t.ledger_id AND merged.source_transaction_id = t.transaction_id) "
        "ORDER BY t.occurred_on, t.transaction_id LIMIT ?",
        (ctx["ledger_id"], start, end, limit),
    ).fetchall()
    duplicate_rows = conn.execute(
        "SELECT left_row.transaction_id AS left_id, right_row.transaction_id AS right_id "
        "FROM ledger_transactions AS left_row JOIN ledger_transactions AS right_row "
        "ON right_row.ledger_id = left_row.ledger_id AND right_row.transaction_id > left_row.transaction_id "
        "AND right_row.occurred_on = left_row.occurred_on AND right_row.kind = left_row.kind "
        "AND right_row.amount_minor = left_row.amount_minor AND right_row.category_id IS left_row.category_id "
        "WHERE left_row.ledger_id = ? AND left_row.voided_at IS NULL AND right_row.voided_at IS NULL "
        "AND left_row.occurred_on BETWEEN ? AND ? "
        "AND NOT EXISTS (SELECT 1 FROM ledger_transaction_merge_links AS m WHERE m.ledger_id = left_row.ledger_id AND (m.source_transaction_id IN (left_row.transaction_id, right_row.transaction_id))) "
        "ORDER BY left_row.occurred_on, left_id, right_id LIMIT ?",
        (ctx["ledger_id"], start, end, limit),
    ).fetchall()
    return {
        "month": month,
        "period": {"start_date": start, "end_date": end},
        "open_drafts": open_drafts[:limit],
        "estimated_transactions": [_transaction_payload(conn, ctx["ledger_id"], row["transaction_id"]) for row in estimated_ids],
        "unclassified_transactions": [_transaction_payload(conn, ctx["ledger_id"], row["transaction_id"]) for row in unclassified_ids],
        "pending_transactions_by_charge_date": [_transaction_payload(conn, ctx["ledger_id"], row["transaction_id"]) for row in pending_ids],
        "bookkeeping_drafts": _bookkeeping_month_drafts(conn, ctx, start, end),
        "possible_duplicate_pairs": [{"left": _transaction_payload(conn, ctx["ledger_id"], row["left_id"]), "right": _transaction_payload(conn, ctx["ledger_id"], row["right_id"])} for row in duplicate_rows],
        "ledger": _ledger_info(ctx),
    }


def _bookkeeping_month_drafts(conn: sqlite3.Connection, ctx: dict, start: str, end: str) -> list[dict]:
    rows = conn.execute(
        "SELECT draft_id, version, created_at, evidence_sha256 IS NOT NULL AS has_evidence FROM bookkeeping_drafts d "
        "WHERE ledger_id=? AND principal_id=? AND status='open' AND EXISTS "
        "(SELECT 1 FROM json_each(d.items_json) AS i WHERE COALESCE(json_extract(i.value,'$.occurred_on'),"
        "date(d.created_at, '+8 hours')) BETWEEN ? AND ?) ORDER BY created_at DESC LIMIT 100",
        (ctx["ledger_id"], ctx["principal_id"], start, end),
    ).fetchall()
    return [dict(row) for row in rows]


def _get_cashflow_summary(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    start_date, end_date = _period_values(params)
    date_basis = params.get("date_basis", "charged_on")
    if date_basis not in _DATE_BASES:
        _fail("invalid_input", "date_basis must be occurred_on or charged_on")
    filters = _normalize_filters(params.get("filters", {}), _REPORT_FILTERS)
    base = _aggregate_period(conn, ctx, start_date, end_date, filters, date_basis)
    from . import movements

    adjustments = movements.cashflow_adjustments(conn, ctx["ledger_id"], start_date, end_date, date_basis)
    project_id = filters.get("project_id")
    if project_id:
        relevant_transaction_ids = {item["transaction_id"] or item["related_transaction_id"] for item in adjustments if item["transaction_id"] or item["related_transaction_id"]}
        if relevant_transaction_ids:
            placeholders = ",".join("?" for _ in relevant_transaction_ids)
            rows = conn.execute(
                "SELECT transaction_id FROM project_entries WHERE ledger_id = ? AND project_id = ? AND transaction_id IN (" + placeholders + ")",
                [ctx["ledger_id"], project_id, *sorted(relevant_transaction_ids)],
            ).fetchall()
            project_transactions = {row["transaction_id"] for row in rows}
            adjustments = [item for item in adjustments if (item["transaction_id"] or item["related_transaction_id"]) in project_transactions]
        else:
            adjustments = []
    refund_minor = sum(item["amount_minor"] for item in adjustments if item["adjustment_type"] == "refund")
    reimbursement_minor = sum(item["amount_minor"] for item in adjustments if item["adjustment_type"] == "reimbursement_receipt")
    adjustment_income_minor = sum(item["amount_minor"] for item in adjustments if item["kind"] == "income")
    adjustment_expense_minor = sum(item["amount_minor"] for item in adjustments if item["kind"] == "expense")
    transfer_minor = sum(item["amount_minor"] for item in adjustments if item["kind"] == "transfer")
    result = {
        "period": {"start_date": start_date, "end_date": end_date},
        "date_basis": date_basis,
        "expense_minor": base["expense_minor"],
        "refund_minor": refund_minor,
        "reimbursement_receipt_minor": reimbursement_minor,
        "net_spend_minor": base["expense_minor"] - refund_minor - reimbursement_minor,
        "pending_count": base["pending_count"],
        "pending_expense_minor": base["pending_expense_minor"],
        "filters": filters,
        "ledger": _ledger_info(ctx),
    }
    if not ctx["cross_account"]:
        result["income_minor"] = base["income_minor"]
        result["adjustment_income_minor"] = adjustment_income_minor
        result["transfer_minor"] = transfer_minor
        result["adjustment_expense_minor"] = adjustment_expense_minor
        result["adjustment_count"] = len(adjustments)
        result["cashflow_net_minor"] = base["income_minor"] - base["expense_minor"] + adjustment_income_minor - adjustment_expense_minor
        result["adjustments"] = [
            {"adjustment_type": item["adjustment_type"], "kind": item["kind"], "amount_minor": item["amount_minor"], "occurred_on": item["occurred_on"], "charged_on": item["charged_on"], "transaction_id": item["transaction_id"], "related_transaction_id": item["related_transaction_id"]}
            for item in adjustments
        ]
        result["transfers"] = [{"adjustment_type": item["adjustment_type"], "amount_minor": item["amount_minor"], "occurred_on": item["occurred_on"], "charged_on": item["charged_on"]} for item in adjustments if item["kind"] == "transfer"]
    return result


def _get_project_spending(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    project_id = _required_text(params.get("project_id"), "project_id")
    start_date, end_date = _period_values(params)
    filters = _normalize_filters(params.get("filters", {}), _REPORT_FILTERS)
    if filters.get("project_id") and filters["project_id"] != project_id:
        _fail("invalid_input", "project_id cannot conflict with filters.project_id")
    filters["project_id"] = project_id
    result = _get_cashflow_summary(conn, ctx, {"start_date": start_date, "end_date": end_date, "date_basis": params.get("date_basis", "occurred_on"), "filters": filters})
    return {"project_id": project_id, "summary": result, "ledger": _ledger_info(ctx)}


def _get_merchant_breakdown(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_scope(ctx, own_only=True)
    start_date, end_date = _period_values(params)
    date_basis = params.get("date_basis", "occurred_on")
    if date_basis not in _DATE_BASES:
        _fail("invalid_input", "date_basis must be occurred_on or charged_on")
    filters = _normalize_filters(params.get("filters", {}), _REPORT_FILTERS)
    limit = params.get("limit", 100)
    if type(limit) is not int or not 1 <= limit <= 100:
        _fail("invalid_input", "limit must be from 1 to 100")
    clause, values = _filter_sql(conn, ctx, filters, report=True, start_date=start_date, end_date=end_date, date_basis=date_basis)
    rows = conn.execute(
        "SELECT t.transaction_id, t.kind, t.amount_minor, t.confirmation_status, t.estimated, t.merchant "
        "FROM ledger_transactions AS t WHERE " + clause + " AND t.merchant IS NOT NULL ORDER BY t.merchant, t.transaction_id",
        values,
    ).fetchall()
    groups = {}
    for row in rows:
        key, canonical_name = _canonical_merchant(conn, ctx["ledger_id"], row["merchant"])
        group = groups.setdefault(key, {"merchant": canonical_name, "original_names": set(), "income_minor": 0, "expense_minor": 0, "pending_minor": 0, "record_count": 0, "estimated_count": 0})
        group["original_names"].add(row["merchant"])
        group["record_count"] += 1
        group["estimated_count"] += int(row["estimated"])
        if row["confirmation_status"] == "pending":
            group["pending_minor"] += row["amount_minor"]
        else:
            group[row["kind"] + "_minor"] += row["amount_minor"]
    items = [
        {**group, "original_names": sorted(group["original_names"]), "net_minor": group["income_minor"] - group["expense_minor"]}
        for group in groups.values()
    ]
    items.sort(key=lambda item: (-item["expense_minor"], item["merchant"].casefold()))
    if ctx["source"] == "mcp":
        for index, item in enumerate(items, 1):
            item["merchant_label"] = f"商家 {index}"
    return {"period": {"start_date": start_date, "end_date": end_date}, "date_basis": date_basis, "items": items[:limit], "count": len(items), "filters": filters, "ledger": _ledger_info(ctx)}


_STRING = {"type": "string"}
_DATE = {"type": "string", "format": "date"}
_NULLABLE_STRING = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_KIND = {"type": "string", "enum": ["income", "expense"]}
_TAGS = {"type": "array", "items": {"type": "string", "minLength": 1}, "uniqueItems": True}


def _object(properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


_TRANSACTION_PROPERTIES = {
    "kind": _KIND,
    "amount_minor": {"type": "integer", "minimum": 1},
    "occurred_on": _DATE,
    "charged_on": {"anyOf": [_DATE, {"type": "null"}]},
    "template_id": _STRING,
    "estimated": {"type": "boolean"},
    "confirmation_status": {"type": "string", "enum": ["confirmed", "pending"]},
    "category_id": _NULLABLE_STRING,
    "tags": _TAGS,
    "merchant": _NULLABLE_STRING,
    "note": _NULLABLE_STRING,
}

_UPDATE_PROPERTIES = {
    "occurred_on": _DATE,
    "charged_on": {"anyOf": [_DATE, {"type": "null"}]},
    "kind": _KIND,
    "amount_minor": {"type": "integer", "minimum": 1},
    "estimated": {"type": "boolean"},
    "confirmation_status": {"type": "string", "enum": ["confirmed", "pending"]},
    "category_id": _NULLABLE_STRING,
    "tags": _TAGS,
    "merchant": _NULLABLE_STRING,
    "note": _NULLABLE_STRING,
}

_SEARCH_FILTER_PROPERTIES = {
    "start_date": _DATE,
    "end_date": _DATE,
    "kind": _KIND,
    "category_id": {"type": "string", "minLength": 1},
    "tag": {"type": "string", "minLength": 1},
    "keyword": {"type": "string", "minLength": 1},
    "merchant": {"type": "string", "minLength": 1},
    "note_keyword": {"type": "string", "minLength": 1},
    "amount_min_minor": {"type": "integer", "minimum": 0},
    "amount_max_minor": {"type": "integer", "minimum": 0},
    "project_id": {"type": "string", "minLength": 1},
    "exclude_category_id": {"type": "string", "minLength": 1},
    "exclude_tag": {"type": "string", "minLength": 1},
    "exclude_keyword": {"type": "string", "minLength": 1},
    "exclude_merchant": {"type": "string", "minLength": 1},
}
_REPORT_FILTER_PROPERTIES = {key: value for key, value in _SEARCH_FILTER_PROPERTIES.items() if key not in ("start_date", "end_date")}


def _filter_schema(properties: dict) -> dict:
    return _object(properties)


_TRANSACTION_OUTPUT_FIELDS = "transaction_id, version, occurred_on, charged_on, kind, amount_minor, estimated, confirmation_status, classification_status, category_id, category_path, tags, merchant, note, voided, voided_at, void_reason"

_DRAFT_PROPERTIES = {
    "template_id": _STRING,
    "occurred_on": _DATE,
    "charged_on": {"anyOf": [_DATE, {"type": "null"}]},
    "kind": _KIND,
    "amount_minor": {"type": "integer", "minimum": 1},
    "estimated": {"type": "boolean"},
    "confirmation_status": {"type": "string", "enum": ["confirmed", "pending"]},
    "category_id": _NULLABLE_STRING,
    "tags": _TAGS,
    "merchant": _NULLABLE_STRING,
    "note": _NULLABLE_STRING,
}

_RULE_CONDITIONS = _object({
    "merchant": _STRING,
    "keyword": _STRING,
    "amount_min_minor": {"type": "integer", "minimum": 0},
    "amount_max_minor": {"type": "integer", "minimum": 0},
})

_DATE_BASIS = {"type": "string", "enum": ["occurred_on", "charged_on"]}


ACTIONS = {
    "add_transactions": {
        "description": "Add one or more complete income or expense transactions to the current ledger. Omitted dates use the ledger business date; unresolved categories remain unclassified.",
        "inputSchema": _object(
            {"transactions": {"type": "array", "minItems": 1, "maxItems": 100, "items": _object(_TRANSACTION_PROPERTIES, ("amount_minor",))}},
            ("transactions",),
        ),
        "scope": "own_write",
        "run": _add_transactions,
    },
    "get_transaction": {
        "description": "Read a transaction in the caller's own ledger by its ID.",
        "inputSchema": _object({"transaction_id": _STRING}, ("transaction_id",)),
        "scope": "own_read",
        "run": _get_transaction_action,
    },
    "update_transaction": {
        "description": "Update one transaction with an expected version; successful changes create a revision and can be undone.",
        "inputSchema": _object(
            {
                "transaction_id": _STRING,
                "expected_version": {"type": "integer", "minimum": 1},
                "patch": {**_object(_UPDATE_PROPERTIES), "minProperties": 1},
            },
            ("transaction_id", "expected_version", "patch"),
        ),
        "scope": "own_write",
        "run": _update_transaction,
    },
    "void_transaction": {
        "description": "Void one transaction with an expected version; voided transactions are omitted from search and reports.",
        "inputSchema": _object(
            {"transaction_id": _STRING, "expected_version": {"type": "integer", "minimum": 1}, "reason": _NULLABLE_STRING},
            ("transaction_id", "expected_version"),
        ),
        "scope": "own_write",
        "run": _void_transaction,
    },
    "undo_operation": {
        "description": "Undo a prior write by its request_id after checking every affected transaction version.",
        "inputSchema": _object({"target_request_id": _STRING}, ("target_request_id",)),
        "scope": "own_write",
        "run": _undo_operation,
    },
    "resolve_reference": {
        "description": "Resolve a recent transaction or operation in the same trusted caller, ledger, and conversation.",
        "inputSchema": _object({"reference": {"type": "string", "enum": ["last_transaction", "last_operation"]}}, ("reference",)),
        "scope": "own_read",
        "run": _resolve_reference,
    },
    "create_category_or_tag": {
        "description": "When the user asks for a new label, create a category or a tag in the current ledger. "
                       "For a subcategory, use the ID of an active top-level category as parent_category_id. "
                       "An equivalent existing name is reused and returns created=false. "
                       "Use category.category_id or tag.name in transaction tools.",
        "inputSchema": _object({
            "type": {"type": "string", "enum": ["category", "tag"]},
            "name": {"type": "string", "minLength": 1},
            "parent_category_id": {
                "type": "string", "minLength": 1,
                "description": "Optional top-level category ID. Omit for top-level categories and tags.",
            },
        }, ("type", "name")),
        "scope": "own_write",
        "run": _create_category_or_tag,
    },
    "list_categories": {
        "description": "List the stable two-level categories available in the caller's own ledger.",
        "inputSchema": _object({"include_inactive": {"type": "boolean"}}),
        "scope": "own_read",
        "run": _list_categories,
    },
    "list_tags": {
        "description": "List tags in the caller's own ledger.",
        "inputSchema": _object({"query": _STRING, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}),
        "scope": "own_read",
        "run": _list_tags,
    },
    "search_transactions": {
        "description": "Search the caller's own non-voided transactions with full-range totals and cursor-paged details.",
        "inputSchema": _object(
            {
                "filters": _filter_schema(_SEARCH_FILTER_PROPERTIES),
                "page": _object({"limit": {"type": "integer", "minimum": 1, "maximum": 100}, "after": _STRING}),
                "date_basis": _DATE_BASIS,
            }
        ),
        "scope": "own_read",
        "run": _search_transactions,
    },
    "apply_classification": {
        "description": "Change a transaction category and add or remove tags with an expected version.",
        "inputSchema": _object(
            {
                "transaction_id": _STRING,
                "expected_version": {"type": "integer", "minimum": 1},
                "category_id": _NULLABLE_STRING,
                "tags_add": _TAGS,
                "tags_remove": _TAGS,
            },
            ("transaction_id", "expected_version", "category_id"),
        ),
        "scope": "own_write",
        "run": _apply_classification,
    },
    "get_period_summary": {
        "description": "Summarize income, expense, net cash flow, estimates, and pending amounts for an explicit inclusive date period; cross-account access returns spending fields only.",
        "inputSchema": _object(
            {"start_date": _DATE, "end_date": _DATE, "filters": _filter_schema(_REPORT_FILTER_PROPERTIES), "date_basis": _DATE_BASIS},
            ("start_date", "end_date"),
        ),
        "scope": "admin_spending",
        "run": _get_period_summary,
    },
    "get_category_breakdown": {
        "description": "Summarize expense amounts and shares by category for an explicit inclusive date period.",
        "inputSchema": _object(
            {"start_date": _DATE, "end_date": _DATE, "filters": _filter_schema(_REPORT_FILTER_PROPERTIES), "date_basis": _DATE_BASIS},
            ("start_date", "end_date"),
        ),
        "scope": "admin_spending",
        "run": _get_category_breakdown,
    },
    "get_spending_comparison": {
        "description": "Compare spending across two or more explicit date periods using the same filters.",
        "inputSchema": _object(
            {
                "periods": {
                    "type": "array",
                    "minItems": 2,
                    "items": _object({"start_date": _DATE, "end_date": _DATE, "label": _STRING}, ("start_date", "end_date")),
                },
                "filters": _filter_schema(_REPORT_FILTER_PROPERTIES),
                "date_basis": _DATE_BASIS,
            },
            ("periods",),
        ),
        "scope": "admin_spending",
        "run": _get_spending_comparison,
    },
    "evaluate_amount_expression": {
        "description": "Evaluate a supported money expression in yuan and return an exact integer-fen amount for confirmation before recording.",
        "inputSchema": _object({"expression": {"type": "string", "minLength": 1}}, ("expression",)),
        "scope": "own_read",
        "run": _evaluate_amount_expression,
    },
    "upsert_transaction_template": {
        "description": "Create or update a caller-owned transaction template with preset direction, category, tags, merchant, or note.",
        "inputSchema": _object(
            {
                "template_id": _STRING,
                "expected_version": {"type": "integer", "minimum": 1},
                "name": _STRING,
                "defaults": _object({"kind": _KIND, "category_id": _NULLABLE_STRING, "tags": _TAGS, "merchant": _NULLABLE_STRING, "note": _NULLABLE_STRING}, ("kind",)),
            },
            ("name", "defaults"),
        ),
        "scope": "own_write",
        "run": _upsert_transaction_template,
    },
    "list_transaction_templates": {
        "description": "List caller-owned transaction templates.",
        "inputSchema": _object({"query": _STRING}),
        "scope": "own_read",
        "run": _list_transaction_templates,
    },
    "upsert_transaction_draft": {
        "description": "Create or update an incomplete caller-owned transaction draft without adding a ledger transaction.",
        "inputSchema": _object(
            {"draft_id": _STRING, "expected_version": {"type": "integer", "minimum": 1}, "fields": _object(_DRAFT_PROPERTIES)},
            ("fields",),
        ),
        "scope": "own_write",
        "run": _upsert_transaction_draft,
    },
    "copy_transaction_to_draft": {
        "description": "Copy permitted fields from one existing transaction into a new draft. The new amount and occurrence date must be supplied before commit.",
        "inputSchema": _object({"transaction_id": _STRING}, ("transaction_id",)),
        "scope": "own_write",
        "run": _copy_transaction_to_draft,
    },
    "list_transaction_drafts": {
        "description": "List the caller's open or committed drafts, including drafts that can be continued in another chat.",
        "inputSchema": _object({"status": {"type": "string", "enum": ["open", "committed", "all"]}}),
        "scope": "own_read",
        "run": _list_transaction_drafts,
    },
    "commit_transaction_draft": {
        "description": "Commit a complete draft as one transaction using the existing idempotent write receipt.",
        "inputSchema": _object({"draft_id": _STRING, "expected_version": {"type": "integer", "minimum": 1}}, ("draft_id", "expected_version")),
        "scope": "own_write",
        "run": _commit_transaction_draft,
    },
    "list_estimated_transactions": {
        "description": "List estimated transactions for an explicit period or the current ledger business month.",
        "inputSchema": _object({"start_date": _DATE, "end_date": _DATE, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}),
        "scope": "own_read",
        "run": _list_estimated_transactions,
    },
    "confirm_estimated_transaction": {
        "description": "Replace an estimated amount with the confirmed amount and preserve a versioned correction history.",
        "inputSchema": _object(
            {"transaction_id": _STRING, "expected_version": {"type": "integer", "minimum": 1}, "amount_minor": {"type": "integer", "minimum": 1}, "occurred_on": _DATE, "charged_on": {"anyOf": [_DATE, {"type": "null"}]}},
            ("transaction_id", "expected_version", "amount_minor"),
        ),
        "scope": "own_write",
        "run": _confirm_estimated_transaction,
    },
    "set_transaction_splits": {
        "description": "Allocate one transaction across two or more categories while keeping the original total unchanged.",
        "inputSchema": _object(
            {"transaction_id": _STRING, "expected_version": {"type": "integer", "minimum": 1}, "splits": {"type": "array", "minItems": 2, "items": _object({"category_id": _STRING, "amount_minor": {"type": "integer", "minimum": 1}}, ("category_id", "amount_minor"))}},
            ("transaction_id", "expected_version", "splits"),
        ),
        "scope": "own_write",
        "run": _set_transaction_splits,
    },
    "upsert_merchant_alias": {
        "description": "Create or update a normalized merchant alias while retaining original transaction merchant names.",
        "inputSchema": _object({"alias_name": _STRING, "canonical_name": _STRING}, ("alias_name", "canonical_name")),
        "scope": "own_write",
        "run": _upsert_merchant_alias,
    },
    "list_merchant_aliases": {
        "description": "List merchant aliases for review.",
        "inputSchema": _object({"query": _STRING}),
        "scope": "own_read",
        "run": _list_merchant_aliases,
    },
    "upsert_classification_rule": {
        "description": "Create or update a future automatic category rule with explicit conditions and priority.",
        "inputSchema": _object(
            {"rule_id": _STRING, "expected_version": {"type": "integer", "minimum": 1}, "name": _STRING, "conditions": _RULE_CONDITIONS, "category_id": _STRING, "priority": {"type": "integer"}, "active": {"type": "boolean"}},
            ("name", "conditions", "category_id"),
        ),
        "scope": "own_write",
        "run": _upsert_classification_rule,
    },
    "list_classification_rules": {
        "description": "List the caller's automatic classification rules in priority order.",
        "inputSchema": _object({}),
        "scope": "own_read",
        "run": _list_classification_rules,
    },
    "preview_classification_rule": {
        "description": "Preview how a category rule matches existing transactions without changing them.",
        "inputSchema": _object({"conditions": _RULE_CONDITIONS, "category_id": _STRING, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, ("conditions", "category_id")),
        "scope": "own_read",
        "run": _preview_classification_rule,
    },
    "deactivate_classification_rule": {
        "description": "Deactivate one caller-owned classification rule using its expected version.",
        "inputSchema": _object({"rule_id": _STRING, "expected_version": {"type": "integer", "minimum": 1}}, ("rule_id", "expected_version")),
        "scope": "own_write",
        "run": _deactivate_classification_rule,
    },
    "preview_category_change": {
        "description": "Preview the affected transactions and rules before merging a category or deactivating it.",
        "inputSchema": _object({"category_id": _STRING, "target_category_id": _NULLABLE_STRING}, ("category_id",)),
        "scope": "own_write",
        "run": _preview_category_change,
    },
    "apply_category_change": {
        "description": "Apply a previously previewed category merge or deactivation if its affected data has not changed.",
        "inputSchema": _object({"preview_id": _STRING}, ("preview_id",)),
        "scope": "own_write",
        "run": _apply_category_change,
    },
    "preview_tag_merge": {
        "description": "Preview merging tag synonyms or removing unused tags before applying the cleanup.",
        "inputSchema": _object({"source_tags": {"type": "array", "items": _STRING, "uniqueItems": True}, "target_tag": _NULLABLE_STRING}),
        "scope": "own_write",
        "run": _preview_tag_merge,
    },
    "apply_tag_merge": {
        "description": "Apply a previously previewed tag merge and empty-tag cleanup.",
        "inputSchema": _object({"preview_id": _STRING}, ("preview_id",)),
        "scope": "own_write",
        "run": _apply_tag_merge,
    },
    "preview_transaction_bulk_change": {
        "description": "Show the exact transactions and per-record changes before applying a bulk classification update.",
        "inputSchema": _object(
            {
                "filters": _filter_schema(_SEARCH_FILTER_PROPERTIES),
                "transaction_ids": {"type": "array", "items": _STRING, "uniqueItems": True},
                "changes": _object({
                    "category_id": _NULLABLE_STRING,
                    "tags_add": _TAGS,
                    "tags_remove": _TAGS,
                    "estimated": {"type": "boolean"},
                    "confirmation_status": {"type": "string", "enum": ["confirmed", "pending"]},
                }),
            },
            ("changes",),
        ),
        "scope": "own_write",
        "run": _preview_transaction_bulk_change,
    },
    "apply_transaction_bulk_change": {
        "description": "Apply a previously previewed bulk change only when every transaction still has the previewed version.",
        "inputSchema": _object({"preview_id": _STRING}, ("preview_id",)),
        "scope": "own_write",
        "run": _apply_transaction_bulk_change,
    },
    "preview_duplicate_merge": {
        "description": "Compare two same-date, same-direction, same-amount transactions and preview notes, tags, project, and financial links to carry over.",
        "inputSchema": _object(
            {"source_transaction_id": _STRING, "target_transaction_id": _STRING, "source_expected_version": {"type": "integer", "minimum": 1}, "target_expected_version": {"type": "integer", "minimum": 1}},
            ("source_transaction_id", "target_transaction_id", "source_expected_version", "target_expected_version"),
        ),
        "scope": "own_write",
        "run": _preview_duplicate_merge,
    },
    "merge_duplicate_transactions": {
        "description": "Merge a previously previewed duplicate into its selected retained transaction, keeping one financial amount.",
        "inputSchema": _object({"preview_id": _STRING}, ("preview_id",)),
        "scope": "own_write",
        "run": _merge_duplicate_transactions,
    },
    "list_transaction_revisions": {
        "description": "Show versioned correction history for one transaction, including before and after values and operation source.",
        "inputSchema": _object({"transaction_id": _STRING, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, ("transaction_id",)),
        "scope": "own_read",
        "run": _list_transaction_revisions,
    },
    "upsert_saved_transaction_filter": {
        "description": "Save or update a caller-owned transaction search and date basis for reuse.",
        "inputSchema": _object(
            {"filter_id": _STRING, "expected_version": {"type": "integer", "minimum": 1}, "name": _STRING, "filters": _filter_schema(_SEARCH_FILTER_PROPERTIES), "date_basis": _DATE_BASIS},
            ("name", "filters"),
        ),
        "scope": "own_write",
        "run": _upsert_saved_transaction_filter,
    },
    "list_saved_transaction_filters": {
        "description": "List the caller's saved transaction searches.",
        "inputSchema": _object({}),
        "scope": "own_read",
        "run": _list_saved_transaction_filters,
    },
    "run_saved_transaction_filter": {
        "description": "Run a saved transaction search and remember it as the current conversation search when the host supplies a trusted conversation ID.",
        "inputSchema": _object({"filter_id": _STRING, "page": _object({"limit": {"type": "integer", "minimum": 1, "maximum": 100}, "after": _STRING})}, ("filter_id",)),
        "scope": "own_read",
        "run": _run_saved_transaction_filter,
    },
    "continue_transaction_search": {
        "description": "Refine or replace the last search in the same trusted conversation, including category or keyword exclusions.",
        "inputSchema": _object({"filters": _filter_schema(_SEARCH_FILTER_PROPERTIES), "mode": {"type": "string", "enum": ["merge", "replace"]}, "date_basis": _DATE_BASIS, "page": _object({"limit": {"type": "integer", "minimum": 1, "maximum": 100}, "after": _STRING})}),
        "scope": "own_read",
        "run": _continue_transaction_search,
    },
    "get_month_end_checklist": {
        "description": "List drafts, estimates, unclassified transactions, possible duplicates, and pending ledger charges for a calendar month using the ledger business date by default.",
        "inputSchema": _object({"month": {"type": "string", "pattern": "^\\d{4}-\\d{2}$"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}),
        "scope": "own_read",
        "run": _get_month_end_checklist,
    },
    "get_cashflow_summary": {
        "description": "Summarize ledger transactions by consumption or charge date and show refunds, reimbursements, and transfers as separate cashflow adjustments.",
        "inputSchema": _object({"start_date": _DATE, "end_date": _DATE, "date_basis": _DATE_BASIS, "filters": _filter_schema(_REPORT_FILTER_PROPERTIES)}, ("start_date", "end_date")),
        "scope": "admin_spending",
        "run": _get_cashflow_summary,
    },
    "get_project_spending": {
        "description": "Summarize a project's actual ledger spending and linked cashflow adjustments without combining plan or budget values.",
        "inputSchema": _object({"project_id": _STRING, "start_date": _DATE, "end_date": _DATE, "date_basis": _DATE_BASIS, "filters": _filter_schema(_REPORT_FILTER_PROPERTIES)}, ("project_id", "start_date", "end_date")),
        "scope": "admin_spending",
        "run": _get_project_spending,
    },
    "get_merchant_breakdown": {
        "description": "Group a caller's own transaction totals by merchant alias while retaining the original merchant names for review.",
        "inputSchema": _object({"start_date": _DATE, "end_date": _DATE, "date_basis": _DATE_BASIS, "filters": _filter_schema(_REPORT_FILTER_PROPERTIES), "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, ("start_date", "end_date")),
        "scope": "own_read",
        "run": _get_merchant_breakdown,
    },
}

