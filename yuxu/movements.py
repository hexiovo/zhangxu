from __future__ import annotations

import re
import sqlite3
import unicodedata
import uuid
from datetime import date, datetime, timezone

from . import planning
from .contract import BusinessError


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS movement_accounts (
    ledger_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    name TEXT NOT NULL,
    account_type TEXT NOT NULL CHECK (account_type IN ('cash', 'bank', 'e_wallet', 'credit_card')),
    currency TEXT NOT NULL DEFAULT 'CNY' CHECK (currency = 'CNY'),
    opening_balance_minor INTEGER NOT NULL DEFAULT 0 CHECK (opening_balance_minor >= 0),
    statement_day INTEGER CHECK (statement_day IS NULL OR statement_day BETWEEN 1 AND 31),
    due_day INTEGER CHECK (due_day IS NULL OR due_day BETWEEN 1 AND 31),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, account_id)
);

CREATE TABLE IF NOT EXISTS movement_account_balance_checks (
    ledger_id TEXT NOT NULL,
    check_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    balance_date TEXT NOT NULL,
    actual_balance_minor INTEGER NOT NULL,
    book_balance_minor INTEGER NOT NULL,
    difference_minor INTEGER NOT NULL CHECK (difference_minor = actual_balance_minor - book_balance_minor),
    checked_by TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, check_id),
    FOREIGN KEY (ledger_id, account_id) REFERENCES movement_accounts(ledger_id, account_id)
);

CREATE TABLE IF NOT EXISTS movement_events (
    ledger_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    adjustment_type TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('income', 'expense', 'transfer')),
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
    occurred_on TEXT NOT NULL,
    charged_on TEXT,
    transaction_id TEXT,
    related_transaction_id TEXT,
    project_id TEXT,
    subject_id TEXT,
    participant_name TEXT,
    status TEXT NOT NULL DEFAULT 'confirmed' CHECK (status = 'confirmed'),
    request_id TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, event_id),
    FOREIGN KEY (ledger_id, transaction_id) REFERENCES ledger_transactions(ledger_id, transaction_id),
    FOREIGN KEY (ledger_id, related_transaction_id) REFERENCES ledger_transactions(ledger_id, transaction_id)
);

CREATE TABLE IF NOT EXISTS movement_event_reversals (
    ledger_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    reversed_by TEXT NOT NULL,
    reversed_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, event_id),
    FOREIGN KEY (ledger_id, event_id) REFERENCES movement_events(ledger_id, event_id)
);

CREATE VIEW IF NOT EXISTS movement_active_events AS
SELECT v.* FROM movement_events AS v
WHERE NOT EXISTS (
    SELECT 1 FROM movement_event_reversals AS r
    WHERE r.ledger_id = v.ledger_id AND r.event_id = v.event_id
);

CREATE TABLE IF NOT EXISTS movement_account_entries (
    ledger_id TEXT NOT NULL,
    entry_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    delta_minor INTEGER NOT NULL CHECK (delta_minor != 0),
    PRIMARY KEY (ledger_id, entry_id),
    UNIQUE (ledger_id, event_id, account_id),
    FOREIGN KEY (ledger_id, account_id) REFERENCES movement_accounts(ledger_id, account_id),
    FOREIGN KEY (ledger_id, event_id) REFERENCES movement_events(ledger_id, event_id)
);

CREATE TABLE IF NOT EXISTS movement_transaction_accounts (
    ledger_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    PRIMARY KEY (ledger_id, transaction_id),
    FOREIGN KEY (ledger_id, transaction_id) REFERENCES ledger_transactions(ledger_id, transaction_id),
    FOREIGN KEY (ledger_id, account_id) REFERENCES movement_accounts(ledger_id, account_id),
    FOREIGN KEY (ledger_id, event_id) REFERENCES movement_events(ledger_id, event_id)
);

CREATE TABLE IF NOT EXISTS movement_transfers (
    ledger_id TEXT NOT NULL,
    transfer_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    source_account_id TEXT NOT NULL,
    destination_account_id TEXT NOT NULL,
    PRIMARY KEY (ledger_id, transfer_id),
    UNIQUE (ledger_id, event_id),
    FOREIGN KEY (ledger_id, event_id) REFERENCES movement_events(ledger_id, event_id),
    FOREIGN KEY (ledger_id, source_account_id) REFERENCES movement_accounts(ledger_id, account_id),
    FOREIGN KEY (ledger_id, destination_account_id) REFERENCES movement_accounts(ledger_id, account_id)
);

CREATE TABLE IF NOT EXISTS movement_reimbursements (
    ledger_id TEXT NOT NULL,
    reimbursement_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
    project_id TEXT,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, reimbursement_id),
    UNIQUE (ledger_id, transaction_id),
    FOREIGN KEY (ledger_id, transaction_id) REFERENCES ledger_transactions(ledger_id, transaction_id)
);

CREATE TABLE IF NOT EXISTS movement_reimbursement_receipts (
    ledger_id TEXT NOT NULL,
    receipt_id TEXT NOT NULL,
    reimbursement_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    PRIMARY KEY (ledger_id, receipt_id),
    UNIQUE (ledger_id, event_id),
    FOREIGN KEY (ledger_id, reimbursement_id) REFERENCES movement_reimbursements(ledger_id, reimbursement_id),
    FOREIGN KEY (ledger_id, event_id) REFERENCES movement_events(ledger_id, event_id)
);

CREATE TABLE IF NOT EXISTS movement_splits (
    ledger_id TEXT NOT NULL,
    split_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    total_minor INTEGER NOT NULL CHECK (total_minor > 0),
    personal_share_minor INTEGER NOT NULL CHECK (personal_share_minor >= 0),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, split_id),
    UNIQUE (ledger_id, transaction_id),
    FOREIGN KEY (ledger_id, transaction_id) REFERENCES ledger_transactions(ledger_id, transaction_id)
);

CREATE TABLE IF NOT EXISTS movement_split_participants (
    ledger_id TEXT NOT NULL,
    split_id TEXT NOT NULL,
    participant_name TEXT NOT NULL,
    participant_key TEXT NOT NULL,
    owed_minor INTEGER NOT NULL CHECK (owed_minor > 0),
    PRIMARY KEY (ledger_id, split_id, participant_key),
    FOREIGN KEY (ledger_id, split_id) REFERENCES movement_splits(ledger_id, split_id)
);

CREATE TABLE IF NOT EXISTS movement_personal_loans (
    ledger_id TEXT NOT NULL,
    loan_id TEXT NOT NULL,
    counterparty TEXT NOT NULL,
    counterparty_key TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('lend', 'borrow')),
    principal_minor INTEGER NOT NULL CHECK (principal_minor > 0),
    occurred_on TEXT NOT NULL,
    charged_on TEXT,
    account_id TEXT,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, loan_id),
    FOREIGN KEY (ledger_id, account_id) REFERENCES movement_accounts(ledger_id, account_id)
);

CREATE INDEX IF NOT EXISTS idx_movement_entries_account
    ON movement_account_entries(ledger_id, account_id, entry_id);
CREATE INDEX IF NOT EXISTS idx_movement_events_period
    ON movement_events(ledger_id, occurred_on, charged_on, status);
CREATE INDEX IF NOT EXISTS idx_movement_balance_checks_latest
    ON movement_account_balance_checks(ledger_id, account_id, checked_at);
CREATE INDEX IF NOT EXISTS idx_movement_events_related
    ON movement_events(ledger_id, related_transaction_id, adjustment_type);
CREATE INDEX IF NOT EXISTS idx_movement_loans_counterparty
    ON movement_personal_loans(ledger_id, counterparty_key, occurred_on);
"""


_STRING = {"type": "string", "minLength": 1}
_DATE = {"type": "string", "format": "date"}
_NULLABLE_STRING = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_AMOUNT = {"type": "integer", "minimum": 1}


def _object(properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


def _fail(status: str, message: str, details: dict | None = None) -> None:
    raise BusinessError(status, message, details)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _new_id() -> str:
    return str(uuid.uuid4())


def _text(value: object, field: str) -> str:
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


def _write(ctx: dict) -> None:
    if ctx["cross_account"]:
        _fail("forbidden", "Cross-account writes are not allowed")
    if not ctx["request_id"]:
        _fail("invalid_input", "A request_id is required for writes")


def _own_read(ctx: dict) -> None:
    if ctx["cross_account"]:
        _fail("forbidden", "This action is available only in the caller's own ledger")


def _ledger_info(ctx: dict) -> dict:
    return {"ledger_id": ctx["ledger_id"], "account_id": ctx["account_id"], "account_name": ctx["account_name"]}


def _date_params(params: dict, field: str, default: str | None = None) -> str:
    value = params.get(field, default)
    if value is None:
        _fail("needs_clarification", f"{field} is required", {"field": field})
    return _date_value(value, field)


def _event(
    conn: sqlite3.Connection,
    ctx: dict,
    *,
    event_type: str,
    adjustment_type: str,
    kind: str,
    amount_minor: int,
    occurred_on: str,
    charged_on: str | None = None,
    transaction_id: str | None = None,
    related_transaction_id: str | None = None,
    project_id: str | None = None,
    subject_id: str | None = None,
    participant_name: str | None = None,
) -> str:
    planning.assert_period_open(conn, ctx, occurred_on, action_name=event_type)
    if charged_on and charged_on[:7] != occurred_on[:7]:
        planning.assert_period_open(conn, ctx, charged_on, action_name=event_type)
    event_id = _new_id()
    conn.execute(
        "INSERT INTO movement_events(ledger_id, event_id, event_type, adjustment_type, kind, amount_minor, "
        "occurred_on, charged_on, transaction_id, related_transaction_id, project_id, subject_id, "
        "participant_name, request_id, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ctx["ledger_id"], event_id, event_type, adjustment_type, kind, amount_minor, occurred_on,
            charged_on or occurred_on, transaction_id, related_transaction_id, project_id, subject_id,
            participant_name, ctx["request_id"], ctx["principal_id"], _now(),
        ),
    )
    return event_id


def _account(conn: sqlite3.Connection, ledger_id: str, account_id: object, *, active: bool = True) -> sqlite3.Row:
    account_id = _text(account_id, "account_id")
    row = conn.execute(
        "SELECT * FROM movement_accounts WHERE ledger_id = ? AND account_id = ?",
        (ledger_id, account_id),
    ).fetchone()
    if row is None or active and row["status"] != "active":
        _fail("not_found", "Payment account not found or inactive in this ledger", {"account_id": account_id})
    return row


def _entry(conn: sqlite3.Connection, ledger_id: str, account_id: str, event_id: str, delta_minor: int) -> None:
    conn.execute(
        "INSERT INTO movement_account_entries(ledger_id, entry_id, account_id, event_id, delta_minor) VALUES (?, ?, ?, ?, ?)",
        (ledger_id, _new_id(), account_id, event_id, delta_minor),
    )


def _balance(conn: sqlite3.Connection, account: sqlite3.Row, as_of: str | None = None) -> int:
    query = (
        "SELECT COALESCE(SUM(e.delta_minor), 0) AS movements FROM movement_account_entries e "
        "JOIN movement_active_events v ON v.ledger_id = e.ledger_id AND v.event_id = e.event_id "
        "WHERE e.ledger_id = ? AND e.account_id = ? AND v.status = 'confirmed' "
        "AND v.event_type != 'transaction_payment'"
    )
    values: list[object] = [account["ledger_id"], account["account_id"]]
    if as_of is not None:
        query += " AND COALESCE(v.charged_on, v.occurred_on) <= ?"
        values.append(as_of)
    movements = conn.execute(query, values).fetchone()["movements"]
    transaction_query = (
        "SELECT COALESCE(SUM(CASE WHEN t.kind = 'expense' THEN -1 ELSE 1 END * t.amount_minor), 0) AS movements "
        "FROM movement_transaction_accounts a JOIN ledger_transactions t "
        "ON t.ledger_id = a.ledger_id AND t.transaction_id = a.transaction_id "
        "LEFT JOIN ledger_transaction_dates d ON d.ledger_id = t.ledger_id AND d.transaction_id = t.transaction_id "
        "WHERE a.ledger_id = ? AND a.account_id = ? AND t.confirmation_status = 'confirmed' AND t.voided_at IS NULL"
    )
    transaction_values: list[object] = [account["ledger_id"], account["account_id"]]
    if as_of is not None:
        transaction_query += " AND COALESCE(d.charged_on, t.occurred_on) <= ?"
        transaction_values.append(as_of)
    linked = conn.execute(transaction_query, transaction_values).fetchone()["movements"]
    if account["account_type"] == "credit_card":
        linked = -linked
    return account["opening_balance_minor"] + movements + linked


def _account_payload(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    return {
        "account_id": row["account_id"],
        "name": row["name"],
        "account_type": row["account_type"],
        "currency": row["currency"],
        "opening_balance_minor": row["opening_balance_minor"],
        "balance_minor": _balance(conn, row),
        "statement_day": row["statement_day"],
        "due_day": row["due_day"],
        "status": row["status"],
        "version": row["version"],
    }


def _transaction(conn: sqlite3.Connection, ledger_id: str, transaction_id: object, expected_version: object) -> sqlite3.Row:
    transaction_id = _text(transaction_id, "transaction_id")
    row = conn.execute(
        "SELECT t.transaction_id, t.version, t.occurred_on, t.kind, t.amount_minor, t.confirmation_status, "
        "t.voided_at, d.charged_on FROM ledger_transactions t LEFT JOIN ledger_transaction_dates d "
        "ON d.ledger_id = t.ledger_id AND d.transaction_id = t.transaction_id "
        "WHERE t.ledger_id = ? AND t.transaction_id = ?",
        (ledger_id, transaction_id),
    ).fetchone()
    if row is None:
        _fail("not_found", "Transaction not found in this ledger", {"transaction_id": transaction_id})
    if type(expected_version) is not int or expected_version < 1:
        _fail("invalid_input", "expected_version must be a positive integer")
    if row["version"] != expected_version:
        _fail("conflict", "The transaction changed after it was read", {
            "transaction_id": transaction_id, "expected_version": expected_version, "actual_version": row["version"],
        })
    if row["voided_at"] is not None or row["confirmation_status"] != "confirmed":
        _fail("conflict", "Only confirmed, non-voided transactions can be linked")
    return row


def _subject_version(row: sqlite3.Row, expected_version: object, subject: str) -> None:
    if type(expected_version) is not int or expected_version < 1:
        _fail("invalid_input", "expected_version must be a positive integer")
    if row["version"] != expected_version:
        _fail("conflict", f"The {subject} changed after it was read", {
            "expected_version": expected_version, "actual_version": row["version"],
        })


def _create_or_update_account(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _write(ctx)
    name = _text(params.get("name"), "name")
    account_type = params.get("account_type")
    if account_type not in ("cash", "bank", "e_wallet", "credit_card"):
        _fail("invalid_input", "account_type must be cash, bank, e_wallet, or credit_card")
    statement_day = params.get("statement_day")
    due_day = params.get("due_day")
    for field, value in (("statement_day", statement_day), ("due_day", due_day)):
        if value is not None and (type(value) is not int or not 1 <= value <= 31):
            _fail("invalid_input", f"{field} must be an integer from 1 to 31 or null", {"field": field})
    if account_type != "credit_card" and (statement_day is not None or due_day is not None):
        _fail("invalid_input", "Billing days apply only to credit card accounts")

    ledger_id = ctx["ledger_id"]
    account_id = params.get("account_id")
    if account_id is None:
        opening = params.get("opening_balance_minor", 0)
        if type(opening) is not int or opening < 0:
            _fail("invalid_input", "opening_balance_minor must be a non-negative integer")
        if params.get("status", "active") != "active":
            _fail("invalid_input", "A payment account must be created as active")
        account_id = _new_id()
        now = _now()
        conn.execute(
            "INSERT INTO movement_accounts(ledger_id, account_id, name, account_type, opening_balance_minor, "
            "statement_day, due_day, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ledger_id, account_id, name, account_type, opening, statement_day, due_day, now, now),
        )
    else:
        row = _account(conn, ledger_id, account_id, active=False)
        _subject_version(row, params.get("expected_version"), "payment account")
        if row["account_type"] != account_type:
            _fail("conflict", "The account type cannot be changed")
        if "opening_balance_minor" in params and params["opening_balance_minor"] != row["opening_balance_minor"]:
            _fail("conflict", "The opening balance is fixed when an account is created")
        status = params.get("status", row["status"])
        if status not in ("active", "archived"):
            _fail("invalid_input", "status must be active or archived")
        if status == "archived" and _balance(conn, row) != 0:
            _fail("conflict", "A payment account can be archived only with a zero balance")
        conn.execute(
            "UPDATE movement_accounts SET name = ?, statement_day = ?, due_day = ?, status = ?, "
            "version = version + 1, updated_at = ? WHERE ledger_id = ? AND account_id = ?",
            (name, params.get("statement_day", row["statement_day"]), params.get("due_day", row["due_day"]), status, _now(), ledger_id, account_id),
        )
    row = _account(conn, ledger_id, account_id, active=False)
    return {"account": _account_payload(conn, row), "ledger": _ledger_info(ctx)}


def _list_accounts(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_read(ctx)
    rows = conn.execute(
        "SELECT * FROM movement_accounts WHERE ledger_id = ? AND status = 'active' ORDER BY name, account_id",
        (ctx["ledger_id"],),
    ).fetchall()
    return {"accounts": [_account_payload(conn, row) for row in rows], "ledger": _ledger_info(ctx)}


def _record_payment_account_balance_check(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _write(ctx)
    account = _account(conn, ctx["ledger_id"], params.get("account_id"))
    balance_date = _date_value(params.get("balance_date"), "balance_date")
    actual_balance = params.get("actual_balance_minor")
    if type(actual_balance) is not int:
        _fail("invalid_input", "actual_balance_minor must be an integer number of fen")
    book_balance = _balance(conn, account, balance_date)
    difference = actual_balance - book_balance
    check_id = _new_id()
    checked_at = _now()
    conn.execute(
        "INSERT INTO movement_account_balance_checks(ledger_id, check_id, account_id, balance_date, actual_balance_minor, book_balance_minor, difference_minor, checked_by, checked_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ctx["ledger_id"], check_id, account["account_id"], balance_date, actual_balance, book_balance, difference, ctx["principal_id"], checked_at),
    )
    return {
        "check": {"check_id": check_id, "account_id": account["account_id"], "balance_date": balance_date,
                  "actual_balance_minor": actual_balance, "book_balance_minor": book_balance,
                  "difference_minor": difference, "checked_at": checked_at},
        "ledger": _ledger_info(ctx),
    }


def _get_payment_account_balance_check(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_read(ctx)
    account = _account(conn, ctx["ledger_id"], params.get("account_id"), active=False)
    row = conn.execute(
        "SELECT check_id, account_id, balance_date, actual_balance_minor, book_balance_minor, difference_minor, checked_at "
        "FROM movement_account_balance_checks WHERE ledger_id = ? AND account_id = ? "
        "ORDER BY checked_at DESC, rowid DESC LIMIT 1",
        (ctx["ledger_id"], account["account_id"]),
    ).fetchone()
    return {"check": dict(row) if row else None, "ledger": _ledger_info(ctx)}


def _account_activity(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_read(ctx)
    account = _account(conn, ctx["ledger_id"], params.get("account_id"), active=False)
    start = _date_params(params, "start_date")
    end = _date_params(params, "end_date")
    date_basis = params.get("date_basis", "occurred_on")
    if start > end:
        _fail("invalid_input", "start_date must not be after end_date")
    if date_basis not in ("occurred_on", "charged_on"):
        _fail("invalid_input", "date_basis must be occurred_on or charged_on")
    date_column = "v.occurred_on" if date_basis == "occurred_on" else "COALESCE(v.charged_on, v.occurred_on)"
    transaction_date = "t.occurred_on" if date_basis == "occurred_on" else "COALESCE(d.charged_on, t.occurred_on)"
    rows = conn.execute(
        "SELECT v.event_id, v.event_type, v.adjustment_type, v.amount_minor, v.kind, v.occurred_on, v.charged_on, "
        "v.transaction_id, v.related_transaction_id, e.delta_minor FROM movement_account_entries e "
        "JOIN movement_active_events v ON v.ledger_id = e.ledger_id AND v.event_id = e.event_id "
        f"WHERE e.ledger_id = ? AND e.account_id = ? AND v.status = 'confirmed' AND v.event_type != 'transaction_payment' "
        f"AND {date_column} BETWEEN ? AND ? "
        f"ORDER BY {date_column}, v.event_id",
        (ctx["ledger_id"], account["account_id"], start, end),
    ).fetchall()
    linked_rows = conn.execute(
        "SELECT v.event_id, v.event_type, v.adjustment_type, t.amount_minor, t.kind AS transaction_kind, "
        "t.occurred_on, COALESCE(d.charged_on, t.occurred_on) AS charged_on, t.transaction_id, "
        "t.transaction_id AS related_transaction_id, t.kind AS transaction_kind "
        "FROM movement_transaction_accounts a JOIN ledger_transactions t "
        "ON t.ledger_id = a.ledger_id AND t.transaction_id = a.transaction_id "
        "JOIN movement_active_events v ON v.ledger_id = a.ledger_id AND v.event_id = a.event_id "
        "LEFT JOIN ledger_transaction_dates d ON d.ledger_id = t.ledger_id AND d.transaction_id = t.transaction_id "
        f"WHERE a.ledger_id = ? AND a.account_id = ? AND t.confirmation_status = 'confirmed' AND t.voided_at IS NULL "
        f"AND {transaction_date} BETWEEN ? AND ? ORDER BY {transaction_date}, t.transaction_id",
        (ctx["ledger_id"], account["account_id"], start, end),
    ).fetchall()
    activity = [dict(row) for row in rows]
    for row in linked_rows:
        delta = row["amount_minor"] if row["transaction_kind"] == "income" else -row["amount_minor"]
        if account["account_type"] == "credit_card":
            delta = -delta
        activity.append({
            "event_id": row["event_id"], "event_type": row["event_type"], "adjustment_type": row["adjustment_type"],
            "amount_minor": row["amount_minor"], "kind": row["transaction_kind"], "occurred_on": row["occurred_on"],
            "charged_on": row["charged_on"], "transaction_id": row["transaction_id"],
            "related_transaction_id": row["related_transaction_id"], "delta_minor": delta,
        })
    activity.sort(key=lambda item: (item["charged_on"] if date_basis == "charged_on" else item["occurred_on"], item["event_id"]))
    return {"account": _account_payload(conn, account), "start_date": start, "end_date": end,
            "date_basis": date_basis, "activity": activity, "ledger": _ledger_info(ctx)}


def _record_account_transaction(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _write(ctx)
    transaction = _transaction(conn, ctx["ledger_id"], params.get("transaction_id"), params.get("expected_version"))
    account = _account(conn, ctx["ledger_id"], params.get("account_id"))
    if conn.execute(
        "SELECT 1 FROM movement_transaction_accounts WHERE ledger_id = ? AND transaction_id = ?",
        (ctx["ledger_id"], transaction["transaction_id"]),
    ).fetchone():
        _fail("conflict", "The transaction is already linked to a payment account")
    amount = transaction["amount_minor"]
    event_id = _event(
        conn, ctx, event_type="transaction_payment", adjustment_type="transaction_payment", kind="transfer",
        amount_minor=amount, occurred_on=transaction["occurred_on"], charged_on=transaction["charged_on"],
        transaction_id=transaction["transaction_id"], related_transaction_id=transaction["transaction_id"],
    )
    conn.execute(
        "INSERT INTO movement_transaction_accounts(ledger_id, transaction_id, account_id, event_id) VALUES (?, ?, ?, ?)",
        (ctx["ledger_id"], transaction["transaction_id"], account["account_id"], event_id),
    )
    return {"transaction_id": transaction["transaction_id"], "account": _account_payload(conn, _account(conn, ctx["ledger_id"], account["account_id"])), "ledger": _ledger_info(ctx)}


def _transfer(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _write(ctx)
    ledger_id = ctx["ledger_id"]
    source = _account(conn, ledger_id, params.get("source_account_id"))
    destination = _account(conn, ledger_id, params.get("destination_account_id"))
    if source["account_id"] == destination["account_id"]:
        _fail("invalid_input", "Source and destination accounts must differ")
    if source["account_type"] == "credit_card" or destination["account_type"] == "credit_card":
        _fail("invalid_input", "Internal transfers require two asset accounts; use credit card repayment for a card")
    amount = _amount(params.get("amount_minor"))
    if _balance(conn, source) < amount:
        _fail("conflict", "The source account does not have enough available balance")
    occurred_on = _date_params(params, "occurred_on")
    transfer_id = _new_id()
    event_id = _event(
        conn, ctx, event_type="transfer", adjustment_type="transfer", kind="transfer",
        amount_minor=amount, occurred_on=occurred_on,
    )
    _entry(conn, ledger_id, source["account_id"], event_id, -amount)
    _entry(conn, ledger_id, destination["account_id"], event_id, amount)
    conn.execute(
        "INSERT INTO movement_transfers(ledger_id, transfer_id, event_id, source_account_id, destination_account_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (ledger_id, transfer_id, event_id, source["account_id"], destination["account_id"]),
    )
    return {"transfer": get_transfer(conn, ctx, transfer_id), "ledger": _ledger_info(ctx)}


def get_transfer(conn: sqlite3.Connection, ctx: dict, transfer_id: str) -> dict | None:
    _own_read(ctx)
    row = conn.execute(
        "SELECT t.transfer_id, t.ledger_id, t.source_account_id, s.name AS source_account_name, "
        "t.destination_account_id, d.name AS destination_account_name, e.amount_minor, e.occurred_on, e.status "
        "FROM movement_transfers t JOIN movement_active_events e ON e.ledger_id = t.ledger_id AND e.event_id = t.event_id "
        "JOIN movement_accounts s ON s.ledger_id = t.ledger_id AND s.account_id = t.source_account_id "
        "JOIN movement_accounts d ON d.ledger_id = t.ledger_id AND d.account_id = t.destination_account_id "
        "WHERE t.ledger_id = ? AND t.transfer_id = ? AND e.status = 'confirmed'",
        (ctx["ledger_id"], transfer_id),
    ).fetchone()
    return dict(row) if row else None


def _credit_card_due(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_read(ctx)
    ledger_id = ctx["ledger_id"]
    account = _account(conn, ledger_id, params.get("account_id"))
    if account["account_type"] != "credit_card":
        _fail("invalid_input", "account_id must refer to a credit card")
    start = _date_params(params, "start_date")
    end = _date_params(params, "end_date")
    if start > end:
        _fail("invalid_input", "start_date must not be after end_date")
    charges = conn.execute(
        "SELECT COALESCE(SUM(t.amount_minor), 0) AS total FROM movement_transaction_accounts a "
        "JOIN ledger_transactions t ON t.ledger_id = a.ledger_id AND t.transaction_id = a.transaction_id "
        "LEFT JOIN ledger_transaction_dates d ON d.ledger_id = t.ledger_id AND d.transaction_id = t.transaction_id "
        "WHERE a.ledger_id = ? AND a.account_id = ? AND t.kind = 'expense' AND t.confirmation_status = 'confirmed' "
        "AND t.voided_at IS NULL AND COALESCE(d.charged_on, t.occurred_on) BETWEEN ? AND ?",
        (ledger_id, account["account_id"], start, end),
    ).fetchone()["total"]
    refunds = conn.execute(
        "SELECT COALESCE(SUM(-a.delta_minor), 0) AS total FROM movement_account_entries a "
        "JOIN movement_active_events e ON e.ledger_id = a.ledger_id AND e.event_id = a.event_id "
        "WHERE a.ledger_id = ? AND a.account_id = ? AND e.event_type = 'refund' AND a.delta_minor < 0 "
        "AND COALESCE(e.charged_on, e.occurred_on) BETWEEN ? AND ?",
        (ledger_id, account["account_id"], start, end),
    ).fetchone()["total"]
    payments = conn.execute(
        "SELECT COALESCE(SUM(e.amount_minor), 0) AS total FROM movement_account_entries a "
        "JOIN movement_active_events e ON e.ledger_id = a.ledger_id AND e.event_id = a.event_id "
        "WHERE a.ledger_id = ? AND a.account_id = ? AND e.event_type = 'credit_card_repayment' "
        "AND COALESCE(e.charged_on, e.occurred_on) BETWEEN ? AND ?",
        (ledger_id, account["account_id"], start, end),
    ).fetchone()["total"]
    return {
        "account_id": account["account_id"], "start_date": start, "end_date": end,
        "statement_charges_minor": charges, "refunds_minor": refunds,
        "repayments_minor": payments, "period_net_due_minor": charges - refunds - payments,
        "balance_due_minor": _balance(conn, account, end), "ledger": _ledger_info(ctx),
    }


def _credit_card_repayment(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _write(ctx)
    ledger_id = ctx["ledger_id"]
    card = _account(conn, ledger_id, params.get("account_id"))
    source = _account(conn, ledger_id, params.get("source_account_id"))
    if card["account_type"] != "credit_card" or source["account_type"] == "credit_card":
        _fail("invalid_input", "A credit card and an asset payment account are required")
    amount = _amount(params.get("amount_minor"))
    if amount > _balance(conn, card):
        _fail("conflict", "The repayment exceeds the current credit card balance")
    if amount > _balance(conn, source):
        _fail("conflict", "The payment account does not have enough available balance")
    occurred_on = _date_params(params, "occurred_on")
    event_id = _event(
        conn, ctx, event_type="credit_card_repayment", adjustment_type="credit_card_repayment", kind="transfer",
        amount_minor=amount, occurred_on=occurred_on,
    )
    _entry(conn, ledger_id, source["account_id"], event_id, -amount)
    _entry(conn, ledger_id, card["account_id"], event_id, -amount)
    return {
        "amount_minor": amount,
        "source_balance_minor": _balance(conn, source),
        "card_balance_minor": _balance(conn, card),
        "event_id": event_id,
        "ledger": _ledger_info(ctx),
    }


def _record_refund(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _write(ctx)
    transaction = _transaction(conn, ctx["ledger_id"], params.get("transaction_id"), params.get("expected_version"))
    if transaction["kind"] != "expense":
        _fail("needs_clarification", "A refund must be linked to an expense transaction")
    amount = _amount(params.get("amount_minor"))
    refunded = conn.execute(
        "SELECT COALESCE(SUM(amount_minor), 0) AS total FROM movement_active_events "
        "WHERE ledger_id = ? AND event_type = 'refund' AND related_transaction_id = ? AND status = 'confirmed'",
        (ctx["ledger_id"], transaction["transaction_id"]),
    ).fetchone()["total"]
    if refunded + amount > transaction["amount_minor"]:
        _fail("conflict", "The refund exceeds the remaining amount of the original expense", {
            "original_amount_minor": transaction["amount_minor"], "already_refunded_minor": refunded,
        })
    occurred_on = _date_params(params, "occurred_on")
    charged_on = _date_value(params["charged_on"], "charged_on") if params.get("charged_on") is not None else occurred_on
    account_id = params.get("account_id")
    account = _account(conn, ctx["ledger_id"], account_id) if account_id else None
    event_id = _event(
        conn, ctx, event_type="refund", adjustment_type="refund", kind="income", amount_minor=amount,
        occurred_on=occurred_on, charged_on=charged_on, related_transaction_id=transaction["transaction_id"],
    )
    if account:
        delta = -amount if account["account_type"] == "credit_card" else amount
        if account["account_type"] == "credit_card":
            linked = conn.execute(
                "SELECT 1 FROM movement_transaction_accounts WHERE ledger_id = ? AND transaction_id = ? AND account_id = ?",
                (ctx["ledger_id"], transaction["transaction_id"], account["account_id"]),
            ).fetchone()
            if not linked:
                _fail("conflict", "A card refund must return to the card used for the original expense")
        _entry(conn, ctx["ledger_id"], account["account_id"], event_id, delta)
    return {
        "refund": {"event_id": event_id, "transaction_id": transaction["transaction_id"],
                   "amount_minor": amount, "refunded_total_minor": refunded + amount,
                   "net_spend_minor": transaction["amount_minor"] - refunded - amount},
        "ledger": _ledger_info(ctx),
    }


def _record_reimbursement_advance(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _write(ctx)
    transaction = _transaction(conn, ctx["ledger_id"], params.get("transaction_id"), params.get("expected_version"))
    if transaction["kind"] != "expense":
        _fail("needs_clarification", "A reimbursement advance must link to an expense transaction")
    amount = _amount(params.get("amount_minor"))
    if amount > transaction["amount_minor"]:
        _fail("invalid_input", "The reimbursement amount cannot exceed the original expense")
    reimbursement_id = _new_id()
    project_id = params.get("project_id")
    if project_id is not None:
        project_id = _text(project_id, "project_id")
        from . import planning

        planning.ACTIONS["link_project_transaction"]["run"](
            conn, ctx, {"project_id": project_id, "transaction_id": transaction["transaction_id"]}
        )
    conn.execute(
        "INSERT INTO movement_reimbursements(ledger_id, reimbursement_id, transaction_id, amount_minor, project_id, created_by, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ctx["ledger_id"], reimbursement_id, transaction["transaction_id"], amount, project_id, ctx["principal_id"], _now()),
    )
    return {
        "reimbursement": {"reimbursement_id": reimbursement_id, "transaction_id": transaction["transaction_id"],
                          "amount_minor": amount, "received_minor": 0, "outstanding_minor": amount,
                          "project_id": project_id, "status": "pending", "version": 1},
        "ledger": _ledger_info(ctx),
    }


def _record_reimbursement_receipt(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _write(ctx)
    reimbursement_id = _text(params.get("reimbursement_id"), "reimbursement_id")
    row = conn.execute(
        "SELECT * FROM movement_reimbursements WHERE ledger_id = ? AND reimbursement_id = ?",
        (ctx["ledger_id"], reimbursement_id),
    ).fetchone()
    if row is None:
        _fail("not_found", "Reimbursement not found in this ledger", {"reimbursement_id": reimbursement_id})
    _subject_version(row, params.get("expected_version"), "reimbursement")
    amount = _amount(params.get("amount_minor"))
    received = conn.execute(
        "SELECT COALESCE(SUM(e.amount_minor), 0) AS total FROM movement_reimbursement_receipts r "
        "JOIN movement_active_events e ON e.ledger_id = r.ledger_id AND e.event_id = r.event_id "
        "WHERE r.ledger_id = ? AND r.reimbursement_id = ? AND e.status = 'confirmed'",
        (ctx["ledger_id"], reimbursement_id),
    ).fetchone()["total"]
    if received + amount > row["amount_minor"]:
        _fail("conflict", "The receipt exceeds the outstanding reimbursement amount", {
            "advance_minor": row["amount_minor"], "received_minor": received,
        })
    occurred_on = _date_params(params, "occurred_on")
    charged_on = _date_value(params["charged_on"], "charged_on") if params.get("charged_on") is not None else occurred_on
    account = _account(conn, ctx["ledger_id"], params["account_id"]) if params.get("account_id") else None
    if account and account["account_type"] == "credit_card":
        _fail("invalid_input", "Reimbursement receipts must be deposited to a cash, bank, or e-wallet account")
    event_id = _event(
        conn, ctx, event_type="reimbursement_receipt", adjustment_type="reimbursement_receipt", kind="income",
        amount_minor=amount, occurred_on=occurred_on, charged_on=charged_on,
        related_transaction_id=row["transaction_id"], project_id=row["project_id"], subject_id=reimbursement_id,
    )
    receipt_id = _new_id()
    conn.execute(
        "INSERT INTO movement_reimbursement_receipts(ledger_id, receipt_id, reimbursement_id, event_id) VALUES (?, ?, ?, ?)",
        (ctx["ledger_id"], receipt_id, reimbursement_id, event_id),
    )
    if account:
        _entry(conn, ctx["ledger_id"], account["account_id"], event_id, amount)
    conn.execute(
        "UPDATE movement_reimbursements SET version = version + 1 WHERE ledger_id = ? AND reimbursement_id = ?",
        (ctx["ledger_id"], reimbursement_id),
    )
    received += amount
    return {
        "reimbursement": {"reimbursement_id": reimbursement_id, "transaction_id": row["transaction_id"],
                          "advance_minor": row["amount_minor"], "received_minor": received,
                          "outstanding_minor": row["amount_minor"] - received,
                          "project_id": row["project_id"],
                          "status": "received" if received == row["amount_minor"] else "partial",
                          "version": row["version"] + 1},
        "ledger": _ledger_info(ctx),
    }


def _list_pending_reimbursements(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_read(ctx)
    rows = conn.execute(
        "SELECT r.reimbursement_id, r.transaction_id, r.amount_minor, r.project_id, r.version, t.occurred_on, "
        "t.merchant, t.note, COALESCE(SUM(e.amount_minor), 0) AS received_minor "
        "FROM movement_reimbursements r JOIN ledger_transactions t ON t.ledger_id = r.ledger_id AND t.transaction_id = r.transaction_id "
        "LEFT JOIN movement_reimbursement_receipts x ON x.ledger_id = r.ledger_id AND x.reimbursement_id = r.reimbursement_id "
        "LEFT JOIN movement_active_events e ON e.ledger_id = x.ledger_id AND e.event_id = x.event_id AND e.status = 'confirmed' "
        "WHERE r.ledger_id = ? GROUP BY r.ledger_id, r.reimbursement_id "
        "HAVING COALESCE(SUM(e.amount_minor), 0) < r.amount_minor ORDER BY t.occurred_on, r.reimbursement_id",
        (ctx["ledger_id"],),
    ).fetchall()
    return {"reimbursements": [
        {"reimbursement_id": row["reimbursement_id"], "transaction_id": row["transaction_id"],
         "occurred_on": row["occurred_on"], "merchant": row["merchant"], "note": row["note"],
         "advance_minor": row["amount_minor"], "received_minor": row["received_minor"],
         "outstanding_minor": row["amount_minor"] - row["received_minor"], "project_id": row["project_id"],
         "status": "partial" if row["received_minor"] else "pending", "version": row["version"]}
        for row in rows
    ], "ledger": _ledger_info(ctx)}


def project_reimbursements(conn: sqlite3.Connection, ledger_id: str, project_id: str) -> dict:
    row = conn.execute(
        "SELECT COALESCE(SUM(r.amount_minor), 0) AS advance_minor, "
        "COALESCE(SUM(receipts.received_minor), 0) AS received_minor "
        "FROM movement_reimbursements r LEFT JOIN ("
        "SELECT x.ledger_id, x.reimbursement_id, SUM(e.amount_minor) AS received_minor "
        "FROM movement_reimbursement_receipts x JOIN movement_active_events e "
        "ON e.ledger_id = x.ledger_id AND e.event_id = x.event_id AND e.status = 'confirmed' "
        "GROUP BY x.ledger_id, x.reimbursement_id) receipts "
        "ON receipts.ledger_id = r.ledger_id AND receipts.reimbursement_id = r.reimbursement_id "
        "WHERE r.ledger_id = ? AND r.project_id = ?",
        (ledger_id, project_id),
    ).fetchone()
    transactions = conn.execute(
        "SELECT transaction_id FROM movement_reimbursements WHERE ledger_id = ? AND project_id = ? ORDER BY transaction_id",
        (ledger_id, project_id),
    ).fetchall()
    return {"advance_minor": row["advance_minor"], "received_minor": row["received_minor"],
            "net_reimbursable_minor": row["advance_minor"] - row["received_minor"],
            "advance_transaction_ids": [item["transaction_id"] for item in transactions]}


def preview_transaction_links(
    conn: sqlite3.Connection,
    ledger_id: str,
    source_transaction_id: str,
    target_transaction_id: str,
) -> dict:
    if source_transaction_id == target_transaction_id:
        return {"source_transaction_id": source_transaction_id, "target_transaction_id": target_transaction_id,
                "can_repoint": False, "conflicts": ["source_and_target_are_identical"], "moved_links": {}}
    for transaction_id in (source_transaction_id, target_transaction_id):
        if conn.execute(
            "SELECT 1 FROM ledger_transactions WHERE ledger_id = ? AND transaction_id = ?",
            (ledger_id, transaction_id),
        ).fetchone() is None:
            _fail("not_found", "Source and target transactions must belong to the same ledger", {
                "transaction_id": transaction_id,
            })

    moved_links = {}
    conflicts = []
    for table, label, conflict_code in (
        ("movement_transaction_accounts", "payment_accounts", "payment_account_link_conflict"),
        ("movement_reimbursements", "reimbursements", "reimbursement_link_conflict"),
        ("movement_splits", "personal_splits", "personal_split_link_conflict"),
    ):
        source_count = conn.execute(
            f"SELECT COUNT(*) AS count FROM {table} WHERE ledger_id = ? AND transaction_id = ?",
            (ledger_id, source_transaction_id),
        ).fetchone()["count"]
        target_count = conn.execute(
            f"SELECT COUNT(*) AS count FROM {table} WHERE ledger_id = ? AND transaction_id = ?",
            (ledger_id, target_transaction_id),
        ).fetchone()["count"]
        moved_links[label] = source_count
        if source_count and target_count:
            conflicts.append(conflict_code)

    source_refunds = conn.execute(
        "SELECT COUNT(*) AS count FROM movement_active_events WHERE ledger_id = ? AND event_type = 'refund' AND related_transaction_id = ?",
        (ledger_id, source_transaction_id),
    ).fetchone()["count"]
    target_refunds = conn.execute(
        "SELECT COUNT(*) AS count FROM movement_active_events WHERE ledger_id = ? AND event_type = 'refund' AND related_transaction_id = ?",
        (ledger_id, target_transaction_id),
    ).fetchone()["count"]
    moved_links["refunds"] = source_refunds
    if source_refunds and target_refunds:
        conflicts.append("refund_adjustment_conflict")
    for event_type, label in (
        ("transaction_payment", "transaction_payment_events"),
        ("reimbursement_receipt", "reimbursement_receipts"),
        ("split_settlement", "split_settlements"),
    ):
        column = "transaction_id" if event_type == "transaction_payment" else "related_transaction_id"
        moved_links[label] = conn.execute(
            f"SELECT COUNT(*) AS count FROM movement_active_events WHERE ledger_id = ? AND event_type = ? AND {column} = ?",
            (ledger_id, event_type, source_transaction_id),
        ).fetchone()["count"]
    return {"source_transaction_id": source_transaction_id, "target_transaction_id": target_transaction_id,
            "can_repoint": not conflicts, "conflicts": conflicts, "moved_links": moved_links}


def repoint_transaction_links(
    conn: sqlite3.Connection,
    ledger_id: str,
    source_transaction_id: str,
    target_transaction_id: str,
) -> dict:
    preview = preview_transaction_links(conn, ledger_id, source_transaction_id, target_transaction_id)
    if not preview["can_repoint"]:
        _fail("conflict", "Movement links cannot be repointed without resolving the merge conflicts", preview)

    for table in ("movement_transaction_accounts", "movement_reimbursements", "movement_splits"):
        conn.execute(
            f"UPDATE {table} SET transaction_id = ? WHERE ledger_id = ? AND transaction_id = ?",
            (target_transaction_id, ledger_id, source_transaction_id),
        )
    conn.execute(
        "UPDATE movement_events SET related_transaction_id = ? WHERE ledger_id = ? AND event_type = 'refund' AND related_transaction_id = ?",
        (target_transaction_id, ledger_id, source_transaction_id),
    )
    conn.execute(
        "UPDATE movement_events SET transaction_id = ?, related_transaction_id = ? "
        "WHERE ledger_id = ? AND event_type = 'transaction_payment' AND transaction_id = ?",
        (target_transaction_id, target_transaction_id, ledger_id, source_transaction_id),
    )
    conn.execute(
        "UPDATE movement_events SET related_transaction_id = ? "
        "WHERE ledger_id = ? AND related_transaction_id = ? AND event_type IN ('reimbursement_receipt', 'split_settlement')",
        (target_transaction_id, ledger_id, source_transaction_id),
    )
    return preview


def _record_personal_split(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _write(ctx)
    transaction = _transaction(conn, ctx["ledger_id"], params.get("transaction_id"), params.get("expected_version"))
    if transaction["kind"] != "expense":
        _fail("needs_clarification", "A personal split must link to an expense transaction")
    own_share = params.get("personal_share_minor")
    if type(own_share) is not int or own_share < 0 or own_share > transaction["amount_minor"]:
        _fail("invalid_input", "personal_share_minor must be from zero to the expense amount")
    participants = params.get("participants")
    if not isinstance(participants, list) or not participants:
        _fail("needs_clarification", "At least one participant and amount owed are required")
    normalized: list[tuple[str, str, int]] = []
    seen = set()
    for item in participants:
        if not isinstance(item, dict) or set(item) != {"name", "owed_minor"}:
            _fail("invalid_input", "Each participant needs only name and owed_minor")
        name = _text(item["name"], "participant.name")
        key = unicodedata.normalize("NFKC", name).casefold()
        if key in seen:
            _fail("invalid_input", "Participant names must be unique")
        seen.add(key)
        normalized.append((name, key, _amount(item["owed_minor"], "owed_minor")))
    if sum(row[2] for row in normalized) != transaction["amount_minor"] - own_share:
        _fail("invalid_input", "Participant amounts must equal the part paid for others")
    split_id = _new_id()
    conn.execute(
        "INSERT INTO movement_splits(ledger_id, split_id, transaction_id, total_minor, personal_share_minor, created_by, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ctx["ledger_id"], split_id, transaction["transaction_id"], transaction["amount_minor"], own_share,
         ctx["principal_id"], _now()),
    )
    conn.executemany(
        "INSERT INTO movement_split_participants(ledger_id, split_id, participant_name, participant_key, owed_minor) "
        "VALUES (?, ?, ?, ?, ?)",
        [(ctx["ledger_id"], split_id, name, key, owed) for name, key, owed in normalized],
    )
    return {"split": {"split_id": split_id, "transaction_id": transaction["transaction_id"],
                      "total_minor": transaction["amount_minor"], "personal_share_minor": own_share,
                      "owed_minor": sum(row[2] for row in normalized), "received_minor": 0,
                      "version": 1, "participants": [{"name": n, "owed_minor": a, "received_minor": 0} for n, _, a in normalized]},
            "ledger": _ledger_info(ctx)}


def _record_split_settlement(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _write(ctx)
    split_id = _text(params.get("split_id"), "split_id")
    row = conn.execute(
        "SELECT * FROM movement_splits WHERE ledger_id = ? AND split_id = ?",
        (ctx["ledger_id"], split_id),
    ).fetchone()
    if row is None:
        _fail("not_found", "Personal split not found in this ledger", {"split_id": split_id})
    _subject_version(row, params.get("expected_version"), "personal split")
    name = _text(params.get("participant_name"), "participant_name")
    key = unicodedata.normalize("NFKC", name).casefold()
    participant = conn.execute(
        "SELECT * FROM movement_split_participants WHERE ledger_id = ? AND split_id = ? AND participant_key = ?",
        (ctx["ledger_id"], split_id, key),
    ).fetchone()
    if participant is None:
        _fail("not_found", "Participant not found in this personal split", {"participant_name": name})
    amount = _amount(params.get("amount_minor"))
    received = conn.execute(
        "SELECT COALESCE(SUM(amount_minor), 0) AS total FROM movement_active_events WHERE ledger_id = ? "
        "AND event_type = 'split_settlement' AND subject_id = ? AND participant_name = ? AND status = 'confirmed'",
        (ctx["ledger_id"], split_id, participant["participant_name"]),
    ).fetchone()["total"]
    if received + amount > participant["owed_minor"]:
        _fail("conflict", "The settlement exceeds this participant's outstanding amount", {
            "owed_minor": participant["owed_minor"], "received_minor": received,
        })
    occurred_on = _date_params(params, "occurred_on")
    charged_on = _date_value(params["charged_on"], "charged_on") if params.get("charged_on") is not None else occurred_on
    account = _account(conn, ctx["ledger_id"], params["account_id"]) if params.get("account_id") else None
    if account and account["account_type"] == "credit_card":
        _fail("invalid_input", "A settlement must be deposited to an asset account")
    event_id = _event(
        conn, ctx, event_type="split_settlement", adjustment_type="split_settlement", kind="transfer",
        amount_minor=amount, occurred_on=occurred_on, charged_on=charged_on,
        related_transaction_id=row["transaction_id"], subject_id=split_id, participant_name=participant["participant_name"],
    )
    if account:
        _entry(conn, ctx["ledger_id"], account["account_id"], event_id, amount)
    conn.execute(
        "UPDATE movement_splits SET version = version + 1 WHERE ledger_id = ? AND split_id = ?",
        (ctx["ledger_id"], split_id),
    )
    return {"split_id": split_id, "participant_name": participant["participant_name"],
            "received_minor": received + amount, "outstanding_minor": participant["owed_minor"] - received - amount,
            "version": row["version"] + 1, "event_id": event_id, "ledger": _ledger_info(ctx)}


def _record_personal_loan(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _write(ctx)
    counterparty = _text(params.get("counterparty"), "counterparty")
    direction = params.get("direction")
    if direction not in ("lend", "borrow"):
        _fail("invalid_input", "direction must be lend or borrow")
    amount = _amount(params.get("amount_minor"))
    occurred_on = _date_params(params, "occurred_on")
    charged_on = _date_value(params["charged_on"], "charged_on") if params.get("charged_on") is not None else occurred_on
    account = _account(conn, ctx["ledger_id"], params["account_id"]) if params.get("account_id") else None
    if account and account["account_type"] == "credit_card":
        _fail("invalid_input", "Loan principal must move through a cash, bank, or e-wallet account")
    loan_id = _new_id()
    key = unicodedata.normalize("NFKC", counterparty).casefold()
    conn.execute(
        "INSERT INTO movement_personal_loans(ledger_id, loan_id, counterparty, counterparty_key, direction, "
        "principal_minor, occurred_on, charged_on, account_id, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ctx["ledger_id"], loan_id, counterparty, key, direction, amount, occurred_on, charged_on,
         account["account_id"] if account else None, ctx["principal_id"], _now()),
    )
    event_id = _event(
        conn, ctx, event_type="loan_principal", adjustment_type="loan_principal", kind="transfer",
        amount_minor=amount, occurred_on=occurred_on, charged_on=charged_on, subject_id=loan_id,
    )
    if account:
        _entry(conn, ctx["ledger_id"], account["account_id"], event_id, -amount if direction == "lend" else amount)
    return {"loan": {"loan_id": loan_id, "counterparty": counterparty, "direction": direction,
                      "principal_minor": amount, "repaid_minor": 0, "outstanding_minor": amount,
                      "occurred_on": occurred_on, "version": 1}, "ledger": _ledger_info(ctx)}


def _loan_repaid(conn: sqlite3.Connection, ledger_id: str, loan_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_minor), 0) AS total FROM movement_active_events WHERE ledger_id = ? "
        "AND event_type = 'loan_repayment' AND subject_id = ? AND status = 'confirmed'",
        (ledger_id, loan_id),
    ).fetchone()
    return row["total"]


def _record_loan_repayment(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _write(ctx)
    loan_id = _text(params.get("loan_id"), "loan_id")
    row = conn.execute(
        "SELECT * FROM movement_personal_loans WHERE ledger_id = ? AND loan_id = ?",
        (ctx["ledger_id"], loan_id),
    ).fetchone()
    if row is None:
        _fail("not_found", "Personal loan not found in this ledger", {"loan_id": loan_id})
    _subject_version(row, params.get("expected_version"), "personal loan")
    amount = _amount(params.get("amount_minor"))
    repaid = _loan_repaid(conn, ctx["ledger_id"], loan_id)
    if repaid + amount > row["principal_minor"]:
        _fail("conflict", "The repayment exceeds the outstanding loan principal", {
            "principal_minor": row["principal_minor"], "repaid_minor": repaid,
        })
    occurred_on = _date_params(params, "occurred_on")
    charged_on = _date_value(params["charged_on"], "charged_on") if params.get("charged_on") is not None else occurred_on
    account = _account(conn, ctx["ledger_id"], params["account_id"]) if params.get("account_id") else None
    if account and account["account_type"] == "credit_card":
        _fail("invalid_input", "Loan principal must move through a cash, bank, or e-wallet account")
    event_id = _event(
        conn, ctx, event_type="loan_repayment", adjustment_type="loan_repayment", kind="transfer",
        amount_minor=amount, occurred_on=occurred_on, charged_on=charged_on, subject_id=loan_id,
    )
    if account:
        _entry(conn, ctx["ledger_id"], account["account_id"], event_id, amount if row["direction"] == "lend" else -amount)
    conn.execute(
        "UPDATE movement_personal_loans SET version = version + 1 WHERE ledger_id = ? AND loan_id = ?",
        (ctx["ledger_id"], loan_id),
    )
    return {"loan_id": loan_id, "repaid_minor": repaid + amount,
            "outstanding_minor": row["principal_minor"] - repaid - amount,
            "version": row["version"] + 1, "event_id": event_id, "ledger": _ledger_info(ctx)}


def _list_personal_loans(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_read(ctx)
    rows = conn.execute(
        "SELECT l.*, COALESCE(SUM(e.amount_minor), 0) AS repaid_minor FROM movement_personal_loans l "
        "LEFT JOIN movement_active_events e ON e.ledger_id = l.ledger_id AND e.subject_id = l.loan_id "
        "AND e.event_type = 'loan_repayment' AND e.status = 'confirmed' WHERE l.ledger_id = ? "
        "GROUP BY l.ledger_id, l.loan_id ORDER BY l.occurred_on DESC, l.loan_id",
        (ctx["ledger_id"],),
    ).fetchall()
    return {"loans": [
        {"loan_id": row["loan_id"], "counterparty": row["counterparty"], "direction": row["direction"],
         "principal_minor": row["principal_minor"], "repaid_minor": row["repaid_minor"],
         "outstanding_minor": row["principal_minor"] - row["repaid_minor"], "occurred_on": row["occurred_on"],
         "version": row["version"]}
        for row in rows
    ], "ledger": _ledger_info(ctx)}


REVERSIBLE_EVENT_TYPES = {"refund", "transfer", "credit_card_repayment", "reimbursement_receipt"}


def undo_movement(conn: sqlite3.Connection, ctx: dict, request_id: str, *, include_payments: bool = True) -> dict | None:
    events = conn.execute(
        "SELECT * FROM movement_events WHERE ledger_id=? AND created_by=? AND request_id=?",
        (ctx["ledger_id"], ctx["principal_id"], request_id),
    ).fetchall()
    if not include_payments:
        events = [event for event in events if event["event_type"] != "transaction_payment"]
    if not events:
        return None
    effects = []
    for event in events:
        if event["event_type"] not in REVERSIBLE_EVENT_TYPES:
            _fail("conflict", "该资金操作需按原对象核对后更正。", {"event_type": event["event_type"]})
        planning.assert_period_open(conn, ctx, event["occurred_on"], action_name="undo_operation")
        if event["charged_on"] and event["charged_on"][:7] != event["occurred_on"][:7]:
            planning.assert_period_open(conn, ctx, event["charged_on"], action_name="undo_operation")
        change = conn.execute(
            "INSERT OR IGNORE INTO movement_event_reversals VALUES (?, ?, ?, ?, ?)",
            (ctx["ledger_id"], event["event_id"], ctx["request_id"], ctx["principal_id"], _now()),
        )
        if change.rowcount != 1:
            _fail("conflict", "该资金操作已经撤销。", {"event_id": event["event_id"]})
        if event["event_type"] == "reimbursement_receipt":
            conn.execute(
                "UPDATE movement_reimbursements SET version=version+1 WHERE ledger_id=? AND reimbursement_id IN "
                "(SELECT reimbursement_id FROM movement_reimbursement_receipts WHERE ledger_id=? AND event_id=?)",
                (ctx["ledger_id"], ctx["ledger_id"], event["event_id"]),
            )
        effects.append({"event_id": event["event_id"], "action": "reverse", "event_type": event["event_type"]})
    return {"target_request_id": request_id, "effects": effects, "ledger": _ledger_info(ctx)}


def cashflow_adjustments(
    conn: sqlite3.Connection,
    ledger_id: str,
    start: str,
    end: str,
    date_basis: str = "occurred_on",
) -> list[dict]:
    if date_basis not in ("occurred_on", "charged_on"):
        _fail("invalid_input", "date_basis must be occurred_on or charged_on")
    date_column = "occurred_on" if date_basis == "occurred_on" else "COALESCE(charged_on, occurred_on)"
    rows = conn.execute(
        "SELECT occurred_on, charged_on, kind, adjustment_type, amount_minor, status, transaction_id, related_transaction_id "
        f"FROM movement_active_events WHERE ledger_id = ? AND status = 'confirmed' AND event_type != 'transaction_payment' "
        f"AND {date_column} BETWEEN ? AND ? ORDER BY {date_column}, event_id",
        (ledger_id, start, end),
    ).fetchall()
    return [dict(row) for row in rows]


_PARTICIPANT = _object({"name": _STRING, "owed_minor": _AMOUNT}, ("name", "owed_minor"))


ACTIONS = {
    "upsert_payment_account": {
        "description": "Create or update a payment account in the caller's own ledger. Account type and opening balance are fixed after creation.",
        "inputSchema": _object({
            "account_id": _STRING, "expected_version": {"type": "integer", "minimum": 1},
            "name": _STRING, "account_type": {"type": "string", "enum": ["cash", "bank", "e_wallet", "credit_card"]},
            "opening_balance_minor": {"type": "integer", "minimum": 0},
            "statement_day": {"anyOf": [{"type": "integer", "minimum": 1, "maximum": 31}, {"type": "null"}]},
            "due_day": {"anyOf": [{"type": "integer", "minimum": 1, "maximum": 31}, {"type": "null"}]},
            "status": {"type": "string", "enum": ["active", "archived"]},
        }, ("name", "account_type")),
        "scope": "own_write", "run": _create_or_update_account,
    },
    "list_payment_accounts": {
        "description": "List active payment accounts and their current balances.",
        "inputSchema": _object({}), "scope": "own_read", "run": _list_accounts,
    },
    "record_payment_account_balance_check": {
        "description": "Record an actual payment-account balance for an explicit date and snapshot the matching book balance using the ledger charge-date basis.",
        "inputSchema": _object({"account_id": _STRING, "balance_date": _DATE, "actual_balance_minor": {"type": "integer"}},
                             ("account_id", "balance_date", "actual_balance_minor")),
        "scope": "own_write", "run": _record_payment_account_balance_check,
    },
    "get_payment_account_balance_check": {
        "description": "Return the latest saved actual-versus-book balance check for one payment account.",
        "inputSchema": _object({"account_id": _STRING}, ("account_id",)),
        "scope": "own_read", "run": _get_payment_account_balance_check,
    },
    "get_payment_account_activity": {
        "description": "List a payment account's confirmed transaction and movement history for an explicit date range.",
        "inputSchema": _object({"account_id": _STRING, "start_date": _DATE, "end_date": _DATE,
                                 "date_basis": {"type": "string", "enum": ["occurred_on", "charged_on"]}},
                             ("account_id", "start_date", "end_date")),
        "scope": "own_read", "run": _account_activity,
    },
    "record_account_transaction": {
        "description": "Link a confirmed ledger transaction to one payment account so balances reflect its actual payment or receipt without creating another income or expense.",
        "inputSchema": _object({"transaction_id": _STRING, "expected_version": {"type": "integer", "minimum": 1}, "account_id": _STRING},
                             ("transaction_id", "expected_version", "account_id")),
        "scope": "own_write", "run": _record_account_transaction,
    },
    "transfer": {
        "description": "Transfer money between two active asset accounts in the same ledger. Internal transfers do not count as income or expense.",
        "inputSchema": _object({"source_account_id": _STRING, "destination_account_id": _STRING, "amount_minor": _AMOUNT, "occurred_on": _DATE},
                             ("source_account_id", "destination_account_id", "amount_minor", "occurred_on")),
        "scope": "own_write", "run": _transfer,
    },
    "get_credit_card_due": {
        "description": "Summarize charges, refunds, repayments, and outstanding balance for a credit card over an explicit inclusive statement period.",
        "inputSchema": _object({"account_id": _STRING, "start_date": _DATE, "end_date": _DATE}, ("account_id", "start_date", "end_date")),
        "scope": "own_read", "run": _credit_card_due,
    },
    "record_credit_card_repayment": {
        "description": "Pay down a credit card from an asset account without recording a second expense.",
        "inputSchema": _object({"account_id": _STRING, "source_account_id": _STRING, "amount_minor": _AMOUNT, "occurred_on": _DATE},
                             ("account_id", "source_account_id", "amount_minor", "occurred_on")),
        "scope": "own_write", "run": _credit_card_repayment,
    },
    "record_refund": {
        "description": "Record a partial or full refund linked to a confirmed expense; refunds cannot exceed the original amount and remain separate from general income.",
        "inputSchema": _object({"transaction_id": _STRING, "expected_version": {"type": "integer", "minimum": 1},
                                 "amount_minor": _AMOUNT, "occurred_on": _DATE, "charged_on": _DATE, "account_id": _STRING},
                             ("transaction_id", "expected_version", "amount_minor", "occurred_on")),
        "scope": "own_write", "run": _record_refund,
    },
    "record_reimbursement_advance": {
        "description": "Mark all or part of an existing confirmed expense as a reimbursement advance. The linked transaction remains the only expense entry.",
        "inputSchema": _object({"transaction_id": _STRING, "expected_version": {"type": "integer", "minimum": 1},
                                 "amount_minor": _AMOUNT, "project_id": _STRING},
                             ("transaction_id", "expected_version", "amount_minor")),
        "scope": "own_write", "run": _record_reimbursement_advance,
    },
    "record_reimbursement_receipt": {
        "description": "Record an actual partial or full reimbursement receipt against an advance; it is a separate cash inflow, not general income.",
        "inputSchema": _object({"reimbursement_id": _STRING, "expected_version": {"type": "integer", "minimum": 1},
                                 "amount_minor": _AMOUNT, "occurred_on": _DATE, "charged_on": _DATE, "account_id": _STRING},
                             ("reimbursement_id", "expected_version", "amount_minor", "occurred_on")),
        "scope": "own_write", "run": _record_reimbursement_receipt,
    },
    "list_pending_reimbursements": {
        "description": "List reimbursement advances with their received and outstanding amounts.",
        "inputSchema": _object({}), "scope": "own_read", "run": _list_pending_reimbursements,
    },
    "record_personal_split": {
        "description": "Split an existing expense into the caller's share and amounts owed by named people.",
        "inputSchema": _object({"transaction_id": _STRING, "expected_version": {"type": "integer", "minimum": 1},
                                 "personal_share_minor": {"type": "integer", "minimum": 0},
                                 "participants": {"type": "array", "minItems": 1, "items": _PARTICIPANT}},
                             ("transaction_id", "expected_version", "personal_share_minor", "participants")),
        "scope": "own_write", "run": _record_personal_split,
    },
    "record_split_settlement": {
        "description": "Record an actual partial or full repayment from one participant in a personal split.",
        "inputSchema": _object({"split_id": _STRING, "expected_version": {"type": "integer", "minimum": 1},
                                 "participant_name": _STRING, "amount_minor": _AMOUNT, "occurred_on": _DATE,
                                 "charged_on": _DATE, "account_id": _STRING},
                             ("split_id", "expected_version", "participant_name", "amount_minor", "occurred_on")),
        "scope": "own_write", "run": _record_split_settlement,
    },
    "record_personal_loan": {
        "description": "Record money lent to or borrowed from a named person; principal is tracked separately from income and expense.",
        "inputSchema": _object({"counterparty": _STRING, "direction": {"type": "string", "enum": ["lend", "borrow"]},
                                 "amount_minor": _AMOUNT, "occurred_on": _DATE, "charged_on": _DATE, "account_id": _STRING},
                             ("counterparty", "direction", "amount_minor", "occurred_on")),
        "scope": "own_write", "run": _record_personal_loan,
    },
    "record_loan_repayment": {
        "description": "Record a partial or full repayment of personal loan principal without counting it as income or expense.",
        "inputSchema": _object({"loan_id": _STRING, "expected_version": {"type": "integer", "minimum": 1},
                                 "amount_minor": _AMOUNT, "occurred_on": _DATE, "charged_on": _DATE, "account_id": _STRING},
                             ("loan_id", "expected_version", "amount_minor", "occurred_on")),
        "scope": "own_write", "run": _record_loan_repayment,
    },
    "list_personal_loans": {
        "description": "List personal loans with the original principal, repayments, and outstanding principal.",
        "inputSchema": _object({}), "scope": "own_read", "run": _list_personal_loans,
    },
}
