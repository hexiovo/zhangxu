from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from calendar import monthrange

from .contract import BusinessError
from . import ledger


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS insight_financial_cycles (
    ledger_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    start_day INTEGER NOT NULL CHECK (start_day BETWEEN 1 AND 31),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS insight_expense_view_rules (
    ledger_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    name TEXT NOT NULL,
    match_type TEXT NOT NULL CHECK (match_type IN ('category', 'merchant')),
    match_key TEXT NOT NULL,
    match_value TEXT NOT NULL,
    classification TEXT NOT NULL CHECK (classification IN ('fixed', 'flexible')),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, rule_id),
    UNIQUE (ledger_id, match_type, match_key)
);

CREATE INDEX IF NOT EXISTS idx_insight_expense_rules_match
    ON insight_expense_view_rules(ledger_id, match_type, match_key);

CREATE TABLE IF NOT EXISTS insight_budget_scenario_drafts (
    ledger_id TEXT NOT NULL,
    draft_id TEXT NOT NULL,
    name TEXT NOT NULL,
    month TEXT NOT NULL,
    assumptions_json TEXT NOT NULL,
    date_basis TEXT NOT NULL CHECK (date_basis IN ('occurred_on', 'charged_on')),
    result_json TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, draft_id)
);

CREATE INDEX IF NOT EXISTS idx_insight_scenario_drafts_month
    ON insight_budget_scenario_drafts(ledger_id, month, updated_at DESC);

CREATE TABLE IF NOT EXISTS insight_monthly_report_templates (
    ledger_id TEXT NOT NULL,
    template_id TEXT NOT NULL,
    name TEXT NOT NULL,
    config_json TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, template_id),
    UNIQUE (ledger_id, name)
);

CREATE TABLE IF NOT EXISTS insight_report_snapshots (
    ledger_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    template_id TEXT NOT NULL,
    template_name TEXT NOT NULL,
    template_config_json TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    date_basis TEXT NOT NULL CHECK (date_basis IN ('occurred_on', 'charged_on')),
    result_json TEXT NOT NULL,
    source_facts_json TEXT NOT NULL,
    result_sha256 TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, snapshot_id)
);

CREATE INDEX IF NOT EXISTS idx_insight_report_snapshots_period
    ON insight_report_snapshots(ledger_id, template_id, start_date, end_date, created_at DESC);

CREATE TABLE IF NOT EXISTS insight_saved_views (
    ledger_id TEXT NOT NULL,
    view_id TEXT NOT NULL,
    name TEXT NOT NULL,
    config_json TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, view_id),
    UNIQUE (ledger_id, name)
);

CREATE TABLE IF NOT EXISTS insight_merchant_refs (
    ledger_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    merchant_ref TEXT NOT NULL,
    merchant_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, principal_id, token_id, merchant_ref),
    UNIQUE (ledger_id, principal_id, token_id, merchant_key)
);

CREATE INDEX IF NOT EXISTS idx_insight_merchant_refs_expiry
    ON insight_merchant_refs(ledger_id, principal_id, token_id, expires_at);
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


def _date(value: object, field: str) -> str:
    if not isinstance(value, str):
        _fail("invalid_input", f"{field} must use YYYY-MM-DD", {"field": field})
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        _fail("invalid_input", f"{field} must use YYYY-MM-DD", {"field": field})
    if parsed.isoformat() != value:
        _fail("invalid_input", f"{field} must use YYYY-MM-DD", {"field": field})
    return value


def _month(value: object, field: str = "month") -> str:
    if not isinstance(value, str) or len(value) != 7 or value[4] != "-":
        _fail("invalid_input", f"{field} must use YYYY-MM", {"field": field})
    year, month_number = value.split("-", 1)
    if not year.isdigit() or not month_number.isdigit():
        _fail("invalid_input", f"{field} must use YYYY-MM", {"field": field})
    try:
        date(int(year), int(month_number), 1)
    except ValueError:
        _fail("invalid_input", f"{field} must use a valid calendar month", {"field": field})
    return value


def _period(start_value: object, end_value: object) -> tuple[str, str]:
    start_date = _date(start_value, "start_date")
    end_date = _date(end_value, "end_date")
    if start_date > end_date:
        _fail("invalid_input", "start_date must be on or before end_date")
    return start_date, end_date


def _date_basis(value: object = "occurred_on") -> str:
    if value not in ("occurred_on", "charged_on"):
        _fail("invalid_input", "date_basis must be occurred_on or charged_on")
    return value


def _own_ledger(ctx: dict, *, write: bool = False) -> None:
    if ctx["cross_account"]:
        _fail("forbidden", "Insight reports are available only in the caller's own ledger")
    if write and not ctx["request_id"]:
        _fail("invalid_input", "A request_id is required for writes")


def _ledger_info(ctx: dict) -> dict:
    return {"ledger_id": ctx["ledger_id"], "account_id": ctx["account_id"], "account_name": ctx["account_name"]}


def _filters(value: object) -> dict:
    return ledger._normalize_filters({} if value is None else value, ledger._REPORT_FILTERS)


def _effective_date_sql(date_basis: str) -> str:
    if date_basis == "occurred_on":
        return "t.occurred_on"
    return (
        "COALESCE((SELECT ledger_date.charged_on FROM ledger_transaction_dates AS ledger_date "
        "WHERE ledger_date.ledger_id = t.ledger_id AND ledger_date.transaction_id = t.transaction_id), t.occurred_on)"
    )


def _transaction_rows(
    conn: sqlite3.Connection,
    ctx: dict,
    start_date: str | None,
    end_date: str | None,
    filters: dict,
    date_basis: str,
) -> list[sqlite3.Row]:
    clause, values = ledger._filter_sql(
        conn, ctx, filters, report=True, start_date=start_date, end_date=end_date, date_basis=date_basis,
    )
    date_sql = _effective_date_sql(date_basis)
    return conn.execute(
        "SELECT t.transaction_id, t.version, t.occurred_on, t.kind, t.amount_minor, t.estimated, "
        "t.confirmation_status, t.classification_status, t.category_id, t.merchant, "
        + date_sql + " AS date_basis_date FROM ledger_transactions AS t WHERE " + clause
        + " ORDER BY date_basis_date, t.created_at, t.transaction_id",
        values,
    ).fetchall()


def _category_path(conn: sqlite3.Connection, ledger_id: str, category_id: str | None) -> str | None:
    if category_id is None:
        return None
    return ledger._category_path(conn, ledger_id, category_id)


def _category_matches(conn: sqlite3.Connection, ledger_id: str, category_id: str | None, selected: str | None) -> bool:
    if selected is None:
        return category_id is None
    if category_id is None:
        return False
    selected_path = _category_path(conn, ledger_id, selected)
    actual_path = _category_path(conn, ledger_id, category_id)
    return actual_path == selected_path or actual_path.startswith(selected_path + "／")


def _project_info(conn: sqlite3.Connection, ledger_id: str, transaction_id: str) -> tuple[str | None, str | None]:
    row = conn.execute(
        "SELECT p.project_id, p.name FROM project_entries AS e JOIN planning_projects AS p "
        "ON p.ledger_id = e.ledger_id AND p.project_id = e.project_id "
        "WHERE e.ledger_id = ? AND e.transaction_id = ?",
        (ledger_id, transaction_id),
    ).fetchone()
    return (row["project_id"], row["name"]) if row else (None, None)


def _expense_rules(conn: sqlite3.Connection, ledger_id: str) -> tuple[dict[str, dict], dict[str, dict]]:
    rows = conn.execute(
        "SELECT match_type, match_key, match_value, classification FROM insight_expense_view_rules WHERE ledger_id = ?",
        (ledger_id,),
    ).fetchall()
    categories = {}
    merchants = {}
    for row in rows:
        target = categories if row["match_type"] == "category" else merchants
        target[row["match_key"]] = dict(row)
    return categories, merchants


def _expense_style(
    conn: sqlite3.Connection,
    ledger_id: str,
    category_id: str | None,
    merchant: str | None,
    rules: tuple[dict[str, dict], dict[str, dict]] | None = None,
) -> str:
    categories, merchants = rules or _expense_rules(conn, ledger_id)
    if category_id and category_id in categories:
        return categories[category_id]["classification"]
    if merchant:
        merchant_key, _ = ledger._canonical_merchant(conn, ledger_id, merchant)
        if merchant_key in merchants:
            return merchants[merchant_key]["classification"]
    return "unclassified"


def _merchant_drilldown_ref(conn: sqlite3.Connection, ctx: dict, merchant: str | None) -> str | None:
    if merchant is None:
        return None
    ledger_id = ctx["ledger_id"]
    principal_id = ctx["principal_id"]
    token_id = ctx.get("token_id")
    if not token_id:
        _fail("forbidden", "An authenticated token is required for merchant drilldown references")
    merchant_key = ledger._canonical_merchant(conn, ledger_id, merchant)[0]
    now = _now()
    conn.execute(
        "DELETE FROM insight_merchant_refs WHERE ledger_id = ? AND principal_id = ? AND token_id = ? AND expires_at <= ?",
        (ledger_id, principal_id, token_id, now),
    )
    conn.execute(
        "INSERT INTO insight_merchant_refs(ledger_id,principal_id,token_id,merchant_ref,merchant_key,created_at,expires_at) "
        "VALUES (?,?,?,?,?,?,?) ON CONFLICT(ledger_id,principal_id,token_id,merchant_key) DO NOTHING",
        (ledger_id, principal_id, token_id, _new_id(), merchant_key, now,
         (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(timespec="seconds").replace("+00:00", "Z")),
    )
    row = conn.execute(
        "SELECT merchant_ref FROM insight_merchant_refs WHERE ledger_id = ? AND principal_id = ? AND token_id = ? AND merchant_key = ?",
        (ledger_id, principal_id, token_id, merchant_key),
    ).fetchone()
    return row["merchant_ref"]


def _merchant_key_for_ref(conn: sqlite3.Connection, ctx: dict, merchant_ref: str) -> str:
    token_id = ctx.get("token_id")
    if not token_id:
        _fail("forbidden", "An authenticated token is required for merchant drilldown references")
    row = conn.execute(
        "SELECT merchant_key FROM insight_merchant_refs WHERE ledger_id = ? AND principal_id = ? AND token_id = ? AND merchant_ref = ? AND expires_at > ?",
        (ctx["ledger_id"], ctx["principal_id"], token_id, merchant_ref, _now()),
    ).fetchone()
    if row is None:
        _fail("not_found", "Merchant drilldown reference is expired or unavailable in this ledger and token")
    return row["merchant_key"]


def _merchant_group_value(conn: sqlite3.Connection, ctx: dict, merchant: str | None) -> dict:
    if ctx.get("source") == "web":
        _own_ledger(ctx)
        return {"merchant": merchant}
    return {"merchant_ref": _merchant_drilldown_ref(conn, ctx, merchant)}


def _allocation_facts(
    conn: sqlite3.Connection,
    ctx: dict,
    rows: list[sqlite3.Row],
    *,
    expand_categories: bool,
    selected_category: str | None = None,
    classify_expense: bool = False,
) -> list[dict]:
    ledger_id = ctx["ledger_id"]
    rules = _expense_rules(conn, ledger_id) if classify_expense else None
    facts = []
    for row in rows:
        split_rows = conn.execute(
            "SELECT category_id, amount_minor FROM ledger_transaction_splits "
            "WHERE ledger_id = ? AND transaction_id = ? ORDER BY position, split_id",
            (ledger_id, row["transaction_id"]),
        ).fetchall() if expand_categories else []
        parts = split_rows or [{"category_id": row["category_id"], "amount_minor": row["amount_minor"]}]
        merchant = ledger._canonical_merchant(conn, ledger_id, row["merchant"])[1] if row["merchant"] else None
        project_id, project_name = _project_info(conn, ledger_id, row["transaction_id"])
        for part in parts:
            category_id = part["category_id"]
            if selected_category is not None and not _category_matches(conn, ledger_id, category_id, selected_category):
                continue
            style = _expense_style(conn, ledger_id, category_id, row["merchant"], rules) if classify_expense else None
            facts.append({
                "transaction_id": row["transaction_id"],
                "version": row["version"],
                "date_basis_date": row["date_basis_date"],
                "occurred_on": row["occurred_on"],
                "kind": row["kind"],
                "amount_minor": part["amount_minor"],
                "transaction_amount_minor": row["amount_minor"],
                "confirmation_status": row["confirmation_status"],
                "estimated": bool(row["estimated"]),
                "category_id": category_id,
                "category_path": _category_path(conn, ledger_id, category_id),
                "merchant": merchant,
                "project_id": project_id,
                "project_name": project_name,
                "expense_style": style,
            })
    return facts


def _detail_action(
    start_date: str,
    end_date: str,
    group_values: dict,
    filters: dict,
    date_basis: str,
) -> dict:
    return {
        "action": "get_report_drilldown",
        "arguments": {
            "start_date": start_date,
            "end_date": end_date,
            "group_values": group_values,
            "filters": filters,
            "date_basis": date_basis,
        },
    }


def _month_bounds(month: str) -> tuple[str, str]:
    year, month_number = (int(part) for part in month.split("-"))
    return f"{month}-01", f"{month}-{monthrange(year, month_number)[1]:02d}"


def _month_shift(month: str, delta: int) -> str:
    year, month_number = (int(part) for part in month.split("-"))
    index = year * 12 + month_number - 1 + delta
    year, month_index = divmod(index, 12)
    return f"{year:04d}-{month_index + 1:02d}"


def _month_first(value: date) -> date:
    return value.replace(day=1)


def _month_anchor(year: int, month_number: int, start_day: int) -> date:
    return date(year, month_number, min(start_day, monthrange(year, month_number)[1]))


def _financial_period(conn: sqlite3.Connection, ctx: dict, as_of: str) -> dict:
    row = conn.execute(
        "SELECT name, start_day, version FROM insight_financial_cycles WHERE ledger_id = ?",
        (ctx["ledger_id"],),
    ).fetchone()
    name, start_day, version = (row["name"], row["start_day"], row["version"]) if row else ("自然月", 1, 0)
    target = date.fromisoformat(as_of)
    anchor = _month_anchor(target.year, target.month, start_day)
    if target < anchor:
        previous = _month_shift(target.strftime("%Y-%m"), -1)
        year, month_number = (int(part) for part in previous.split("-"))
        anchor = _month_anchor(year, month_number, start_day)
    next_month = _month_shift(anchor.strftime("%Y-%m"), 1)
    year, month_number = (int(part) for part in next_month.split("-"))
    next_anchor = _month_anchor(year, month_number, start_day)
    return {
        "name": name,
        "start_day": start_day,
        "version": version,
        "as_of": as_of,
        "start_date": anchor.isoformat(),
        "end_date": (next_anchor - timedelta(days=1)).isoformat(),
    }


def _upsert_financial_cycle(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_ledger(ctx, write=True)
    name = _text(params.get("name"), "name")
    start_day = params.get("start_day")
    if type(start_day) is not int or not 1 <= start_day <= 31:
        _fail("invalid_input", "start_day must be an integer from 1 to 31")
    now = _now()
    conn.execute(
        "INSERT INTO insight_financial_cycles(ledger_id,name,start_day,version,updated_by,updated_at) "
        "VALUES (?,?,?,1,?,?) ON CONFLICT(ledger_id) DO UPDATE SET name=excluded.name,start_day=excluded.start_day, "
        "version=insight_financial_cycles.version+1,updated_by=excluded.updated_by,updated_at=excluded.updated_at",
        (ctx["ledger_id"], name, start_day, ctx["principal_id"], now),
    )
    current = _financial_period(conn, ctx, ctx["today"])
    return {"cycle": {key: current[key] for key in ("name", "start_day", "version")}, "current_period": current, "ledger": _ledger_info(ctx)}


def _get_financial_cycle(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_ledger(ctx)
    as_of = _date(params.get("as_of", ctx["today"]), "as_of")
    period = _financial_period(conn, ctx, as_of)
    return {"cycle": {key: period[key] for key in ("name", "start_day", "version")}, "period": period, "ledger": _ledger_info(ctx)}


def _get_financial_period_summary(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_ledger(ctx)
    as_of = _date(params.get("as_of", ctx["today"]), "as_of")
    period = _financial_period(conn, ctx, as_of)
    basis = _date_basis(params.get("date_basis", "occurred_on"))
    filters = _filters(params.get("filters", {}))
    summary = ledger._aggregate_period(conn, ctx, period["start_date"], period["end_date"], filters, basis)
    return {"period": period, "summary": summary, "filters": filters, "date_basis": basis, "ledger": _ledger_info(ctx)}


def _previous_calendar_year(value: str) -> str:
    current = date.fromisoformat(value)
    previous_year = current.year - 1
    if previous_year < 1:
        _fail("invalid_input", "A previous calendar year is unavailable for this date")
    return date(previous_year, current.month, min(current.day, monthrange(previous_year, current.month)[1])).isoformat()


def _get_insight_period_comparison(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_ledger(ctx)
    mode = params.get("mode")
    modes = {"previous_equal_length", "previous_calendar_year", "previous_financial_cycle", "explicit"}
    if not isinstance(mode, str) or mode not in modes:
        _fail("invalid_input", "mode must be a supported comparison mode")
    start_date, end_date = _period(params.get("start_date"), params.get("end_date"))
    basis = _date_basis(params.get("date_basis", "occurred_on"))
    filters = _filters(params.get("filters", {}))
    if filters.get("kind") not in (None, "expense"):
        _fail("invalid_input", "Insight period comparisons are limited to expenses")
    filters["kind"] = "expense"

    unavailable = False
    if mode == "explicit":
        comparison_start, comparison_end = _period(
            params.get("comparison_start_date"), params.get("comparison_end_date"),
        )
    elif "comparison_start_date" in params or "comparison_end_date" in params:
        _fail("invalid_input", "Explicit comparison dates are only valid with mode=explicit")
    elif mode == "previous_equal_length":
        current_start = date.fromisoformat(start_date)
        current_end = date.fromisoformat(end_date)
        duration = (current_end - current_start).days + 1
        try:
            comparison_end = (current_start - timedelta(days=1)).isoformat()
            comparison_start = (current_start - timedelta(days=duration)).isoformat()
        except OverflowError:
            _fail("invalid_input", "A previous equal-length period is unavailable for these dates")
    elif mode == "previous_calendar_year":
        comparison_start = _previous_calendar_year(start_date)
        comparison_end = _previous_calendar_year(end_date)
    else:
        current_cycle = _financial_period(conn, ctx, end_date)
        if current_cycle["version"] == 0:
            unavailable = True
            comparison_start = comparison_end = None
        else:
            try:
                previous_as_of = (date.fromisoformat(current_cycle["start_date"]) - timedelta(days=1)).isoformat()
            except OverflowError:
                _fail("invalid_input", "A previous financial cycle is unavailable for this date")
            previous_cycle = _financial_period(conn, ctx, previous_as_of)
            comparison_start, comparison_end = previous_cycle["start_date"], previous_cycle["end_date"]

    if unavailable:
        current = ledger._aggregate_period(conn, ctx, start_date, end_date, filters, basis)
        current_item = {
            "start_date": start_date,
            "end_date": end_date,
            "label": "本期",
            "expense_minor": current["expense_minor"],
            "record_count": current["record_count"],
            "empty": current["empty"],
            "estimated_count": current["estimated_count"],
            "pending_count": current["pending_count"],
            "pending_minor": current["pending_expense_minor"],
            "date_basis": basis,
        }
        return {
            "mode": mode,
            "comparison_available": False,
            "unavailable_reason": "financial_cycle_not_configured",
            "date_basis": basis,
            "periods": [current_item],
            "filters": filters,
            "ledger": _ledger_info(ctx),
        }
    result = ledger.ACTIONS["get_spending_comparison"]["run"](
        conn,
        ctx,
        {
            "periods": [
                {"start_date": comparison_start, "end_date": comparison_end, "label": "对照期"},
                {"start_date": start_date, "end_date": end_date, "label": "本期"},
            ],
            "filters": filters,
            "date_basis": basis,
        },
    )
    return {**result, "mode": mode, "comparison_available": True, "date_basis": basis}


def _validate_report_inputs(ctx: dict, params: dict) -> tuple[str, str, str, dict]:
    _own_ledger(ctx)
    start_date, end_date = _period(params.get("start_date"), params.get("end_date"))
    basis = _date_basis(params.get("date_basis", "occurred_on"))
    filters = _filters(params.get("filters", {}))
    return start_date, end_date, basis, filters


def _canonical_values(fact: dict, dimension: str) -> dict:
    if dimension == "month":
        return {"month": fact["date_basis_date"][:7]}
    if dimension == "merchant":
        return {"merchant": fact["merchant"]}
    if dimension == "category":
        return {"category_id": fact["category_id"], "category_path": fact["category_path"] or "待归类"}
    if dimension == "project":
        return {"project_id": fact["project_id"], "project_name": fact["project_name"]}
    return {"expense_style": fact["expense_style"]}


def _group_key(fact: dict, dimensions: list[str]) -> tuple:
    keys = []
    for dimension in dimensions:
        if dimension == "month":
            keys.append(fact["date_basis_date"][:7])
        elif dimension == "merchant":
            keys.append(fact["merchant"])
        elif dimension == "category":
            keys.append((fact["category_id"], fact["category_path"] or "待归类"))
        elif dimension == "project":
            keys.append((fact["project_id"], fact["project_name"]))
        else:
            keys.append(fact["expense_style"])
    return tuple(keys)


def _group_values(fact: dict, dimensions: list[str]) -> dict:
    result = {}
    for dimension in dimensions:
        result.update(_canonical_values(fact, dimension))
    return result


def _cross_tab_report(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    start_date, end_date, basis, filters = _validate_report_inputs(ctx, params)
    dimensions = params.get("dimensions")
    allowed = {"month", "merchant", "category", "project", "expense_style"}
    if not isinstance(dimensions, list) or not 1 <= len(dimensions) <= 2 or any(item not in allowed for item in dimensions) or len(set(dimensions)) != len(dimensions):
        _fail("invalid_input", "dimensions must contain one or two distinct supported dimensions")
    if "expense_style" in dimensions:
        if filters.get("kind") not in (None, "expense"):
            _fail("invalid_input", "expense_style grouping applies only to expenses")
        filters["kind"] = "expense"
    rows = _transaction_rows(conn, ctx, start_date, end_date, filters, basis)
    facts = _allocation_facts(
        conn, ctx, rows,
        expand_categories="category" in dimensions or "expense_style" in dimensions,
        classify_expense="expense_style" in dimensions,
    )
    groups: dict[tuple, dict] = {}
    for fact in facts:
        key = _group_key(fact, dimensions)
        group = groups.setdefault(key, {
            "dimensions": _group_values(fact, dimensions),
            "income_minor": 0,
            "expense_minor": 0,
            "pending_income_minor": 0,
            "pending_expense_minor": 0,
            "estimated_minor": 0,
            "transaction_ids": set(),
            "pending_ids": set(),
            "estimated_ids": set(),
        })
        if fact["confirmation_status"] == "pending":
            group["pending_" + fact["kind"] + "_minor"] += fact["amount_minor"]
            group["pending_ids"].add(fact["transaction_id"])
        else:
            group[fact["kind"] + "_minor"] += fact["amount_minor"]
        group["transaction_ids"].add(fact["transaction_id"])
        if fact["estimated"]:
            group["estimated_minor"] += fact["amount_minor"]
            group["estimated_ids"].add(fact["transaction_id"])
    items = []
    for key in sorted(groups, key=lambda item: tuple("" if value is None else str(value) for value in item)):
        group = groups[key]
        net = group["income_minor"] - group["expense_minor"]
        group_values = {}
        for dimension in dimensions:
            value = group["dimensions"]
            if dimension == "month":
                group_values["month"] = value["month"]
            elif dimension == "merchant":
                group_values.update(_merchant_group_value(conn, ctx, value["merchant"]))
            elif dimension == "category":
                group_values["category_id"] = value["category_id"]
            elif dimension == "project":
                group_values["project_id"] = value["project_id"]
            else:
                group_values["expense_style"] = value["expense_style"]
        display_dimensions = dict(group["dimensions"])
        if "merchant" in dimensions and ctx.get("source") == "mcp":
            ref = group_values["merchant_ref"]
            display_dimensions["merchant_label"] = f"商家 {ref[:8]}" if ref else "未知商家"
        items.append({
            "dimensions": display_dimensions,
            "income_minor": group["income_minor"],
            "expense_minor": group["expense_minor"],
            "pending_income_minor": group["pending_income_minor"],
            "pending_expense_minor": group["pending_expense_minor"],
            "net_minor": net,
            "record_count": len(group["transaction_ids"]),
            "pending_record_count": len(group["pending_ids"]),
            "estimated_count": len(group["estimated_ids"]),
            "estimated_minor": group["estimated_minor"],
            "empty": False,
            "zero": net == 0,
            "drilldown": _detail_action(start_date, end_date, group_values, filters, basis),
        })
    total_count = len({fact["transaction_id"] for fact in facts})
    return {
        "period": {"start_date": start_date, "end_date": end_date},
        "date_basis": basis,
        "dimensions": dimensions,
        "filters": filters,
        "items": items,
        "record_count": total_count,
        "empty": total_count == 0,
        "ledger": _ledger_info(ctx),
    }


def _safe_detail(fact: dict) -> dict:
    return {
        "transaction_id": fact["transaction_id"],
        "version": fact["version"],
        "date_basis_date": fact["date_basis_date"],
        "occurred_on": fact["occurred_on"],
        "kind": fact["kind"],
        "amount_minor": fact["amount_minor"],
        "transaction_amount_minor": fact["transaction_amount_minor"],
        "confirmation_status": fact["confirmation_status"],
        "estimated": fact["estimated"],
        "category_id": fact["category_id"],
        "category_path": fact["category_path"] or "待归类",
        "project_id": fact["project_id"],
        "project_name": fact["project_name"],
        "expense_style": fact["expense_style"],
    }


def _report_drilldown(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    start_date, end_date, basis, filters = _validate_report_inputs(ctx, params)
    group_values = params.get("group_values")
    allowed = {"month", "merchant", "merchant_ref", "category_id", "project_id", "expense_style"}
    if not isinstance(group_values, dict) or set(group_values) - allowed:
        _fail("invalid_input", "group_values must use only supported dimensions")
    if "merchant" in group_values and "merchant_ref" in group_values:
        _fail("invalid_input", "Use only one merchant drilldown field")
    if "merchant" in group_values and ctx.get("source") != "web":
        _fail("forbidden", "Plain merchant drilldown values are available only to the same-ledger web client")
    if "merchant_ref" in group_values and ctx.get("source") == "web":
        _fail("forbidden", "Web drilldowns must use merchant values from the same-ledger report")
    if "expense_style" in group_values:
        if group_values["expense_style"] not in ("fixed", "flexible", "unclassified"):
            _fail("invalid_input", "expense_style must be fixed, flexible, or unclassified")
        if filters.get("kind") not in (None, "expense"):
            _fail("invalid_input", "expense_style drilldown applies only to expenses")
        filters["kind"] = "expense"
    if "month" in group_values:
        group_values["month"] = _month(group_values["month"], "group_values.month")
    if "expense_style" in group_values and group_values["expense_style"] not in ("fixed", "flexible", "unclassified"):
        _fail("invalid_input", "expense_style must be fixed, flexible, or unclassified")
    for key in ("category_id", "project_id"):
        if key in group_values and group_values[key] is not None:
            group_values[key] = _text(group_values[key], f"group_values.{key}")
    if "merchant" in group_values and group_values["merchant"] is not None:
        group_values["merchant"] = _text(group_values["merchant"], "group_values.merchant")
    if "merchant_ref" in group_values and group_values["merchant_ref"] is not None:
        group_values["merchant_ref"] = _text(group_values["merchant_ref"], "group_values.merchant_ref")
    if "merchant" in group_values:
        _own_ledger(ctx)
        merchant_key = ledger._canonical_merchant(conn, ctx["ledger_id"], group_values["merchant"])[0] if group_values["merchant"] is not None else None
    else:
        merchant_key = _merchant_key_for_ref(conn, ctx, group_values["merchant_ref"]) if group_values.get("merchant_ref") is not None else None
    limit = params.get("limit", 100)
    offset = params.get("offset", 0)
    if type(limit) is not int or not 1 <= limit <= 500 or type(offset) is not int or offset < 0:
        _fail("invalid_input", "limit must be 1 to 500 and offset must be non-negative")
    rows = _transaction_rows(conn, ctx, start_date, end_date, filters, basis)
    facts = _allocation_facts(
        conn, ctx, rows,
        expand_categories="category_id" in group_values or "expense_style" in group_values,
        classify_expense="expense_style" in group_values,
    )
    selected = []
    for fact in facts:
        if "month" in group_values and fact["date_basis_date"][:7] != group_values["month"]:
            continue
        merchant_field = "merchant" if "merchant" in group_values else "merchant_ref"
        if merchant_field in group_values:
            merchant_value = group_values[merchant_field]
            if merchant_value is None:
                if fact["merchant"] is not None:
                    continue
            elif fact["merchant"] is None or ledger._canonical_merchant(conn, ctx["ledger_id"], fact["merchant"])[0] != merchant_key:
                continue
        if "category_id" in group_values:
            if fact["category_id"] != group_values["category_id"]:
                continue
        if "project_id" in group_values and fact["project_id"] != group_values["project_id"]:
            continue
        if "expense_style" in group_values and fact["expense_style"] != group_values["expense_style"]:
            continue
        selected.append(fact)
    selected.sort(key=lambda item: (item["date_basis_date"], item["transaction_id"], item["category_id"] or ""))
    actual_income = sum(item["amount_minor"] for item in selected if item["kind"] == "income" and item["confirmation_status"] == "confirmed")
    actual_expense = sum(item["amount_minor"] for item in selected if item["kind"] == "expense" and item["confirmation_status"] == "confirmed")
    pending_income = sum(item["amount_minor"] for item in selected if item["kind"] == "income" and item["confirmation_status"] == "pending")
    pending_expense = sum(item["amount_minor"] for item in selected if item["kind"] == "expense" and item["confirmation_status"] == "pending")
    return {
        "period": {"start_date": start_date, "end_date": end_date},
        "date_basis": basis,
        "group_values": group_values,
        "filters": filters,
        "income_minor": actual_income,
        "expense_minor": actual_expense,
        "pending_income_minor": pending_income,
        "pending_expense_minor": pending_expense,
        "net_minor": actual_income - actual_expense,
        "zero": bool(selected) and actual_income == actual_expense,
        "record_count": len({item["transaction_id"] for item in selected}),
        "detail_count": len(selected),
        "empty": not selected,
        "items": [_safe_detail(item) for item in selected[offset:offset + limit]],
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < len(selected),
        "next_offset": offset + limit if offset + limit < len(selected) else None,
        "ledger": _ledger_info(ctx),
    }


def _purchase_frequency(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    start_date, end_date, basis, filters = _validate_report_inputs(ctx, params)
    if filters.get("kind") not in (None, "expense"):
        _fail("invalid_input", "Purchase frequency is limited to expenses")
    filters["kind"] = "expense"
    group_by = params.get("group_by")
    if group_by not in ("merchant", "category"):
        _fail("invalid_input", "group_by must be merchant or category")
    limit = params.get("limit", 100)
    if type(limit) is not int or not 1 <= limit <= 500:
        _fail("invalid_input", "limit must be from 1 to 500")
    rows = _transaction_rows(conn, ctx, start_date, end_date, filters, basis)
    facts = _allocation_facts(conn, ctx, rows, expand_categories=group_by == "category")
    groups: dict[object, dict] = {}
    for fact in facts:
        key = fact["merchant"] if group_by == "merchant" else fact["category_id"]
        group = groups.setdefault(key, {
            "merchant": key if group_by == "merchant" else None,
            "category_id": key if group_by == "category" else None,
            "category_path": fact["category_path"] or "待归类" if group_by == "category" else None,
            "confirmed_ids": set(),
            "pending_ids": set(),
            "estimated_ids": set(),
            "estimated_minor": 0,
            "total_minor": 0,
            "pending_minor": 0,
        })
        target_ids = "pending_ids" if fact["confirmation_status"] == "pending" else "confirmed_ids"
        group[target_ids].add(fact["transaction_id"])
        if fact["confirmation_status"] == "pending":
            group["pending_minor"] += fact["amount_minor"]
        else:
            group["total_minor"] += fact["amount_minor"]
        if fact["estimated"]:
            group["estimated_minor"] += fact["amount_minor"]
            group["estimated_ids"].add(fact["transaction_id"])
    items = []
    for key, group in groups.items():
        count = len(group["confirmed_ids"])
        group_values = _merchant_group_value(conn, ctx, key) if group_by == "merchant" else {"category_id": key}
        display = {group_by: key}
        if group_by == "merchant" and ctx.get("source") == "mcp":
            ref = group_values["merchant_ref"]
            display = {"merchant_label": f"商家 {ref[:8]}" if ref else "未知商家"}
        items.append({
            **display,
            "category_path": group["category_path"],
            "transaction_count": count,
            "pending_transaction_count": len(group["pending_ids"]),
            "total_expense_minor": group["total_minor"],
            "average_minor_per_transaction": round(group["total_minor"] / count) if count else None,
            "pending_expense_minor": group["pending_minor"],
            "estimated_count": len(group["estimated_ids"]),
            "estimated_minor": group["estimated_minor"],
            "empty": count == 0 and not group["pending_ids"],
            "zero": count > 0 and group["total_minor"] == 0,
            "drilldown": _detail_action(start_date, end_date, group_values, filters, basis),
        })
    items.sort(key=lambda item: (-item["total_expense_minor"], str(item[group_by] or "")))
    return {
        "period": {"start_date": start_date, "end_date": end_date},
        "date_basis": basis,
        "group_by": group_by,
        "filters": filters,
        "items": items[:limit],
        "count": len(items),
        "empty": not items,
        "ledger": _ledger_info(ctx),
    }


def _category_allocations(conn: sqlite3.Connection, ctx: dict, start_date: str, end_date: str, filters: dict, basis: str) -> list[dict]:
    if filters.get("kind") not in (None, "expense"):
        _fail("invalid_input", "Spending reports are limited to expenses")
    expense_filters = dict(filters)
    expense_filters["kind"] = "expense"
    rows = _transaction_rows(conn, ctx, start_date, end_date, expense_filters, basis)
    return _allocation_facts(conn, ctx, rows, expand_categories=True)


def _spending_variance(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_ledger(ctx)
    current_start, current_end = _period(params.get("current_start_date"), params.get("current_end_date"))
    comparison_start, comparison_end = _period(params.get("comparison_start_date"), params.get("comparison_end_date"))
    basis = _date_basis(params.get("date_basis", "occurred_on"))
    filters = _filters(params.get("filters", {}))
    limit = params.get("limit", 10)
    if type(limit) is not int or not 1 <= limit <= 50:
        _fail("invalid_input", "limit must be from 1 to 50")
    current = _category_allocations(conn, ctx, current_start, current_end, filters, basis)
    comparison = _category_allocations(conn, ctx, comparison_start, comparison_end, filters, basis)
    categories: dict[str | None, dict] = {}
    for period_name, facts in (("current", current), ("comparison", comparison)):
        for fact in facts:
            item = categories.setdefault(fact["category_id"], {
                "category_id": fact["category_id"],
                "category_path": fact["category_path"] or "待归类",
                "current_minor": 0,
                "comparison_minor": 0,
                "current_pending_minor": 0,
                "comparison_pending_minor": 0,
                "current_estimated_minor": 0,
                "comparison_estimated_minor": 0,
                "current_estimated_ids": set(),
                "comparison_estimated_ids": set(),
                "current_pending_ids": set(),
                "comparison_pending_ids": set(),
                "current_facts": [],
                "comparison_facts": [],
                "current_ids": set(),
                "comparison_ids": set(),
            })
            if fact["confirmation_status"] == "pending":
                item[period_name + "_pending_minor"] += fact["amount_minor"]
                item[period_name + "_pending_ids"].add(fact["transaction_id"])
            else:
                item[period_name + "_minor"] += fact["amount_minor"]
            item[period_name + "_facts"].append(fact)
            item[period_name + "_ids"].add(fact["transaction_id"])
            if fact["estimated"]:
                item[period_name + "_estimated_minor"] += fact["amount_minor"]
                item[period_name + "_estimated_ids"].add(fact["transaction_id"])
    items = []
    for category_id, item in categories.items():
        change = item["current_minor"] - item["comparison_minor"]
        if item["comparison_minor"]:
            change_percent = round(change * 100 / item["comparison_minor"], 2)
            comparison_basis = "available"
        elif item["current_minor"]:
            change_percent = None
            comparison_basis = "no_comparison_spend"
        else:
            change_percent = 0
            comparison_basis = "zero_in_both_periods"
        current_details = sorted(item["current_facts"], key=lambda fact: (-fact["amount_minor"], fact["date_basis_date"], fact["transaction_id"]))[:limit]
        comparison_details = sorted(item["comparison_facts"], key=lambda fact: (-fact["amount_minor"], fact["date_basis_date"], fact["transaction_id"]))[:limit]
        group_values = {"category_id": category_id}
        items.append({
            "category_id": category_id,
            "category_path": item["category_path"],
            "current_expense_minor": item["current_minor"],
            "comparison_expense_minor": item["comparison_minor"],
            "change_minor": change,
            "change_percent": change_percent,
            "comparison_basis": comparison_basis,
            "current_pending_minor": item["current_pending_minor"],
            "comparison_pending_minor": item["comparison_pending_minor"],
            "current_estimated_minor": item["current_estimated_minor"],
            "comparison_estimated_minor": item["comparison_estimated_minor"],
            "current_transaction_count": len(item["current_ids"]),
            "comparison_transaction_count": len(item["comparison_ids"]),
            "current_pending_count": len(item["current_pending_ids"]),
            "comparison_pending_count": len(item["comparison_pending_ids"]),
            "current_estimated_count": len(item["current_estimated_ids"]),
            "comparison_estimated_count": len(item["comparison_estimated_ids"]),
            "empty": not item["current_ids"] and not item["comparison_ids"],
            "zero": bool(item["current_ids"] or item["comparison_ids"]) and change == 0,
            "current_details": [_safe_detail(fact) for fact in current_details],
            "comparison_details": [_safe_detail(fact) for fact in comparison_details],
            "current_drilldown": _detail_action(current_start, current_end, group_values, filters, basis),
            "comparison_drilldown": _detail_action(comparison_start, comparison_end, group_values, filters, basis),
        })
    items.sort(key=lambda item: (-abs(item["change_minor"]), item["category_path"]))
    return {
        "current_period": {"start_date": current_start, "end_date": current_end},
        "comparison_period": {"start_date": comparison_start, "end_date": comparison_end},
        "date_basis": basis,
        "filters": filters,
        "items": items,
        "current_expense_minor": sum(item["current_expense_minor"] for item in items),
        "comparison_expense_minor": sum(item["comparison_expense_minor"] for item in items),
        "change_minor": sum(item["change_minor"] for item in items),
        "empty": not items,
        "ledger": _ledger_info(ctx),
    }


def _rolling_spending_trend(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    start_date, end_date = _period(params.get("start_date"), params.get("end_date"))
    basis = _date_basis(params.get("date_basis", "occurred_on"))
    filters = _filters(params.get("filters", {}))
    if filters.get("kind") not in (None, "expense"):
        _fail("invalid_input", "Rolling spending is limited to expenses")
    filters["kind"] = "expense"
    window_days = params.get("window_days", 30)
    if type(window_days) is not int or not 1 <= window_days <= 365:
        _fail("invalid_input", "window_days must be from 1 to 365")
    first_day = date.fromisoformat(start_date)
    last_day = date.fromisoformat(end_date)
    extended_start = (first_day - timedelta(days=window_days - 1)).isoformat()
    rows = _transaction_rows(conn, ctx, extended_start, end_date, filters, basis)
    daily: dict[str, dict] = defaultdict(lambda: {"actual_minor": 0, "pending_minor": 0, "record_count": 0, "pending_count": 0, "estimated_count": 0, "estimated_minor": 0})
    for row in rows:
        item = daily[row["date_basis_date"]]
        item["record_count"] += 1
        if row["confirmation_status"] == "pending":
            item["pending_minor"] += row["amount_minor"]
            item["pending_count"] += 1
        else:
            item["actual_minor"] += row["amount_minor"]
        item["estimated_count"] += int(bool(row["estimated"]))
        if row["estimated"]:
            item["estimated_minor"] += row["amount_minor"]
    clause, values = ledger._filter_sql(conn, ctx, filters, report=True, end_date=end_date, date_basis=basis)
    date_sql = _effective_date_sql(basis)
    earliest_row = conn.execute(
        "SELECT MIN(" + date_sql + ") AS first_date FROM ledger_transactions AS t WHERE " + clause,
        values,
    ).fetchone()
    first_known_date = earliest_row["first_date"]
    series = []
    cursor = first_day
    while cursor <= last_day:
        day = cursor.isoformat()
        window_start = cursor - timedelta(days=window_days - 1)
        observed_start = max(window_start, date.fromisoformat(first_known_date)) if first_known_date else cursor + timedelta(days=1)
        coverage_days = max(0, (cursor - observed_start).days + 1)
        amount = sum(daily[(observed_start + timedelta(days=offset)).isoformat()]["actual_minor"] for offset in range(coverage_days)) if coverage_days else 0
        daily_item = daily.get(day, {"actual_minor": 0, "pending_minor": 0, "record_count": 0, "pending_count": 0, "estimated_count": 0, "estimated_minor": 0})
        series.append({
            "date": day,
            "actual_expense_minor": daily_item["actual_minor"],
            "pending_expense_minor": daily_item["pending_minor"],
            "record_count": daily_item["record_count"],
            "pending_count": daily_item["pending_count"],
            "estimated_count": daily_item["estimated_count"],
            "estimated_minor": daily_item["estimated_minor"],
            "rolling_average_minor": round(amount / coverage_days) if coverage_days else None,
            "window_days": window_days,
            "coverage_days": coverage_days,
            "insufficient_data": coverage_days < window_days,
            "empty": daily_item["record_count"] == 0,
            "zero": daily_item["actual_minor"] == 0,
            "drilldown": _detail_action(day, day, {}, filters, basis),
        })
        cursor += timedelta(days=1)
    return {
        "period": {"start_date": start_date, "end_date": end_date},
        "date_basis": basis,
        "window_days": window_days,
        "coverage_basis": "earliest matching non-voided transaction in the selected ledger and filters",
        "first_known_date": first_known_date,
        "filters": filters,
        "daily": series,
        "empty": not any(item["record_count"] for item in series),
        "ledger": _ledger_info(ctx),
    }


def _pending_expenses_by_category(
    conn: sqlite3.Connection,
    ctx: dict,
    start_date: str,
    end_date: str,
) -> dict[str | None, int]:
    rows = _transaction_rows(conn, ctx, start_date, end_date, {"kind": "expense"}, "occurred_on")
    facts = _allocation_facts(conn, ctx, rows, expand_categories=True)
    totals: dict[str | None, int] = defaultdict(int)
    for fact in facts:
        if fact["confirmation_status"] == "pending":
            totals[fact["category_id"]] += fact["amount_minor"]
    return dict(totals)


def _get_budget_period_progress(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_ledger(ctx)
    as_of = _date(params.get("as_of", ctx["today"]), "as_of")
    month = _month(params.get("month", as_of[:7]))
    start_date, end_date = _month_bounds(month)
    first_day = date.fromisoformat(start_date)
    last_day = date.fromisoformat(end_date)
    total_days = (last_day - first_day).days + 1
    elapsed_days = max(0, min(total_days, (date.fromisoformat(as_of) - first_day).days + 1))

    from . import planning

    budget = planning.ACTIONS["get_monthly_category_budgets"]["run"](conn, ctx, {"month": month})
    pending = _pending_expenses_by_category(conn, ctx, start_date, end_date)
    items = budget["items"]
    for item in items:
        item["pending_expense_minor"] = pending.pop(item.get("category_id"), 0)
    for category_id, amount_minor in pending.items():
        category_path = ledger._category_path(conn, ctx["ledger_id"], category_id) if category_id else None
        items.append({
            "unbudgeted": True,
            "category_id": category_id,
            "category_path": category_path or "待归类",
            "actual_paid_minor": 0,
            "pending_expense_minor": amount_minor,
        })
    return {
        "period": {"start_date": start_date, "end_date": end_date, "as_of": as_of},
        "elapsed_days": elapsed_days,
        "total_days": total_days,
        "progress_ratio": round(elapsed_days / total_days, 4),
        "items": items,
        "ledger": budget["ledger"],
    }


def _annual_spending_calendar(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_ledger(ctx)
    year = params.get("year")
    if type(year) is not int or not 1 <= year <= 9999:
        _fail("invalid_input", "year must be an integer from 1 to 9999")
    basis = _date_basis(params.get("date_basis", "occurred_on"))
    filters = _filters(params.get("filters", {}))
    start_date = date(year, 1, 1)
    end_date = date(year, 12, 31)
    rows = _transaction_rows(conn, ctx, start_date.isoformat(), end_date.isoformat(), filters, basis)
    daily: dict[str, dict] = defaultdict(lambda: {
        "transaction_ids": set(), "pending_ids": set(),
        "actual_expense_minor": 0, "pending_expense_minor": 0,
    })
    if filters.get("category_id"):
        facts = _allocation_facts(
            conn, ctx, rows, expand_categories=True, selected_category=filters["category_id"],
        )
        for fact in facts:
            item = daily[fact["date_basis_date"]]
            item["transaction_ids"].add(fact["transaction_id"])
            if fact["confirmation_status"] == "pending":
                item["pending_ids"].add(fact["transaction_id"])
                if fact["kind"] == "expense":
                    item["pending_expense_minor"] += fact["amount_minor"]
            elif fact["kind"] == "expense":
                item["actual_expense_minor"] += fact["amount_minor"]
    else:
        for row in rows:
            item = daily[row["date_basis_date"]]
            item["transaction_ids"].add(row["transaction_id"])
            if row["confirmation_status"] == "pending":
                item["pending_ids"].add(row["transaction_id"])
                if row["kind"] == "expense":
                    item["pending_expense_minor"] += row["amount_minor"]
            elif row["kind"] == "expense":
                item["actual_expense_minor"] += row["amount_minor"]

    calendar_days = (end_date - start_date).days + 1
    series = []
    for day_offset in range(calendar_days):
        day = (start_date + timedelta(days=day_offset)).isoformat()
        item = daily.get(day, {})
        record_count = len(item.get("transaction_ids", ()))
        pending_count = len(item.get("pending_ids", ()))
        actual_expense_minor = item.get("actual_expense_minor", 0)
        status = (
            "pending" if pending_count else
            "amount" if actual_expense_minor > 0 else
            "recorded_zero" if record_count else
            "empty"
        )
        series.append({
            "date": day,
            "status": status,
            "actual_expense_minor": actual_expense_minor,
            "pending_expense_minor": item.get("pending_expense_minor", 0),
            "record_count": record_count,
            "pending_count": pending_count,
        })
    return {
        "year": year,
        "date_basis": basis,
        "filters": filters,
        "daily": series,
        "empty": not any(item["record_count"] for item in series),
        "ledger": _ledger_info(ctx),
    }


def _expense_rule_key(conn: sqlite3.Connection, ledger_id: str, match_type: str, match_value: object) -> tuple[str, str]:
    value = _text(match_value, "match_value")
    if match_type == "category":
        path = ledger._category_path(conn, ledger_id, value)
        return value, path
    if match_type == "merchant":
        key, canonical_name = ledger._canonical_merchant(conn, ledger_id, value)
        return key, canonical_name
    _fail("invalid_input", "match_type must be category or merchant")


def _upsert_expense_view_rule(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_ledger(ctx, write=True)
    name = _text(params.get("name"), "name")
    match_type = params.get("match_type")
    match_key, match_value = _expense_rule_key(conn, ctx["ledger_id"], match_type, params.get("match_value"))
    classification = params.get("classification")
    if classification not in ("fixed", "flexible"):
        _fail("invalid_input", "classification must be fixed or flexible")
    rule_id = params.get("rule_id")
    if rule_id is not None:
        rule_id = _text(rule_id, "rule_id")
    existing = conn.execute(
        "SELECT * FROM insight_expense_view_rules WHERE ledger_id = ? AND match_type = ? AND match_key = ?",
        (ctx["ledger_id"], match_type, match_key),
    ).fetchone()
    if existing is not None and rule_id is not None and existing["rule_id"] != rule_id:
        _fail("conflict", "A rule already exists for this match", {"rule_id": existing["rule_id"]})
    if rule_id is not None and existing is None:
        existing = conn.execute(
            "SELECT * FROM insight_expense_view_rules WHERE ledger_id = ? AND rule_id = ?",
            (ctx["ledger_id"], rule_id),
        ).fetchone()
        if existing is None:
            _fail("not_found", "Expense view rule not found", {"rule_id": rule_id})
        if (existing["match_type"], existing["match_key"]) != (match_type, match_key):
            conflict = conn.execute(
                "SELECT rule_id FROM insight_expense_view_rules WHERE ledger_id = ? AND match_type = ? AND match_key = ?",
                (ctx["ledger_id"], match_type, match_key),
            ).fetchone()
            if conflict:
                _fail("conflict", "Another rule already uses this match", {"rule_id": conflict["rule_id"]})
    now = _now()
    if existing is None:
        rule_id = rule_id or _new_id()
        conn.execute(
            "INSERT INTO insight_expense_view_rules(ledger_id,rule_id,name,match_type,match_key,match_value,classification,version,created_by,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,1,?,?,?)",
            (ctx["ledger_id"], rule_id, name, match_type, match_key, match_value, classification, ctx["principal_id"], now, now),
        )
    else:
        rule_id = existing["rule_id"]
        expected = params.get("expected_version")
        if expected is not None and (type(expected) is not int or expected != existing["version"]):
            _fail("conflict", "Expense view rule version is stale", {"current_version": existing["version"]})
        conn.execute(
            "UPDATE insight_expense_view_rules SET name=?,match_type=?,match_key=?,match_value=?,classification=?,version=version+1,updated_at=? "
            "WHERE ledger_id=? AND rule_id=?",
            (name, match_type, match_key, match_value, classification, now, ctx["ledger_id"], rule_id),
        )
    row = conn.execute(
        "SELECT rule_id,name,match_type,match_value,classification,version,updated_at FROM insight_expense_view_rules WHERE ledger_id=? AND rule_id=?",
        (ctx["ledger_id"], rule_id),
    ).fetchone()
    return {"rule": dict(row), "ledger": _ledger_info(ctx)}


def _list_expense_view_rules(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_ledger(ctx)
    rows = conn.execute(
        "SELECT rule_id,name,match_type,match_value,classification,version,updated_at FROM insight_expense_view_rules "
        "WHERE ledger_id=? ORDER BY match_type,match_value,rule_id",
        (ctx["ledger_id"],),
    ).fetchall()
    return {"rules": [dict(row) for row in rows], "ledger": _ledger_info(ctx)}


def _expense_style_summary(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    start_date, end_date, basis, filters = _validate_report_inputs(ctx, params)
    if filters.get("kind") not in (None, "expense"):
        _fail("invalid_input", "Expense style is limited to expenses")
    filters["kind"] = "expense"
    rows = _transaction_rows(conn, ctx, start_date, end_date, filters, basis)
    facts = _allocation_facts(conn, ctx, rows, expand_categories=True, classify_expense=True)
    groups: dict[str, dict] = {
        name: {"expense_minor": 0, "pending_minor": 0, "transaction_ids": set(), "pending_ids": set(), "estimated_ids": set(), "estimated_minor": 0}
        for name in ("fixed", "flexible", "unclassified")
    }
    for fact in facts:
        group = groups[fact["expense_style"]]
        if fact["confirmation_status"] == "pending":
            group["pending_minor"] += fact["amount_minor"]
        else:
            group["expense_minor"] += fact["amount_minor"]
        group["transaction_ids"].add(fact["transaction_id"])
        if fact["confirmation_status"] == "pending":
            group["pending_ids"].add(fact["transaction_id"])
        if fact["estimated"]:
            group["estimated_ids"].add(fact["transaction_id"])
            group["estimated_minor"] += fact["amount_minor"]
    items = [
        {
            "classification": classification,
            "expense_minor": group["expense_minor"],
            "pending_expense_minor": group["pending_minor"],
            "transaction_count": len(group["transaction_ids"]),
            "pending_transaction_count": len(group["pending_ids"]),
            "estimated_count": len(group["estimated_ids"]),
            "estimated_minor": group["estimated_minor"],
            "empty": not group["transaction_ids"],
            "zero": bool(group["transaction_ids"]) and group["expense_minor"] == 0,
            "drilldown": _detail_action(start_date, end_date, {"expense_style": classification}, filters, basis),
        }
        for classification, group in groups.items()
    ]
    return {
        "period": {"start_date": start_date, "end_date": end_date},
        "date_basis": basis,
        "filters": filters,
        "items": items,
        "empty": not facts,
        "ledger": _ledger_info(ctx),
    }


def _normalize_assumptions(conn: sqlite3.Connection, ledger_id: str, value: object) -> list[dict]:
    if not isinstance(value, list) or not value:
        _fail("invalid_input", "assumptions must be a non-empty array")
    seen = set()
    normalized = []
    for item in value:
        if not isinstance(item, dict):
            _fail("invalid_input", "Each assumption must be an object")
        category_id = _text(item.get("category_id"), "category_id")
        ledger._category_path(conn, ledger_id, category_id)
        delta_minor = item.get("delta_minor")
        if type(delta_minor) is not int or delta_minor == 0:
            _fail("invalid_input", "delta_minor must be a non-zero integer in fen")
        if category_id in seen:
            _fail("invalid_input", "Each category may have only one scenario adjustment", {"category_id": category_id})
        seen.add(category_id)
        normalized.append({"category_id": category_id, "delta_minor": delta_minor})
    return normalized


def _scenario_result(
    conn: sqlite3.Connection,
    ctx: dict,
    month: str,
    assumptions: list[dict],
    basis: str,
) -> dict:
    target_budget = conn.execute(
        "SELECT category_id, budget_minor, carry_in_minor FROM planning_monthly_category_budgets "
        "WHERE ledger_id = ? AND month = ? ORDER BY category_id",
        (ctx["ledger_id"], month),
    ).fetchall()
    allowances = {row["category_id"]: row["budget_minor"] + row["carry_in_minor"] for row in target_budget}
    total_allowance = sum(allowances.values())
    target_start, _ = _month_bounds(month)
    baseline_end = (date.fromisoformat(target_start) - timedelta(days=1)).isoformat()
    baseline_end_month = baseline_end[:7]
    baseline_start = _month_bounds(_month_shift(baseline_end_month, -2))[0]
    base_filters = {"kind": "expense"}
    rows = _transaction_rows(conn, ctx, baseline_start, baseline_end, base_filters, basis)
    facts = _allocation_facts(conn, ctx, rows, expand_categories=True)
    sums: dict[str | None, int] = defaultdict(int)
    pending_sums: dict[str | None, int] = defaultdict(int)
    estimated_sums: dict[str | None, int] = defaultdict(int)
    pending_ids: dict[str | None, set[str]] = defaultdict(set)
    estimated_ids: dict[str | None, set[str]] = defaultdict(set)
    for fact in facts:
        if fact["confirmation_status"] == "confirmed":
            sums[fact["category_id"]] += fact["amount_minor"]
        else:
            pending_sums[fact["category_id"]] += fact["amount_minor"]
            pending_ids[fact["category_id"]].add(fact["transaction_id"])
        if fact["estimated"]:
            estimated_sums[fact["category_id"]] += fact["amount_minor"]
            estimated_ids[fact["category_id"]].add(fact["transaction_id"])
    baseline_minor = {category_id: round(amount / 3) for category_id, amount in sums.items()}
    baseline_pending_minor = {category_id: round(amount / 3) for category_id, amount in pending_sums.items()}
    baseline_estimated_minor = {category_id: round(amount / 3) for category_id, amount in estimated_sums.items()}
    adjusted = dict(baseline_minor)
    unapplied_reduction = {}
    for item in assumptions:
        category_id = item["category_id"]
        original = adjusted.get(category_id, 0)
        requested = item["delta_minor"]
        adjusted[category_id] = max(0, original + requested)
        if requested < 0 and original + requested < 0:
            unapplied_reduction[category_id] = -(original + requested)
    baseline_spend = sum(baseline_minor.values())
    scenario_spend = sum(adjusted.values())
    first_known = min((fact["date_basis_date"] for fact in facts), default=None)
    start_date = date.fromisoformat(baseline_start)
    end_date = date.fromisoformat(baseline_end)
    baseline_full = first_known is not None and date.fromisoformat(first_known) <= start_date
    category_rows = []
    keys = set(baseline_minor) | set(baseline_pending_minor) | set(baseline_estimated_minor) | {item["category_id"] for item in assumptions} | set(allowances)
    assumption_map = {item["category_id"]: item["delta_minor"] for item in assumptions}
    for category_id in sorted(keys, key=lambda value: value or ""):
        category_rows.append({
            "category_id": category_id,
            "category_path": ledger._category_path(conn, ctx["ledger_id"], category_id) if category_id else "待归类",
            "budget_allowance_minor": allowances.get(category_id, 0),
            "baseline_average_monthly_spend_minor": baseline_minor.get(category_id, 0),
            "baseline_average_monthly_pending_minor": baseline_pending_minor.get(category_id, 0),
            "baseline_average_monthly_estimated_minor": baseline_estimated_minor.get(category_id, 0),
            "baseline_pending_transaction_count": len(pending_ids.get(category_id, set())),
            "baseline_estimated_transaction_count": len(estimated_ids.get(category_id, set())),
            "assumed_delta_minor": assumption_map.get(category_id, 0),
            "scenario_spend_minor": adjusted.get(category_id, baseline_minor.get(category_id, 0)),
            "baseline_drilldown": _detail_action(
                baseline_start, baseline_end, {"category_id": category_id}, base_filters, basis,
            ),
        })
    return {
        "month": month,
        "date_basis": basis,
        "budget_allowance_minor": total_allowance,
        "budget_defined": bool(target_budget),
        "baseline_period": {"start_date": baseline_start, "end_date": baseline_end, "months": 3},
        "baseline_method": "Average confirmed spending for each category over the prior three complete calendar months; pending and estimated amounts are surfaced separately, and months without spending count as zero.",
        "baseline_data_start": first_known,
        "baseline_insufficient_data": not baseline_full,
        "baseline_projected_spend_minor": baseline_spend,
        "baseline_remaining_minor": total_allowance - baseline_spend if target_budget else None,
        "empty": not facts,
        "zero": bool(facts) and baseline_spend == 0,
        "baseline_average_monthly_pending_minor": sum(baseline_pending_minor.values()),
        "baseline_average_monthly_estimated_minor": sum(baseline_estimated_minor.values()),
        "baseline_pending_transaction_count": len({fact["transaction_id"] for fact in facts if fact["confirmation_status"] == "pending"}),
        "baseline_estimated_transaction_count": len({fact["transaction_id"] for fact in facts if fact["estimated"]}),
        "scenario_projected_spend_minor": scenario_spend,
        "scenario_remaining_minor": total_allowance - scenario_spend if target_budget else None,
        "unapplied_reduction_minor_by_category": unapplied_reduction,
        "categories": category_rows,
        "independent_draft": True,
        "ledger": _ledger_info(ctx),
    }


def _scenario_payload(conn: sqlite3.Connection, ctx: dict, params: dict) -> tuple[str, list[dict], str, str, dict]:
    month = _month(params.get("month"))
    assumptions = _normalize_assumptions(conn, ctx["ledger_id"], params.get("assumptions"))
    basis = _date_basis(params.get("date_basis", "occurred_on"))
    name = _text(params.get("name", "情景试算"), "name")
    return month, assumptions, basis, name, _scenario_result(conn, ctx, month, assumptions, basis)


def _create_budget_scenario_draft(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_ledger(ctx, write=True)
    month, assumptions, basis, name, result = _scenario_payload(conn, ctx, params)
    draft_id = _new_id()
    now = _now()
    conn.execute(
        "INSERT INTO insight_budget_scenario_drafts(ledger_id,draft_id,name,month,assumptions_json,date_basis,result_json,version,created_by,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,1,?,?,?)",
        (ctx["ledger_id"], draft_id, name, month, json.dumps(assumptions, ensure_ascii=False, sort_keys=True), basis,
         json.dumps(result, ensure_ascii=False, sort_keys=True), ctx["principal_id"], now, now),
    )
    return {"draft_id": draft_id, "name": name, "version": 1, "result": result, "ledger": _ledger_info(ctx)}


def _get_budget_scenario_draft(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_ledger(ctx)
    draft_id = _text(params.get("draft_id"), "draft_id")
    row = conn.execute(
        "SELECT * FROM insight_budget_scenario_drafts WHERE ledger_id = ? AND draft_id = ?",
        (ctx["ledger_id"], draft_id),
    ).fetchone()
    if row is None:
        _fail("not_found", "Budget scenario draft not found", {"draft_id": draft_id})
    return {
        "draft_id": draft_id,
        "name": row["name"],
        "month": row["month"],
        "assumptions": json.loads(row["assumptions_json"]),
        "date_basis": row["date_basis"],
        "version": row["version"],
        "result": json.loads(row["result_json"]),
        "updated_at": row["updated_at"],
        "ledger": _ledger_info(ctx),
    }


def _list_budget_scenario_drafts(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_ledger(ctx)
    rows = conn.execute(
        "SELECT draft_id,name,month,version,updated_at,result_json FROM insight_budget_scenario_drafts "
        "WHERE ledger_id = ? ORDER BY updated_at DESC,draft_id",
        (ctx["ledger_id"],),
    ).fetchall()
    drafts = []
    for row in rows:
        result = json.loads(row["result_json"])
        drafts.append({
            "draft_id": row["draft_id"],
            "name": row["name"],
            "month": row["month"],
            "version": row["version"],
            "updated_at": row["updated_at"],
            "baseline_remaining_minor": result["baseline_remaining_minor"],
            "scenario_remaining_minor": result["scenario_remaining_minor"],
        })
    return {"drafts": drafts, "ledger": _ledger_info(ctx)}


def _update_budget_scenario_draft(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_ledger(ctx, write=True)
    draft_id = _text(params.get("draft_id"), "draft_id")
    row = conn.execute(
        "SELECT * FROM insight_budget_scenario_drafts WHERE ledger_id = ? AND draft_id = ?",
        (ctx["ledger_id"], draft_id),
    ).fetchone()
    if row is None:
        _fail("not_found", "Budget scenario draft not found", {"draft_id": draft_id})
    expected = params.get("expected_version")
    if type(expected) is not int or expected != row["version"]:
        _fail("conflict", "Budget scenario draft version is stale", {"current_version": row["version"]})
    name = _text(params.get("name", row["name"]), "name")
    month, assumptions, basis, _, result = _scenario_payload(conn, ctx, {
        "name": name,
        "month": params.get("month"),
        "assumptions": params.get("assumptions"),
        "date_basis": params.get("date_basis", row["date_basis"]),
    })
    now = _now()
    conn.execute(
        "UPDATE insight_budget_scenario_drafts SET name=?,month=?,assumptions_json=?,date_basis=?,result_json=?,version=version+1,updated_at=? "
        "WHERE ledger_id=? AND draft_id=?",
        (name, month, json.dumps(assumptions, ensure_ascii=False, sort_keys=True), basis,
         json.dumps(result, ensure_ascii=False, sort_keys=True), now, ctx["ledger_id"], draft_id),
    )
    return {"draft_id": draft_id, "name": name, "month": month, "version": row["version"] + 1, "result": result, "ledger": _ledger_info(ctx)}


_METRIC_TYPES = {"net_cash_flow", "expense_category", "saving_goal_progress", "expense_style"}
_CHART_TYPES = {"table", "bar", "line", "doughnut"}


def _normalize_metrics(conn: sqlite3.Connection, ctx: dict, value: object) -> list[dict]:
    if not isinstance(value, list) or not 1 <= len(value) <= 20:
        _fail("invalid_input", "metrics must contain 1 to 20 items")
    result = []
    seen = set()
    from . import planning

    for item in value:
        if not isinstance(item, dict):
            _fail("invalid_input", "Each report metric must be an object")
        metric_id = _text(item.get("id"), "metrics.id")
        metric_type = item.get("type")
        if metric_type not in _METRIC_TYPES:
            _fail("invalid_input", "Metric type must be net_cash_flow, expense_category, saving_goal_progress, or expense_style")
        if metric_id in seen:
            _fail("invalid_input", "Metric IDs must be unique", {"id": metric_id})
        seen.add(metric_id)
        chart = item.get("chart", "table")
        if chart not in _CHART_TYPES:
            _fail("invalid_input", "chart must be table, bar, line, or doughnut")
        normalized = {"id": metric_id, "type": metric_type, "label": _text(item.get("label", metric_id), "metrics.label"), "chart": chart}
        if metric_type == "expense_category":
            category_id = _text(item.get("category_id"), "metrics.category_id")
            ledger._category_path(conn, ctx["ledger_id"], category_id)
            normalized["category_id"] = category_id
        elif metric_type == "saving_goal_progress":
            goal_id = _text(item.get("goal_id"), "metrics.goal_id")
            planning._goal(conn, ctx["ledger_id"], goal_id)
            normalized["goal_id"] = goal_id
        if metric_type not in ("expense_category",) and item.get("category_id") is not None:
            _fail("invalid_input", "category_id is only valid for expense_category metrics", {"id": metric_id})
        if metric_type != "saving_goal_progress" and item.get("goal_id") is not None:
            _fail("invalid_input", "goal_id is only valid for saving_goal_progress metrics", {"id": metric_id})
        result.append(normalized)
    return result


def _normalize_template_config(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    basis = _date_basis(params.get("date_basis", "occurred_on"))
    metrics = _normalize_metrics(conn, ctx, params.get("metrics"))
    return {"date_basis": basis, "metrics": metrics}


def _normalize_insight_view_config(conn: sqlite3.Connection, ctx: dict, value: object) -> dict:
    required = {"period", "date_basis", "category_id", "chart", "aggregation"}
    allowed = required | {"comparison"}
    if not isinstance(value, dict) or not required.issubset(value) or set(value) - allowed:
        _fail("invalid_input", "config must contain only the supported insight view fields")

    period = value["period"]
    if not isinstance(period, dict):
        _fail("invalid_input", "period must be a preset or a fixed date range")
    if set(period) == {"preset"}:
        preset = period["preset"]
        if not isinstance(preset, str) or preset not in {"this_month", "last_month", "last_7_days", "last_30_days", "this_year"}:
            _fail("invalid_input", "period.preset is not supported")
        normalized_period = {"preset": preset}
    elif set(period) == {"start_date", "end_date"}:
        start_date, end_date = _period(period["start_date"], period["end_date"])
        normalized_period = {"start_date": start_date, "end_date": end_date}
    else:
        _fail("invalid_input", "period must contain either preset or start_date and end_date")

    basis = _date_basis(value["date_basis"])
    category_id = value["category_id"]
    if category_id is not None:
        category_id = _text(category_id, "category_id")
        ledger._category_path(conn, ctx["ledger_id"], category_id)
    chart = value["chart"]
    if not isinstance(chart, str) or chart not in {"pie", "bar", "table"}:
        _fail("invalid_input", "chart must be pie, bar, or table")
    aggregation = value["aggregation"]
    if not isinstance(aggregation, str) or aggregation not in {"day", "week", "month"}:
        _fail("invalid_input", "aggregation must be day, week, or month")

    config = {
        "period": normalized_period,
        "date_basis": basis,
        "category_id": category_id,
        "chart": chart,
        "aggregation": aggregation,
    }
    if "comparison" in value:
        comparison = value["comparison"]
        if not isinstance(comparison, dict):
            _fail("invalid_input", "comparison must be a supported comparison mode")
        mode = comparison.get("mode")
        if isinstance(mode, str) and mode in {"previous_equal_length", "previous_calendar_year", "previous_financial_cycle"} and set(comparison) == {"mode"}:
            config["comparison"] = {"mode": mode}
        elif mode == "explicit" and set(comparison) == {"mode", "start_date", "end_date"}:
            comparison_start, comparison_end = _period(comparison["start_date"], comparison["end_date"])
            config["comparison"] = {"mode": mode, "start_date": comparison_start, "end_date": comparison_end}
        else:
            _fail("invalid_input", "comparison must contain a supported mode and only its required fields")
    return config


def _insight_view_payload(row: sqlite3.Row) -> dict:
    return {
        "view_id": row["view_id"],
        "name": row["name"],
        "config": json.loads(row["config_json"]),
        "version": row["version"],
        "updated_at": row["updated_at"],
    }


def _upsert_insight_view(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_ledger(ctx, write=True)
    name = _text(params.get("name"), "name")
    config = _normalize_insight_view_config(conn, ctx, params.get("config"))
    view_id = params.get("view_id")
    expected_version = params.get("expected_version")
    if view_id is None:
        if expected_version is not None:
            _fail("invalid_input", "expected_version requires an existing view_id")
        conflict = conn.execute(
            "SELECT view_id FROM insight_saved_views WHERE ledger_id = ? AND name = ?",
            (ctx["ledger_id"], name),
        ).fetchone()
        if conflict:
            _fail("conflict", "An insight view already uses this name", {"view_id": conflict["view_id"]})
        view_id = _new_id()
        version = 1
        now = _now()
        conn.execute(
            "INSERT INTO insight_saved_views(ledger_id,view_id,name,config_json,version,created_by,created_at,updated_at) "
            "VALUES (?,?,?,?,1,?,?,?)",
            (ctx["ledger_id"], view_id, name, json.dumps(config, ensure_ascii=False, sort_keys=True), ctx["principal_id"], now, now),
        )
    else:
        view_id = _text(view_id, "view_id")
        if type(expected_version) is not int or expected_version < 1:
            _fail("invalid_input", "expected_version is required to update an insight view")
        row = conn.execute(
            "SELECT version FROM insight_saved_views WHERE ledger_id = ? AND view_id = ?",
            (ctx["ledger_id"], view_id),
        ).fetchone()
        if row is None:
            _fail("not_found", "Insight view not found", {"view_id": view_id})
        if row["version"] != expected_version:
            _fail("conflict", "Insight view version is stale", {"current_version": row["version"]})
        conflict = conn.execute(
            "SELECT view_id FROM insight_saved_views WHERE ledger_id = ? AND name = ? AND view_id <> ?",
            (ctx["ledger_id"], name, view_id),
        ).fetchone()
        if conflict:
            _fail("conflict", "An insight view already uses this name", {"view_id": conflict["view_id"]})
        now = _now()
        cursor = conn.execute(
            "UPDATE insight_saved_views SET name = ?, config_json = ?, version = version + 1, updated_at = ? "
            "WHERE ledger_id = ? AND view_id = ? AND version = ?",
            (name, json.dumps(config, ensure_ascii=False, sort_keys=True), now, ctx["ledger_id"], view_id, expected_version),
        )
        if cursor.rowcount != 1:
            _fail("conflict", "Insight view changed before the update could be applied")
        version = expected_version + 1
    return {
        "view": {"view_id": view_id, "name": name, "config": config, "version": version, "updated_at": now},
        "ledger": _ledger_info(ctx),
    }


def _list_insight_views(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_ledger(ctx)
    rows = conn.execute(
        "SELECT view_id,name,config_json,version,updated_at FROM insight_saved_views "
        "WHERE ledger_id = ? ORDER BY name,view_id",
        (ctx["ledger_id"],),
    ).fetchall()
    return {"views": [_insight_view_payload(row) for row in rows], "ledger": _ledger_info(ctx)}


def _delete_insight_view(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_ledger(ctx, write=True)
    view_id = _text(params.get("view_id"), "view_id")
    expected_version = params.get("expected_version")
    if type(expected_version) is not int or expected_version < 1:
        _fail("invalid_input", "expected_version is required to delete an insight view")
    row = conn.execute(
        "SELECT version FROM insight_saved_views WHERE ledger_id = ? AND view_id = ?",
        (ctx["ledger_id"], view_id),
    ).fetchone()
    if row is None:
        _fail("not_found", "Insight view not found", {"view_id": view_id})
    if row["version"] != expected_version:
        _fail("conflict", "Insight view version is stale", {"current_version": row["version"]})
    cursor = conn.execute(
        "DELETE FROM insight_saved_views WHERE ledger_id = ? AND view_id = ? AND version = ?",
        (ctx["ledger_id"], view_id, expected_version),
    )
    if cursor.rowcount != 1:
        _fail("conflict", "Insight view changed before it could be deleted")
    return {"view_id": view_id, "deleted": True, "ledger": _ledger_info(ctx)}


def _template(conn: sqlite3.Connection, ledger_id: str, template_id: object) -> sqlite3.Row:
    template_id = _text(template_id, "template_id")
    row = conn.execute(
        "SELECT * FROM insight_monthly_report_templates WHERE ledger_id = ? AND template_id = ?",
        (ledger_id, template_id),
    ).fetchone()
    if row is None:
        _fail("not_found", "Monthly report template not found", {"template_id": template_id})
    return row


def _source_rows(conn: sqlite3.Connection, ctx: dict, start_date: str, end_date: str, filters: dict, basis: str) -> list[dict]:
    rows = _transaction_rows(conn, ctx, start_date, end_date, filters, basis)
    facts = _allocation_facts(conn, ctx, rows, expand_categories=True, classify_expense=True)
    return [_safe_detail(fact) for fact in facts]


def _metric_run(
    conn: sqlite3.Connection,
    ctx: dict,
    metric: dict,
    start_date: str,
    end_date: str,
    basis: str,
) -> tuple[dict, dict]:
    metric_id = metric["id"]
    metric_type = metric["type"]
    label = metric["label"]
    chart = metric["chart"]
    if metric_type == "net_cash_flow":
        cashflow = ledger.ACTIONS["get_cashflow_summary"]["run"](
            conn, ctx, {"start_date": start_date, "end_date": end_date, "date_basis": basis, "filters": {}}
        )
        base_summary = ledger._aggregate_period(conn, ctx, start_date, end_date, {}, basis)
        value = {key: cashflow.get(key) for key in (
            "income_minor", "expense_minor", "refund_minor", "reimbursement_receipt_minor",
            "adjustment_income_minor", "adjustment_expense_minor", "cashflow_net_minor",
            "pending_count", "pending_expense_minor",
        )}
        value["estimated_count"] = base_summary["estimated_count"]
        value["estimated_minor"] = base_summary["estimated_minor"]
        value["record_count"] = base_summary["record_count"]
        value["pending_income_minor"] = base_summary.get("pending_income_minor", 0)
        sources = {
            "transactions": _source_rows(conn, ctx, start_date, end_date, {}, basis),
            "adjustments": cashflow.get("adjustments", []),
        }
        result = {
            "id": metric_id, "label": label, "type": metric_type, "chart": chart,
            "value": value, "empty": base_summary["empty"],
            "drilldown": {"action": "get_cashflow_summary", "arguments": {"start_date": start_date, "end_date": end_date, "date_basis": basis}},
        }
        return result, sources
    if metric_type == "expense_category":
        selected_category = metric["category_id"]
        facts = _category_allocations(conn, ctx, start_date, end_date, {"kind": "expense"}, basis)
        facts = [fact for fact in facts if _category_matches(conn, ctx["ledger_id"], fact["category_id"], selected_category)]
        groups: dict[str | None, dict] = {}
        for fact in facts:
            key = fact["category_id"]
            group = groups.setdefault(key, {"category_id": key, "category_path": fact["category_path"] or "待归类", "expense_minor": 0, "pending_expense_minor": 0, "ids": set(), "pending_ids": set(), "estimated_ids": set(), "estimated_minor": 0})
            target = "pending_expense_minor" if fact["confirmation_status"] == "pending" else "expense_minor"
            group[target] += fact["amount_minor"]
            group["ids"].add(fact["transaction_id"])
            if fact["confirmation_status"] == "pending":
                group["pending_ids"].add(fact["transaction_id"])
            if fact["estimated"]:
                group["estimated_ids"].add(fact["transaction_id"])
                group["estimated_minor"] += fact["amount_minor"]
        total = sum(item["expense_minor"] for item in groups.values())
        items = []
        for group in sorted(groups.values(), key=lambda item: (item["category_path"], item["category_id"] or "")):
            items.append({
                "category_id": group["category_id"], "category_path": group["category_path"],
                "expense_minor": group["expense_minor"], "pending_expense_minor": group["pending_expense_minor"],
                "transaction_count": len(group["ids"]), "estimated_count": len(group["estimated_ids"]),
                "pending_transaction_count": len(group["pending_ids"]),
                "estimated_minor": group["estimated_minor"],
                "empty": not group["ids"], "zero": bool(group["ids"]) and group["expense_minor"] == 0,
                "drilldown": _detail_action(start_date, end_date, {"category_id": group["category_id"]}, {"kind": "expense"}, basis),
            })
        value = {
            "category_id": selected_category,
            "category_path": ledger._category_path(conn, ctx["ledger_id"], selected_category),
            "expense_minor": total,
            "pending_expense_minor": sum(item["pending_expense_minor"] for item in groups.values()),
            "estimated_minor": sum(item["estimated_minor"] for item in groups.values()),
            "items": items,
        }
        return {
            "id": metric_id, "label": label, "type": metric_type, "chart": chart,
            "value": value, "empty": not facts,
            "drilldown": _detail_action(start_date, end_date, {}, {"kind": "expense", "category_id": selected_category}, basis),
        }, {"transactions": [_safe_detail(fact) for fact in facts]}
    if metric_type == "expense_style":
        summary = _expense_style_summary(conn, ctx, {"start_date": start_date, "end_date": end_date, "date_basis": basis})
        rows = _transaction_rows(conn, ctx, start_date, end_date, {"kind": "expense"}, basis)
        facts = _allocation_facts(conn, ctx, rows, expand_categories=True, classify_expense=True)
        value = {"items": summary["items"]}
        return {
            "id": metric_id, "label": label, "type": metric_type, "chart": chart,
            "value": value, "empty": summary["empty"],
            "drilldown": _detail_action(start_date, end_date, {}, {"kind": "expense"}, basis),
        }, {"transactions": [_safe_detail(fact) for fact in facts]}
    from . import planning

    goal = planning._goal(conn, ctx["ledger_id"], metric["goal_id"])
    progress = planning._goal_progress(conn, ctx, goal, end_date, 3)
    changes = conn.execute(
        "SELECT entry_id,amount_minor,occurred_on,source_type FROM planning_goal_changes "
        "WHERE ledger_id = ? AND goal_id = ? AND occurred_on <= ? ORDER BY occurred_on,entry_id",
        (ctx["ledger_id"], goal["goal_id"], end_date),
    ).fetchall()
    public_goal = {key: progress[key] for key in (
        "goal_id", "name", "status", "target_minor", "current_minor", "gap_minor", "start_date", "due_date",
        "months_remaining", "required_monthly_increase_minor", "estimated_completion_date",
    )}
    public_goal["average_monthly_increase_minor"] = progress["pace"]["average_monthly_increase_minor"]
    return {
        "id": metric_id, "label": label, "type": metric_type, "chart": chart,
        "value": public_goal, "empty": False,
        "drilldown": {"action": "get_saving_goal_progress", "arguments": {"goal_id": goal["goal_id"], "as_of": end_date}},
    }, {
        "goal_state": {"goal_id": goal["goal_id"], "name": goal["name"], "target_minor": goal["target_minor"], "due_date": goal["due_date"], "start_date": goal["start_date"]},
        "goal_changes": [dict(row) for row in changes],
    }


def _run_monthly_report_config(
    conn: sqlite3.Connection,
    ctx: dict,
    template_id: str,
    template_name: str,
    config: dict,
    start_date: str,
    end_date: str,
    template_version: int,
) -> tuple[dict, dict]:
    result_metrics = []
    source_facts = {}
    for metric in config["metrics"]:
        result, sources = _metric_run(conn, ctx, metric, start_date, end_date, config["date_basis"])
        result_metrics.append(result)
        source_facts[metric["id"]] = sources
    return {
        "template_id": template_id,
        "template_name": template_name,
        "template_version": template_version,
        "period": {"start_date": start_date, "end_date": end_date},
        "date_basis": config["date_basis"],
        "metrics": result_metrics,
        "empty": all(item["empty"] for item in result_metrics),
        "ledger": _ledger_info(ctx),
    }, source_facts


def _upsert_monthly_report_template(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_ledger(ctx, write=True)
    template_id = params.get("template_id")
    if template_id is not None:
        template_id = _text(template_id, "template_id")
    name = _text(params.get("name"), "name")
    config = _normalize_template_config(conn, ctx, params)
    now = _now()
    row = _template(conn, ctx["ledger_id"], template_id) if template_id else None
    if row is None:
        name_conflict = conn.execute(
            "SELECT template_id FROM insight_monthly_report_templates WHERE ledger_id = ? AND name = ?",
            (ctx["ledger_id"], name),
        ).fetchone()
        if name_conflict:
            _fail("conflict", "A monthly report template already uses this name", {"template_id": name_conflict["template_id"]})
        template_id = template_id or _new_id()
        conn.execute(
            "INSERT INTO insight_monthly_report_templates(ledger_id,template_id,name,config_json,version,created_by,created_at,updated_at) "
            "VALUES (?,?,?, ?,1,?,?,?)",
            (ctx["ledger_id"], template_id, name, json.dumps(config, ensure_ascii=False, sort_keys=True), ctx["principal_id"], now, now),
        )
        version = 1
    else:
        expected = params.get("expected_version")
        if expected is not None and (type(expected) is not int or expected != row["version"]):
            _fail("conflict", "Monthly report template version is stale", {"current_version": row["version"]})
        name_conflict = conn.execute(
            "SELECT template_id FROM insight_monthly_report_templates WHERE ledger_id = ? AND name = ? AND template_id <> ?",
            (ctx["ledger_id"], name, template_id),
        ).fetchone()
        if name_conflict:
            _fail("conflict", "A monthly report template already uses this name", {"template_id": name_conflict["template_id"]})
        conn.execute(
            "UPDATE insight_monthly_report_templates SET name=?,config_json=?,version=version+1,updated_at=? WHERE ledger_id=? AND template_id=?",
            (name, json.dumps(config, ensure_ascii=False, sort_keys=True), now, ctx["ledger_id"], template_id),
        )
        version = row["version"] + 1
    return {"template_id": template_id, "name": name, "config": config, "version": version, "ledger": _ledger_info(ctx)}


def _list_monthly_report_templates(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_ledger(ctx)
    rows = conn.execute(
        "SELECT template_id,name,config_json,version,updated_at FROM insight_monthly_report_templates "
        "WHERE ledger_id=? ORDER BY name,template_id",
        (ctx["ledger_id"],),
    ).fetchall()
    return {"templates": [{
        "template_id": row["template_id"], "name": row["name"], "config": json.loads(row["config_json"]),
        "version": row["version"], "updated_at": row["updated_at"],
    } for row in rows], "ledger": _ledger_info(ctx)}


def _monthly_report(conn: sqlite3.Connection, ctx: dict, params: dict) -> tuple[dict, dict, sqlite3.Row, str, str]:
    _own_ledger(ctx)
    template = _template(conn, ctx["ledger_id"], params.get("template_id"))
    start_date, end_date = _period(params.get("start_date"), params.get("end_date"))
    config = json.loads(template["config_json"])
    result, sources = _run_monthly_report_config(
        conn, ctx, template["template_id"], template["name"], config,
        start_date, end_date, template["version"],
    )
    return result, sources, template, start_date, end_date


def _run_monthly_report(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    result, _, _, _, _ = _monthly_report(conn, ctx, params)
    return result


def _json_hash(value: object) -> tuple[str, str]:
    packed = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return packed, hashlib.sha256(packed.encode("utf-8")).hexdigest()


def _save_monthly_report_snapshot(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_ledger(ctx, write=True)
    result, sources, template, start_date, end_date = _monthly_report(conn, ctx, params)
    result_json, result_hash = _json_hash(result)
    source_json, source_hash = _json_hash(sources)
    snapshot_id = _new_id()
    now = _now()
    conn.execute(
        "INSERT INTO insight_report_snapshots(ledger_id,snapshot_id,template_id,template_name,template_config_json,start_date,end_date,date_basis,result_json,source_facts_json,result_sha256,source_sha256,created_by,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (ctx["ledger_id"], snapshot_id, template["template_id"], template["name"], template["config_json"], start_date, end_date,
         json.loads(template["config_json"])["date_basis"], result_json, source_json, result_hash, source_hash, ctx["principal_id"], now),
    )
    return {
        "snapshot_id": snapshot_id,
        "template_id": template["template_id"],
        "template_name": template["name"],
        "period": {"start_date": start_date, "end_date": end_date},
        "result_sha256": result_hash,
        "source_sha256": source_hash,
        "result": result,
        "created_at": now,
        "ledger": _ledger_info(ctx),
    }


def _list_report_snapshots(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_ledger(ctx)
    month = _month(params["month"]) if params.get("month") is not None else None
    start_date = params.get("start_date")
    end_date = params.get("end_date")
    if (start_date is None) != (end_date is None):
        _fail("invalid_input", "start_date and end_date must be supplied together")
    if month is not None and start_date is not None:
        _fail("invalid_input", "Use either month or an explicit period filter")
    if start_date is not None:
        start_date, end_date = _period(start_date, end_date)
    template_id = _text(params["template_id"], "template_id") if params.get("template_id") is not None else None
    limit = params.get("limit", 50)
    if type(limit) is not int or not 1 <= limit <= 200:
        _fail("invalid_input", "limit must be from 1 to 200")
    query = (
        "SELECT snapshot_id,template_id,template_name,start_date,end_date,date_basis,result_json,created_at "
        "FROM insight_report_snapshots WHERE ledger_id = ?"
    )
    values: list[object] = [ctx["ledger_id"]]
    if month is not None:
        month_start, month_end = _month_bounds(month)
        query += " AND end_date >= ? AND start_date <= ?"
        values.extend((month_start, month_end))
    elif start_date is not None:
        query += " AND end_date >= ? AND start_date <= ?"
        values.extend((start_date, end_date))
    if template_id is not None:
        query += " AND template_id = ?"
        values.append(template_id)
    query += " ORDER BY created_at DESC,snapshot_id DESC LIMIT ?"
    values.append(limit)
    rows = conn.execute(query, values).fetchall()
    snapshots = []
    for row in rows:
        result = json.loads(row["result_json"])
        snapshots.append({
            "snapshot_id": row["snapshot_id"],
            "template_id": row["template_id"],
            "template_name": row["template_name"],
            "period": {"start_date": row["start_date"], "end_date": row["end_date"]},
            "date_basis": row["date_basis"],
            "created_at": row["created_at"],
            "summary": {
                "metric_count": len(result["metrics"]),
                "empty": result["empty"],
                "metrics": [{
                    "id": metric["id"], "label": metric["label"],
                    "type": metric["type"], "chart": metric["chart"],
                    "empty": metric["empty"],
                } for metric in result["metrics"]],
            },
        })
    return {"snapshots": snapshots, "count": len(snapshots), "ledger": _ledger_info(ctx)}


def _get_monthly_report_snapshot(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_ledger(ctx)
    snapshot_id = _text(params.get("snapshot_id"), "snapshot_id")
    row = conn.execute(
        "SELECT snapshot_id,template_id,template_name,start_date,end_date,date_basis,result_json,result_sha256,source_sha256,created_at "
        "FROM insight_report_snapshots WHERE ledger_id=? AND snapshot_id=?",
        (ctx["ledger_id"], snapshot_id),
    ).fetchone()
    if row is None:
        _fail("not_found", "Monthly report snapshot not found", {"snapshot_id": snapshot_id})
    return {
        "snapshot_id": row["snapshot_id"], "template_id": row["template_id"], "template_name": row["template_name"],
        "period": {"start_date": row["start_date"], "end_date": row["end_date"]}, "date_basis": row["date_basis"],
        "result": json.loads(row["result_json"]), "result_sha256": row["result_sha256"],
        "source_sha256": row["source_sha256"], "created_at": row["created_at"], "ledger": _ledger_info(ctx),
    }


def _numeric_changes(before: object, after: object, path: str = "") -> list[dict]:
    if isinstance(before, (int, float)) and not isinstance(before, bool) and isinstance(after, (int, float)) and not isinstance(after, bool):
        delta = after - before
        return [{"field": path or "$", "saved": before, "current": after, "delta": delta}] if delta else []
    changes = []
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            if key in before and key in after:
                changes.extend(_numeric_changes(before[key], after[key], f"{path}.{key}" if path else key))
        return changes
    if isinstance(before, list) and isinstance(after, list):
        def identity(item: object, index: int) -> str:
            if isinstance(item, dict):
                for key in ("id", "category_id", "classification", "goal_id", "merchant", "month"):
                    if key in item:
                        return f"{key}:{item[key]}"
            return str(index)
        old_map = {identity(item, index): item for index, item in enumerate(before)}
        new_map = {identity(item, index): item for index, item in enumerate(after)}
        for key in sorted(set(old_map) & set(new_map)):
            changes.extend(_numeric_changes(old_map[key], new_map[key], f"{path}[{key}]"))
    return changes


def _source_diff(before: dict, after: dict) -> dict:
    result = {"added": [], "removed": [], "changed": []}
    for collection in ("transactions", "adjustments", "goal_changes"):
        old_rows = before.get(collection, [])
        new_rows = after.get(collection, [])
        def key(row: dict) -> str:
            if collection == "adjustments":
                return "adjustment:" + ":".join(str(row.get(item)) for item in ("adjustment_type", "kind", "transaction_id", "related_transaction_id", "occurred_on", "charged_on"))
            if "transaction_id" in row:
                return "transaction:" + row["transaction_id"] + ":" + str(row.get("category_id"))
            if "entry_id" in row:
                return "goal_change:" + row["entry_id"]
            return json.dumps(row, ensure_ascii=False, sort_keys=True)
        old_map = {key(row): row for row in old_rows}
        new_map = {key(row): row for row in new_rows}
        for identity in sorted(set(new_map) - set(old_map)):
            result["added"].append({"collection": collection, **new_map[identity]})
        for identity in sorted(set(old_map) - set(new_map)):
            result["removed"].append({"collection": collection, **old_map[identity]})
        for identity in sorted(set(old_map) & set(new_map)):
            if old_map[identity] != new_map[identity]:
                result["changed"].append({"collection": collection, "before": old_map[identity], "current": new_map[identity]})
    if before.get("goal_state") != after.get("goal_state"):
        result["changed"].append({"collection": "goal_state", "before": before.get("goal_state"), "current": after.get("goal_state")})
    return result


def _compare_monthly_report_snapshot(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_ledger(ctx)
    snapshot_id = _text(params.get("snapshot_id"), "snapshot_id")
    snapshot = conn.execute(
        "SELECT * FROM insight_report_snapshots WHERE ledger_id=? AND snapshot_id=?",
        (ctx["ledger_id"], snapshot_id),
    ).fetchone()
    if snapshot is None:
        _fail("not_found", "Monthly report snapshot not found", {"snapshot_id": snapshot_id})
    config = json.loads(snapshot["template_config_json"])
    saved_result = json.loads(snapshot["result_json"])
    current_result, current_sources = _run_monthly_report_config(
        conn, ctx, snapshot["template_id"], snapshot["template_name"], config,
        snapshot["start_date"], snapshot["end_date"], saved_result["template_version"],
    )
    saved_sources = json.loads(snapshot["source_facts_json"])
    current_result_json, current_hash = _json_hash(current_result)
    metric_changes = []
    old_metrics = {item["id"]: item for item in saved_result["metrics"]}
    new_metrics = {item["id"]: item for item in current_result["metrics"]}
    for metric_id in [item["id"] for item in saved_result["metrics"]]:
        before = old_metrics[metric_id]
        current = new_metrics.get(metric_id)
        if current is None:
            metric_changes.append({"metric_id": metric_id, "removed": True, "saved": before, "current": None, "numeric_changes": []})
        else:
            metric_changes.append({
                "metric_id": metric_id,
                "changed": before != current,
                "saved": before,
                "current": current,
                "numeric_changes": _numeric_changes(before.get("value"), current.get("value")),
                "source_differences": _source_diff(saved_sources.get(metric_id, {}), current_sources.get(metric_id, {})),
            })
    for metric_id in [item["id"] for item in current_result["metrics"] if item["id"] not in old_metrics]:
        metric_changes.append({"metric_id": metric_id, "added": True, "saved": None, "current": new_metrics[metric_id], "numeric_changes": []})
    return {
        "snapshot_id": snapshot_id,
        "saved_at": snapshot["created_at"],
        "period": {"start_date": snapshot["start_date"], "end_date": snapshot["end_date"]},
        "date_basis": snapshot["date_basis"],
        "saved_result_sha256": snapshot["result_sha256"],
        "current_result_sha256": current_hash,
        "changed": current_hash != snapshot["result_sha256"],
        "metric_changes": metric_changes,
        "saved_result": saved_result,
        "current_result": current_result,
        "ledger": _ledger_info(ctx),
    }


def _object(properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


_STRING = {"type": "string", "minLength": 1}
_DATE = {"type": "string", "format": "date"}
_MONTH = {"type": "string", "pattern": r"^\d{4}-\d{2}$"}
_DATE_BASIS = {"type": "string", "enum": ["occurred_on", "charged_on"]}
_REPORT_FILTER_PROPERTIES = {
    "kind": {"type": "string", "enum": ["income", "expense"]},
    "category_id": _STRING,
    "tag": _STRING,
    "keyword": _STRING,
    "merchant": _STRING,
    "note_keyword": _STRING,
    "amount_min_minor": {"type": "integer", "minimum": 0},
    "amount_max_minor": {"type": "integer", "minimum": 0},
    "project_id": _STRING,
    "exclude_category_id": _STRING,
    "exclude_tag": _STRING,
    "exclude_keyword": _STRING,
    "exclude_merchant": _STRING,
}
_FILTERS = _object(_REPORT_FILTER_PROPERTIES)
_CYCLE_START_DAY = {"type": "integer", "minimum": 1, "maximum": 31}
_LIMIT = {"type": "integer", "minimum": 1, "maximum": 500}
_OFFSET = {"type": "integer", "minimum": 0}
_DRILLDOWN_VALUES = _object({
    "month": _MONTH,
    "merchant": {"anyOf": [_STRING, {"type": "null"}]},
    "merchant_ref": {"anyOf": [_STRING, {"type": "null"}]},
    "category_id": {"anyOf": [_STRING, {"type": "null"}]},
    "project_id": {"anyOf": [_STRING, {"type": "null"}]},
    "expense_style": {"type": "string", "enum": ["fixed", "flexible", "unclassified"]},
})
_ASSUMPTIONS = {
    "type": "array",
    "minItems": 1,
    "items": _object({"category_id": _STRING, "delta_minor": {"type": "integer", "not": {"const": 0}}}, ("category_id", "delta_minor")),
}
_REPORT_METRICS = {
    "type": "array",
    "minItems": 1,
    "maxItems": 20,
    "items": _object({
        "id": _STRING,
        "label": _STRING,
        "type": {"type": "string", "enum": sorted(_METRIC_TYPES)},
        "chart": {"type": "string", "enum": sorted(_CHART_TYPES)},
        "category_id": _STRING,
        "goal_id": _STRING,
    }, ("id", "type")),
}
_VIEW_PRESETS = ["this_month", "last_month", "last_7_days", "last_30_days", "this_year"]
_VIEW_COMPARISON = {
    "oneOf": [
        _object({"mode": {"type": "string", "enum": ["previous_equal_length", "previous_calendar_year", "previous_financial_cycle"]}}, ("mode",)),
        _object({"mode": {"type": "string", "const": "explicit"}, "start_date": _DATE, "end_date": _DATE}, ("mode", "start_date", "end_date")),
    ]
}
_VIEW_CONFIG = _object({
    "period": {
        "oneOf": [
            _object({"preset": {"type": "string", "enum": _VIEW_PRESETS}}, ("preset",)),
            _object({"start_date": _DATE, "end_date": _DATE}, ("start_date", "end_date")),
        ]
    },
    "date_basis": _DATE_BASIS,
    "category_id": {"anyOf": [_STRING, {"type": "null"}]},
    "chart": {"type": "string", "enum": ["pie", "bar", "table"]},
    "aggregation": {"type": "string", "enum": ["day", "week", "month"]},
    "comparison": _VIEW_COMPARISON,
}, ("period", "date_basis", "category_id", "chart", "aggregation"))


def _action(description: str, schema: dict, scope: str, host_permission: str, run) -> dict:
    return {
        "description": description,
        "inputSchema": schema,
        "scope": scope,
        "run": run,
        "host_permission": host_permission,
    }


ACTIONS = {
    "set_financial_cycle": _action(
        "Set the ledger's custom financial cycle, such as a payroll period starting on the 15th; dates are clamped to each month's last day.",
        _object({"name": _STRING, "start_day": _CYCLE_START_DAY}, ("name", "start_day")),
        "own_write", "write", _upsert_financial_cycle,
    ),
    "get_financial_cycle": _action(
        "Read the configured financial cycle and the period containing a selected date.",
        _object({"as_of": _DATE}), "own_read", "summary_read", _get_financial_cycle,
    ),
    "get_financial_period_summary": _action(
        "Summarize confirmed, pending, and estimated ledger activity for the configured custom financial period.",
        _object({"as_of": _DATE, "filters": _FILTERS, "date_basis": _DATE_BASIS}),
        "own_read", "summary_read", _get_financial_period_summary,
    ),
    "get_insight_period_comparison": _action(
        "Compare an expense period with the preceding equal-length period, matching calendar dates last year, the preceding configured financial cycle, or a second explicit period.",
        _object({
            "mode": {"type": "string", "enum": ["previous_equal_length", "previous_calendar_year", "previous_financial_cycle", "explicit"]},
            "start_date": _DATE, "end_date": _DATE,
            "comparison_start_date": _DATE, "comparison_end_date": _DATE,
            "filters": _FILTERS, "date_basis": _DATE_BASIS,
        }, ("mode", "start_date", "end_date")),
        "own_read", "summary_read", _get_insight_period_comparison,
    ),
    "get_cross_tab_report": _action(
        "Group the same filtered ledger transactions by month, merchant, category, project, or fixed/flexible style and return matching drilldown arguments for every cell.",
        _object({
            "start_date": _DATE, "end_date": _DATE,
            "dimensions": {"type": "array", "minItems": 1, "maxItems": 2, "uniqueItems": True,
                           "items": {"type": "string", "enum": ["month", "merchant", "category", "project", "expense_style"]}},
            "filters": _FILTERS, "date_basis": _DATE_BASIS,
        }, ("start_date", "end_date", "dimensions")),
        "own_read", "summary_read", _cross_tab_report,
    ),
    "get_report_drilldown": _action(
        "Return the exact transactions or category allocations behind report dimensions using the same date basis and filters; notes and merchant names are omitted. The same-ledger web client uses a merchant label; MCP uses a token-scoped opaque merchant reference valid for 30 minutes.",
        _object({
            "start_date": _DATE, "end_date": _DATE, "group_values": _DRILLDOWN_VALUES,
            "filters": _FILTERS, "date_basis": _DATE_BASIS, "limit": _LIMIT, "offset": _OFFSET,
        }, ("start_date", "end_date", "group_values")),
        "own_read", "detail_read", _report_drilldown,
    ),
    "get_purchase_frequency": _action(
        "Summarize confirmed purchase count, spending total, and average amount per transaction by merchant or category, with pending and estimate counts separated.",
        _object({"start_date": _DATE, "end_date": _DATE, "group_by": {"type": "string", "enum": ["merchant", "category"]},
                 "filters": _FILTERS, "date_basis": _DATE_BASIS, "limit": _LIMIT}, ("start_date", "end_date", "group_by")),
        "own_read", "summary_read", _purchase_frequency,
    ),
    "get_spending_variance": _action(
        "Compare spending between two explicit periods by category and show the transactions contributing to each increase or decrease.",
        _object({
            "current_start_date": _DATE, "current_end_date": _DATE,
            "comparison_start_date": _DATE, "comparison_end_date": _DATE,
            "filters": _FILTERS, "date_basis": _DATE_BASIS,
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        }, ("current_start_date", "current_end_date", "comparison_start_date", "comparison_end_date")),
        "own_read", "detail_read", _spending_variance,
    ),
    "get_rolling_spending_trend": _action(
        "Return daily confirmed spending and a rolling daily average with observed coverage days and explicit insufficient-data markers.",
        _object({"start_date": _DATE, "end_date": _DATE, "window_days": {"type": "integer", "minimum": 1, "maximum": 365},
                 "filters": _FILTERS, "date_basis": _DATE_BASIS}, ("start_date", "end_date")),
        "own_read", "summary_read", _rolling_spending_trend,
    ),
    "get_budget_period_progress": _action(
        "Read the selected calendar month's category budget usage, remaining balances, separate pending expenses, and elapsed calendar-day progress using occurred_on dates.",
        _object({"month": _MONTH, "as_of": _DATE}),
        "own_read", "summary_read", _get_budget_period_progress,
    ),
    "get_annual_spending_calendar": _action(
        "Return every day of a calendar year with actual expense, pending amounts, record counts, and a status that distinguishes empty days, zero-spend records, pending records, and spending.",
        _object({"year": {"type": "integer", "minimum": 1, "maximum": 9999}, "filters": _FILTERS, "date_basis": _DATE_BASIS}, ("year",)),
        "own_read", "summary_read", _annual_spending_calendar,
    ),
    "upsert_insight_view": _action(
        "Create or update a ledger-scoped saved insight view with a validated period, category, chart, aggregation, and optional comparison configuration.",
        _object({
            "view_id": _STRING, "expected_version": {"type": "integer", "minimum": 1},
            "name": _STRING, "config": _VIEW_CONFIG,
        }, ("name", "config")),
        "own_write", "write", _upsert_insight_view,
    ),
    "list_insight_views": _action(
        "List saved insight views in the caller's ledger.",
        _object({}), "own_read", "summary_read", _list_insight_views,
    ),
    "delete_insight_view": _action(
        "Delete a saved insight view from the caller's ledger using its current version.",
        _object({"view_id": _STRING, "expected_version": {"type": "integer", "minimum": 1}}, ("view_id", "expected_version")),
        "own_write", "write", _delete_insight_view,
    ),
    "upsert_expense_view_rule": _action(
        "Classify expenses as fixed or flexible by an exact category or canonical merchant rule; category rules take precedence over merchant rules.",
        _object({
            "rule_id": _STRING, "expected_version": {"type": "integer", "minimum": 1}, "name": _STRING,
            "match_type": {"type": "string", "enum": ["category", "merchant"]}, "match_value": _STRING,
            "classification": {"type": "string", "enum": ["fixed", "flexible"]},
        }, ("name", "match_type", "match_value", "classification")),
        "own_write", "write", _upsert_expense_view_rule,
    ),
    "list_expense_view_rules": _action(
        "List fixed/flexible classification rules configured in the caller's ledger.",
        _object({}), "own_read", "summary_read", _list_expense_view_rules,
    ),
    "get_expense_style_summary": _action(
        "Summarize fixed, flexible, and unclassified expenses for the selected period, with a matching drilldown for each group.",
        _object({"start_date": _DATE, "end_date": _DATE, "filters": _FILTERS, "date_basis": _DATE_BASIS}, ("start_date", "end_date")),
        "own_read", "summary_read", _expense_style_summary,
    ),
    "create_budget_scenario_draft": _action(
        "Create an independent budget scenario draft using the target month's category allowances and prior-three-month average confirmed spending; it does not change budgets or transactions.",
        _object({"name": _STRING, "month": _MONTH, "assumptions": _ASSUMPTIONS, "date_basis": _DATE_BASIS}, ("month", "assumptions")),
        "own_write", "write", _create_budget_scenario_draft,
    ),
    "update_budget_scenario_draft": _action(
        "Update and recalculate an independent budget scenario draft without changing the ledger or monthly budgets.",
        _object({"draft_id": _STRING, "expected_version": {"type": "integer", "minimum": 1}, "name": _STRING,
                 "month": _MONTH, "assumptions": _ASSUMPTIONS, "date_basis": _DATE_BASIS},
                ("draft_id", "expected_version", "month", "assumptions")),
        "own_write", "write", _update_budget_scenario_draft,
    ),
    "get_budget_scenario_draft": _action(
        "Read a saved scenario draft and the exact result from its last calculation.",
        _object({"draft_id": _STRING}, ("draft_id",)), "own_read", "summary_read", _get_budget_scenario_draft,
    ),
    "list_budget_scenario_drafts": _action(
        "List saved budget scenario drafts and their last projected remaining amounts.",
        _object({}), "own_read", "summary_read", _list_budget_scenario_drafts,
    ),
    "upsert_monthly_report_template": _action(
        "Create or update an ordered monthly report template with selected metrics and chart types.",
        _object({"template_id": _STRING, "expected_version": {"type": "integer", "minimum": 1}, "name": _STRING,
                 "metrics": _REPORT_METRICS, "date_basis": _DATE_BASIS}, ("name", "metrics")),
        "own_write", "write", _upsert_monthly_report_template,
    ),
    "list_monthly_report_templates": _action(
        "List saved monthly report templates and their ordered metric definitions.",
        _object({}), "own_read", "summary_read", _list_monthly_report_templates,
    ),
    "run_monthly_report": _action(
        "Run a saved monthly report template for explicit dates and return metrics with drilldown actions.",
        _object({"template_id": _STRING, "start_date": _DATE, "end_date": _DATE}, ("template_id", "start_date", "end_date")),
        "own_read", "summary_read", _run_monthly_report,
    ),
    "save_monthly_report_snapshot": _action(
        "Run and save the actual monthly report result plus its source facts and hashes for later comparison.",
        _object({"template_id": _STRING, "start_date": _DATE, "end_date": _DATE}, ("template_id", "start_date", "end_date")),
        "own_write", "write", _save_monthly_report_snapshot,
    ),
    "list_report_snapshots": _action(
        "Find saved report snapshots by overlapping month or period and optional template, returning dates and a private-data-free metric summary.",
        _object({"month": _MONTH, "start_date": _DATE, "end_date": _DATE, "template_id": _STRING,
                 "limit": {"type": "integer", "minimum": 1, "maximum": 200}}),
        "own_read", "summary_read", _list_report_snapshots,
    ),
    "get_monthly_report_snapshot": _action(
        "Read the result that was actually saved for a monthly report snapshot.",
        _object({"snapshot_id": _STRING}, ("snapshot_id",)), "own_read", "summary_read", _get_monthly_report_snapshot,
    ),
    "compare_monthly_report_snapshot": _action(
        "Compare a saved report result with a current recomputation using its saved template definition and return changed transaction or goal sources.",
        _object({"snapshot_id": _STRING}, ("snapshot_id",)), "own_read", "detail_read", _compare_monthly_report_snapshot,
    ),
}
