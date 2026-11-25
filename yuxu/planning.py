from __future__ import annotations

import json
import math
import re
import sqlite3
import uuid
from calendar import monthrange
from datetime import date, datetime, timezone

from .contract import BusinessError


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS planning_projects (
    ledger_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    budget_minor INTEGER NOT NULL CHECK (budget_minor > 0),
    start_date TEXT,
    end_date TEXT,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    closed_at TEXT,
    PRIMARY KEY (ledger_id, project_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_planning_projects_open_name
    ON planning_projects(ledger_id, name_key) WHERE status = 'open';

CREATE TABLE IF NOT EXISTS planning_project_budget_items (
    ledger_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    category_id TEXT NOT NULL,
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
    position INTEGER NOT NULL,
    PRIMARY KEY (ledger_id, project_id, category_id),
    FOREIGN KEY (ledger_id, project_id) REFERENCES planning_projects(ledger_id, project_id),
    FOREIGN KEY (ledger_id, category_id) REFERENCES ledger_categories(ledger_id, category_id)
);

CREATE TABLE IF NOT EXISTS project_entries (
    ledger_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    PRIMARY KEY (ledger_id, project_id, transaction_id),
    UNIQUE (ledger_id, transaction_id),
    FOREIGN KEY (ledger_id, project_id) REFERENCES planning_projects(ledger_id, project_id),
    FOREIGN KEY (ledger_id, transaction_id) REFERENCES ledger_transactions(ledger_id, transaction_id)
);

CREATE INDEX IF NOT EXISTS idx_project_entries_project
    ON project_entries(ledger_id, project_id, transaction_id);

CREATE TABLE IF NOT EXISTS planning_monthly_category_budgets (
    ledger_id TEXT NOT NULL,
    month TEXT NOT NULL,
    category_id TEXT NOT NULL,
    budget_minor INTEGER NOT NULL DEFAULT 0 CHECK (budget_minor >= 0),
    carry_in_minor INTEGER NOT NULL DEFAULT 0 CHECK (carry_in_minor >= 0),
    carry_source_month TEXT,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, month, category_id),
    FOREIGN KEY (ledger_id, category_id) REFERENCES ledger_categories(ledger_id, category_id)
);

CREATE TABLE IF NOT EXISTS planning_saving_goals (
    ledger_id TEXT NOT NULL,
    goal_id TEXT NOT NULL,
    name TEXT NOT NULL,
    target_minor INTEGER NOT NULL CHECK (target_minor > 0),
    start_date TEXT NOT NULL,
    due_date TEXT,
    savings_account_id TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'completed', 'paused')),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, goal_id)
);

CREATE TABLE IF NOT EXISTS planning_goal_changes (
    ledger_id TEXT NOT NULL,
    entry_id TEXT NOT NULL,
    goal_id TEXT NOT NULL,
    amount_minor INTEGER NOT NULL CHECK (amount_minor <> 0),
    occurred_on TEXT NOT NULL,
    note TEXT,
    source_type TEXT NOT NULL CHECK (source_type IN ('manual', 'transfer')),
    source_id TEXT,
    request_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, entry_id),
    FOREIGN KEY (ledger_id, goal_id) REFERENCES planning_saving_goals(ledger_id, goal_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_planning_goal_source_once
    ON planning_goal_changes(ledger_id, source_type, source_id) WHERE source_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_planning_goal_changes_period
    ON planning_goal_changes(ledger_id, goal_id, occurred_on, entry_id);

CREATE TABLE IF NOT EXISTS planning_income_classifications (
    ledger_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    classification TEXT NOT NULL CHECK (classification IN ('recurring', 'one_off')),
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, transaction_id),
    FOREIGN KEY (ledger_id, transaction_id) REFERENCES ledger_transactions(ledger_id, transaction_id)
);

CREATE TABLE IF NOT EXISTS planning_period_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ledger_id TEXT NOT NULL,
    month TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    action TEXT NOT NULL CHECK (action IN ('close', 'reopen')),
    reason TEXT,
    snapshot_json TEXT,
    request_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (ledger_id, month, version)
);

CREATE INDEX IF NOT EXISTS idx_planning_period_events_latest
    ON planning_period_events(ledger_id, month, version DESC);
"""


def _fail(status: str, message: str, details: dict | None = None, code: str | None = None) -> None:
    raise BusinessError(status, message, details, code=code)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _new_id() -> str:
    return str(uuid.uuid4())


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail("invalid_input", f"{field} must be a non-empty string", {"field": field})
    return value.strip()


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _text(value, field)


def _date(value: object, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        _fail("invalid_input", f"{field} must use YYYY-MM-DD", {"field": field})
    try:
        date.fromisoformat(value)
    except ValueError:
        _fail("invalid_input", f"{field} is not a valid date", {"field": field})
    return value


def _month(value: object, field: str = "month") -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}", value):
        _fail("invalid_input", f"{field} must use YYYY-MM", {"field": field})
    try:
        date.fromisoformat(f"{value}-01")
    except ValueError:
        _fail("invalid_input", f"{field} is not a valid month", {"field": field})
    return value


def _amount(value: object, field: str = "amount_minor", *, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    if type(value) is not int or value < minimum:
        _fail("invalid_input", f"{field} must be an integer in fen and at least {minimum}", {"field": field})
    return value


def _require_write(ctx: dict) -> None:
    if ctx["cross_account"]:
        _fail("forbidden", "Cross-account writes are not allowed")
    if not ctx["request_id"]:
        _fail("invalid_input", "A request_id is required for writes")


def _ledger(ctx: dict) -> dict:
    return {"ledger_id": ctx["ledger_id"], "account_id": ctx["account_id"], "account_name": ctx["account_name"]}


def _category(conn: sqlite3.Connection, ledger_id: str, value: object) -> tuple[str, str]:
    category_id = _text(value, "category_id")
    row = conn.execute(
        "SELECT category_id, path FROM ledger_categories WHERE ledger_id = ? AND category_id = ? AND active = 1",
        (ledger_id, category_id),
    ).fetchone()
    if row is None:
        _fail("not_found", "Category not found or inactive in this ledger", {"category_id": category_id})
    return row["category_id"], row["path"]


def _project(conn: sqlite3.Connection, ledger_id: str, project_id: object) -> sqlite3.Row:
    project_id = _text(project_id, "project_id")
    row = conn.execute(
        "SELECT * FROM planning_projects WHERE ledger_id = ? AND project_id = ?",
        (ledger_id, project_id),
    ).fetchone()
    if row is None:
        _fail("not_found", "Project not found in this ledger", {"project_id": project_id})
    return row


def _goal(conn: sqlite3.Connection, ledger_id: str, goal_id: object) -> sqlite3.Row:
    goal_id = _text(goal_id, "goal_id")
    row = conn.execute(
        "SELECT * FROM planning_saving_goals WHERE ledger_id = ? AND goal_id = ?",
        (ledger_id, goal_id),
    ).fetchone()
    if row is None:
        _fail("not_found", "Saving goal not found in this ledger", {"goal_id": goal_id})
    return row


def _version(row: sqlite3.Row, expected: object) -> int:
    if expected is None:
        return row["version"]
    if type(expected) is not int or expected != row["version"]:
        _fail("conflict", "The item changed; read its current version before updating", {"current_version": row["version"]})
    return expected


def _month_of(value: str) -> str:
    return value[:7]


def assert_period_open(
    conn: sqlite3.Connection,
    ctx: dict,
    business_date: str | date,
    *,
    action_name: str | None = None,
) -> None:
    if isinstance(business_date, date):
        month = business_date.strftime("%Y-%m")
    else:
        month = _month_of(_date(business_date, "business_date"))
    row = conn.execute(
        "SELECT version FROM planning_period_events "
        "WHERE ledger_id = ? AND month = ? AND action = 'close' "
        "AND NOT EXISTS (SELECT 1 FROM planning_period_events AS later "
        "WHERE later.ledger_id = planning_period_events.ledger_id "
        "AND later.month = planning_period_events.month AND later.version > planning_period_events.version "
        "AND later.action = 'reopen') ORDER BY version DESC LIMIT 1",
        (ctx["ledger_id"], month),
    ).fetchone()
    if row is not None:
        _fail(
            "conflict",
            "This accounting period is closed; reopen it before making changes",
            {"period": month, "action_name": action_name, "closed_version": row["version"]},
            code="period_closed",
        )


def _latest_period_event(conn: sqlite3.Connection, ledger_id: str, month: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM planning_period_events WHERE ledger_id = ? AND month = ? ORDER BY version DESC LIMIT 1",
        (ledger_id, month),
    ).fetchone()


def _next_period_version(conn: sqlite3.Connection, ledger_id: str, month: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) AS version FROM planning_period_events WHERE ledger_id = ? AND month = ?",
        (ledger_id, month),
    ).fetchone()
    return row["version"] + 1


def _link_project_transaction(conn: sqlite3.Connection, ctx: dict, project_id: str, transaction_id: object) -> dict:
    transaction_id = _text(transaction_id, "transaction_id")
    existing = conn.execute(
        "SELECT project_id FROM project_entries WHERE ledger_id = ? AND transaction_id = ?",
        (ctx["ledger_id"], transaction_id),
    ).fetchone()
    if existing is not None:
        if existing["project_id"] == project_id:
            return {"project_id": project_id, "transaction_id": transaction_id, "linked": True, "already_linked": True}
        _fail("conflict", "The transaction is already linked to another project", {"project_id": existing["project_id"], "transaction_id": transaction_id})
    project = _project(conn, ctx["ledger_id"], project_id)
    if project["status"] != "open":
        _fail("conflict", "A closed project cannot accept transactions", {"project_id": project_id})
    transaction = conn.execute(
        "SELECT transaction_id, occurred_on, kind, voided_at FROM ledger_transactions "
        "WHERE ledger_id = ? AND transaction_id = ?",
        (ctx["ledger_id"], transaction_id),
    ).fetchone()
    if transaction is None:
        _fail("not_found", "Transaction not found in this ledger", {"transaction_id": transaction_id})
    if transaction["kind"] != "expense" or transaction["voided_at"] is not None:
        _fail("invalid_input", "Only a non-voided expense can be linked to a project", {"transaction_id": transaction_id})
    assert_period_open(conn, ctx, transaction["occurred_on"], action_name="link_project_transaction")
    conn.execute(
        "INSERT INTO project_entries(ledger_id, project_id, transaction_id) VALUES (?, ?, ?)",
        (ctx["ledger_id"], project_id, transaction_id),
    )
    return {"project_id": project_id, "transaction_id": transaction_id, "linked": True, "already_linked": False}


def _project_summary(conn: sqlite3.Connection, ctx: dict, project: sqlite3.Row) -> dict:
    ledger_id = ctx["ledger_id"]
    project_id = project["project_id"]
    from .movements import project_reimbursements

    reimbursements = project_reimbursements(conn, ledger_id, project_id)
    transaction_ids = {
        row["transaction_id"] for row in conn.execute(
            "SELECT transaction_id FROM project_entries WHERE ledger_id = ? AND project_id = ?",
            (ledger_id, project_id),
        ).fetchall()
    }
    transaction_ids.update(reimbursements["advance_transaction_ids"])
    if transaction_ids:
        placeholders = ", ".join("?" for _ in transaction_ids)
        rows = conn.execute(
            "SELECT transaction_id, category_id, amount_minor FROM ledger_transactions "
            "WHERE ledger_id = ? AND transaction_id IN (" + placeholders + ") AND kind = 'expense' "
            "AND confirmation_status = 'confirmed' AND voided_at IS NULL ORDER BY occurred_on, transaction_id",
            [ledger_id, *sorted(transaction_ids)],
        ).fetchall()
    else:
        rows = []
    actual_paid_minor = 0
    spent_by_category: dict[str | None, int] = {}
    for row in rows:
        amount_minor = row["amount_minor"]
        actual_paid_minor += amount_minor
        splits = conn.execute(
            "SELECT category_id, amount_minor FROM ledger_transaction_splits "
            "WHERE ledger_id = ? AND transaction_id = ? ORDER BY position, split_id",
            (ledger_id, row["transaction_id"]),
        ).fetchall()
        parts = splits or [{"category_id": row["category_id"], "amount_minor": amount_minor}]
        for part in parts:
            category_id = part["category_id"]
            spent_by_category[category_id] = spent_by_category.get(category_id, 0) + part["amount_minor"]
    item_rows = conn.execute(
        "SELECT b.category_id, c.path AS category_path, b.amount_minor FROM planning_project_budget_items AS b "
        "JOIN ledger_categories AS c ON c.ledger_id = b.ledger_id AND c.category_id = b.category_id "
        "WHERE b.ledger_id = ? AND b.project_id = ? ORDER BY b.position, b.category_id",
        (ledger_id, project_id),
    ).fetchall()
    item_amount = sum(row["amount_minor"] for row in item_rows)
    budget_items = [
        {
            "category_id": row["category_id"],
            "category_path": row["category_path"],
            "budget_minor": row["amount_minor"],
            "actual_paid_minor": spent_by_category.get(row["category_id"], 0),
            "remaining_minor": row["amount_minor"] - spent_by_category.get(row["category_id"], 0),
        }
        for row in item_rows
    ]
    unbudgeted_categories = []
    item_ids = {row["category_id"] for row in item_rows}
    for category_id, amount in sorted(spent_by_category.items(), key=lambda item: (item[0] or "")):
        if category_id not in item_ids:
            category = conn.execute(
                "SELECT path FROM ledger_categories WHERE ledger_id = ? AND category_id = ?",
                (ledger_id, category_id),
            ).fetchone() if category_id else None
            unbudgeted_categories.append({
                "category_id": category_id,
                "category_path": category["path"] if category else "待归类",
                "actual_paid_minor": amount,
            })
    return {
        "project_id": project_id,
        "name": project["name"],
        "status": project["status"],
        "version": project["version"],
        "start_date": project["start_date"],
        "end_date": project["end_date"],
        "budget_minor": project["budget_minor"],
        "budget_items": budget_items,
        "unallocated_budget_minor": project["budget_minor"] - item_amount,
        "actual_paid_minor": actual_paid_minor,
        "remaining_minor": project["budget_minor"] - actual_paid_minor,
        "over_budget_minor": max(0, actual_paid_minor - project["budget_minor"]),
        "unbudgeted_categories": unbudgeted_categories,
        "reimbursements": reimbursements,
        "net_spend_minor": actual_paid_minor - reimbursements["received_minor"],
        "ledger": _ledger(ctx),
    }


def _project_budget_upsert(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_write(ctx)
    budget_minor = _amount(params.get("budget_minor"), "budget_minor")
    name = _text(params.get("name"), "name")
    name_key = name.casefold()
    start_date = _date(params["start_date"], "start_date") if params.get("start_date") is not None else None
    end_date = _date(params["end_date"], "end_date") if params.get("end_date") is not None else None
    if start_date and end_date and start_date > end_date:
        _fail("invalid_input", "start_date must be on or before end_date")
    budget_items = params.get("budget_items")
    normalized_items = None
    if budget_items is not None:
        if not isinstance(budget_items, list):
            _fail("invalid_input", "budget_items must be an array")
        normalized_items = []
        seen = set()
        for item in budget_items:
            if not isinstance(item, dict):
                _fail("invalid_input", "Each budget item must be an object")
            category_id, category_path = _category(conn, ctx["ledger_id"], item.get("category_id"))
            if category_id in seen:
                _fail("invalid_input", "Each category may appear once in a project budget", {"category_id": category_id})
            seen.add(category_id)
            normalized_items.append((category_id, category_path, _amount(item.get("amount_minor"), "amount_minor")))
        if sum(item[2] for item in normalized_items) > budget_minor:
            _fail("invalid_input", "Project budget items cannot exceed the total budget", {"budget_minor": budget_minor})
    project_id = _optional_text(params.get("project_id"), "project_id")
    now = _now()
    if project_id is None:
        _fail_if_closed(conn, ctx, ctx["today"], "upsert_project_budget")
        duplicate = conn.execute(
            "SELECT project_id FROM planning_projects WHERE ledger_id = ? AND name_key = ? AND status = 'open'",
            (ctx["ledger_id"], name_key),
        ).fetchone()
        if duplicate:
            _fail("conflict", "An open project already uses this name", {"project_id": duplicate["project_id"]})
        project_id = _new_id()
        conn.execute(
            "INSERT INTO planning_projects(ledger_id, project_id, name, name_key, budget_minor, start_date, end_date, "
            "status, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)",
            (ctx["ledger_id"], project_id, name, name_key, budget_minor, start_date, end_date, ctx["principal_id"], now, now),
        )
    else:
        project = _project(conn, ctx["ledger_id"], project_id)
        if project["status"] != "open":
            _fail("conflict", "A closed project cannot be updated", {"project_id": project_id})
        _version(project, params.get("expected_version"))
        _fail_if_closed(conn, ctx, ctx["today"], "upsert_project_budget")
        duplicate = conn.execute(
            "SELECT project_id FROM planning_projects WHERE ledger_id = ? AND name_key = ? AND status = 'open' AND project_id <> ?",
            (ctx["ledger_id"], name_key, project_id),
        ).fetchone()
        if duplicate:
            _fail("conflict", "An open project already uses this name", {"project_id": duplicate["project_id"]})
        conn.execute(
            "UPDATE planning_projects SET name = ?, name_key = ?, budget_minor = ?, start_date = ?, end_date = ?, "
            "version = version + 1, updated_at = ? WHERE ledger_id = ? AND project_id = ?",
            (name, name_key, budget_minor, start_date, end_date, now, ctx["ledger_id"], project_id),
        )
    if normalized_items is not None:
        conn.execute(
            "DELETE FROM planning_project_budget_items WHERE ledger_id = ? AND project_id = ?",
            (ctx["ledger_id"], project_id),
        )
        conn.executemany(
            "INSERT INTO planning_project_budget_items(ledger_id, project_id, category_id, amount_minor, position) "
            "VALUES (?, ?, ?, ?, ?)",
            [(ctx["ledger_id"], project_id, category_id, amount, position) for position, (category_id, _, amount) in enumerate(normalized_items)],
        )
    transaction_id = params.get("transaction_id")
    linked = _link_project_transaction(conn, ctx, project_id, transaction_id) if transaction_id is not None else None
    return {"project": _project_summary(conn, ctx, _project(conn, ctx["ledger_id"], project_id)), "linked_transaction": linked}


def _fail_if_closed(conn: sqlite3.Connection, ctx: dict, value: str | date, action_name: str) -> None:
    assert_period_open(conn, ctx, value, action_name=action_name)


def _link_project_action(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_write(ctx)
    linked = _link_project_transaction(conn, ctx, _text(params.get("project_id"), "project_id"), params.get("transaction_id"))
    return {**linked, "ledger": _ledger(ctx)}


def _get_project_budget(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    project = _project(conn, ctx["ledger_id"], params.get("project_id"))
    return _project_summary(conn, ctx, project)


def _list_project_budgets(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    status = params.get("status")
    if status not in (None, "open", "closed"):
        _fail("invalid_input", "status must be open or closed")
    sql = "SELECT * FROM planning_projects WHERE ledger_id = ?"
    values: list[object] = [ctx["ledger_id"]]
    if status is not None:
        sql += " AND status = ?"
        values.append(status)
    sql += " ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, name_key, project_id"
    rows = conn.execute(sql, values).fetchall()
    return {"projects": [_project_summary(conn, ctx, row) for row in rows], "ledger": _ledger(ctx)}


def _close_project(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_write(ctx)
    _fail_if_closed(conn, ctx, ctx["today"], "close_project")
    project = _project(conn, ctx["ledger_id"], params.get("project_id"))
    if project["status"] == "closed":
        _fail("conflict", "The project is already closed", {"project_id": project["project_id"]})
    _version(project, params.get("expected_version"))
    summary = _project_summary(conn, ctx, project)
    now = _now()
    conn.execute(
        "UPDATE planning_projects SET status = 'closed', version = version + 1, closed_at = ?, updated_at = ? "
        "WHERE ledger_id = ? AND project_id = ?",
        (now, now, ctx["ledger_id"], project["project_id"]),
    )
    return {"project": {**summary, "status": "closed", "version": project["version"] + 1, "closed_at": now}, "ledger": _ledger(ctx)}


def _month_bounds(month: str) -> tuple[str, str]:
    year, month_number = (int(part) for part in month.split("-"))
    return f"{month}-01", f"{month}-{monthrange(year, month_number)[1]:02d}"


def _monthly_spend(conn: sqlite3.Connection, ledger_id: str, month: str) -> dict[str, int]:
    start_date, end_date = _month_bounds(month)
    rows = conn.execute(
        "SELECT transaction_id, category_id, amount_minor FROM ledger_transactions "
        "WHERE ledger_id = ? AND kind = 'expense' AND confirmation_status = 'confirmed' AND voided_at IS NULL "
        "AND occurred_on BETWEEN ? AND ? ORDER BY occurred_on, transaction_id",
        (ledger_id, start_date, end_date),
    ).fetchall()
    spent: dict[str, int] = {}
    for row in rows:
        splits = conn.execute(
            "SELECT category_id, amount_minor FROM ledger_transaction_splits WHERE ledger_id = ? AND transaction_id = ?",
            (ledger_id, row["transaction_id"]),
        ).fetchall()
        parts = splits or [{"category_id": row["category_id"], "amount_minor": row["amount_minor"]}]
        for part in parts:
            key = part["category_id"] or "__unclassified__"
            spent[key] = spent.get(key, 0) + part["amount_minor"]
    return spent


def _monthly_budgets(conn: sqlite3.Connection, ctx: dict, month: str) -> list[dict]:
    spent = _monthly_spend(conn, ctx["ledger_id"], month)
    rows = conn.execute(
        "SELECT b.category_id, c.path AS category_path, b.budget_minor, b.carry_in_minor, b.carry_source_month, b.version "
        "FROM planning_monthly_category_budgets AS b JOIN ledger_categories AS c "
        "ON c.ledger_id = b.ledger_id AND c.category_id = b.category_id "
        "WHERE b.ledger_id = ? AND b.month = ? ORDER BY c.path, b.category_id",
        (ctx["ledger_id"], month),
    ).fetchall()
    result = []
    for row in rows:
        key = row["category_id"]
        allowance = row["budget_minor"] + row["carry_in_minor"]
        actual = spent.pop(key, 0)
        result.append({
            "category_id": key,
            "category_path": row["category_path"],
            "budget_minor": row["budget_minor"],
            "carry_in_minor": row["carry_in_minor"],
            "carry_source_month": row["carry_source_month"],
            "total_allowance_minor": allowance,
            "actual_paid_minor": actual,
            "remaining_minor": allowance - actual,
            "carryable_minor": max(0, allowance - actual),
            "version": row["version"],
        })
    unbudgeted = []
    for key, actual in sorted(spent.items()):
        category = conn.execute(
            "SELECT path FROM ledger_categories WHERE ledger_id = ? AND category_id = ?",
            (ctx["ledger_id"], key),
        ).fetchone() if key != "__unclassified__" else None
        unbudgeted.append({"category_id": None if key == "__unclassified__" else key, "category_path": category["path"] if category else "待归类", "actual_paid_minor": actual})
    return result + [{"unbudgeted": True, **item} for item in unbudgeted]


def _upsert_monthly_budget(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_write(ctx)
    month = _month(params.get("month"))
    category_id, category_path = _category(conn, ctx["ledger_id"], params.get("category_id"))
    budget_minor = _amount(params.get("budget_minor"), "budget_minor", allow_zero=True)
    _fail_if_closed(conn, ctx, f"{month}-01", "upsert_monthly_category_budget")
    now = _now()
    conn.execute(
        "INSERT INTO planning_monthly_category_budgets "
        "(ledger_id, month, category_id, budget_minor, carry_in_minor, carry_source_month, version, created_by, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 0, NULL, 1, ?, ?, ?) "
        "ON CONFLICT(ledger_id, month, category_id) DO UPDATE SET budget_minor = excluded.budget_minor, "
        "version = planning_monthly_category_budgets.version + 1, updated_at = excluded.updated_at",
        (ctx["ledger_id"], month, category_id, budget_minor, ctx["principal_id"], now, now),
    )
    row = conn.execute(
        "SELECT budget_minor, carry_in_minor, carry_source_month, version FROM planning_monthly_category_budgets "
        "WHERE ledger_id = ? AND month = ? AND category_id = ?",
        (ctx["ledger_id"], month, category_id),
    ).fetchone()
    spent = _monthly_spend(conn, ctx["ledger_id"], month).get(category_id, 0)
    allowance = row["budget_minor"] + row["carry_in_minor"]
    return {
        "month": month,
        "category_id": category_id,
        "category_path": category_path,
        "budget_minor": row["budget_minor"],
        "carry_in_minor": row["carry_in_minor"],
        "total_allowance_minor": allowance,
        "actual_paid_minor": spent,
        "remaining_minor": allowance - spent,
        "version": row["version"],
        "ledger": _ledger(ctx),
    }


def _get_monthly_budgets(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    month = _month(params.get("month"))
    return {"month": month, "items": _monthly_budgets(conn, ctx, month), "ledger": _ledger(ctx)}


def _next_month(month: str) -> str:
    year, month_number = (int(part) for part in month.split("-"))
    if month_number == 12:
        return f"{year + 1:04d}-01"
    return f"{year:04d}-{month_number + 1:02d}"


def _carry_forward(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_write(ctx)
    from_month = _month(params.get("from_month"), "from_month")
    to_month = _month(params.get("to_month"), "to_month")
    if _next_month(from_month) != to_month:
        _fail("invalid_input", "to_month must immediately follow from_month")
    category_id, category_path = _category(conn, ctx["ledger_id"], params.get("category_id"))
    source_event = _latest_period_event(conn, ctx["ledger_id"], from_month)
    if source_event is None or source_event["action"] != "close":
        _fail("conflict", "Close the source month before carrying its remaining budget forward", {"from_month": from_month}, code="period_not_closed")
    source = conn.execute(
        "SELECT budget_minor, carry_in_minor FROM planning_monthly_category_budgets "
        "WHERE ledger_id = ? AND month = ? AND category_id = ?",
        (ctx["ledger_id"], from_month, category_id),
    ).fetchone()
    if source is None:
        _fail("not_found", "No monthly budget exists for this category", {"month": from_month, "category_id": category_id})
    _fail_if_closed(conn, ctx, f"{to_month}-01", "carry_forward_category_budget")
    spend = _monthly_spend(conn, ctx["ledger_id"], from_month).get(category_id, 0)
    carry_minor = max(0, source["budget_minor"] + source["carry_in_minor"] - spend)
    existing = conn.execute(
        "SELECT carry_source_month FROM planning_monthly_category_budgets "
        "WHERE ledger_id = ? AND month = ? AND category_id = ?",
        (ctx["ledger_id"], to_month, category_id),
    ).fetchone()
    if existing and existing["carry_source_month"] not in (None, from_month):
        _fail("conflict", "The next month already carries a different source month", {"carry_source_month": existing["carry_source_month"]})
    now = _now()
    conn.execute(
        "INSERT INTO planning_monthly_category_budgets "
        "(ledger_id, month, category_id, budget_minor, carry_in_minor, carry_source_month, version, created_by, created_at, updated_at) "
        "VALUES (?, ?, ?, 0, ?, ?, 1, ?, ?, ?) "
        "ON CONFLICT(ledger_id, month, category_id) DO UPDATE SET carry_in_minor = excluded.carry_in_minor, "
        "carry_source_month = excluded.carry_source_month, version = planning_monthly_category_budgets.version + 1, updated_at = excluded.updated_at",
        (ctx["ledger_id"], to_month, category_id, carry_minor, from_month, ctx["principal_id"], now, now),
    )
    target = conn.execute(
        "SELECT budget_minor, carry_in_minor, version FROM planning_monthly_category_budgets "
        "WHERE ledger_id = ? AND month = ? AND category_id = ?",
        (ctx["ledger_id"], to_month, category_id),
    ).fetchone()
    return {
        "category_id": category_id,
        "category_path": category_path,
        "from_month": from_month,
        "to_month": to_month,
        "source_actual_paid_minor": spend,
        "carry_in_minor": carry_minor,
        "next_month_budget_minor": target["budget_minor"],
        "next_month_total_allowance_minor": target["budget_minor"] + target["carry_in_minor"],
        "version": target["version"],
        "ledger": _ledger(ctx),
    }


def _set_income_classification(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_write(ctx)
    transaction_id = _text(params.get("transaction_id"), "transaction_id")
    classification = params.get("classification")
    if classification not in ("recurring", "one_off"):
        _fail("invalid_input", "classification must be recurring or one_off")
    row = conn.execute(
        "SELECT kind, occurred_on, voided_at FROM ledger_transactions WHERE ledger_id = ? AND transaction_id = ?",
        (ctx["ledger_id"], transaction_id),
    ).fetchone()
    if row is None:
        _fail("not_found", "Transaction not found in this ledger", {"transaction_id": transaction_id})
    if row["kind"] != "income" or row["voided_at"] is not None:
        _fail("invalid_input", "Only a non-voided income transaction can be classified", {"transaction_id": transaction_id})
    _fail_if_closed(conn, ctx, row["occurred_on"], "set_income_classification")
    conn.execute(
        "INSERT INTO planning_income_classifications(ledger_id, transaction_id, classification, updated_by, updated_at) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(ledger_id, transaction_id) DO UPDATE SET "
        "classification = excluded.classification, updated_by = excluded.updated_by, updated_at = excluded.updated_at",
        (ctx["ledger_id"], transaction_id, classification, ctx["principal_id"], _now()),
    )
    return {"transaction_id": transaction_id, "classification": classification, "ledger": _ledger(ctx)}


def _income_summary(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    start_date = _date(params.get("start_date"), "start_date")
    end_date = _date(params.get("end_date"), "end_date")
    if start_date > end_date:
        _fail("invalid_input", "start_date must be on or before end_date")
    classification = params.get("classification", "all")
    if classification not in ("all", "recurring", "one_off", "unclassified"):
        _fail("invalid_input", "classification must be all, recurring, one_off, or unclassified")
    rows = conn.execute(
        "SELECT t.confirmation_status, COALESCE(c.classification, 'unclassified') AS classification, "
        "COUNT(*) AS record_count, SUM(t.amount_minor) AS amount_minor, "
        "SUM(CASE WHEN t.estimated = 1 THEN 1 ELSE 0 END) AS estimated_count "
        "FROM ledger_transactions AS t LEFT JOIN planning_income_classifications AS c "
        "ON c.ledger_id = t.ledger_id AND c.transaction_id = t.transaction_id "
        "WHERE t.ledger_id = ? AND t.kind = 'income' AND t.voided_at IS NULL "
        "AND t.occurred_on BETWEEN ? AND ? AND (? = 'all' OR COALESCE(c.classification, 'unclassified') = ?) "
        "GROUP BY t.confirmation_status, COALESCE(c.classification, 'unclassified')",
        (ctx["ledger_id"], start_date, end_date, classification, classification),
    ).fetchall()
    totals = {"confirmed": 0, "pending": 0}
    by_classification = {}
    for row in rows:
        key = row["classification"]
        item = by_classification.setdefault(key, {"classification": key, "income_minor": 0, "pending_income_minor": 0, "record_count": 0, "estimated_count": 0})
        amount = row["amount_minor"] or 0
        if row["confirmation_status"] == "confirmed":
            item["income_minor"] += amount
            totals["confirmed"] += amount
        else:
            item["pending_income_minor"] += amount
            totals["pending"] += amount
        item["record_count"] += row["record_count"]
        item["estimated_count"] += row["estimated_count"] or 0
    ordered = [by_classification[key] for key in ("recurring", "one_off", "unclassified") if key in by_classification]
    return {
        "period": {"start_date": start_date, "end_date": end_date},
        "classification_filter": classification,
        "income_minor": totals["confirmed"],
        "pending_income_minor": totals["pending"],
        "total_income_and_pending_minor": totals["confirmed"] + totals["pending"],
        "items": ordered,
        "ledger": _ledger(ctx),
    }


def _upsert_saving_goal(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_write(ctx)
    name = _text(params.get("name"), "name")
    target_minor = _amount(params.get("target_minor"), "target_minor")
    due_date = _date(params["due_date"], "due_date") if params.get("due_date") is not None else None
    start_date = _date(params.get("start_date", ctx["today"]), "start_date")
    if due_date and due_date < start_date:
        _fail("invalid_input", "due_date must be on or after start_date")
    savings_account_id = _optional_text(params.get("savings_account_id"), "savings_account_id")
    goal_id = _optional_text(params.get("goal_id"), "goal_id")
    now = _now()
    _fail_if_closed(conn, ctx, start_date, "upsert_saving_goal")
    if goal_id is None:
        goal_id = _new_id()
        conn.execute(
            "INSERT INTO planning_saving_goals(ledger_id, goal_id, name, target_minor, start_date, due_date, savings_account_id, "
            "status, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)",
            (ctx["ledger_id"], goal_id, name, target_minor, start_date, due_date, savings_account_id, ctx["principal_id"], now, now),
        )
    else:
        goal = _goal(conn, ctx["ledger_id"], goal_id)
        if goal["status"] != "active":
            _fail("conflict", "Only an active saving goal can be updated", {"goal_id": goal_id})
        _version(goal, params.get("expected_version"))
        conn.execute(
            "UPDATE planning_saving_goals SET name = ?, target_minor = ?, start_date = ?, due_date = ?, savings_account_id = ?, "
            "version = version + 1, updated_at = ? WHERE ledger_id = ? AND goal_id = ?",
            (name, target_minor, start_date, due_date, savings_account_id, now, ctx["ledger_id"], goal_id),
        )
    return {"goal": _goal_progress(conn, ctx, _goal(conn, ctx["ledger_id"], goal_id), ctx["today"], 3), "ledger": _ledger(ctx)}


def _add_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    year, month_number = divmod(month_index, 12)
    month_number += 1
    return date(year, month_number, min(value.day, monthrange(year, month_number)[1]))


def _goal_progress(conn: sqlite3.Connection, ctx: dict, goal: sqlite3.Row, as_of: str, pace_months: int) -> dict:
    goal_id = goal["goal_id"]
    current_row = conn.execute(
        "SELECT COALESCE(SUM(amount_minor), 0) AS amount_minor FROM planning_goal_changes "
        "WHERE ledger_id = ? AND goal_id = ? AND occurred_on <= ?",
        (ctx["ledger_id"], goal_id, as_of),
    ).fetchone()
    current_minor = current_row["amount_minor"]
    gap_minor = max(0, goal["target_minor"] - current_minor)
    as_of_date = date.fromisoformat(as_of)
    pace_start = _add_months(as_of_date, -pace_months).isoformat()
    pace_row = conn.execute(
        "SELECT COALESCE(SUM(amount_minor), 0) AS amount_minor FROM planning_goal_changes "
        "WHERE ledger_id = ? AND goal_id = ? AND occurred_on BETWEEN ? AND ?",
        (ctx["ledger_id"], goal_id, pace_start, as_of),
    ).fetchone()
    elapsed_days = max(1, (as_of_date - date.fromisoformat(pace_start)).days)
    monthly_pace_minor = round(pace_row["amount_minor"] * 30.436875 / elapsed_days)
    estimated_completion_date = None
    if gap_minor == 0:
        estimated_completion_date = as_of
    elif monthly_pace_minor > 0:
        months_to_goal = (gap_minor + monthly_pace_minor - 1) // monthly_pace_minor
        estimated_completion_date = _add_months(as_of_date, months_to_goal).isoformat()
    required_monthly_increase_minor = None
    months_remaining = None
    if goal["due_date"]:
        due = date.fromisoformat(goal["due_date"])
        months_remaining = max(0.0, ((due.year - as_of_date.year) * 12) + due.month - as_of_date.month + (due.day - as_of_date.day) / 30.436875)
        if months_remaining > 0:
            required_monthly_increase_minor = math.ceil(gap_minor / months_remaining)
        else:
            required_monthly_increase_minor = gap_minor
    return {
        "goal_id": goal_id,
        "name": goal["name"],
        "status": goal["status"],
        "version": goal["version"],
        "target_minor": goal["target_minor"],
        "current_minor": current_minor,
        "gap_minor": gap_minor,
        "start_date": goal["start_date"],
        "due_date": goal["due_date"],
        "savings_account_id": goal["savings_account_id"],
        "pace": {
            "from_date": pace_start,
            "to_date": as_of,
            "months": pace_months,
            "net_increase_minor": pace_row["amount_minor"],
            "average_monthly_increase_minor": monthly_pace_minor,
        },
        "months_remaining": round(months_remaining, 2) if months_remaining is not None else None,
        "required_monthly_increase_minor": required_monthly_increase_minor,
        "estimated_completion_date": estimated_completion_date,
    }


def _get_saving_goal_progress(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    goal = _goal(conn, ctx["ledger_id"], params.get("goal_id"))
    as_of = _date(params.get("as_of", ctx["today"]), "as_of")
    pace_months = params.get("pace_months", 3)
    if type(pace_months) is not int or not 1 <= pace_months <= 24:
        _fail("invalid_input", "pace_months must be an integer from 1 to 24")
    return {"goal": _goal_progress(conn, ctx, goal, as_of, pace_months), "ledger": _ledger(ctx)}


def _list_saving_goals(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    as_of = _date(params.get("as_of", ctx["today"]), "as_of")
    status = params.get("status", "active")
    if status not in ("active", "completed", "paused", "all"):
        _fail("invalid_input", "status must be active, completed, paused, or all")
    query = (
        "SELECT g.goal_id, g.name, g.status, g.target_minor, g.start_date, g.due_date, "
        "COALESCE(SUM(CASE WHEN c.occurred_on <= ? THEN c.amount_minor ELSE 0 END), 0) AS current_minor "
        "FROM planning_saving_goals AS g LEFT JOIN planning_goal_changes AS c "
        "ON c.ledger_id = g.ledger_id AND c.goal_id = g.goal_id WHERE g.ledger_id = ?"
    )
    values: list[object] = [as_of, ctx["ledger_id"]]
    if status != "all":
        query += " AND g.status = ?"
        values.append(status)
    query += " GROUP BY g.ledger_id, g.goal_id ORDER BY CASE g.status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 ELSE 2 END, g.name, g.goal_id"
    rows = conn.execute(query, values).fetchall()
    return {
        "as_of": as_of,
        "goals": [
            {
                "goal_id": row["goal_id"],
                "name": row["name"],
                "status": row["status"],
                "target_minor": row["target_minor"],
                "current_minor": row["current_minor"],
                "gap_minor": max(0, row["target_minor"] - row["current_minor"]),
                "start_date": row["start_date"],
                "due_date": row["due_date"],
            }
            for row in rows
        ],
        "ledger": _ledger(ctx),
    }


def _record_saving_goal_change(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_write(ctx)
    goal = _goal(conn, ctx["ledger_id"], params.get("goal_id"))
    if goal["status"] != "active":
        _fail("conflict", "Only an active saving goal can receive progress changes", {"goal_id": goal["goal_id"]})
    amount_minor = params.get("amount_minor")
    if type(amount_minor) is not int or amount_minor == 0:
        _fail("invalid_input", "amount_minor must be a non-zero integer in fen")
    occurred_on = _date(params.get("occurred_on", ctx["today"]), "occurred_on")
    note = _optional_text(params.get("note"), "note")
    _fail_if_closed(conn, ctx, occurred_on, "record_saving_goal_change")
    current = conn.execute(
        "SELECT COALESCE(SUM(amount_minor), 0) AS amount_minor FROM planning_goal_changes "
        "WHERE ledger_id = ? AND goal_id = ? AND occurred_on <= ?",
        (ctx["ledger_id"], goal["goal_id"], occurred_on),
    ).fetchone()["amount_minor"]
    if current + amount_minor < 0:
        _fail("invalid_input", "A goal change cannot reduce confirmed progress below zero", {"current_minor": current, "amount_minor": amount_minor})
    entry_id = _new_id()
    conn.execute(
        "INSERT INTO planning_goal_changes(ledger_id, entry_id, goal_id, amount_minor, occurred_on, note, source_type, source_id, request_id, actor_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'manual', NULL, ?, ?, ?)",
        (ctx["ledger_id"], entry_id, goal["goal_id"], amount_minor, occurred_on, note, ctx["request_id"], ctx["principal_id"], _now()),
    )
    return {
        "entry_id": entry_id,
        "change_minor": amount_minor,
        "goal": _goal_progress(conn, ctx, goal, ctx["today"], 3),
        "ledger": _ledger(ctx),
    }


def _associate_saving_goal_transfer(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_write(ctx)
    goal = _goal(conn, ctx["ledger_id"], params.get("goal_id"))
    if goal["status"] != "active":
        _fail("conflict", "Only an active saving goal can receive a transfer", {"goal_id": goal["goal_id"]})
    transfer_id = _text(params.get("transfer_id"), "transfer_id")
    from .movements import get_transfer

    transfer = get_transfer(conn, ctx, transfer_id)
    if transfer is None:
        _fail("not_found", "A confirmed transfer in this ledger is required", {"transfer_id": transfer_id})
    if transfer["ledger_id"] != ctx["ledger_id"] or transfer["status"] != "confirmed":
        _fail("forbidden", "Only a confirmed transfer in this ledger can be allocated")
    if goal["savings_account_id"] and goal["savings_account_id"] != transfer["destination_account_id"]:
        _fail("invalid_input", "The transfer destination does not match this goal's savings account", {
            "expected_destination_account_id": goal["savings_account_id"],
            "destination_account_id": transfer["destination_account_id"],
        })
    existing = conn.execute(
        "SELECT goal_id, entry_id FROM planning_goal_changes "
        "WHERE ledger_id = ? AND source_type = 'transfer' AND source_id = ?",
        (ctx["ledger_id"], transfer_id),
    ).fetchone()
    if existing is not None:
        if existing["goal_id"] != goal["goal_id"]:
            _fail("conflict", "This transfer is already allocated to another goal", {"goal_id": existing["goal_id"], "transfer_id": transfer_id})
        return {
            "transfer_id": transfer_id,
            "source_account_id": transfer["source_account_id"],
            "destination_account_id": transfer["destination_account_id"],
            "allocated_minor": transfer["amount_minor"],
            "already_allocated": True,
            "goal": _goal_progress(conn, ctx, goal, ctx["today"], 3),
            "ledger": _ledger(ctx),
        }
    _fail_if_closed(conn, ctx, transfer["occurred_on"], "associate_saving_goal_transfer")
    conn.execute(
        "INSERT INTO planning_goal_changes(ledger_id, entry_id, goal_id, amount_minor, occurred_on, note, source_type, source_id, request_id, actor_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, NULL, 'transfer', ?, ?, ?, ?)",
        (ctx["ledger_id"], _new_id(), goal["goal_id"], transfer["amount_minor"], transfer["occurred_on"], transfer_id, ctx["request_id"], ctx["principal_id"], _now()),
    )
    return {
        "transfer_id": transfer_id,
        "source_account_id": transfer["source_account_id"],
        "destination_account_id": transfer["destination_account_id"],
        "allocated_minor": transfer["amount_minor"],
        "already_allocated": False,
        "goal": _goal_progress(conn, ctx, goal, ctx["today"], 3),
        "ledger": _ledger(ctx),
    }


def _close_preview(conn: sqlite3.Connection, ctx: dict, month: str) -> dict:
    start_date, end_date = _month_bounds(month)
    totals = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN kind = 'income' AND confirmation_status = 'confirmed' THEN amount_minor ELSE 0 END), 0) AS income_minor, "
        "COALESCE(SUM(CASE WHEN kind = 'expense' AND confirmation_status = 'confirmed' THEN amount_minor ELSE 0 END), 0) AS expense_minor, "
        "COALESCE(SUM(CASE WHEN kind = 'income' AND confirmation_status = 'pending' THEN amount_minor ELSE 0 END), 0) AS pending_income_minor, "
        "COALESCE(SUM(CASE WHEN kind = 'expense' AND confirmation_status = 'pending' THEN amount_minor ELSE 0 END), 0) AS pending_expense_minor, "
        "COUNT(*) AS record_count FROM ledger_transactions WHERE ledger_id = ? AND occurred_on BETWEEN ? AND ? AND voided_at IS NULL",
        (ctx["ledger_id"], start_date, end_date),
    ).fetchone()
    unresolved_count = conn.execute(
        "SELECT COUNT(*) AS count FROM ledger_transactions WHERE ledger_id = ? AND occurred_on BETWEEN ? AND ? "
        "AND voided_at IS NULL AND (confirmation_status = 'pending' OR classification_status = 'unclassified')",
        (ctx["ledger_id"], start_date, end_date),
    ).fetchone()["count"]
    rows = conn.execute(
        "SELECT transaction_id, occurred_on, kind, amount_minor, confirmation_status, classification_status, category_id "
        "FROM ledger_transactions WHERE ledger_id = ? AND occurred_on BETWEEN ? AND ? AND voided_at IS NULL "
        "AND (confirmation_status = 'pending' OR classification_status = 'unclassified') "
        "ORDER BY occurred_on, transaction_id LIMIT 200",
        (ctx["ledger_id"], start_date, end_date),
    ).fetchall()
    unresolved = [{
        "transaction_id": row["transaction_id"],
        "occurred_on": row["occurred_on"],
        "kind": row["kind"],
        "amount_minor": row["amount_minor"],
        "confirmation_status": row["confirmation_status"],
        "classification_status": row["classification_status"],
        "category_id": row["category_id"],
    } for row in rows]
    return {
        "month": month,
        "period": {"start_date": start_date, "end_date": end_date},
        "record_count": totals["record_count"],
        "income_minor": totals["income_minor"],
        "expense_minor": totals["expense_minor"],
        "net_minor": totals["income_minor"] - totals["expense_minor"],
        "pending_income_minor": totals["pending_income_minor"],
        "pending_expense_minor": totals["pending_expense_minor"],
        "unresolved_count": unresolved_count,
        "unresolved_items": unresolved,
        "unresolved_truncated": unresolved_count > len(unresolved),
    }


def _get_month_close_preview(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    month = _month(params.get("month"))
    latest = _latest_period_event(conn, ctx["ledger_id"], month)
    if latest and latest["action"] == "close":
        _fail("conflict", "This period is already closed", {"month": month, "closed_version": latest["version"]}, code="period_closed")
    return {**_close_preview(conn, ctx, month), "ledger": _ledger(ctx)}


def _close_month(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_write(ctx)
    month = _month(params.get("month"))
    latest = _latest_period_event(conn, ctx["ledger_id"], month)
    if latest and latest["action"] == "close":
        _fail("conflict", "This period is already closed", {"month": month, "closed_version": latest["version"]}, code="period_closed")
    preview = _close_preview(conn, ctx, month)
    if preview["unresolved_count"]:
        _fail(
            "needs_clarification",
            "Resolve pending or unclassified transactions before closing this month",
            {"month": month, "unresolved_count": preview["unresolved_count"], "unresolved_items": preview["unresolved_items"], "unresolved_truncated": preview["unresolved_truncated"]},
            code="month_review_required",
        )
    version = _next_period_version(conn, ctx["ledger_id"], month)
    snapshot = {key: value for key, value in preview.items() if key != "ledger"}
    now = _now()
    conn.execute(
        "INSERT INTO planning_period_events(ledger_id, month, version, action, reason, snapshot_json, request_id, actor_id, created_at) "
        "VALUES (?, ?, ?, 'close', NULL, ?, ?, ?, ?)",
        (ctx["ledger_id"], month, version, json.dumps(snapshot, ensure_ascii=False, sort_keys=True), ctx["request_id"], ctx["principal_id"], now),
    )
    return {"month": month, "version": version, "status": "closed", "closed_at": now, "snapshot": snapshot, "ledger": _ledger(ctx)}


def _reopen_month(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _require_write(ctx)
    month = _month(params.get("month"))
    reason = _text(params.get("reason"), "reason")
    latest = _latest_period_event(conn, ctx["ledger_id"], month)
    if latest is None or latest["action"] != "close":
        _fail("conflict", "The period is not currently closed", {"month": month})
    version = _next_period_version(conn, ctx["ledger_id"], month)
    now = _now()
    conn.execute(
        "INSERT INTO planning_period_events(ledger_id, month, version, action, reason, snapshot_json, request_id, actor_id, created_at) "
        "VALUES (?, ?, ?, 'reopen', ?, NULL, ?, ?, ?)",
        (ctx["ledger_id"], month, version, reason, ctx["request_id"], ctx["principal_id"], now),
    )
    return {"month": month, "version": version, "status": "open", "reopened_at": now, "reason": reason, "ledger": _ledger(ctx)}


def reminder_snapshot(conn: sqlite3.Connection, ctx: dict) -> dict:
    """Return current-month budget balances and active-goal progress for reminder rules."""
    if ctx["cross_account"]:
        _fail("forbidden", "Cross-account goal reads do not produce reminders")
    month = _month_of(ctx["today"])
    budgets = [item for item in _monthly_budgets(conn, ctx, month) if not item.get("unbudgeted")]
    goals = conn.execute(
        "SELECT * FROM planning_saving_goals WHERE ledger_id = ? AND status = 'active' ORDER BY created_at, goal_id",
        (ctx["ledger_id"],),
    ).fetchall()
    goal_progress = []
    for goal in goals:
        progress = _goal_progress(conn, ctx, goal, ctx["today"], 3)
        goal_progress.append({
            "goal_id": progress["goal_id"],
            "name": progress["name"],
            "target_minor": progress["target_minor"],
            "current_minor": progress["current_minor"],
            "gap_minor": progress["gap_minor"],
            "due_date": progress["due_date"],
            "required_monthly_increase_minor": progress["required_monthly_increase_minor"],
            "average_monthly_increase_minor": progress["pace"]["average_monthly_increase_minor"],
            "estimated_completion_date": progress["estimated_completion_date"],
        })
    return {"period": month, "monthly_budgets": budgets, "saving_goals": goal_progress, "ledger": _ledger(ctx)}


def project_transaction_ids(conn: sqlite3.Connection, ctx: dict, project_id: str) -> list[str]:
    """Return the IDs of a project's linked transactions in the current ledger."""
    if ctx["cross_account"]:
        _fail("forbidden", "Project exports are available only in the caller's own ledger")
    project_id = _text(project_id, "project_id")
    _project(conn, ctx["ledger_id"], project_id)
    rows = conn.execute(
        "SELECT transaction_id FROM project_entries WHERE ledger_id = ? AND project_id = ? ORDER BY transaction_id",
        (ctx["ledger_id"], project_id),
    ).fetchall()
    from .movements import project_reimbursements

    reimbursement = project_reimbursements(conn, ctx["ledger_id"], project_id)
    return sorted({row["transaction_id"] for row in rows} | set(reimbursement["advance_transaction_ids"]))


def _obj(properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


_STRING = {"type": "string", "minLength": 1}
_DATE = {"type": "string", "format": "date"}
_MONTH = {"type": "string", "pattern": r"^\d{4}-\d{2}$"}
_INTEGER = {"type": "integer", "minimum": 0}
_POSITIVE_INTEGER = {"type": "integer", "minimum": 1}


ACTIONS = {
    "upsert_project_budget": {
        "description": "Create or update a project budget with optional category budgets and an optional linked expense transaction.",
        "inputSchema": _obj({
            "project_id": _STRING,
            "expected_version": _POSITIVE_INTEGER,
            "name": _STRING,
            "budget_minor": _POSITIVE_INTEGER,
            "start_date": _DATE,
            "end_date": _DATE,
            "budget_items": {"type": "array", "items": _obj({"category_id": _STRING, "amount_minor": _POSITIVE_INTEGER}, ("category_id", "amount_minor"))},
            "transaction_id": _STRING,
        }, ("name", "budget_minor")),
        "scope": "own_write",
        "run": _project_budget_upsert,
    },
    "link_project_transaction": {
        "description": "Link one non-voided expense to an open project in the same ledger; its confirmed payment consumes the project budget.",
        "inputSchema": _obj({"project_id": _STRING, "transaction_id": _STRING}, ("project_id", "transaction_id")),
        "scope": "own_write",
        "run": _link_project_action,
    },
    "get_project_budget": {
        "description": "Read a project's total and category budget, confirmed paid expenses, remaining amount, and separately tracked reimbursements.",
        "inputSchema": _obj({"project_id": _STRING}, ("project_id",)),
        "scope": "own_read",
        "run": _get_project_budget,
    },
    "list_project_budgets": {
        "description": "List project budgets and actual paid progress in the caller's ledger.",
        "inputSchema": _obj({"status": {"type": "string", "enum": ["open", "closed"]}}),
        "scope": "own_read",
        "run": _list_project_budgets,
    },
    "close_project": {
        "description": "Close a project and return its final budget, actual paid costs, reimbursements, and category totals.",
        "inputSchema": _obj({"project_id": _STRING, "expected_version": _POSITIVE_INTEGER}, ("project_id",)),
        "scope": "own_write",
        "run": _close_project,
    },
    "upsert_monthly_category_budget": {
        "description": "Set a category's base budget for a month; confirmed paid expenses determine spending and remaining budget.",
        "inputSchema": _obj({"month": _MONTH, "category_id": _STRING, "budget_minor": _INTEGER}, ("month", "category_id", "budget_minor")),
        "scope": "own_write",
        "run": _upsert_monthly_budget,
    },
    "get_monthly_category_budgets": {
        "description": "Read monthly category budgets, carry-in amounts, confirmed spending, and remaining balances.",
        "inputSchema": _obj({"month": _MONTH}, ("month",)),
        "scope": "own_read",
        "run": _get_monthly_budgets,
    },
    "carry_forward_category_budget": {
        "description": "Carry the unspent amount for one category from a closed month into the immediately following month.",
        "inputSchema": _obj({"from_month": _MONTH, "to_month": _MONTH, "category_id": _STRING}, ("from_month", "to_month", "category_id")),
        "scope": "own_write",
        "run": _carry_forward,
    },
    "set_income_classification": {
        "description": "Classify a confirmed income transaction as recurring or one-off without changing cash-flow totals.",
        "inputSchema": _obj({"transaction_id": _STRING, "classification": {"type": "string", "enum": ["recurring", "one_off"]}}, ("transaction_id", "classification")),
        "scope": "own_write",
        "run": _set_income_classification,
    },
    "get_income_summary": {
        "description": "Read income totals by recurring, one-off, and unclassified status while preserving confirmed and pending totals.",
        "inputSchema": _obj({"start_date": _DATE, "end_date": _DATE, "classification": {"type": "string", "enum": ["all", "recurring", "one_off", "unclassified"]}}, ("start_date", "end_date")),
        "scope": "own_read",
        "run": _income_summary,
    },
    "upsert_saving_goal": {
        "description": "Create or update a savings goal and optional target savings account; progress changes are recorded separately.",
        "inputSchema": _obj({
            "goal_id": _STRING,
            "expected_version": _POSITIVE_INTEGER,
            "name": _STRING,
            "target_minor": _POSITIVE_INTEGER,
            "start_date": _DATE,
            "due_date": _DATE,
            "savings_account_id": _STRING,
        }, ("name", "target_minor")),
        "scope": "own_write",
        "run": _upsert_saving_goal,
    },
    "record_saving_goal_change": {
        "description": "Record a user-confirmed positive or negative change to a savings goal, independently of income transactions.",
        "inputSchema": _obj({"goal_id": _STRING, "amount_minor": {"type": "integer", "not": {"const": 0}}, "occurred_on": _DATE, "note": _STRING}, ("goal_id", "amount_minor")),
        "scope": "own_write",
        "run": _record_saving_goal_change,
    },
    "get_saving_goal_progress": {
        "description": "Read a savings goal's confirmed progress, remaining target, recent pace, estimated completion date, and required monthly increase.",
        "inputSchema": _obj({"goal_id": _STRING, "as_of": _DATE, "pace_months": {"type": "integer", "minimum": 1, "maximum": 24}}, ("goal_id",)),
        "scope": "admin_goal",
        "run": _get_saving_goal_progress,
    },
    "list_saving_goals": {
        "description": "List goal names and confirmed progress in the selected authorized ledger so a goal can be selected for a detailed progress query.",
        "inputSchema": _obj({
            "status": {"type": "string", "enum": ["active", "completed", "paused", "all"]},
            "as_of": _DATE,
        }),
        "scope": "admin_goal",
        "run": _list_saving_goals,
    },
    "associate_saving_goal_transfer": {
        "description": "Allocate a confirmed real transfer to one savings goal without creating another transfer or income entry.",
        "inputSchema": _obj({"goal_id": _STRING, "transfer_id": _STRING}, ("goal_id", "transfer_id")),
        "scope": "own_write",
        "run": _associate_saving_goal_transfer,
    },
    "get_month_close_preview": {
        "description": "Preview a month's confirmed totals and pending or unclassified transactions before closing it.",
        "inputSchema": _obj({"month": _MONTH}, ("month",)),
        "scope": "own_read",
        "run": _get_month_close_preview,
    },
    "close_month": {
        "description": "Close a reviewed month and preserve an immutable snapshot; unresolved transactions must be handled first.",
        "inputSchema": _obj({"month": _MONTH}, ("month",)),
        "scope": "own_write",
        "run": _close_month,
    },
    "reopen_month": {
        "description": "Reopen a closed month for corrections and record the reason in the period history.",
        "inputSchema": _obj({"month": _MONTH, "reason": _STRING}, ("month", "reason")),
        "scope": "own_write",
        "run": _reopen_month,
    },
}
