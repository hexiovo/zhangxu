from __future__ import annotations

import calendar
import json
import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone

from .contract import BusinessError
from . import ledger, planning


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS yuxu_recurring_rules (
    ledger_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    rule_kind TEXT NOT NULL CHECK (rule_kind IN ('fixed', 'subscription')),
    name TEXT NOT NULL,
    merchant TEXT,
    category_id TEXT,
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
    first_due_date TEXT NOT NULL,
    interval_months INTEGER NOT NULL CHECK (interval_months BETWEEN 1 AND 120),
    due_day INTEGER NOT NULL CHECK (due_day BETWEEN 1 AND 31),
    posting_mode TEXT NOT NULL CHECK (posting_mode IN ('pending', 'auto')),
    state TEXT NOT NULL CHECK (state IN ('active', 'paused')),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, rule_id),
    FOREIGN KEY (ledger_id, category_id) REFERENCES ledger_categories(ledger_id, category_id)
);

CREATE TABLE IF NOT EXISTS yuxu_recurring_price_history (
    ledger_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
    created_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, rule_id, effective_date),
    FOREIGN KEY (ledger_id, rule_id) REFERENCES yuxu_recurring_rules(ledger_id, rule_id)
);

CREATE TABLE IF NOT EXISTS yuxu_recurring_exceptions (
    ledger_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    period TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('skip', 'override')),
    amount_minor INTEGER,
    due_date TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, rule_id, period),
    FOREIGN KEY (ledger_id, rule_id) REFERENCES yuxu_recurring_rules(ledger_id, rule_id),
    CHECK ((action = 'skip' AND amount_minor IS NULL AND due_date IS NULL) OR
           (action = 'override' AND amount_minor IS NOT NULL AND amount_minor > 0))
);

CREATE TABLE IF NOT EXISTS yuxu_recurring_occurrences (
    ledger_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    period TEXT NOT NULL,
    due_date TEXT NOT NULL,
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
    transaction_id TEXT,
    state TEXT NOT NULL CHECK (state IN ('pending', 'confirmed', 'skipped')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, rule_id, period),
    FOREIGN KEY (ledger_id, rule_id) REFERENCES yuxu_recurring_rules(ledger_id, rule_id),
    FOREIGN KEY (ledger_id, transaction_id) REFERENCES ledger_transactions(ledger_id, transaction_id)
);

CREATE TABLE IF NOT EXISTS yuxu_cashflow_plans (
    ledger_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('income', 'expense')),
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
    expected_date TEXT NOT NULL,
    category_id TEXT,
    merchant TEXT,
    state TEXT NOT NULL CHECK (state IN ('active', 'cancelled')),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, plan_id),
    FOREIGN KEY (ledger_id, category_id) REFERENCES ledger_categories(ledger_id, category_id)
);

CREATE TABLE IF NOT EXISTS yuxu_reminder_settings (
    ledger_id TEXT PRIMARY KEY,
    enabled_kinds_json TEXT NOT NULL,
    budget_low_percent INTEGER NOT NULL CHECK (budget_low_percent BETWEEN 0 AND 100),
    anomaly_min_samples INTEGER NOT NULL CHECK (anomaly_min_samples BETWEEN 2 AND 100),
    anomaly_multiplier_milli INTEGER NOT NULL CHECK (anomaly_multiplier_milli BETWEEN 1000 AND 10000),
    goal_risk_percent INTEGER NOT NULL CHECK (goal_risk_percent BETWEEN 1 AND 100),
    renewal_notice_days INTEGER NOT NULL CHECK (renewal_notice_days BETWEEN 1 AND 90),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS yuxu_reminders (
    ledger_id TEXT NOT NULL,
    reminder_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('budget_low', 'abnormal_spending', 'goal_risk', 'subscription_renewal')),
    period TEXT NOT NULL,
    message TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    read_at TEXT,
    PRIMARY KEY (ledger_id, reminder_id),
    UNIQUE (ledger_id, kind, period)
);
"""


_DATE = {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"}
_PERIOD = {"type": "string", "pattern": "^\\d{4}-(0[1-9]|1[0-2])$"}
_STRING = {"type": "string", "minLength": 1, "maxLength": 200}
_KIND = {"type": "string", "enum": ["budget_low", "abnormal_spending", "goal_risk", "subscription_renewal"]}


def _object(properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


def _fail(status: str, message: str, code: str, details: dict | None = None) -> None:
    raise BusinessError(status, message, details, code)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 200:
        _fail("invalid_input", f"{field} 必须是 1 至 200 个字符。", "invalid_input", {"field": field})
    return value.strip()


def _integer(value: object, field: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        _fail("invalid_input", f"{field} 必须是 {minimum} 至 {maximum} 的整数。", "invalid_input", {"field": field})
    return value


def _date(value: object, field: str) -> date:
    if not isinstance(value, str):
        _fail("invalid_input", f"{field} 必须是 ISO 日期。", "invalid_date", {"field": field})
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        _fail("invalid_input", f"{field} 必须是 ISO 日期。", "invalid_date", {"field": field})
    if parsed.isoformat() != value:
        _fail("invalid_input", f"{field} 必须使用 YYYY-MM-DD。", "invalid_date", {"field": field})
    return parsed


def _period(value: object) -> str:
    if not isinstance(value, str) or len(value) != 7 or value[4] != "-":
        _fail("invalid_input", "period 必须使用 YYYY-MM。", "invalid_period")
    year, month = value.split("-", 1)
    if not year.isdigit() or not month.isdigit() or not 1 <= int(month) <= 12:
        _fail("invalid_input", "period 必须使用 YYYY-MM。", "invalid_period")
    try:
        date(int(year), int(month), 1)
    except ValueError:
        _fail("invalid_input", "period 必须使用有效年份。", "invalid_period")
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return str(uuid.uuid4())


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _month_date(first: date, months: int, due_day: int) -> date:
    month_index = first.year * 12 + first.month - 1 + months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    return date(year, month, min(due_day, calendar.monthrange(year, month)[1]))


def _scheduled_date(rule: sqlite3.Row, period: str) -> date | None:
    first = date.fromisoformat(rule["first_due_date"])
    year, month = (int(part) for part in period.split("-"))
    month_delta = (year - first.year) * 12 + month - first.month
    if month_delta < 0 or month_delta % rule["interval_months"]:
        return None
    return date(year, month, min(rule["due_day"], calendar.monthrange(year, month)[1]))


def _occurrence_dates(rule: sqlite3.Row, start_date: date, end_date: date):
    first = date.fromisoformat(rule["first_due_date"])
    months = (start_date.year - first.year) * 12 + start_date.month - first.month
    intervals = max(0, months // rule["interval_months"])
    due = _month_date(first, intervals * rule["interval_months"], rule["due_day"])
    if due < start_date:
        intervals += 1
        due = _month_date(first, intervals * rule["interval_months"], rule["due_day"])
    while due <= end_date:
        if first <= due:
            yield due
        intervals += 1
        due = _month_date(first, intervals * rule["interval_months"], rule["due_day"])


def _planning_period_open(conn, ctx: dict, business_date: date, action_name: str) -> None:
    planning.assert_period_open(conn, ctx, business_date.isoformat(), action_name=action_name)


def _amount(value: object, field: str = "amount_minor") -> int:
    return _integer(value, field, 1, 2**53 - 1)


def _rule_row(conn: sqlite3.Connection, ctx: dict, rule_id: object) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM yuxu_recurring_rules WHERE ledger_id = ? AND rule_id = ?",
        (ctx["ledger_id"], _text(rule_id, "rule_id")),
    ).fetchone()
    if row is None:
        _fail("not_found", "定期规则不存在。", "not_found")
    return row


def _price_on(conn: sqlite3.Connection, ctx: dict, rule: sqlite3.Row, on_date: date) -> int:
    row = conn.execute(
        "SELECT amount_minor FROM yuxu_recurring_price_history "
        "WHERE ledger_id = ? AND rule_id = ? AND effective_date <= ? "
        "ORDER BY effective_date DESC LIMIT 1",
        (ctx["ledger_id"], rule["rule_id"], on_date.isoformat()),
    ).fetchone()
    return row["amount_minor"] if row else rule["amount_minor"]


def _next_due(conn: sqlite3.Connection, ctx: dict, rule: sqlite3.Row, today: date) -> date:
    first = date.fromisoformat(rule["first_due_date"])
    months = max(0, ((today.year - first.year) * 12 + today.month - first.month) // rule["interval_months"])
    candidate = _month_date(first, months * rule["interval_months"], rule["due_day"])
    if candidate < today:
        candidate = _month_date(first, (months + 1) * rule["interval_months"], rule["due_day"])
    return candidate


def _rule_payload(conn: sqlite3.Connection, ctx: dict, rule: sqlite3.Row) -> dict:
    today = date.fromisoformat(ctx["today"])
    prices = conn.execute(
        "SELECT effective_date, amount_minor FROM yuxu_recurring_price_history "
        "WHERE ledger_id = ? AND rule_id = ? ORDER BY effective_date",
        (ctx["ledger_id"], rule["rule_id"]),
    ).fetchall()
    current_amount = _price_on(conn, ctx, rule, today)
    return {
        "rule_id": rule["rule_id"],
        "rule_kind": rule["rule_kind"],
        "name": rule["name"],
        "merchant": rule["merchant"],
        "category_id": rule["category_id"],
        "amount_minor": current_amount,
        "first_due_date": rule["first_due_date"],
        "interval_months": rule["interval_months"],
        "due_day": rule["due_day"],
        "posting_mode": rule["posting_mode"],
        "state": rule["state"],
        "version": rule["version"],
        "next_due_date": _next_due(conn, ctx, rule, today).isoformat(),
        "annualized_cost_minor": current_amount * 12 // rule["interval_months"] if rule["rule_kind"] == "subscription" else None,
        "price_history": [{"effective_date": row["effective_date"], "amount_minor": row["amount_minor"]} for row in prices],
    }


def _create_rule(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    rule_kind = params.get("rule_kind")
    if rule_kind not in ("fixed", "subscription"):
        _fail("invalid_input", "rule_kind 必须是 fixed 或 subscription。", "invalid_rule_kind")
    name = _text(params.get("name"), "name")
    amount_minor = _amount(params.get("amount_minor"))
    first_due = _date(params.get("first_due_date"), "first_due_date")
    interval_months = _integer(params.get("interval_months"), "interval_months", 1, 120)
    due_day = _integer(params.get("due_day"), "due_day", 1, 31)
    expected_day = min(due_day, calendar.monthrange(first_due.year, first_due.month)[1])
    if first_due.day != expected_day:
        _fail("invalid_input", "first_due_date 必须与 due_day 在该月对应。", "invalid_first_due_date")
    posting_mode = params.get("posting_mode")
    if posting_mode not in ("pending", "auto"):
        _fail("invalid_input", "posting_mode 必须是 pending 或 auto。", "invalid_posting_mode")
    merchant = ledger._clean_optional_text(params.get("merchant"), "merchant")
    category_id = ledger._validate_category(conn, ctx, params.get("category_id"))
    _planning_period_open(conn, ctx, first_due, "create_recurring_rule")
    rule_id = _new_id()
    now = _now()
    conn.execute(
        "INSERT INTO yuxu_recurring_rules "
        "(ledger_id, rule_id, rule_kind, name, merchant, category_id, amount_minor, first_due_date, "
        "interval_months, due_day, posting_mode, state, version, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 1, ?, ?)",
        (
            ctx["ledger_id"], rule_id, rule_kind, name, merchant, category_id, amount_minor,
            first_due.isoformat(), interval_months, due_day, posting_mode, now, now,
        ),
    )
    conn.execute(
        "INSERT INTO yuxu_recurring_price_history(ledger_id, rule_id, effective_date, amount_minor, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (ctx["ledger_id"], rule_id, first_due.isoformat(), amount_minor, now),
    )
    return {"rule": _rule_payload(conn, ctx, _rule_row(conn, ctx, rule_id))}


def _list_rules(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    state = params.get("state")
    if state is not None and state not in ("active", "paused"):
        _fail("invalid_input", "state 必须是 active 或 paused。", "invalid_state")
    query = "SELECT * FROM yuxu_recurring_rules WHERE ledger_id = ?"
    values: list[object] = [ctx["ledger_id"]]
    if state:
        query += " AND state = ?"
        values.append(state)
    query += " ORDER BY first_due_date, name, rule_id"
    rows = conn.execute(query, values).fetchall()
    return {"rules": [_rule_payload(conn, ctx, row) for row in rows]}


def _rule_state(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    rule = _rule_row(conn, ctx, params.get("rule_id"))
    state = params.get("state")
    if state not in ("active", "paused"):
        _fail("invalid_input", "state 必须是 active 或 paused。", "invalid_state")
    _planning_period_open(conn, ctx, date.fromisoformat(ctx["today"]), "set_recurring_rule_state")
    now = _now()
    conn.execute(
        "UPDATE yuxu_recurring_rules SET state = ?, version = version + 1, updated_at = ? "
        "WHERE ledger_id = ? AND rule_id = ?",
        (state, now, ctx["ledger_id"], rule["rule_id"]),
    )
    return {"rule": _rule_payload(conn, ctx, _rule_row(conn, ctx, rule["rule_id"]))}


def _set_exception(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    rule = _rule_row(conn, ctx, params.get("rule_id"))
    period = _period(params.get("period"))
    action = params.get("action")
    if action not in ("skip", "override"):
        _fail("invalid_input", "action 必须是 skip 或 override。", "invalid_exception")
    amount_minor = _amount(params.get("amount_minor")) if params.get("amount_minor") is not None else None
    due_date = _date(params["due_date"], "due_date") if params.get("due_date") is not None else None
    if action == "skip" and (amount_minor is not None or due_date is not None):
        _fail("invalid_input", "skip 例外不能带金额或日期。", "invalid_exception")
    if action == "override" and amount_minor is None:
        _fail("invalid_input", "override 例外必须提供 amount_minor。", "invalid_exception")
    if due_date is not None and due_date.strftime("%Y-%m") != period:
        _fail("invalid_input", "单期例外日期必须仍处于该账期。", "invalid_exception_date")
    scheduled = _scheduled_date(rule, period)
    if scheduled is None:
        _fail("invalid_input", "period 不是该规则的账期。", "invalid_rule_period")
    _planning_period_open(conn, ctx, due_date or scheduled, "set_recurring_exception")
    occurrence = conn.execute(
        "SELECT state FROM yuxu_recurring_occurrences WHERE ledger_id = ? AND rule_id = ? AND period = ?",
        (ctx["ledger_id"], rule["rule_id"], period),
    ).fetchone()
    if occurrence:
        _fail("conflict", "该账期已处理，不能再更改单期例外。", "occurrence_exists")
    now = _now()
    conn.execute(
        "INSERT INTO yuxu_recurring_exceptions(ledger_id, rule_id, period, action, amount_minor, due_date, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(ledger_id, rule_id, period) DO UPDATE SET "
        "action = excluded.action, amount_minor = excluded.amount_minor, due_date = excluded.due_date, created_at = excluded.created_at",
        (ctx["ledger_id"], rule["rule_id"], period, action, amount_minor, due_date.isoformat() if due_date else None, now),
    )
    return {"rule_id": rule["rule_id"], "period": period, "action": action, "amount_minor": amount_minor, "due_date": due_date.isoformat() if due_date else scheduled.isoformat()}


def _update_subscription_price(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    rule = _rule_row(conn, ctx, params.get("rule_id"))
    if rule["rule_kind"] != "subscription":
        _fail("invalid_input", "只有订阅规则可以记录涨价。", "not_a_subscription")
    effective_date = _date(params.get("effective_date"), "effective_date")
    amount_minor = _amount(params.get("amount_minor"))
    _planning_period_open(conn, ctx, effective_date, "update_subscription_price")
    if effective_date < date.fromisoformat(rule["first_due_date"]):
        _fail("invalid_input", "生效日期不能早于订阅开始日期。", "invalid_effective_date")
    now = _now()
    conn.execute(
        "INSERT INTO yuxu_recurring_price_history(ledger_id, rule_id, effective_date, amount_minor, created_at) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(ledger_id, rule_id, effective_date) "
        "DO UPDATE SET amount_minor = excluded.amount_minor, created_at = excluded.created_at",
        (ctx["ledger_id"], rule["rule_id"], effective_date.isoformat(), amount_minor, now),
    )
    conn.execute(
        "UPDATE yuxu_recurring_rules SET version = version + 1, updated_at = ? WHERE ledger_id = ? AND rule_id = ?",
        (now, ctx["ledger_id"], rule["rule_id"]),
    )
    return {"rule": _rule_payload(conn, ctx, _rule_row(conn, ctx, rule["rule_id"])), "effective_date": effective_date.isoformat()}


def _process_due(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    through = _date(params.get("through_date", ctx["today"]), "through_date")
    today = date.fromisoformat(ctx["today"])
    if through > today:
        _fail("invalid_input", "through_date 不能晚于今天。", "future_processing_date")
    rules = conn.execute(
        "SELECT * FROM yuxu_recurring_rules WHERE ledger_id = ? AND state = 'active' ORDER BY rule_id",
        (ctx["ledger_id"],),
    ).fetchall()
    pending = []
    generated = []
    skipped = []
    for rule in rules:
        first = date.fromisoformat(rule["first_due_date"])
        for due in _occurrence_dates(rule, first, through):
            period = due.strftime("%Y-%m")
            exists = conn.execute(
                "SELECT 1 FROM yuxu_recurring_occurrences WHERE ledger_id = ? AND rule_id = ? AND period = ?",
                (ctx["ledger_id"], rule["rule_id"], period),
            ).fetchone()
            if exists:
                continue
            exception = conn.execute(
                "SELECT action, amount_minor, due_date FROM yuxu_recurring_exceptions "
                "WHERE ledger_id = ? AND rule_id = ? AND period = ?",
                (ctx["ledger_id"], rule["rule_id"], period),
            ).fetchone()
            actual_due = date.fromisoformat(exception["due_date"]) if exception and exception["due_date"] else due
            if actual_due > through:
                continue
            _planning_period_open(conn, ctx, actual_due, "process_due_recurring")
            now = _now()
            if exception and exception["action"] == "skip":
                conn.execute(
                    "INSERT INTO yuxu_recurring_occurrences "
                    "(ledger_id, rule_id, period, due_date, amount_minor, transaction_id, state, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, NULL, 'skipped', ?, ?)",
                    (ctx["ledger_id"], rule["rule_id"], period, actual_due.isoformat(), _price_on(conn, ctx, rule, actual_due), now, now),
                )
                skipped.append({"rule_id": rule["rule_id"], "period": period, "state": "skipped"})
                continue
            amount = exception["amount_minor"] if exception and exception["amount_minor"] is not None else _price_on(conn, ctx, rule, actual_due)
            transaction = {
                "kind": "expense",
                "amount_minor": amount,
                "occurred_on": actual_due.isoformat(),
                "estimated": rule["posting_mode"] == "pending",
                "confirmation_status": "pending" if rule["posting_mode"] == "pending" else "confirmed",
                "category_id": rule["category_id"],
                "merchant": rule["merchant"] or rule["name"],
            }
            pending.append((rule, period, actual_due, amount, transaction))
    for offset in range(0, len(pending), 100):
        batch = pending[offset:offset + 100]
        batch_ctx = dict(ctx)
        batch_ctx["request_id"] = uuid.uuid5(uuid.NAMESPACE_URL, f"yuxu-recurring:{ctx['request_id']}:{offset}").hex
        result = ledger._add_transactions(conn, batch_ctx, {"transactions": [item[4] for item in batch]})
        for (rule, period, due, amount, _), transaction_id in zip(batch, result["transaction_ids"], strict=True):
            now = _now()
            state = "pending" if rule["posting_mode"] == "pending" else "confirmed"
            conn.execute(
                "INSERT INTO yuxu_recurring_occurrences "
                "(ledger_id, rule_id, period, due_date, amount_minor, transaction_id, state, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ctx["ledger_id"], rule["rule_id"], period, due.isoformat(), amount, transaction_id, state, now, now),
            )
            generated.append({"rule_id": rule["rule_id"], "period": period, "state": state, "transaction_id": transaction_id})
    generated.sort(key=lambda item: (item["period"], item["rule_id"]))
    return {"through_date": through.isoformat(), "processed": generated + skipped}


def _confirm_occurrence(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    rule = _rule_row(conn, ctx, params.get("rule_id"))
    period = _period(params.get("period"))
    amount_minor = _amount(params.get("actual_amount_minor"), "actual_amount_minor")
    actual_date = _date(params.get("actual_date"), "actual_date")
    occurrence = conn.execute(
        "SELECT * FROM yuxu_recurring_occurrences WHERE ledger_id = ? AND rule_id = ? AND period = ?",
        (ctx["ledger_id"], rule["rule_id"], period),
    ).fetchone()
    if occurrence is None or occurrence["state"] == "skipped":
        _fail("not_found", "没有可确认的该账期定期款。", "pending_occurrence_not_found")
    if occurrence["state"] == "confirmed":
        transaction = ledger._transaction_payload(conn, ctx["ledger_id"], occurrence["transaction_id"])
        return {"already_confirmed": True, "transaction": transaction, "period": period}
    _planning_period_open(conn, ctx, actual_date, "confirm_recurring_transaction")
    row = ledger._get_transaction(conn, ctx["ledger_id"], occurrence["transaction_id"])
    result = ledger._update_transaction(conn, ctx, {
        "transaction_id": occurrence["transaction_id"],
        "expected_version": row["version"],
        "patch": {
            "amount_minor": amount_minor,
            "occurred_on": actual_date.isoformat(),
            "estimated": False,
            "confirmation_status": "confirmed",
        },
    })
    now = _now()
    conn.execute(
        "UPDATE yuxu_recurring_occurrences SET due_date = ?, amount_minor = ?, state = 'confirmed', updated_at = ? "
        "WHERE ledger_id = ? AND rule_id = ? AND period = ?",
        (actual_date.isoformat(), amount_minor, now, ctx["ledger_id"], rule["rule_id"], period),
    )
    return {"already_confirmed": False, "transaction": result["transaction"], "period": period}


def _future_cashflow(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    start = _date(params.get("start_date"), "start_date")
    end = _date(params.get("end_date"), "end_date")
    if end < start or (end - start).days > 730:
        _fail("invalid_input", "日期范围必须从 start_date 开始，且不超过 731 天。", "invalid_date_range")
    rules = conn.execute(
        "SELECT * FROM yuxu_recurring_rules WHERE ledger_id = ? AND state = 'active' ORDER BY rule_id",
        (ctx["ledger_id"],),
    ).fetchall()
    days: dict[str, list[dict]] = {}
    annualized = []
    for rule in rules:
        if rule["rule_kind"] == "subscription":
            amount = _price_on(conn, ctx, rule, date.fromisoformat(ctx["today"]))
            annualized.append({
                "rule_id": rule["rule_id"], "name": rule["name"], "interval_months": rule["interval_months"],
                "amount_minor": amount, "annualized_cost_minor": amount * 12 // rule["interval_months"],
            })
        for due in _occurrence_dates(rule, start, end):
            period = due.strftime("%Y-%m")
            exception = conn.execute(
                "SELECT action, amount_minor, due_date FROM yuxu_recurring_exceptions "
                "WHERE ledger_id = ? AND rule_id = ? AND period = ?",
                (ctx["ledger_id"], rule["rule_id"], period),
            ).fetchone()
            if exception and exception["action"] == "skip":
                continue
            actual_due = date.fromisoformat(exception["due_date"]) if exception and exception["due_date"] else due
            if not start <= actual_due <= end:
                continue
            occurrence = conn.execute(
                "SELECT state, transaction_id, amount_minor FROM yuxu_recurring_occurrences "
                "WHERE ledger_id = ? AND rule_id = ? AND period = ?",
                (ctx["ledger_id"], rule["rule_id"], period),
            ).fetchone()
            amount = occurrence["amount_minor"] if occurrence else exception["amount_minor"] if exception and exception["amount_minor"] else _price_on(conn, ctx, rule, actual_due)
            state = occurrence["state"] if occurrence else "planned"
            if actual_due < date.fromisoformat(ctx["today"]) and state == "planned":
                state = "overdue"
            days.setdefault(actual_due.isoformat(), []).append({
                "source_type": "recurring_rule", "rule_id": rule["rule_id"], "rule_kind": rule["rule_kind"], "name": rule["name"],
                "kind": "expense", "amount_minor": amount, "state": state,
                "transaction_id": occurrence["transaction_id"] if occurrence else None,
            })
    plans = conn.execute(
        "SELECT plan_id, name, kind, amount_minor, expected_date, category_id, merchant FROM yuxu_cashflow_plans "
        "WHERE ledger_id = ? AND state = 'active' AND expected_date BETWEEN ? AND ? ORDER BY expected_date, plan_id",
        (ctx["ledger_id"], start.isoformat(), end.isoformat()),
    ).fetchall()
    for plan in plans:
        days.setdefault(plan["expected_date"], []).append({
            "source_type": "one_off_plan", "plan_id": plan["plan_id"], "name": plan["name"],
            "kind": plan["kind"], "amount_minor": plan["amount_minor"], "state": "planned",
            "transaction_id": None, "category_id": plan["category_id"], "merchant": plan["merchant"],
        })
    result = []
    for day in sorted(days):
        items = sorted(days[day], key=lambda item: (item["kind"], item["name"]))
        income = sum(item["amount_minor"] for item in items if item["kind"] == "income")
        expense = sum(item["amount_minor"] for item in items if item["kind"] == "expense")
        result.append({"date": day, "items": items, "income_minor": income, "expense_minor": expense, "net_minor": income - expense, "total_amount_minor": income + expense})
    return {"start_date": start.isoformat(), "end_date": end.isoformat(), "days": result, "annualized_subscriptions": annualized}


def _create_cashflow_plan(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    name = _text(params.get("name"), "name")
    kind = params.get("kind")
    if kind not in ("income", "expense"):
        _fail("invalid_input", "kind 必须是 income 或 expense。", "invalid_plan_kind")
    amount_minor = _amount(params.get("amount_minor"))
    expected_date = _date(params.get("expected_date"), "expected_date")
    category_id = ledger._validate_category(conn, ctx, params.get("category_id"))
    merchant = ledger._clean_optional_text(params.get("merchant"), "merchant")
    _planning_period_open(conn, ctx, expected_date, "create_cashflow_plan")
    plan_id = _new_id()
    now = _now()
    conn.execute(
        "INSERT INTO yuxu_cashflow_plans "
        "(ledger_id, plan_id, name, kind, amount_minor, expected_date, category_id, merchant, state, version, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', 1, ?, ?)",
        (ctx["ledger_id"], plan_id, name, kind, amount_minor, expected_date.isoformat(), category_id, merchant, now, now),
    )
    return {"plan": {"plan_id": plan_id, "name": name, "kind": kind, "amount_minor": amount_minor, "expected_date": expected_date.isoformat(), "category_id": category_id, "merchant": merchant, "state": "active", "version": 1}}


def _list_cashflow_plans(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    start = _date(params["start_date"], "start_date") if params.get("start_date") is not None else None
    end = _date(params["end_date"], "end_date") if params.get("end_date") is not None else None
    if start and end and start > end:
        _fail("invalid_input", "start_date 不能晚于 end_date。", "invalid_date_range")
    query = "SELECT plan_id, name, kind, amount_minor, expected_date, category_id, merchant, state, version FROM yuxu_cashflow_plans WHERE ledger_id = ?"
    values: list[object] = [ctx["ledger_id"]]
    if start:
        query += " AND expected_date >= ?"
        values.append(start.isoformat())
    if end:
        query += " AND expected_date <= ?"
        values.append(end.isoformat())
    query += " ORDER BY expected_date, plan_id"
    return {"plans": [dict(row) for row in conn.execute(query, values).fetchall()]}


def _cancel_cashflow_plan(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    plan_id = _text(params.get("plan_id"), "plan_id")
    row = conn.execute(
        "SELECT plan_id, name, kind, amount_minor, expected_date, state, version FROM yuxu_cashflow_plans "
        "WHERE ledger_id = ? AND plan_id = ?",
        (ctx["ledger_id"], plan_id),
    ).fetchone()
    if row is None:
        _fail("not_found", "计划收支不存在。", "cashflow_plan_not_found")
    _planning_period_open(conn, ctx, date.fromisoformat(row["expected_date"]), "cancel_cashflow_plan")
    now = _now()
    conn.execute(
        "UPDATE yuxu_cashflow_plans SET state = 'cancelled', version = version + 1, updated_at = ? "
        "WHERE ledger_id = ? AND plan_id = ?",
        (now, ctx["ledger_id"], plan_id),
    )
    return {"plan_id": plan_id, "state": "cancelled", "version": row["version"] + 1}


_DEFAULT_SETTINGS = {
    "enabled_kinds": ["budget_low", "abnormal_spending", "goal_risk", "subscription_renewal"],
    "budget_low_percent": 20,
    "anomaly_min_samples": 5,
    "anomaly_multiplier_milli": 2500,
    "goal_risk_percent": 80,
    "renewal_notice_days": 7,
}


def _settings(conn: sqlite3.Connection, ctx: dict) -> dict:
    row = conn.execute("SELECT * FROM yuxu_reminder_settings WHERE ledger_id = ?", (ctx["ledger_id"],)).fetchone()
    if row is None:
        return dict(_DEFAULT_SETTINGS)
    return {
        "enabled_kinds": json.loads(row["enabled_kinds_json"]),
        "budget_low_percent": row["budget_low_percent"],
        "anomaly_min_samples": row["anomaly_min_samples"],
        "anomaly_multiplier_milli": row["anomaly_multiplier_milli"],
        "goal_risk_percent": row["goal_risk_percent"],
        "renewal_notice_days": row["renewal_notice_days"],
    }


def _get_reminder_settings(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    return {"settings": _settings(conn, ctx)}


def _set_reminder_settings(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    settings = _settings(conn, ctx)
    if "enabled_kinds" in params:
        kinds = params["enabled_kinds"]
        if not isinstance(kinds, list) or any(not isinstance(kind, str) or kind not in ("budget_low", "abnormal_spending", "goal_risk", "subscription_renewal") for kind in kinds) or len(set(kinds)) != len(kinds):
            _fail("invalid_input", "enabled_kinds 必须是不重复的提醒类型列表。", "invalid_settings")
        settings["enabled_kinds"] = kinds
    for key, low, high in (
        ("budget_low_percent", 0, 100),
        ("anomaly_min_samples", 2, 100),
        ("anomaly_multiplier_milli", 1000, 10000),
        ("goal_risk_percent", 1, 100),
        ("renewal_notice_days", 1, 90),
    ):
        if key in params:
            settings[key] = _integer(params[key], key, low, high)
    conn.execute(
        "INSERT INTO yuxu_reminder_settings "
        "(ledger_id, enabled_kinds_json, budget_low_percent, anomaly_min_samples, anomaly_multiplier_milli, goal_risk_percent, renewal_notice_days, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(ledger_id) DO UPDATE SET enabled_kinds_json=excluded.enabled_kinds_json, "
        "budget_low_percent=excluded.budget_low_percent, anomaly_min_samples=excluded.anomaly_min_samples, "
        "anomaly_multiplier_milli=excluded.anomaly_multiplier_milli, goal_risk_percent=excluded.goal_risk_percent, "
        "renewal_notice_days=excluded.renewal_notice_days, updated_at=excluded.updated_at",
        (ctx["ledger_id"], json.dumps(settings["enabled_kinds"]), settings["budget_low_percent"], settings["anomaly_min_samples"], settings["anomaly_multiplier_milli"], settings["goal_risk_percent"], settings["renewal_notice_days"], _now()),
    )
    return {"settings": settings}


def _median(values: list[int]) -> int:
    values.sort()
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) // 2


def _reminder_candidates(conn: sqlite3.Connection, ctx: dict, settings: dict) -> list[dict]:
    today = date.fromisoformat(ctx["today"])
    month_start = _month_start(today)
    period = month_start.strftime("%Y-%m")
    candidates = []
    enabled = set(settings["enabled_kinds"])
    if "budget_low" in enabled or "goal_risk" in enabled:
        snapshot = planning.reminder_snapshot(conn, ctx)
        if "budget_low" in enabled:
            low = []
            for budget in snapshot["monthly_budgets"]:
                allowance = budget["total_allowance_minor"]
                remaining = budget["remaining_minor"]
                threshold = allowance * settings["budget_low_percent"] // 100
                if allowance > 0 and remaining <= threshold:
                    low.append({"category_id": budget["category_id"], "category_path": budget["category_path"], "remaining_minor": remaining, "allowance_minor": allowance})
            if low:
                candidates.append({"kind": "budget_low", "period": period, "message": f"本月有 {len(low)} 项分类预算余量偏低。", "details": {"items": low}})
        if "goal_risk" in enabled:
            at_risk = [
                {"goal_id": goal["goal_id"], "name": goal["name"], "gap_minor": goal["gap_minor"], "required_monthly_increase_minor": goal["required_monthly_increase_minor"], "average_monthly_increase_minor": goal["average_monthly_increase_minor"]}
                for goal in snapshot["saving_goals"]
                if goal["due_date"] and goal["gap_minor"] > 0 and goal["average_monthly_increase_minor"] * 100 < goal["required_monthly_increase_minor"] * settings["goal_risk_percent"]
            ]
            if at_risk:
                candidates.append({"kind": "goal_risk", "period": period, "message": f"本月有 {len(at_risk)} 个攒钱目标低于计划进度。", "details": {"goals": at_risk}})
    if "abnormal_spending" in enabled:
        historical_start = (month_start - timedelta(days=180)).isoformat()
        current = conn.execute(
            "SELECT transaction_id, occurred_on, amount_minor, category_id, merchant FROM ledger_transactions "
            "WHERE ledger_id = ? AND kind = 'expense' AND confirmation_status = 'confirmed' AND voided_at IS NULL "
            "AND estimated = 0 AND occurred_on >= ? AND occurred_on <= ? ORDER BY amount_minor DESC, occurred_on DESC",
            (ctx["ledger_id"], month_start.isoformat(), today.isoformat()),
        ).fetchall()
        for transaction in current:
            samples = [row["amount_minor"] for row in conn.execute(
                "SELECT amount_minor FROM ledger_transactions WHERE ledger_id = ? AND kind = 'expense' "
                "AND confirmation_status = 'confirmed' AND voided_at IS NULL AND estimated = 0 AND occurred_on >= ? AND occurred_on < ? "
                "AND category_id IS ? ORDER BY amount_minor",
                (ctx["ledger_id"], historical_start, month_start.isoformat(), transaction["category_id"]),
            ).fetchall()]
            if len(samples) >= settings["anomaly_min_samples"]:
                median = _median(samples)
                if median > 0 and transaction["amount_minor"] * 1000 >= median * settings["anomaly_multiplier_milli"]:
                    candidates.append({
                        "kind": "abnormal_spending", "period": period,
                        "message": "本月有一笔支出明显高于同类历史样本。",
                        "details": {"transaction_id": transaction["transaction_id"], "occurred_on": transaction["occurred_on"], "amount_minor": transaction["amount_minor"], "merchant": transaction["merchant"], "category_id": transaction["category_id"], "sample_count": len(samples), "sample_median_minor": median},
                    })
                    break
    if "subscription_renewal" in enabled:
        horizon = today + timedelta(days=settings["renewal_notice_days"])
        renewals = []
        rules = conn.execute(
            "SELECT * FROM yuxu_recurring_rules WHERE ledger_id = ? AND rule_kind = 'subscription' AND state = 'active' ORDER BY rule_id",
            (ctx["ledger_id"],),
        ).fetchall()
        for rule in rules:
            due = _next_due(conn, ctx, rule, today)
            if today <= due <= horizon:
                renewals.append({"rule_id": rule["rule_id"], "name": rule["name"], "due_date": due.isoformat(), "amount_minor": _price_on(conn, ctx, rule, due)})
        if renewals:
            candidates.append({"kind": "subscription_renewal", "period": period, "message": f"有 {len(renewals)} 项订阅即将续费。", "details": {"subscriptions": renewals, "notice_days": settings["renewal_notice_days"]}})
    return candidates


def _refresh_reminders(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    settings = _settings(conn, ctx)
    created = []
    for candidate in _reminder_candidates(conn, ctx, settings):
        reminder_id = _new_id()
        now = _now()
        result = conn.execute(
            "INSERT OR IGNORE INTO yuxu_reminders(ledger_id, reminder_id, kind, period, message, details_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ctx["ledger_id"], reminder_id, candidate["kind"], candidate["period"], candidate["message"], json.dumps(candidate["details"], ensure_ascii=False, sort_keys=True), now),
        )
        if result.rowcount:
            created.append({"reminder_id": reminder_id, **candidate, "read_at": None})
    return {"created": created}


def _list_reminders(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    period = _period(params.get("period", ctx["today"][:7]))
    rows = conn.execute(
        "SELECT reminder_id, kind, period, message, details_json, created_at, read_at FROM yuxu_reminders "
        "WHERE ledger_id = ? AND period = ? ORDER BY created_at DESC, reminder_id",
        (ctx["ledger_id"], period),
    ).fetchall()
    return {"period": period, "reminders": [
        {"reminder_id": row["reminder_id"], "kind": row["kind"], "period": row["period"], "message": row["message"], "details": json.loads(row["details_json"]), "created_at": row["created_at"], "read_at": row["read_at"]}
        for row in rows
    ]}


def _mark_reminder_read(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    reminder_id = _text(params.get("reminder_id"), "reminder_id")
    now = _now()
    result = conn.execute(
        "UPDATE yuxu_reminders SET read_at = COALESCE(read_at, ?) WHERE ledger_id = ? AND reminder_id = ?",
        (now, ctx["ledger_id"], reminder_id),
    )
    if result.rowcount != 1:
        _fail("not_found", "提醒不存在。", "reminder_not_found")
    row = conn.execute(
        "SELECT reminder_id, kind, period, message, read_at FROM yuxu_reminders WHERE ledger_id = ? AND reminder_id = ?",
        (ctx["ledger_id"], reminder_id),
    ).fetchone()
    return {"reminder": dict(row)}


ACTIONS = {
    "create_recurring_rule": {
        "description": "Create an explicit fixed-payment or subscription rule. Set posting_mode to pending unless automatic posting was explicitly authorized.",
        "inputSchema": _object({
            "rule_kind": {"type": "string", "enum": ["fixed", "subscription"]},
            "name": _STRING,
            "amount_minor": {"type": "integer", "minimum": 1},
            "first_due_date": _DATE,
            "interval_months": {"type": "integer", "minimum": 1, "maximum": 120},
            "due_day": {"type": "integer", "minimum": 1, "maximum": 31},
            "posting_mode": {"type": "string", "enum": ["pending", "auto"]},
            "category_id": {"type": ["string", "null"]},
            "merchant": {"type": ["string", "null"]},
        }, ("rule_kind", "name", "amount_minor", "first_due_date", "interval_months", "due_day", "posting_mode")),
        "scope": "own_write", "run": _create_rule,
    },
    "list_recurring_rules": {
        "description": "List fixed-payment and subscription rules, next renewal dates, annualized subscription cost, and price history in the current ledger.",
        "inputSchema": _object({"state": {"type": "string", "enum": ["active", "paused"]}}),
        "scope": "own_read", "run": _list_rules,
    },
    "process_due_recurring": {
        "description": "Catch up due fixed-payment and subscription entries through today; each rule and billing period can create only one entry.",
        "inputSchema": _object({"through_date": _DATE}),
        "scope": "own_write", "run": _process_due,
    },
    "confirm_recurring_transaction": {
        "description": "Confirm an existing pending fixed payment or subscription charge by updating that same transaction.",
        "inputSchema": _object({"rule_id": _STRING, "period": _PERIOD, "actual_amount_minor": {"type": "integer", "minimum": 1}, "actual_date": _DATE}, ("rule_id", "period", "actual_amount_minor", "actual_date")),
        "scope": "own_write", "run": _confirm_occurrence,
    },
    "set_recurring_rule_state": {
        "description": "Pause or resume an existing fixed-payment or subscription rule.",
        "inputSchema": _object({"rule_id": _STRING, "state": {"type": "string", "enum": ["active", "paused"]}}, ("rule_id", "state")),
        "scope": "own_write", "run": _rule_state,
    },
    "set_recurring_exception": {
        "description": "Skip or override one billing period without changing later periods.",
        "inputSchema": _object({"rule_id": _STRING, "period": _PERIOD, "action": {"type": "string", "enum": ["skip", "override"]}, "amount_minor": {"type": "integer", "minimum": 1}, "due_date": _DATE}, ("rule_id", "period", "action")),
        "scope": "own_write", "run": _set_exception,
    },
    "update_subscription_price": {
        "description": "Record a subscription price change from its effective date while preserving prior price history.",
        "inputSchema": _object({"rule_id": _STRING, "amount_minor": {"type": "integer", "minimum": 1}, "effective_date": _DATE}, ("rule_id", "amount_minor", "effective_date")),
        "scope": "own_write", "run": _update_subscription_price,
    },
    "get_future_cashflow": {
        "description": "Project fixed payments, subscriptions, and dated one-off planned income or expenses by date without turning plans into actual transactions.",
        "inputSchema": _object({"start_date": _DATE, "end_date": _DATE}, ("start_date", "end_date")),
        "scope": "own_read", "run": _future_cashflow,
    },
    "create_cashflow_plan": {
        "description": "Record one dated expected income or expense for the future cash-flow calendar. This does not create a transaction or change actual totals.",
        "inputSchema": _object({"name": _STRING, "kind": {"type": "string", "enum": ["income", "expense"]}, "amount_minor": {"type": "integer", "minimum": 1}, "expected_date": _DATE, "category_id": {"type": ["string", "null"]}, "merchant": {"type": ["string", "null"]}}, ("name", "kind", "amount_minor", "expected_date")),
        "scope": "own_write", "run": _create_cashflow_plan,
    },
    "list_cashflow_plans": {
        "description": "List dated expected income and expense items without mixing them into actual transactions.",
        "inputSchema": _object({"start_date": _DATE, "end_date": _DATE}),
        "scope": "own_read", "run": _list_cashflow_plans,
    },
    "cancel_cashflow_plan": {
        "description": "Cancel a dated expected cash-flow item without changing transactions or actual totals.",
        "inputSchema": _object({"plan_id": _STRING}, ("plan_id",)),
        "scope": "own_write", "run": _cancel_cashflow_plan,
    },
    "get_reminder_settings": {
        "description": "Read the current ledger's four reminder thresholds and enabled categories.",
        "inputSchema": _object({}), "scope": "own_read", "run": _get_reminder_settings,
    },
    "set_reminder_settings": {
        "description": "Configure reminder categories and thresholds for the current ledger.",
        "inputSchema": _object({
            "enabled_kinds": {"type": "array", "items": _KIND, "uniqueItems": True},
            "budget_low_percent": {"type": "integer", "minimum": 0, "maximum": 100},
            "anomaly_min_samples": {"type": "integer", "minimum": 2, "maximum": 100},
            "anomaly_multiplier_milli": {"type": "integer", "minimum": 1000, "maximum": 10000},
            "goal_risk_percent": {"type": "integer", "minimum": 1, "maximum": 100},
            "renewal_notice_days": {"type": "integer", "minimum": 1, "maximum": 90},
        }),
        "scope": "own_write", "run": _set_reminder_settings,
    },
    "refresh_reminders": {
        "description": "Compute and persist at most one reminder per ledger, reason, and month for low budget, abnormal spending with sufficient samples, saving-goal risk, and upcoming renewals.",
        "inputSchema": _object({}), "scope": "own_write", "run": _refresh_reminders,
    },
    "list_reminders": {
        "description": "List reminders for one month in the current ledger; use refresh_reminders first to calculate newly due reminders.",
        "inputSchema": _object({"period": _PERIOD}), "scope": "own_read", "run": _list_reminders,
    },
    "mark_reminder_read": {
        "description": "Mark one reminder in the current ledger as read.",
        "inputSchema": _object({"reminder_id": _STRING}, ("reminder_id",)),
        "scope": "own_write", "run": _mark_reminder_read,
    },
}
