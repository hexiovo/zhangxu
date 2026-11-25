from __future__ import annotations

import base64
import binascii
import csv
import hashlib
import io
import json
import re
import sqlite3
import unicodedata
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from . import store
from .contract import BusinessError


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS maintenance_import_previews (
    ledger_id TEXT NOT NULL,
    preview_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    file_name TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    preview_sha256 TEXT NOT NULL,
    mapping_json TEXT NOT NULL,
    date_format TEXT NOT NULL,
    amount_unit TEXT NOT NULL,
    duplicate_policy TEXT NOT NULL CHECK (duplicate_policy IN ('skip', 'include')),
    rows_json TEXT NOT NULL,
    errors_json TEXT NOT NULL,
    duplicates_json TEXT NOT NULL,
    ledger_fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    PRIMARY KEY (ledger_id, preview_id)
);

CREATE TABLE IF NOT EXISTS maintenance_reconciliation_previews (
    ledger_id TEXT NOT NULL,
    preview_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    file_name TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    preview_sha256 TEXT NOT NULL,
    mapping_json TEXT NOT NULL,
    date_format TEXT NOT NULL,
    amount_unit TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    payment_account_id TEXT,
    payment_account_version INTEGER,
    items_json TEXT NOT NULL,
    invalid_rows_json TEXT NOT NULL,
    ledger_fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    PRIMARY KEY (ledger_id, preview_id)
);

CREATE TABLE IF NOT EXISTS maintenance_csv_mapping_templates (
    ledger_id TEXT NOT NULL,
    template_id TEXT NOT NULL,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    headers_json TEXT NOT NULL,
    header_signature TEXT NOT NULL,
    mapping_json TEXT NOT NULL,
    date_format TEXT NOT NULL CHECK (date_format IN ('iso', 'slash', 'compact')),
    amount_unit TEXT NOT NULL CHECK (amount_unit IN ('yuan', 'fen')),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, template_id),
    UNIQUE (ledger_id, name_key)
);

CREATE TABLE IF NOT EXISTS maintenance_transaction_files (
    ledger_id TEXT NOT NULL,
    attachment_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    file_name TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    content BLOB,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    removed_at TEXT,
    PRIMARY KEY (ledger_id, attachment_id),
    FOREIGN KEY (ledger_id, transaction_id) REFERENCES ledger_transactions(ledger_id, transaction_id)
);

CREATE TABLE IF NOT EXISTS maintenance_redaction_profiles (
    ledger_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    merchant_policy TEXT NOT NULL CHECK (merchant_policy IN ('keep', 'mask', 'omit')),
    note_policy TEXT NOT NULL CHECK (note_policy IN ('keep', 'mask', 'omit')),
    date_precision TEXT NOT NULL CHECK (date_precision IN ('day', 'month', 'year', 'omit')),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, profile_id),
    UNIQUE (ledger_id, name_key)
);

CREATE TABLE IF NOT EXISTS maintenance_access_events (
    ledger_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('incoming', 'outgoing')),
    host_label TEXT NOT NULL,
    source TEXT NOT NULL,
    permission TEXT,
    action_name TEXT NOT NULL,
    resource_kind TEXT,
    resource_id TEXT,
    range_start TEXT,
    range_end TEXT,
    outcome TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, event_id)
);

CREATE TABLE IF NOT EXISTS maintenance_repair_events (
    ledger_id TEXT NOT NULL,
    repair_id TEXT NOT NULL,
    issue_id TEXT NOT NULL,
    issue_fingerprint TEXT NOT NULL,
    action TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    request_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, repair_id),
    UNIQUE (ledger_id, actor_id, request_id)
);

CREATE TABLE IF NOT EXISTS maintenance_backup_drills (
    ledger_id TEXT NOT NULL,
    drill_id TEXT NOT NULL,
    backup_id TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, drill_id)
);

CREATE INDEX IF NOT EXISTS idx_maintenance_files_transaction
    ON maintenance_transaction_files(ledger_id, transaction_id, created_at);
CREATE INDEX IF NOT EXISTS idx_maintenance_access_period
    ON maintenance_access_events(ledger_id, created_at, direction);
CREATE INDEX IF NOT EXISTS idx_maintenance_import_expiry
    ON maintenance_import_previews(ledger_id, expires_at, consumed_at);
CREATE INDEX IF NOT EXISTS idx_maintenance_reconciliation_expiry
    ON maintenance_reconciliation_previews(ledger_id, expires_at, consumed_at);
CREATE INDEX IF NOT EXISTS idx_maintenance_csv_templates_name
    ON maintenance_csv_mapping_templates(ledger_id, name_key);
"""


_DATE = {"type": "string", "format": "date"}
_STRING = {"type": "string", "minLength": 1}
_NULLABLE_STRING = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_AMOUNT = {"type": "integer", "minimum": 1}
_CONFIRMED = {"type": "boolean", "enum": [True]}


def _object(properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


def _fail(status: str, message: str, details: dict | None = None, code: str | None = None) -> None:
    raise BusinessError(status, message, details, code)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _id() -> str:
    return str(uuid.uuid4())


def _text(value: object, field: str, maximum: int = 200) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        _fail("invalid_input", f"{field} must be a non-empty string no longer than {maximum} characters", {"field": field})
    return value.strip()


def _date(value: object, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        _fail("invalid_input", f"{field} must use YYYY-MM-DD", {"field": field})
    try:
        date.fromisoformat(value)
    except ValueError:
        _fail("invalid_input", f"{field} is not a valid date", {"field": field})
    return value


def _write(ctx: dict) -> None:
    if ctx["cross_account"]:
        _fail("forbidden", "Cross-account writes are not allowed")
    if not ctx["request_id"]:
        _fail("invalid_input", "A request_id is required for writes")


def _own_read(ctx: dict) -> None:
    if ctx["cross_account"]:
        _fail("forbidden", "This action is available only in the caller's own ledger")


def _require_web(ctx: dict) -> None:
    if ctx.get("source") != "web":
        _fail("forbidden", "This action requires the trusted local web session")


def _ledger_info(ctx: dict) -> dict:
    return {"ledger_id": ctx["ledger_id"], "account_id": ctx["account_id"], "account_name": ctx["account_name"]}


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256(encoded)


def _decode_base64(value: object, field: str, maximum: int) -> bytes:
    if not isinstance(value, str):
        _fail("invalid_input", f"{field} must be base64 text", {"field": field})
    try:
        content = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        _fail("invalid_input", f"{field} is not valid base64", {"field": field})
    if not content or len(content) > maximum:
        _fail("invalid_input", f"{field} must decode to between 1 and {maximum} bytes", {"field": field})
    return content


def _required_transaction(conn: sqlite3.Connection, ledger_id: str, transaction_id: object, expected_version: object | None = None) -> sqlite3.Row:
    transaction_id = _text(transaction_id, "transaction_id")
    row = conn.execute(
        "SELECT transaction_id, version, occurred_on, kind, amount_minor, confirmation_status, voided_at "
        "FROM ledger_transactions WHERE ledger_id = ? AND transaction_id = ?",
        (ledger_id, transaction_id),
    ).fetchone()
    if row is None:
        _fail("not_found", "Transaction not found in this ledger", {"transaction_id": transaction_id})
    if expected_version is not None:
        if type(expected_version) is not int or expected_version < 1:
            _fail("invalid_input", "expected_version must be a positive integer")
        if row["version"] != expected_version:
            _fail("conflict", "The transaction changed after it was read", {
                "transaction_id": transaction_id, "expected_version": expected_version, "actual_version": row["version"],
            })
    if row["voided_at"] is not None:
        _fail("conflict", "A voided transaction cannot be linked to a file")
    return row


def _access_log(
    conn: sqlite3.Connection,
    ctx: dict,
    action_name: str,
    resource_kind: str | None = None,
    resource_id: str | None = None,
    outcome: str = "success",
    *,
    permission: str | None = None,
    range_start: str | None = None,
    range_end: str | None = None,
    direction: str = "incoming",
    host_label: str | None = None,
) -> str:
    event_id = _id()
    source = ctx.get("source", "unknown")
    host_label = host_label or ctx.get("host_label") or ("local web session" if source == "web" else source)
    permission = permission or ctx.get("host_permission")
    if resource_id is not None:
        try:
            resource_id = str(uuid.UUID(resource_id))
        except (ValueError, TypeError, AttributeError):
            resource_id = None
    conn.execute(
        "INSERT INTO maintenance_access_events(ledger_id, event_id, principal_id, direction, host_label, source, "
        "permission, action_name, resource_kind, resource_id, range_start, range_end, outcome, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ctx["ledger_id"], event_id, ctx["principal_id"], direction, host_label, source, permission,
         action_name, resource_kind, resource_id, range_start, range_end, outcome, _now()),
    )
    return event_id


def record_access_event(
    conn: sqlite3.Connection,
    ctx: dict,
    action_name: str,
    resource_kind: str | None = None,
    resource_id: str | None = None,
    outcome: str = "success",
    **details: object,
) -> str:
    """Record a metadata-only access event from the authenticated dispatcher."""
    return _access_log(
        conn, ctx, action_name, resource_kind, resource_id, outcome,
        permission=details.get("permission") if isinstance(details.get("permission"), str) else None,
        range_start=(details.get("range_start") if isinstance(details.get("range_start"), str)
                     else ctx.get("requested_start") if isinstance(ctx.get("requested_start"), str) else None),
        range_end=(details.get("range_end") if isinstance(details.get("range_end"), str)
                   else ctx.get("requested_end") if isinstance(ctx.get("requested_end"), str) else None),
    )


def _ledger_fingerprint(conn: sqlite3.Connection, ledger_id: str) -> str:
    rows = conn.execute(
        "SELECT transaction_id, version, occurred_on, kind, amount_minor, merchant, confirmation_status, voided_at "
        "FROM ledger_transactions WHERE ledger_id = ? ORDER BY transaction_id",
        (ledger_id,),
    ).fetchall()
    return _canonical_hash([tuple(row) for row in rows])


def _csv_rows(content: bytes) -> tuple[list[str], list[tuple[int, list[str]]], str | None]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = content.decode("gb18030")
        except UnicodeDecodeError:
            _fail("invalid_input", "CSV 须使用 UTF-8 或 GB18030 编码。")
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    table = []
    try:
        while True:
            line = reader.line_num + 1
            row = next(reader, None)
            if row is None:
                break
            if any(cell.strip() for cell in row):
                table.append((line, row))
            if len(table) > 200:
                _fail("invalid_input", "CSV 内容超过单次对账预览上限。")
    except csv.Error:
        _fail("invalid_input", "CSV 中存在无法解析的行。")
    if not table:
        _fail("invalid_input", "CSV 中找不到表头。")
    first = 0
    provider = None
    for index, (_, row) in enumerate(table[:80]):
        title = row[0].strip() if len(row) == 1 else ""
        if ("微信支付" in title or "微信账单" in title) and ("账单" in title or "交易" in title):
            provider = "wechat"
        if "支付宝" in title and ("账单" in title or "交易" in title):
            provider = "alipay"
        if {"occurred_on", "kind", "amount"} <= _suggest_column_mapping([cell.strip() for cell in row]).keys():
            first = index
            break
        if len(row) > 1 and provider is None:
            break
    headers = [header.strip() for header in table[first][1]]
    if not headers or not all(headers) or len(headers) != len(set(headers)):
        _fail("invalid_input", "CSV 表头应包含非空且互不重复的列名。")
    rows = table[first + 1:]
    if provider:
        rows = [(line, row) for line, row in rows if len(row) != 1 or not re.search(r"^[-=—]+$|^合计", row[0].strip())]
    if len(rows) > 100:
        _fail("invalid_input", "一次预览最多处理 100 条数据行。")
    return headers, rows, provider


def _import_date(value: str, date_format: str) -> str:
    try:
        if date_format == "iso":
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?", value):
                return datetime.fromisoformat(value).date().isoformat()
            parsed = date.fromisoformat(value)
        elif date_format == "slash":
            parsed = datetime.strptime(value, "%Y/%m/%d").date()
        else:
            parsed = datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        raise ValueError("date must match the selected format") from None
    normalized = parsed.isoformat()
    if date_format == "iso" and value != normalized:
        raise ValueError("date must match YYYY-MM-DD")
    return normalized


def _import_amount(value: str, amount_unit: str) -> int:
    if amount_unit == "fen":
        if not re.fullmatch(r"\d+", value):
            raise ValueError("amount must be a non-negative integer number of fen")
        amount = int(value)
    else:
        try:
            parsed = Decimal(value.removeprefix("¥").removeprefix("￥").replace(",", ""))
        except InvalidOperation:
            raise ValueError("amount must be a number in yuan") from None
        if not parsed.is_finite() or parsed <= 0 or parsed * 100 != (parsed * 100).to_integral_value():
            raise ValueError("amount in yuan must be positive and no more precise than one fen")
        amount = int(parsed * 100)
    if amount <= 0 or amount > 2**63 - 1:
        raise ValueError("amount is outside the supported integer-fen range")
    return amount


def _kind(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    if normalized in {"expense", "支出", "支"}:
        return "expense"
    if normalized in {"income", "收入", "收"}:
        return "income"
    raise ValueError("kind must be income/收入 or expense/支出")


def _header_signature(headers: list[str]) -> str:
    return _canonical_hash(headers)


def _suggest_column_mapping(headers: list[str]) -> dict:
    aliases = {
        "occurred_on": ("交易时间", "交易日期", "交易创建时间", "发生时间", "日期", "date"),
        "charged_on": ("入账日期", "记账日期", "扣款日期", "入账时间"),
        "kind": ("收/支", "收支", "收支类型", "交易方向", "direction", "kind"),
        "amount": ("金额(元)", "金额", "交易金额", "人民币金额", "amount"),
        "merchant": ("交易对方", "商户名称", "商品说明", "merchant"),
        "note": ("备注", "摘要", "note"), "external_id": ("交易单号", "交易订单号", "支付订单号", "交易流水号", "订单号"),
        "payment_status": ("当前状态", "交易状态"),
    }
    names = {unicodedata.normalize("NFKC", name).casefold().replace(" ", ""): name for name in headers}
    return {field: next(names[alias] for alias in options if alias in names)
            for field, options in aliases.items() if any(alias in names for alias in options)}


def _parse_statement(conn, ctx, content, headers, rows, mapping, date_format, amount_unit, provider):
    from . import bookkeeping

    if provider is not None and (not isinstance(provider, str) or provider not in bookkeeping._PROVIDERS):
        _fail("invalid_input", "请选择有效的账单来源。")
    valid, invalid = _parse_import_rows(headers, rows, mapping, date_format, amount_unit)
    digest = _sha256(content)
    for row in valid:
        external_id = row.pop("external_id", None)
        if provider is not None and external_id:
            source = bookkeeping._source({"provider": provider, "external_id": external_id})
        else:
            source = ("csv_file", _sha256(f"{digest}:{row['source_line']}".encode("ascii")))
        row["source_ref"] = source
    return valid, invalid


def _statement_source(conn, ctx, row):
    from . import bookkeeping

    return bookkeeping._known_source(conn, ctx["ledger_id"], row.get("source_ref"))


def _claim_statement_source(conn, ctx, row, transaction_id):
    source = row.get("source_ref")
    if source:
        conn.execute("INSERT INTO bookkeeping_sources VALUES (?,?,?,?,?)",
                     (ctx["ledger_id"], *source, "add_transactions", transaction_id))


def _validated_column_mapping(headers: list[str], mapping: object) -> dict:
    if not isinstance(mapping, dict) or set(mapping) - {"occurred_on", "charged_on", "kind", "amount", "merchant", "note", "external_id", "payment_status"}:
        _fail("invalid_input", "column_mapping contains an unsupported field")
    if any(not isinstance(mapping.get(key), str) or mapping[key] not in headers for key in ("occurred_on", "kind", "amount")):
        _fail("invalid_input", "column_mapping must map occurred_on, kind, and amount to existing CSV columns")
    for field in ("charged_on", "merchant", "note", "external_id", "payment_status"):
        if mapping.get(field) is not None and mapping[field] not in headers:
            _fail("invalid_input", f"column_mapping.{field} must name an existing CSV column")
    return mapping


def _parse_import_rows(
    headers: list[str], source_rows: list[tuple[int, list[str]]], mapping: dict,
    date_format: str, amount_unit: str,
) -> tuple[list[dict], list[dict]]:
    if date_format not in ("iso", "slash", "compact") or amount_unit not in ("yuan", "fen"):
        _fail("invalid_input", "date_format or amount_unit is not supported")
    column_index = {header: index for index, header in enumerate(headers)}
    valid_rows = []
    invalid_rows = []
    for line, row in source_rows:
        if len(row) != len(headers):
            invalid_rows.append({"line": line, "field": "row", "error": "row has a different number of columns than the header"})
            continue
        field = "occurred_on"
        try:
            item = {"occurred_on": _import_date(row[column_index[mapping["occurred_on"]]].strip(), date_format)}
            if mapping.get("charged_on"):
                charged_on = row[column_index[mapping["charged_on"]]].strip()
                if charged_on:
                    field = "charged_on"
                    item["charged_on"] = _import_date(charged_on, date_format)
            if mapping.get("payment_status"):
                field = "payment_status"
                status = row[column_index[mapping[field]]].strip()
                if status not in {"支付成功", "交易成功", "还款成功", "已完成", "已转账", "已收款", "对方已收钱", "已支付", "已到账", "成功", "入账成功", "SUCCESS"}:
                    raise ValueError("payment status is pending, unrecognized or requires separate review")
            field = "kind"
            item["kind"] = _kind(row[column_index[mapping["kind"]]])
            field = "amount"
            item["amount_minor"] = _import_amount(row[column_index[mapping["amount"]]].strip(), amount_unit)
            for optional_field in ("merchant", "note", "external_id"):
                column = mapping.get(optional_field)
                if column:
                    item[optional_field] = row[column_index[column]].strip() or None
        except ValueError as error:
            invalid_rows.append({"line": line, "field": field, "error": str(error)})
            continue
        item["source_line"] = line
        valid_rows.append(item)
    return valid_rows, invalid_rows


def _duplicate_candidates(conn: sqlite3.Connection, ledger_id: str, record: dict) -> list[dict]:
    rows = conn.execute(
        "SELECT transaction_id, occurred_on, kind, amount_minor, merchant FROM ledger_transactions "
        "WHERE ledger_id = ? AND occurred_on = ? AND kind = ? AND amount_minor = ? AND voided_at IS NULL "
        "ORDER BY transaction_id LIMIT 10",
        (ledger_id, record["occurred_on"], record["kind"], record["amount_minor"]),
    ).fetchall()
    merchant = unicodedata.normalize("NFKC", (record.get("merchant") or "")).casefold().strip()
    return [
        {"transaction_id": row["transaction_id"], "merchant": row["merchant"],
         "match": "exact" if not merchant or not row["merchant"] or merchant == unicodedata.normalize("NFKC", row["merchant"]).casefold().strip() else "possible"}
        for row in rows
    ]


def _transaction_import_preview(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _write(ctx)
    content = _decode_base64(params.get("content_base64"), "content_base64", 5 * 1024 * 1024)
    file_name = _safe_file_name(params.get("file_name"))
    headers, source_rows, detected_provider = _csv_rows(content)
    source_sha256 = _sha256(content)
    mapping = params.get("column_mapping")
    if mapping is None:
        return {"file_name": file_name, "source_sha256": source_sha256, "columns": headers, "suggested_mapping": _suggest_column_mapping(headers), "suggested_provider": detected_provider,
                "header_signature": _header_signature(headers), "row_count": len(source_rows), "mapping_required": True,
                "ledger": _ledger_info(ctx)}
    mapping = _validated_column_mapping(headers, mapping)
    date_format = params.get("date_format", "iso")
    amount_unit = params.get("amount_unit", "yuan")
    duplicate_policy = params.get("duplicate_policy")
    if duplicate_policy not in ("skip", "include"):
        _fail("needs_clarification", "Choose whether suspected duplicate rows should be skipped or included")
    valid_rows, invalid_rows = _parse_statement(conn, ctx, content, headers, source_rows, mapping, date_format, amount_unit, params.get("provider") or detected_provider)
    duplicates = []
    source_keys = set()
    for item in valid_rows:
        line = item["source_line"]
        candidates = _duplicate_candidates(conn, ctx["ledger_id"], item)
        item["duplicate_candidates"] = candidates
        known = _statement_source(conn, ctx, item)
        source = item["source_ref"]
        if known or source in source_keys:
            duplicates.append({"line": line, "kind": "same_source",
                               "candidate_transaction_ids": [known["target_id"]] if known and known["action"] == "add_transactions" else []})
        elif candidates:
            duplicates.append({"line": line, "kind": "duplicate" if any(x["match"] == "exact" for x in candidates) else "possible",
                               "candidate_transaction_ids": [x["transaction_id"] for x in candidates]})
        source_keys.add(source)
    excluded = sum(item['kind'] == 'same_source' or duplicate_policy == 'skip' for item in duplicates)

    ledger_fingerprint = _ledger_fingerprint(conn, ctx["ledger_id"])
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(timespec="seconds").replace("+00:00", "Z")
    preview_payload = {
        "ledger_id": ctx["ledger_id"], "principal_id": ctx["principal_id"],
        "file_name": file_name, "source_sha256": source_sha256, "mapping": mapping,
        "date_format": date_format, "amount_unit": amount_unit, "duplicate_policy": duplicate_policy,
        "rows": valid_rows, "errors": invalid_rows, "duplicates": duplicates,
        "ledger_fingerprint": ledger_fingerprint, "expires_at": expires_at,
    }
    preview_sha256 = _canonical_hash(preview_payload)
    preview_id = _id()
    conn.execute(
        "DELETE FROM maintenance_import_previews WHERE ledger_id = ? AND (expires_at <= ? OR consumed_at IS NOT NULL)",
        (ctx["ledger_id"], _now()),
    )
    conn.execute(
        "INSERT INTO maintenance_import_previews(ledger_id, preview_id, principal_id, file_name, source_sha256, preview_sha256, "
        "mapping_json, date_format, amount_unit, duplicate_policy, rows_json, errors_json, duplicates_json, ledger_fingerprint, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ctx["ledger_id"], preview_id, ctx["principal_id"], file_name, source_sha256, preview_sha256,
         json.dumps(mapping, ensure_ascii=False, sort_keys=True), date_format, amount_unit, duplicate_policy,
         json.dumps(valid_rows, ensure_ascii=False), json.dumps(invalid_rows, ensure_ascii=False),
         json.dumps(duplicates, ensure_ascii=False), ledger_fingerprint, _now(), expires_at),
    )
    return {
        "preview_id": preview_id, "preview_sha256": preview_sha256, "source_sha256": source_sha256,
        "expires_at": expires_at, "file_name": file_name, "valid_count": len(valid_rows),
        "insert_count": len(valid_rows) - excluded,
        "skipped_duplicate_count": excluded,
        "invalid_count": len(invalid_rows), "duplicate_count": len(duplicates),
        "duplicate_policy": duplicate_policy, "invalid_rows": invalid_rows, "duplicates": duplicates,
        "header_signature": _header_signature(headers),
        "rows": [{key: value for key, value in row.items() if key != "source_ref"}
                 for row in valid_rows], "ledger": _ledger_info(ctx),
    }


def _commit_transaction_import(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _write(ctx)
    if params.get("confirmed") is not True:
        _fail("needs_clarification", "Explicit confirmation is required before importing transactions")
    preview_id = _text(params.get("preview_id"), "preview_id")
    preview_sha256 = _text(params.get("preview_sha256"), "preview_sha256")
    preview = conn.execute(
        "SELECT * FROM maintenance_import_previews WHERE ledger_id = ? AND preview_id = ? AND principal_id = ?",
        (ctx["ledger_id"], preview_id, ctx["principal_id"]),
    ).fetchone()
    if preview is None:
        _fail("not_found", "Import preview not found in this ledger")
    if preview["consumed_at"]:
        _fail("conflict", "This import preview has already been used")
    if preview["expires_at"] <= _now():
        _fail("conflict", "Import preview expired; create a new preview")
    if preview["preview_sha256"] != preview_sha256:
        _fail("conflict", "Import preview hash does not match")
    if preview["ledger_fingerprint"] != _ledger_fingerprint(conn, ctx["ledger_id"]):
        _fail("conflict", "Ledger transactions changed after the preview; review a new preview")
    rows = json.loads(preview["rows_json"])
    duplicates = {item["line"] for item in json.loads(preview["duplicates_json"])
                  if item["kind"] == "same_source" or preview["duplicate_policy"] == "skip"}
    rows = [item for item in rows if item["source_line"] not in duplicates]
    if any(_statement_source(conn, ctx, row) for row in rows):
        _fail("conflict", "已存在同一账单来源，请重新预览。", code="statement_source_changed")
    if not rows:
        _fail("needs_clarification", "There are no rows eligible for import under this preview")

    from . import ledger

    records = [ledger._record_input(conn, ctx, {key: value for key, value in row.items()
                                                if key in {"occurred_on", "charged_on", "kind", "amount_minor", "merchant", "note"}})
               for row in rows]
    business_dates = [value for record in records for value in (record["occurred_on"], record["charged_on"]) if value]
    ledger._assert_period_open(conn, ctx, *business_dates, action_name="commit_transaction_import")
    transaction_ids = [ledger._new_id() for _ in records]
    effects = []
    for row, record, transaction_id in zip(rows, records, transaction_ids):
        _claim_statement_source(conn, ctx, row, transaction_id)
        ledger._insert_transaction(conn, ctx, record, transaction_id)
        snapshot = ledger._snapshot(conn, ctx["ledger_id"], transaction_id)
        ledger._record_revision(conn, ctx, transaction_id, 1, "create", None, snapshot)
        effects.append({"transaction_id": transaction_id, "before": None, "after": snapshot, "after_version": 1})
    ledger._record_operation(conn, ctx, "commit_transaction_import", effects)
    ledger._record_reference(conn, ctx, transaction_ids)
    inserted = [ledger._transaction_payload(conn, ctx["ledger_id"], transaction_id) for transaction_id in transaction_ids]
    conn.execute(
        "UPDATE maintenance_import_previews SET consumed_at = ?, rows_json = '[]', errors_json = '[]', duplicates_json = '[]' "
        "WHERE ledger_id = ? AND preview_id = ?",
        (_now(), ctx["ledger_id"], preview_id),
    )
    return {"preview_id": preview_id, "imported_count": len(inserted), "skipped_duplicate_count": len(duplicates),
            "transactions": inserted, "ledger": _ledger_info(ctx)}


def _csv_mapping_template_payload(row: sqlite3.Row) -> dict:
    return {
        "template_id": row["template_id"], "name": row["name"],
        "headers": json.loads(row["headers_json"]), "header_signature": row["header_signature"],
        "column_mapping": json.loads(row["mapping_json"]), "date_format": row["date_format"],
        "amount_unit": row["amount_unit"], "version": row["version"], "updated_at": row["updated_at"],
    }


def _list_csv_mapping_templates(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_read(ctx)
    rows = conn.execute(
        "SELECT template_id, name, headers_json, header_signature, mapping_json, date_format, amount_unit, version, updated_at "
        "FROM maintenance_csv_mapping_templates WHERE ledger_id = ? ORDER BY name_key",
        (ctx["ledger_id"],),
    ).fetchall()
    return {"templates": [_csv_mapping_template_payload(row) for row in rows], "ledger": _ledger_info(ctx)}


def _upsert_csv_mapping_template(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _write(ctx)
    name = _text(params.get("name"), "name", 80)
    name_key = unicodedata.normalize("NFKC", name).casefold()
    raw_headers = params.get("headers")
    if not isinstance(raw_headers, list) or not raw_headers or any(not isinstance(header, str) or not header.strip() for header in raw_headers):
        _fail("invalid_input", "headers must be a non-empty array of column names")
    headers = [header.strip() for header in raw_headers]
    if len(headers) != len(set(headers)):
        _fail("invalid_input", "CSV column names must be unique")
    mapping = _validated_column_mapping(headers, params.get("column_mapping"))
    date_format = params.get("date_format")
    amount_unit = params.get("amount_unit")
    if date_format not in ("iso", "slash", "compact") or amount_unit not in ("yuan", "fen"):
        _fail("invalid_input", "date_format or amount_unit is not supported")

    template_id = params.get("template_id")
    if template_id is not None:
        template_id = _text(template_id, "template_id")
    name_owner = conn.execute(
        "SELECT template_id FROM maintenance_csv_mapping_templates WHERE ledger_id = ? AND name_key = ?",
        (ctx["ledger_id"], name_key),
    ).fetchone()
    if name_owner is not None and name_owner["template_id"] != template_id:
        _fail("conflict", "A CSV mapping template with this name already exists in this ledger")
    now = _now()
    if template_id is None:
        if params.get("expected_version") is not None:
            _fail("invalid_input", "expected_version requires template_id")
        template_id = _id()
        version = 1
        conn.execute(
            "INSERT INTO maintenance_csv_mapping_templates(ledger_id, template_id, name, name_key, headers_json, "
            "header_signature, mapping_json, date_format, amount_unit, version, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ctx["ledger_id"], template_id, name, name_key, json.dumps(headers, ensure_ascii=False),
             _header_signature(headers), json.dumps(mapping, ensure_ascii=False, sort_keys=True), date_format,
             amount_unit, version, ctx["principal_id"], now, now),
        )
    else:
        row = conn.execute(
            "SELECT version FROM maintenance_csv_mapping_templates WHERE ledger_id = ? AND template_id = ?",
            (ctx["ledger_id"], template_id),
        ).fetchone()
        if row is None:
            _fail("not_found", "CSV mapping template not found in this ledger")
        expected = params.get("expected_version")
        if type(expected) is not int or expected != row["version"]:
            _fail("conflict", "The CSV mapping template changed after it was read", {
                "expected_version": expected, "actual_version": row["version"],
            })
        version = row["version"] + 1
        conn.execute(
            "UPDATE maintenance_csv_mapping_templates SET name = ?, name_key = ?, headers_json = ?, header_signature = ?, "
            "mapping_json = ?, date_format = ?, amount_unit = ?, version = ?, updated_at = ? "
            "WHERE ledger_id = ? AND template_id = ?",
            (name, name_key, json.dumps(headers, ensure_ascii=False), _header_signature(headers),
             json.dumps(mapping, ensure_ascii=False, sort_keys=True), date_format, amount_unit, version, now,
             ctx["ledger_id"], template_id),
        )
    saved = conn.execute(
        "SELECT template_id, name, headers_json, header_signature, mapping_json, date_format, amount_unit, version, updated_at "
        "FROM maintenance_csv_mapping_templates WHERE ledger_id = ? AND template_id = ?",
        (ctx["ledger_id"], template_id),
    ).fetchone()
    return {"template": _csv_mapping_template_payload(saved), "ledger": _ledger_info(ctx)}


def _delete_csv_mapping_template(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _write(ctx)
    template_id = _text(params.get("template_id"), "template_id")
    row = conn.execute(
        "SELECT version FROM maintenance_csv_mapping_templates WHERE ledger_id = ? AND template_id = ?",
        (ctx["ledger_id"], template_id),
    ).fetchone()
    if row is None:
        _fail("not_found", "CSV mapping template not found in this ledger")
    expected = params.get("expected_version")
    if type(expected) is not int or expected != row["version"]:
        _fail("conflict", "The CSV mapping template changed after it was read", {
            "expected_version": expected, "actual_version": row["version"],
        })
    conn.execute(
        "DELETE FROM maintenance_csv_mapping_templates WHERE ledger_id = ? AND template_id = ?",
        (ctx["ledger_id"], template_id),
    )
    return {"deleted_template_id": template_id, "ledger": _ledger_info(ctx)}


def _reconciliation_fingerprint(conn: sqlite3.Connection, ledger_id: str, payment_account_id: str | None) -> str:
    dates = conn.execute(
        "SELECT transaction_id, charged_on FROM ledger_transaction_dates WHERE ledger_id = ? ORDER BY transaction_id",
        (ledger_id,),
    ).fetchall()
    links = conn.execute(
        "SELECT transaction_id, account_id, event_id FROM movement_transaction_accounts WHERE ledger_id = ? ORDER BY transaction_id",
        (ledger_id,),
    ).fetchall()
    account = None
    if payment_account_id is not None:
        row = conn.execute(
            "SELECT account_id, name, account_type, status, version FROM movement_accounts "
            "WHERE ledger_id = ? AND account_id = ?",
            (ledger_id, payment_account_id),
        ).fetchone()
        account = tuple(row) if row else None
    return _canonical_hash({
        "ledger": _ledger_fingerprint(conn, ledger_id),
        "dates": [tuple(row) for row in dates], "payment_links": [tuple(row) for row in links],
        "payment_account": account,
    })


def _reconciliation_ledger_rows(
    conn: sqlite3.Connection, ledger_id: str, start_date: str, end_date: str, date_basis: str,
) -> list[dict]:
    rows = conn.execute(
        "SELECT t.transaction_id, t.occurred_on, COALESCE(d.charged_on, t.occurred_on) AS charged_on, "
        "t.kind, t.amount_minor, t.merchant, t.confirmation_status, t.voided_at, a.account_id "
        "FROM ledger_transactions t "
        "LEFT JOIN ledger_transaction_dates d ON d.ledger_id = t.ledger_id AND d.transaction_id = t.transaction_id "
        "LEFT JOIN movement_transaction_accounts a ON a.ledger_id = t.ledger_id AND a.transaction_id = t.transaction_id "
        "WHERE t.ledger_id = ? AND t.voided_at IS NULL AND "
        "(t.occurred_on BETWEEN ? AND ? OR COALESCE(d.charged_on, t.occurred_on) BETWEEN ? AND ?) "
        "ORDER BY t.transaction_id",
        (ledger_id, start_date, end_date, start_date, end_date),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["comparison_date"] = row["charged_on"] if date_basis == "charged_on" else row["occurred_on"]
        item["alternate_date"] = row["occurred_on"] if date_basis == "charged_on" else row["charged_on"]
        item["in_date_window"] = start_date <= item["comparison_date"] <= end_date
        result.append(item)
    return result


def _reconciliation_merchant_matches(conn: sqlite3.Connection, ledger_id: str, source: str | None, current: str | None) -> bool:
    if not source or not current:
        return True
    from . import ledger

    return ledger._canonical_merchant(conn, ledger_id, source)[0] == ledger._canonical_merchant(conn, ledger_id, current)[0]


def _csv_reconciliation_data(row: dict) -> dict:
    return {
        "occurred_on": row["occurred_on"], "charged_on": row.get("charged_on"),
        "comparison_date": row["comparison_date"], "kind": row["kind"],
        "amount_minor": row["amount_minor"], "merchant": row.get("merchant"), "note": row.get("note"),
    }


def _ledger_reconciliation_data(row: dict) -> dict:
    return {
        "transaction_id": row["transaction_id"], "occurred_on": row["occurred_on"],
        "charged_on": row["charged_on"], "comparison_date": row["comparison_date"],
        "kind": row["kind"], "amount_minor": row["amount_minor"], "merchant": row["merchant"],
        "confirmation_status": row["confirmation_status"], "payment_account_id": row["account_id"],
    }


def _transaction_reconciliation_preview(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _write(ctx)
    content = _decode_base64(params.get("content_base64"), "content_base64", 5 * 1024 * 1024)
    file_name = _safe_file_name(params.get("file_name"))
    headers, source_rows, detected_provider = _csv_rows(content)
    mapping = _validated_column_mapping(headers, params.get("column_mapping"))
    date_format = params.get("date_format")
    amount_unit = params.get("amount_unit")
    start_date = _date(params.get("start_date"), "start_date")
    end_date = _date(params.get("end_date"), "end_date")
    if start_date > end_date:
        _fail("invalid_input", "start_date must not be after end_date")
    if date_format not in ("iso", "slash", "compact") or amount_unit not in ("yuan", "fen"):
        _fail("invalid_input", "date_format or amount_unit is not supported")

    payment_account_id = params.get("payment_account_id")
    payment_account = None
    if payment_account_id is not None:
        from . import movements

        payment_account = movements._account(conn, ctx["ledger_id"], payment_account_id)
    date_basis = "charged_on" if payment_account is not None else "occurred_on"
    source_sha256 = _sha256(content)
    valid_rows, invalid_rows = _parse_statement(conn, ctx, content, headers, source_rows, mapping, date_format, amount_unit, params.get("provider") or detected_provider)
    invalid_count = len(invalid_rows)
    out_of_range_count = 0
    in_range_rows = []
    for row in valid_rows:
        comparison_date = (row.get("charged_on") or row["occurred_on"]) if payment_account is not None else row["occurred_on"]
        row["comparison_date"] = comparison_date
        if not start_date <= comparison_date <= end_date:
            out_of_range_count += 1
            invalid_rows.append({"line": row["source_line"], "field": "date", "error": "date is outside the selected reconciliation window"})
        else:
            in_range_rows.append(row)

    ledger_rows = _reconciliation_ledger_rows(conn, ctx["ledger_id"], start_date, end_date, date_basis)
    selected_account_id = payment_account["account_id"] if payment_account is not None else None
    csv_groups: dict[tuple, list[dict]] = defaultdict(list)
    ledger_groups: dict[tuple, list[dict]] = defaultdict(list)
    account_conflict_groups: dict[tuple, list[dict]] = defaultdict(list)
    exact_source_rows = []
    source_keys = set()
    for row in in_range_rows:
        known = _statement_source(conn, ctx, row)
        source = row["source_ref"]
        if known or source in source_keys:
            exact_source_rows.append((row, known))
        else:
            csv_groups[(row["comparison_date"], row["kind"], row["amount_minor"])].append(row)
        source_keys.add(source)
    exact_ids = {known["target_id"] for _, known in exact_source_rows if known and known["action"] == "add_transactions"}
    for row in ledger_rows:
        if not row["in_date_window"] or row["transaction_id"] in exact_ids:
            continue
        key = (row["comparison_date"], row["kind"], row["amount_minor"])
        if selected_account_id is not None and row["account_id"] != selected_account_id:
            account_conflict_groups[key].append(row)
        else:
            ledger_groups[key].append(row)

    csv_items = []
    classified_lines = set()
    used_ledger_ids = set()
    review_ledger_ids = set()
    for row, known in exact_source_rows:
        current = next((item for item in ledger_rows if known and known["action"] == "add_transactions"
                        and item["transaction_id"] == known["target_id"]), None)
        status = "ambiguous"
        if current is not None:
            consistent = (current["kind"] == row["kind"] and current["amount_minor"] == row["amount_minor"]
                          and (selected_account_id is None or selected_account_id == current["account_id"]))
            status = "matched" if consistent else "amount_mismatch"
            used_ledger_ids.add(current["transaction_id"])
        csv_items.append({"status": status, "csv_line": row["source_line"],
                          "transaction_id": current["transaction_id"] if current else None,
                          "csv": _csv_reconciliation_data(row), "ledger": _ledger_reconciliation_data(current) if current else None,
                          "differences": [] if status == "matched" else ["same_source"]})
        classified_lines.add(row["source_line"])
    for key, source_group in csv_groups.items():
        eligible = ledger_groups.get(key, [])
        account_conflicts = account_conflict_groups.get(key, [])
        candidates = eligible + account_conflicts
        if not candidates:
            continue
        merchant_matches = len(source_group) == 1 and len(eligible) == 1 and _reconciliation_merchant_matches(
            conn, ctx["ledger_id"], source_group[0].get("merchant"), eligible[0].get("merchant"),
        )
        if (len(source_group) == 1 and len(eligible) == 1 and not account_conflicts
                and merchant_matches and eligible[0]["confirmation_status"] == "confirmed"):
            source = source_group[0]
            current = eligible[0]
            csv_items.append({
                "status": "matched", "csv_line": source["source_line"], "transaction_id": current["transaction_id"],
                "candidate_transaction_ids": [], "csv": _csv_reconciliation_data(source),
                "ledger": _ledger_reconciliation_data(current), "differences": [],
                "import_row": {key: value for key, value in source.items() if key in {"occurred_on", "charged_on", "kind", "amount_minor", "merchant", "note", "source_line"}},
            })
            classified_lines.add(source["source_line"])
            used_ledger_ids.add(current["transaction_id"])
            continue
        candidate_ids = sorted({item["transaction_id"] for item in candidates})
        differences = []
        if account_conflicts:
            differences.append("payment_account")
        if len(source_group) > 1 or len(candidates) > 1:
            differences.append("duplicate_candidates")
        if len(source_group) == 1 and len(eligible) == 1 and not merchant_matches:
            differences.append("merchant")
        if any(item["confirmation_status"] != "confirmed" for item in candidates):
            differences.append("confirmation_status")
        for source in source_group:
            csv_items.append({
                "status": "ambiguous", "csv_line": source["source_line"], "transaction_id": None,
                "candidate_transaction_ids": candidate_ids, "csv": _csv_reconciliation_data(source),
                "ledger": _ledger_reconciliation_data(candidates[0]) if len(candidates) == 1 else None,
                "differences": differences or ["multiple_candidates"],
                "import_row": {key: value for key, value in source.items() if key in {"occurred_on", "charged_on", "kind", "amount_minor", "merchant", "note", "source_line"}},
            })
            classified_lines.add(source["source_line"])
        review_ledger_ids.update(candidate_ids)

    remaining_rows = [row for row in in_range_rows if row["source_line"] not in classified_lines]
    remaining_csv_groups: dict[tuple, list[dict]] = defaultdict(list)
    remaining_ledger_groups: dict[tuple, list[dict]] = defaultdict(list)
    remaining_account_conflicts: dict[tuple, list[dict]] = defaultdict(list)
    for row in remaining_rows:
        remaining_csv_groups[(row["comparison_date"], row["kind"])].append(row)
    for row in ledger_rows:
        if not row["in_date_window"] or row["transaction_id"] in used_ledger_ids or row["transaction_id"] in review_ledger_ids:
            continue
        key = (row["comparison_date"], row["kind"])
        if selected_account_id is not None and row["account_id"] != selected_account_id:
            remaining_account_conflicts[key].append(row)
        else:
            remaining_ledger_groups[key].append(row)

    for key, source_group in remaining_csv_groups.items():
        eligible = remaining_ledger_groups.get(key, [])
        account_conflicts = remaining_account_conflicts.get(key, [])
        candidates = eligible + account_conflicts
        if not candidates:
            continue
        if len(source_group) == 1 and len(eligible) == 1 and not account_conflicts:
            source = source_group[0]
            current = eligible[0]
            if (source["amount_minor"] != current["amount_minor"]
                    and _reconciliation_merchant_matches(conn, ctx["ledger_id"], source.get("merchant"), current.get("merchant"))
                    and current["confirmation_status"] == "confirmed"):
                csv_items.append({
                    "status": "amount_mismatch", "csv_line": source["source_line"], "transaction_id": None,
                    "candidate_transaction_ids": [current["transaction_id"]], "csv": _csv_reconciliation_data(source),
                    "ledger": _ledger_reconciliation_data(current), "differences": ["amount_minor"],
                    "import_row": {key: value for key, value in source.items() if key in {"occurred_on", "charged_on", "kind", "amount_minor", "merchant", "note", "source_line"}},
                })
                classified_lines.add(source["source_line"])
                review_ledger_ids.add(current["transaction_id"])
                continue
        candidate_ids = sorted({item["transaction_id"] for item in candidates})
        for source in source_group:
            csv_items.append({
                "status": "ambiguous", "csv_line": source["source_line"], "transaction_id": None,
                "candidate_transaction_ids": candidate_ids, "csv": _csv_reconciliation_data(source),
                "ledger": _ledger_reconciliation_data(candidates[0]) if len(candidates) == 1 else None,
                "differences": ["payment_account"] if account_conflicts else ["multiple_candidates"],
                "import_row": {key: value for key, value in source.items() if key in {"occurred_on", "charged_on", "kind", "amount_minor", "merchant", "note", "source_line"}},
            })
            classified_lines.add(source["source_line"])
        review_ledger_ids.update(candidate_ids)

    remaining_rows = [row for row in in_range_rows if row["source_line"] not in classified_lines]
    for source in remaining_rows:
        alternate_candidates = [
            row for row in ledger_rows
            if row["alternate_date"] == source["comparison_date"] and row["kind"] == source["kind"]
            and row["comparison_date"] != source["comparison_date"]
            and (row["amount_minor"] == source["amount_minor"]
                 or _reconciliation_merchant_matches(conn, ctx["ledger_id"], source.get("merchant"), row.get("merchant")))
        ]
        if alternate_candidates:
            candidate_ids = sorted({row["transaction_id"] for row in alternate_candidates})
            csv_items.append({
                "status": "ambiguous", "csv_line": source["source_line"], "transaction_id": None,
                "candidate_transaction_ids": candidate_ids, "csv": _csv_reconciliation_data(source),
                "ledger": _ledger_reconciliation_data(alternate_candidates[0]) if len(candidate_ids) == 1 else None,
                "differences": ["date_basis"],
                "import_row": {key: value for key, value in source.items() if key in {"occurred_on", "charged_on", "kind", "amount_minor", "merchant", "note", "source_line"}},
            })
            review_ledger_ids.update(candidate_ids)
        else:
            csv_items.append({
                "status": "csv_only", "csv_line": source["source_line"], "transaction_id": None,
                "candidate_transaction_ids": [], "csv": _csv_reconciliation_data(source), "ledger": None,
                "differences": [],
                "import_row": {key: value for key, value in source.items() if key in {"occurred_on", "charged_on", "kind", "amount_minor", "merchant", "note", "source_line"}},
            })
    ledger_only_items = [
        {"status": "ledger_only", "csv_line": None, "transaction_id": row["transaction_id"],
         "candidate_transaction_ids": [], "csv": None, "ledger": _ledger_reconciliation_data(row), "differences": []}
        for row in ledger_rows if row["in_date_window"]
        and (selected_account_id is None or row["account_id"] == selected_account_id)
        and row["transaction_id"] not in used_ledger_ids and row["transaction_id"] not in review_ledger_ids
    ]
    items = sorted(csv_items, key=lambda item: item["csv_line"]) + sorted(
        ledger_only_items, key=lambda item: (item["ledger"]["comparison_date"], item["transaction_id"]),
    )
    summary = {
        "matched_count": sum(item["status"] == "matched" for item in items),
        "csv_only_count": sum(item["status"] == "csv_only" for item in items),
        "ledger_only_count": sum(item["status"] == "ledger_only" for item in items),
        "amount_mismatch_count": sum(item["status"] == "amount_mismatch" for item in items),
        "ambiguous_count": sum(item["status"] == "ambiguous" for item in items),
        "invalid_count": invalid_count, "out_of_range_count": out_of_range_count,
    }
    ledger_fingerprint = _reconciliation_fingerprint(conn, ctx["ledger_id"], selected_account_id)
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(timespec="seconds").replace("+00:00", "Z")
    preview_id = _id()
    preview_payload = {
        "ledger_id": ctx["ledger_id"], "principal_id": ctx["principal_id"], "file_name": file_name,
        "source_sha256": source_sha256, "header_signature": _header_signature(headers), "mapping": mapping,
        "date_format": date_format, "amount_unit": amount_unit, "start_date": start_date, "end_date": end_date,
        "payment_account_id": selected_account_id, "date_basis": date_basis, "summary": summary,
        "items": items, "invalid_rows": invalid_rows, "ledger_fingerprint": ledger_fingerprint,
        "expires_at": expires_at,
    }
    preview_sha256 = _canonical_hash(preview_payload)
    conn.execute(
        "DELETE FROM maintenance_reconciliation_previews WHERE ledger_id = ? AND "
        "((consumed_at IS NULL AND expires_at <= ?) OR (consumed_at IS NOT NULL AND consumed_at <= ?))",
        (ctx["ledger_id"], _now(), (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")),
    )
    conn.execute(
        "INSERT INTO maintenance_reconciliation_previews(ledger_id, preview_id, principal_id, file_name, source_sha256, "
        "preview_sha256, mapping_json, date_format, amount_unit, start_date, end_date, payment_account_id, "
        "payment_account_version, items_json, invalid_rows_json, ledger_fingerprint, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ctx["ledger_id"], preview_id, ctx["principal_id"], file_name, source_sha256, preview_sha256,
         json.dumps(mapping, ensure_ascii=False, sort_keys=True), date_format, amount_unit, start_date, end_date,
         selected_account_id, payment_account["version"] if payment_account is not None else None,
         json.dumps(items, ensure_ascii=False), json.dumps(invalid_rows, ensure_ascii=False), ledger_fingerprint,
         _now(), expires_at),
    )
    response_items = [{key: value for key, value in item.items() if key != "import_row"} for item in items]
    return {
        "preview_id": preview_id, "preview_sha256": preview_sha256, "expires_at": expires_at,
        "source_sha256": source_sha256, "header_signature": _header_signature(headers),
        "date_basis": date_basis, "scope": {"start_date": start_date, "end_date": end_date,
                                                "payment_account_id": selected_account_id,
                                                "payment_account_name": payment_account["name"] if payment_account is not None else None},
        "summary": summary, "items": response_items, "invalid_rows": invalid_rows,
        "review_url": f"{ctx['origin']}/#reconciliation={preview_id}" if ctx.get("origin") else None,
        "ledger": _ledger_info(ctx),
    }


def _commit_transaction_reconciliation(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _write(ctx)
    if params.get("confirmed") is not True:
        _fail("needs_clarification", "Explicit confirmation is required before importing reconciliation rows")
    preview_id = _text(params.get("preview_id"), "preview_id")
    preview_sha256 = _text(params.get("preview_sha256"), "preview_sha256")
    selected_lines = params.get("selected_csv_lines")
    if not isinstance(selected_lines, list) or not selected_lines or any(type(line) is not int or line < 2 for line in selected_lines):
        _fail("invalid_input", "selected_csv_lines must contain physical CSV line numbers starting at 2")
    if len(selected_lines) != len(set(selected_lines)):
        _fail("invalid_input", "selected_csv_lines must not contain duplicates")
    preview = conn.execute(
        "SELECT * FROM maintenance_reconciliation_previews WHERE ledger_id = ? AND preview_id = ? AND principal_id = ?",
        (ctx["ledger_id"], preview_id, ctx["principal_id"]),
    ).fetchone()
    if preview is None:
        _fail("not_found", "Reconciliation preview not found in this ledger")
    if preview["consumed_at"]:
        _fail("conflict", "This reconciliation preview has already been used")
    if preview["expires_at"] <= _now():
        _fail("conflict", "Reconciliation preview expired; create a new preview")
    if preview["preview_sha256"] != preview_sha256:
        _fail("conflict", "Reconciliation preview hash does not match")
    payment_account_id = preview["payment_account_id"]
    if preview["ledger_fingerprint"] != _reconciliation_fingerprint(conn, ctx["ledger_id"], payment_account_id):
        _fail("conflict", "Ledger or payment-account links changed after the preview; reconcile again")
    if payment_account_id is not None:
        from . import movements

        payment_account = movements._account(conn, ctx["ledger_id"], payment_account_id)
        if payment_account["version"] != preview["payment_account_version"]:
            _fail("conflict", "The payment account changed after the preview; reconcile again")

    items = json.loads(preview["items_json"])
    by_line = {item["csv_line"]: item for item in items if item["csv_line"] is not None}
    if any(line not in by_line or by_line[line]["status"] != "csv_only" for line in selected_lines):
        _fail("needs_clarification", "Only CSV-only rows from this preview may be imported", {
            "selected_csv_lines": selected_lines,
        })
    rows = [by_line[line]["import_row"] for line in selected_lines]
    if any(_statement_source(conn, ctx, row) for row in rows):
        _fail("conflict", "所选来源已入账，请重新对账。", code="reconciliation_source_changed")
    from . import ledger

    records = [ledger._record_input(conn, ctx, {key: value for key, value in row.items()
                                                if key in {"occurred_on", "charged_on", "kind", "amount_minor", "merchant", "note"}})
               for row in rows]
    business_dates = [value for record in records for value in (record["occurred_on"], record["charged_on"]) if value]
    ledger._assert_period_open(conn, ctx, *business_dates, action_name="commit_transaction_reconciliation")
    transaction_ids = [ledger._new_id() for _ in records]
    effects = []
    for row, record, transaction_id in zip(rows, records, transaction_ids):
        _claim_statement_source(conn, ctx, row, transaction_id)
        ledger._insert_transaction(conn, ctx, record, transaction_id)
        snapshot = ledger._snapshot(conn, ctx["ledger_id"], transaction_id)
        ledger._record_revision(conn, ctx, transaction_id, 1, "create", None, snapshot)
        effects.append({"transaction_id": transaction_id, "before": None, "after": snapshot, "after_version": 1})
    payment_account_result = None
    if payment_account_id is not None:
        from . import movements

        for transaction_id in transaction_ids:
            payment_account_result = movements._record_account_transaction(conn, ctx, {
                "transaction_id": transaction_id, "expected_version": 1, "account_id": payment_account_id,
            })["account"]
    ledger._record_operation(conn, ctx, "commit_transaction_reconciliation", effects)
    ledger._record_reference(conn, ctx, transaction_ids)
    transactions = [ledger._transaction_payload(conn, ctx["ledger_id"], transaction_id) for transaction_id in transaction_ids]
    result = {"preview_id": preview_id, "status": "committed", "imported_count": len(transactions),
              "selected_csv_lines": selected_lines, "transaction_ids": transaction_ids,
              "transactions": transactions, "payment_account": payment_account_result, "ledger": _ledger_info(ctx)}
    conn.execute(
        "UPDATE maintenance_reconciliation_previews SET consumed_at = ?, result_json=?, items_json = '[]', invalid_rows_json = '[]' "
        "WHERE ledger_id = ? AND preview_id = ?",
        (_now(), json.dumps(result, ensure_ascii=False), ctx["ledger_id"], preview_id),
    )
    return result


def _get_transaction_reconciliation(conn, ctx, params):
    preview_id = _text(params.get("preview_id"), "preview_id")
    row = conn.execute("SELECT * FROM maintenance_reconciliation_previews WHERE ledger_id=? AND principal_id=? AND preview_id=?",
                       (ctx["ledger_id"], ctx["principal_id"], preview_id)).fetchone()
    if row is None:
        _fail("not_found", "找不到本人账本的对账预览。", code="reconciliation_not_found")
    if row["consumed_at"]:
        return json.loads(row["result_json"]) if row["result_json"] else {"status": "committed", "preview_id": preview_id}
    if row["expires_at"] <= _now():
        _fail("conflict", "此对账预览已过期，请重新生成。", code="reconciliation_expired")
    items = [{key: value for key, value in item.items() if key != "import_row"} for item in json.loads(row["items_json"])]
    invalid = json.loads(row["invalid_rows_json"])
    counts = {status + "_count": sum(item["status"] == status for item in items)
              for status in ("matched", "csv_only", "ledger_only", "amount_mismatch", "ambiguous")}
    counts["out_of_range_count"] = sum(item["field"] == "date" for item in invalid)
    counts["invalid_count"] = len(invalid) - counts["out_of_range_count"]
    return {"status": "preview", "preview_id": preview_id, "preview_sha256": row["preview_sha256"],
            "expires_at": row["expires_at"], "date_basis": "charged_on" if row["payment_account_id"] else "occurred_on",
            "scope": {key: row[key] for key in ("start_date", "end_date", "payment_account_id")},
            "summary": counts, "items": items, "invalid_rows": invalid,
            "review_url": f"{ctx['origin']}/#reconciliation={preview_id}" if ctx.get("origin") else None}


def _safe_file_name(value: object) -> str:
    name = _text(value, "file_name", 255)
    if name in {".", ".."} or "/" in name or "\\" in name or any(ord(char) < 32 for char in name):
        _fail("invalid_input", "file_name must be a plain file name without a path")
    return name


def _attach_transaction_file(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _write(ctx)
    transaction = _required_transaction(conn, ctx["ledger_id"], params.get("transaction_id"), params.get("expected_version"))
    file_name = _safe_file_name(params.get("file_name"))
    mime_type = params.get("mime_type")
    if mime_type not in {"application/pdf", "image/jpeg", "image/png", "image/webp"}:
        _fail("invalid_input", "Only PDF, JPEG, PNG, and WebP attachments are supported")
    content = _decode_base64(params.get("content_base64"), "content_base64", 10 * 1024 * 1024)
    signatures = {
        "application/pdf": content.startswith(b"%PDF-"),
        "image/jpeg": content.startswith(b"\xff\xd8\xff"),
        "image/png": content.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/webp": len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP",
    }
    if not signatures[mime_type]:
        _fail("invalid_input", "Attachment content does not match the selected file type")
    attachment_id = _id()
    conn.execute(
        "INSERT INTO maintenance_transaction_files(ledger_id, attachment_id, transaction_id, file_name, mime_type, "
        "content_sha256, content, size_bytes, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ctx["ledger_id"], attachment_id, transaction["transaction_id"], file_name, mime_type,
         _sha256(content), sqlite3.Binary(content), len(content), ctx["principal_id"], _now()),
    )
    return {"attachment": {"attachment_id": attachment_id, "transaction_id": transaction["transaction_id"],
                           "file_name": file_name, "mime_type": mime_type, "size_bytes": len(content),
                           "sha256": _sha256(content), "version": 1}, "ledger": _ledger_info(ctx)}


def _list_transaction_files(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_read(ctx)
    transaction = _required_transaction(conn, ctx["ledger_id"], params.get("transaction_id"))
    rows = conn.execute(
        "SELECT attachment_id, file_name, mime_type, content_sha256, size_bytes, version, created_at "
        "FROM maintenance_transaction_files WHERE ledger_id = ? AND transaction_id = ? AND removed_at IS NULL "
        "ORDER BY created_at, attachment_id",
        (ctx["ledger_id"], transaction["transaction_id"]),
    ).fetchall()
    return {"transaction_id": transaction["transaction_id"], "attachments": [dict(row) for row in rows], "ledger": _ledger_info(ctx)}


def _get_transaction_attachment(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_read(ctx)
    _require_web(ctx)
    attachment_id = _text(params.get("attachment_id"), "attachment_id")
    row = conn.execute(
        "SELECT attachment_id, transaction_id, file_name, mime_type, content_sha256, content, size_bytes, version "
        "FROM maintenance_transaction_files WHERE ledger_id = ? AND attachment_id = ? AND removed_at IS NULL",
        (ctx["ledger_id"], attachment_id),
    ).fetchone()
    if row is None:
        _fail("not_found", "Attachment not found in this ledger")
    return {"attachment": {"attachment_id": row["attachment_id"], "transaction_id": row["transaction_id"],
                            "file_name": row["file_name"], "mime_type": row["mime_type"],
                            "sha256": row["content_sha256"], "size_bytes": row["size_bytes"],
                            "version": row["version"], "content_base64": base64.b64encode(row["content"]).decode("ascii")},
            "ledger": _ledger_info(ctx)}


def _remove_transaction_file(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _write(ctx)
    attachment_id = _text(params.get("attachment_id"), "attachment_id")
    row = conn.execute(
        "SELECT version, transaction_id FROM maintenance_transaction_files WHERE ledger_id = ? AND attachment_id = ? AND removed_at IS NULL",
        (ctx["ledger_id"], attachment_id),
    ).fetchone()
    if row is None:
        _fail("not_found", "Attachment not found in this ledger")
    expected = params.get("expected_version")
    if type(expected) is not int or row["version"] != expected:
        _fail("conflict", "The attachment changed after it was read", {"expected_version": expected, "actual_version": row["version"]})
    conn.execute(
        "UPDATE maintenance_transaction_files SET content = NULL, size_bytes = 0, removed_at = ?, version = version + 1 "
        "WHERE ledger_id = ? AND attachment_id = ?",
        (_now(), ctx["ledger_id"], attachment_id),
    )
    return {"attachment_id": attachment_id, "transaction_id": row["transaction_id"], "removed": True,
            "version": row["version"] + 1, "ledger": _ledger_info(ctx)}


def get_redaction_profile(conn: sqlite3.Connection, ledger_id: str, profile_id: str) -> dict | None:
    row = conn.execute(
        "SELECT profile_id, name, merchant_policy, note_policy, date_precision, version "
        "FROM maintenance_redaction_profiles WHERE ledger_id = ? AND profile_id = ?",
        (ledger_id, profile_id),
    ).fetchone()
    return dict(row) if row else None


def redact_export_rows(conn: sqlite3.Connection, ledger_id: str, profile_id: str, rows: list[dict]) -> list[dict]:
    profile = get_redaction_profile(conn, ledger_id, profile_id)
    if profile is None:
        _fail("not_found", "Redaction profile not found in this ledger", {"profile_id": profile_id})
    result = []
    for source in rows:
        row = dict(source)
        for field, policy in (("merchant", profile["merchant_policy"]), ("note", profile["note_policy"])):
            if policy == "mask":
                row[field] = "已隐藏" if row.get(field) else ""
            elif policy == "omit":
                row.pop(field, None)
        precision = profile["date_precision"]
        if precision == "omit":
            row.pop("date", None)
        elif precision == "month" and row.get("date"):
            row["date"] = row["date"][:7]
        elif precision == "year" and row.get("date"):
            row["date"] = row["date"][:4]
        result.append(row)
    return result


def _upsert_redaction_profile(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _write(ctx)
    name = _text(params.get("name"), "name")
    merchant_policy = params.get("merchant_policy")
    note_policy = params.get("note_policy")
    date_precision = params.get("date_precision")
    if merchant_policy not in ("keep", "mask", "omit") or note_policy not in ("keep", "mask", "omit") or date_precision not in ("day", "month", "year", "omit"):
        _fail("invalid_input", "One or more redaction policies are invalid")
    name_key = unicodedata.normalize("NFKC", name).casefold()
    profile_id = params.get("profile_id")
    if profile_id is None:
        profile_id = _id()
        now = _now()
        conn.execute(
            "INSERT INTO maintenance_redaction_profiles(ledger_id, profile_id, name, name_key, merchant_policy, note_policy, "
            "date_precision, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ctx["ledger_id"], profile_id, name, name_key, merchant_policy, note_policy, date_precision,
             ctx["principal_id"], now, now),
        )
        version = 1
    else:
        row = conn.execute(
            "SELECT version FROM maintenance_redaction_profiles WHERE ledger_id = ? AND profile_id = ?",
            (ctx["ledger_id"], _text(profile_id, "profile_id")),
        ).fetchone()
        if row is None:
            _fail("not_found", "Redaction profile not found in this ledger")
        expected = params.get("expected_version")
        if type(expected) is not int or expected != row["version"]:
            _fail("conflict", "The redaction profile changed after it was read", {"expected_version": expected, "actual_version": row["version"]})
        conn.execute(
            "UPDATE maintenance_redaction_profiles SET name = ?, name_key = ?, merchant_policy = ?, note_policy = ?, "
            "date_precision = ?, version = version + 1, updated_at = ? WHERE ledger_id = ? AND profile_id = ?",
            (name, name_key, merchant_policy, note_policy, date_precision, _now(), ctx["ledger_id"], profile_id),
        )
        version = row["version"] + 1
    return {"profile": {"profile_id": profile_id, "name": name, "merchant_policy": merchant_policy,
                         "note_policy": note_policy, "date_precision": date_precision, "version": version},
            "ledger": _ledger_info(ctx)}


def _list_redaction_profiles(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_read(ctx)
    rows = conn.execute(
        "SELECT profile_id, name, merchant_policy, note_policy, date_precision, version, updated_at "
        "FROM maintenance_redaction_profiles WHERE ledger_id = ? ORDER BY name_key",
        (ctx["ledger_id"],),
    ).fetchall()
    return {"profiles": [dict(row) for row in rows], "ledger": _ledger_info(ctx)}


def _preview_redacted_export(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_read(ctx)
    from . import files

    profile_id = _text(params.get("profile_id"), "profile_id")
    start_date = _date(params.get("start_date"), "start_date")
    end_date = _date(params.get("end_date"), "end_date")
    if start_date > end_date:
        _fail("invalid_input", "start_date must not be after end_date")
    project_id = params.get("project_id")
    selection = {"start_date": start_date, "end_date": end_date,
                 "project_id": _text(project_id, "project_id") if project_id is not None else None}
    rows = files._export_rows(conn, ctx, selection)
    redacted = redact_export_rows(conn, ctx["ledger_id"], profile_id, rows)
    return {"profile_id": profile_id, "record_count": len(redacted),
            "fields": sorted(set().union(*(row.keys() for row in redacted))) if redacted else [],
            "sample_rows": redacted[:10], "start_date": start_date, "end_date": end_date,
            "project_id": selection["project_id"], "ledger": _ledger_info(ctx)}


def list_security_access(conn: sqlite3.Connection, ctx: dict, start_date: str, end_date: str,
                         direction: str | None = None, limit: int = 100) -> list[dict]:
    _own_read(ctx)
    start_date = _date(start_date, "start_date")
    end_date = _date(end_date, "end_date")
    if start_date > end_date:
        _fail("invalid_input", "start_date must not be after end_date")
    if direction not in (None, "incoming", "outgoing"):
        _fail("invalid_input", "direction must be incoming or outgoing")
    if type(limit) is not int or not 1 <= limit <= 500:
        _fail("invalid_input", "limit must be from 1 to 500")
    query = (
        "SELECT event_id, direction, host_label, source, permission, action_name, resource_kind, resource_id, "
        "range_start, range_end, outcome, created_at FROM maintenance_access_events "
        "WHERE ledger_id = ? AND created_at >= ? AND created_at < ? "
        "AND (direction = 'incoming' OR (direction = 'outgoing' AND principal_id = ?))"
    )
    values: list[object] = [ctx["ledger_id"], f"{start_date}T00:00:00",
                            f"{(date.fromisoformat(end_date) + timedelta(days=1)).isoformat()}T00:00:00",
                            ctx["principal_id"]]
    if direction is not None:
        query += " AND direction = ?"
        values.append(direction)
    query += " ORDER BY created_at DESC, event_id DESC LIMIT ?"
    values.append(limit)
    return [dict(row) for row in conn.execute(query, values).fetchall()]


def _token(conn: sqlite3.Connection, ctx: dict, token_id: object) -> sqlite3.Row:
    token_id = _text(token_id, "token_id")
    row = conn.execute(
        "SELECT token_id, label, scopes_json, version, created_at, revoked_at FROM yuxu_api_tokens "
        "WHERE token_id = ? AND principal_id = ?",
        (token_id, ctx["principal_id"]),
    ).fetchone()
    if row is None:
        _fail("not_found", "Host token not found for the authenticated local identity")
    return row


def _list_host_tokens(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_read(ctx)
    rows = conn.execute(
        "SELECT token_id, label, scopes_json, version, created_at, revoked_at FROM yuxu_api_tokens "
        "WHERE principal_id = ? ORDER BY created_at, token_id",
        (ctx["principal_id"],),
    ).fetchall()
    tokens = []
    for row in rows:
        tokens.append({"token_id": row["token_id"], "label": row["label"],
                       "scopes": json.loads(row["scopes_json"]), "version": row["version"],
                       "created_at": row["created_at"], "revoked_at": row["revoked_at"],
                       "active": row["revoked_at"] is None})
    return {"tokens": tokens, "ledger": _ledger_info(ctx)}


def _set_host_token_scopes(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _write(ctx)
    row = _token(conn, ctx, params.get("token_id"))
    if row["revoked_at"] is not None:
        _fail("conflict", "A revoked host token cannot be reauthorized")
    expected = params.get("expected_version")
    if type(expected) is not int or expected != row["version"]:
        _fail("conflict", "Host token permissions changed after they were read", {
            "expected_version": expected, "actual_version": row["version"],
        })
    scopes = params.get("scopes")
    allowed = store.HOST_PERMISSIONS
    if not isinstance(scopes, list) or any(not isinstance(scope, str) or scope not in allowed for scope in scopes) or len(scopes) != len(set(scopes)):
        _fail("invalid_input", "scopes must contain unique supported host permissions")
    scopes = sorted(scopes)
    conn.execute(
        "UPDATE yuxu_api_tokens SET scopes_json = ?, version = version + 1 WHERE token_id = ? AND principal_id = ? AND version = ?",
        (json.dumps(scopes), row["token_id"], ctx["principal_id"], expected),
    )
    _access_log(conn, ctx, "set_host_token_scopes", "host_token_scope", None, "updated",
                permission=", ".join(scopes) if scopes else "none", direction="outgoing", host_label=row["label"])
    return {"token_id": row["token_id"], "label": row["label"], "scopes": scopes,
            "version": row["version"] + 1, "ledger": _ledger_info(ctx)}


def _revoke_host_token(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _write(ctx)
    row = _token(conn, ctx, params.get("token_id"))
    if row["revoked_at"] is not None:
        _fail("conflict", "Host token is already revoked")
    expected = params.get("expected_version")
    if type(expected) is not int or expected != row["version"]:
        _fail("conflict", "Host token changed after it was read", {
            "expected_version": expected, "actual_version": row["version"],
        })
    revoked_at = _now()
    conn.execute(
        "UPDATE yuxu_api_tokens SET revoked_at = ?, version = version + 1 WHERE token_id = ? AND principal_id = ? AND version = ?",
        (revoked_at, row["token_id"], ctx["principal_id"], expected),
    )
    _access_log(conn, ctx, "revoke_host_token", "host_token_scope", None, "revoked",
                permission="revoked", direction="outgoing", host_label=row["label"])
    return {"token_id": row["token_id"], "label": row["label"], "revoked_at": revoked_at,
            "version": row["version"] + 1, "ledger": _ledger_info(ctx)}


def _recurring_summary(conn: sqlite3.Connection, ledger_id: str) -> dict:
    rules = conn.execute(
        "SELECT COUNT(*) AS count FROM yuxu_recurring_rules WHERE ledger_id = ?",
        (ledger_id,),
    ).fetchone()["count"]
    prices = conn.execute(
        "SELECT COUNT(*) AS count FROM yuxu_recurring_price_history WHERE ledger_id = ?",
        (ledger_id,),
    ).fetchone()["count"]
    occurrences = conn.execute(
        "SELECT COUNT(*) AS count FROM yuxu_recurring_occurrences WHERE ledger_id = ?",
        (ledger_id,),
    ).fetchone()["count"]
    periods = conn.execute(
        "SELECT COUNT(DISTINCT period) AS count FROM yuxu_recurring_occurrences WHERE ledger_id = ?",
        (ledger_id,),
    ).fetchone()["count"]
    return {"rule_count": rules, "price_history_count": prices, "occurrence_count": occurrences, "period_count": periods}


def _backup_drill(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _write(ctx)
    backup_id = _text(params.get("backup_id"), "backup_id")
    from . import files

    live_ledgers = files._ledger_summaries(conn)
    live_recurring = {row["ledger_id"]: _recurring_summary(conn, row["ledger_id"]) for row in live_ledgers}
    source, manifest, database_sha256 = files._read_backup(conn, backup_id)
    try:
        check = source.execute("PRAGMA quick_check").fetchone()[0]
        restored_ledgers = files._ledger_summaries(source)
        live_by_id = {row["ledger_id"]: row for row in live_ledgers}
        ledgers = []
        for backup_row in restored_ledgers:
            ledger_id = backup_row["ledger_id"]
            live_row = live_by_id[ledger_id]
            backup_recurring = _recurring_summary(source, ledger_id)
            live_recurring_summary = live_recurring[ledger_id]
            ledgers.append({
                "ledger_id": ledger_id,
                "account_id": backup_row["account_id"],
                "account_name": backup_row["account_name"],
                "backup": {
                    "transaction_count": backup_row["transaction_count"],
                    "balance_minor": backup_row["balance_minor"],
                    **backup_recurring,
                },
                "live": {
                    "transaction_count": live_row["transaction_count"],
                    "balance_minor": live_row["balance_minor"],
                    **live_recurring_summary,
                },
                "matches_live": (
                    backup_row["transaction_count"], backup_row["balance_minor"], backup_recurring
                ) == (live_row["transaction_count"], live_row["balance_minor"], live_recurring_summary),
            })
        result = {
            "backup_id": backup_id, "database_sha256": database_sha256,
            "format_version": manifest["format_version"], "integrity_check": check,
            "isolation": "in-memory SQLite copy; live database was not restored or replaced",
            "live_ledger_summaries_unchanged": files._ledger_summaries(conn) == live_ledgers,
            "ledgers": ledgers,
        }
        drill_id = _id()
        conn.execute(
            "INSERT INTO maintenance_backup_drills(ledger_id, drill_id, backup_id, result_json, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ctx["ledger_id"], drill_id, backup_id, json.dumps(result, ensure_ascii=False, sort_keys=True), ctx["principal_id"], _now()),
        )
        return {"drill_id": drill_id, **result, "ledger": _ledger_info(ctx)}
    finally:
        source.close()


def _issue(issue_type: str, key: str, summary: str, affected: dict, observed: dict,
           repair_options: list[str]) -> dict:
    issue_id = hashlib.sha256(f"{issue_type}:{key}".encode("utf-8")).hexdigest()[:20]
    fingerprint = _canonical_hash({"type": issue_type, "key": key, "affected": affected, "observed": observed})
    return {"issue_id": issue_id, "issue_fingerprint": fingerprint, "type": issue_type,
            "summary": summary, "affected": affected, "observed": observed,
            "repairable": bool(repair_options), "repair_options": repair_options}


def _diagnose_issues(conn: sqlite3.Connection, ledger_id: str) -> list[dict]:
    issues = []
    splits = conn.execute(
        "SELECT s.split_id, s.transaction_id, s.total_minor, s.personal_share_minor, s.version, "
        "t.amount_minor AS transaction_amount, t.version AS transaction_version, t.kind AS transaction_kind, "
        "t.confirmation_status, t.voided_at "
        "FROM movement_splits s LEFT JOIN ledger_transactions t "
        "ON t.ledger_id = s.ledger_id AND t.transaction_id = s.transaction_id WHERE s.ledger_id = ? ORDER BY s.split_id",
        (ledger_id,),
    ).fetchall()
    for split in splits:
        participants = conn.execute(
            "SELECT participant_name, participant_key, owed_minor FROM movement_split_participants "
            "WHERE ledger_id = ? AND split_id = ? ORDER BY participant_key",
            (ledger_id, split["split_id"]),
        ).fetchall()
        received = conn.execute(
            "SELECT participant_name, SUM(amount_minor) AS received_minor FROM movement_active_events "
            "WHERE ledger_id = ? AND event_type = 'split_settlement' AND subject_id = ? GROUP BY participant_name",
            (ledger_id, split["split_id"]),
        ).fetchall()
        received_by_name = {unicodedata.normalize("NFKC", row["participant_name"]).casefold(): row["received_minor"] for row in received}
        participant_data = [{"name": row["participant_name"], "owed_minor": row["owed_minor"],
                             "received_minor": received_by_name.get(row["participant_key"], 0)} for row in participants]
        if (split["transaction_amount"] is None or split["transaction_kind"] != "expense" or
                split["confirmation_status"] != "confirmed" or split["voided_at"] is not None):
            issues.append(_issue(
                "orphan_split", split["split_id"], "Personal split no longer points to a confirmed expense",
                {"split_id": split["split_id"], "transaction_id": split["transaction_id"]},
                {"transaction_exists": split["transaction_amount"] is not None,
                 "transaction_kind": split["transaction_kind"], "transaction_status": split["confirmation_status"],
                 "voided": split["voided_at"] is not None, "participants": participant_data,
                 "split_version": split["version"]}, ["remove_orphan_split"],
            ))
            continue
        owed = sum(row["owed_minor"] for row in participants)
        settled_over = [row for row in participant_data if row["received_minor"] > row["owed_minor"]]
        transaction_amount = split["transaction_amount"]
        allocation = split["personal_share_minor"] + owed
        if transaction_amount is not None and (split["total_minor"] != transaction_amount or allocation != transaction_amount):
            options = ["reallocate_split"]
            if allocation == transaction_amount and split["total_minor"] != transaction_amount:
                options = ["sync_split_total", "reallocate_split"]
            issues.append(_issue(
                "split_allocation_mismatch", split["split_id"], "Split amounts do not equal the linked expense amount",
                {"split_id": split["split_id"], "transaction_id": split["transaction_id"]},
                {"split_total_minor": split["total_minor"], "transaction_amount_minor": transaction_amount,
                 "personal_share_minor": split["personal_share_minor"], "participants": participant_data,
                 "split_version": split["version"], "transaction_version": split["transaction_version"]}, options,
            ))
        for participant in settled_over:
            settlement_events = conn.execute(
                "SELECT event_id, amount_minor, occurred_on FROM movement_active_events WHERE ledger_id = ? "
                "AND event_type = 'split_settlement' AND subject_id = ? AND participant_name = ? "
                "ORDER BY occurred_on, event_id",
                (ledger_id, split["split_id"], participant["name"]),
            ).fetchall()
            issues.append(_issue(
                "split_settlement_overrun", f"{split['split_id']}:{participant['name']}", "Participant settlements exceed the amount owed",
                {"split_id": split["split_id"], "transaction_id": split["transaction_id"], "participant_name": participant["name"]},
                {"owed_minor": participant["owed_minor"], "received_minor": participant["received_minor"],
                 "settlement_events": [dict(row) for row in settlement_events], "split_version": split["version"]},
                ["set_split_settlement_amount", "remove_split_settlement"],
            ))

    refunds = conn.execute(
        "SELECT e.related_transaction_id AS transaction_id, t.amount_minor AS transaction_amount, "
        "t.version AS transaction_version, t.kind AS transaction_kind, t.confirmation_status, t.voided_at, "
        "SUM(e.amount_minor) AS refunded_minor "
        "FROM movement_active_events e LEFT JOIN ledger_transactions t "
        "ON t.ledger_id = e.ledger_id AND t.transaction_id = e.related_transaction_id "
        "WHERE e.ledger_id = ? AND e.event_type = 'refund' AND e.status = 'confirmed' "
        "GROUP BY e.related_transaction_id ORDER BY e.related_transaction_id",
        (ledger_id,),
    ).fetchall()
    for refund in refunds:
        orphan = (refund["transaction_amount"] is None or refund["transaction_kind"] != "expense" or
                  refund["confirmation_status"] != "confirmed" or refund["voided_at"] is not None)
        if orphan or refund["refunded_minor"] > refund["transaction_amount"]:
            events = conn.execute(
                "SELECT event_id, amount_minor, occurred_on FROM movement_active_events WHERE ledger_id = ? "
                "AND event_type = 'refund' AND related_transaction_id = ? ORDER BY occurred_on, event_id",
                (ledger_id, refund["transaction_id"]),
            ).fetchall()
            issues.append(_issue(
                "orphan_refund" if orphan else "refund_overrun",
                refund["transaction_id"], "Refund adjustments exceed or no longer match the original expense",
                {"transaction_id": refund["transaction_id"]},
                {"transaction_amount_minor": refund["transaction_amount"], "refunded_minor": refund["refunded_minor"],
                 "transaction_version": refund["transaction_version"],
                 "refund_events": [dict(row) for row in events]},
                ["remove_orphan_refund"] if orphan else ["set_refund_amount", "remove_refund_event"],
            ))

    reimbursements = conn.execute(
        "SELECT r.reimbursement_id, r.transaction_id, r.amount_minor, r.version, t.amount_minor AS transaction_amount, "
        "t.kind AS transaction_kind, t.confirmation_status, t.voided_at "
        "FROM movement_reimbursements r LEFT JOIN ledger_transactions t "
        "ON t.ledger_id = r.ledger_id AND t.transaction_id = r.transaction_id WHERE r.ledger_id = ? ORDER BY r.reimbursement_id",
        (ledger_id,),
    ).fetchall()
    for reimbursement in reimbursements:
        orphan = (reimbursement["transaction_amount"] is None or reimbursement["transaction_kind"] != "expense" or
                  reimbursement["confirmation_status"] != "confirmed" or reimbursement["voided_at"] is not None)
        if orphan or reimbursement["amount_minor"] > reimbursement["transaction_amount"]:
            received = conn.execute(
                "SELECT COALESCE(SUM(e.amount_minor), 0) AS total FROM movement_reimbursement_receipts x "
                "JOIN movement_active_events e ON e.ledger_id = x.ledger_id AND e.event_id = x.event_id "
                "WHERE x.ledger_id = ? AND x.reimbursement_id = ?",
                (ledger_id, reimbursement["reimbursement_id"]),
            ).fetchone()["total"]
            issues.append(_issue(
                "orphan_reimbursement" if orphan else "reimbursement_overrun",
                reimbursement["reimbursement_id"], "Reimbursement advance exceeds or no longer matches its original expense",
                {"reimbursement_id": reimbursement["reimbursement_id"], "transaction_id": reimbursement["transaction_id"]},
                {"advance_minor": reimbursement["amount_minor"], "transaction_amount_minor": reimbursement["transaction_amount"],
                 "received_minor": received, "version": reimbursement["version"]},
                ["remove_orphan_reimbursement"] if orphan else ["set_reimbursement_amount"],
            ))

    project_entries = conn.execute(
        "SELECT e.project_id, e.transaction_id, p.status AS project_status, t.voided_at, t.confirmation_status, t.kind "
        "FROM project_entries e LEFT JOIN planning_projects p ON p.ledger_id = e.ledger_id AND p.project_id = e.project_id "
        "LEFT JOIN ledger_transactions t ON t.ledger_id = e.ledger_id AND t.transaction_id = e.transaction_id "
        "WHERE e.ledger_id = ? ORDER BY e.project_id, e.transaction_id",
        (ledger_id,),
    ).fetchall()
    for entry in project_entries:
        if (entry["project_status"] is None or entry["voided_at"] is not None or
                entry["confirmation_status"] != "confirmed" or entry["kind"] != "expense"):
            key = f"{entry['project_id']}:{entry['transaction_id']}"
            issues.append(_issue(
                "orphan_project_entry", key, "Project entry no longer points to an open ledger expense",
                {"project_id": entry["project_id"], "transaction_id": entry["transaction_id"]},
                {"project_exists": entry["project_status"] is not None, "project_status": entry["project_status"],
                 "transaction_exists": entry["confirmation_status"] is not None, "transaction_status": entry["confirmation_status"],
                 "voided": entry["voided_at"] is not None}, ["remove_orphan_project_entry"],
            ))

    attachments = conn.execute(
        "SELECT f.attachment_id, f.transaction_id, f.file_name, f.mime_type, f.size_bytes, f.version "
        "FROM maintenance_transaction_files f LEFT JOIN ledger_transactions t "
        "ON t.ledger_id = f.ledger_id AND t.transaction_id = f.transaction_id "
        "WHERE f.ledger_id = ? AND f.removed_at IS NULL AND t.transaction_id IS NULL ORDER BY f.attachment_id",
        (ledger_id,),
    ).fetchall()
    for attachment in attachments:
        issues.append(_issue(
            "orphan_attachment", attachment["attachment_id"], "Attachment no longer points to a ledger transaction",
            {"attachment_id": attachment["attachment_id"], "transaction_id": attachment["transaction_id"]},
            {"file_name": attachment["file_name"], "mime_type": attachment["mime_type"],
             "size_bytes": attachment["size_bytes"], "version": attachment["version"]}, ["remove_orphan_attachment"],
        ))
    return sorted(issues, key=lambda item: (item["type"], item["issue_id"]))


def _preview_maintenance_issues(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _own_read(ctx)
    issues = _diagnose_issues(conn, ctx["ledger_id"])
    return {"issue_count": len(issues), "issues": issues, "ledger": _ledger_info(ctx)}


def _repair_maintenance_issue(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    _write(ctx)
    _require_web(ctx)
    if params.get("confirmed") is not True:
        _fail("needs_clarification", "Explicit confirmation is required for a data repair")
    issue_id = _text(params.get("issue_id"), "issue_id")
    issue_fingerprint = _text(params.get("issue_fingerprint"), "issue_fingerprint")
    issue = next((item for item in _diagnose_issues(conn, ctx["ledger_id"]) if item["issue_id"] == issue_id), None)
    if issue is None:
        _fail("not_found", "The consistency issue is no longer present")
    if issue["issue_fingerprint"] != issue_fingerprint:
        _fail("conflict", "The affected records changed after the repair preview")
    resolution = params.get("resolution")
    if not isinstance(resolution, dict):
        _fail("needs_clarification", "A specific repair resolution is required")
    before = issue["observed"]
    resolution_type = resolution.get("type")
    ledger_id = ctx["ledger_id"]

    if issue["type"] == "orphan_split" and resolution_type == "remove_orphan_split":
        split_id = issue["affected"]["split_id"]
        transaction_id = issue["affected"]["transaction_id"]
        conn.execute(
            "UPDATE movement_events SET subject_id = NULL, related_transaction_id = CASE WHEN EXISTS ("
            "SELECT 1 FROM ledger_transactions WHERE ledger_id = ? AND transaction_id = ?) "
            "THEN related_transaction_id ELSE NULL END WHERE ledger_id = ? AND event_type = 'split_settlement' AND subject_id = ?",
            (ledger_id, transaction_id, ledger_id, split_id),
        )
        conn.execute("DELETE FROM movement_split_participants WHERE ledger_id = ? AND split_id = ?", (ledger_id, split_id))
        result = conn.execute("DELETE FROM movement_splits WHERE ledger_id = ? AND split_id = ?", (ledger_id, split_id))
        if result.rowcount != 1:
            _fail("conflict", "The orphan split changed after the preview")
        after = {"split_id": split_id, "removed": True}
        action = resolution_type

    elif issue["type"] == "split_settlement_overrun" and resolution_type in {"set_split_settlement_amount", "remove_split_settlement"}:
        event_id = _text(resolution.get("event_id"), "event_id")
        allowed_events = {row["event_id"]: row["amount_minor"] for row in issue["observed"]["settlement_events"]}
        if event_id not in allowed_events:
            _fail("invalid_input", "event_id must be one of the settlements shown for this issue")
        split_id = issue["affected"]["split_id"]
        participant_name = issue["affected"]["participant_name"]
        event = conn.execute(
            "SELECT amount_minor FROM movement_active_events WHERE ledger_id = ? AND event_id = ? AND event_type = 'split_settlement'",
            (ledger_id, event_id),
        ).fetchone()
        if resolution_type == "remove_split_settlement":
            conn.execute("DELETE FROM movement_account_entries WHERE ledger_id = ? AND event_id = ?", (ledger_id, event_id))
            conn.execute("DELETE FROM movement_events WHERE ledger_id = ? AND event_id = ?", (ledger_id, event_id))
            after = {"event_id": event_id, "removed": True, "previous_amount_minor": event["amount_minor"]}
        else:
            new_amount = resolution.get("new_amount_minor")
            if type(new_amount) is not int or new_amount <= 0:
                _fail("invalid_input", "new_amount_minor must be a positive integer")
            participant_key = unicodedata.normalize("NFKC", participant_name).casefold()
            participant = conn.execute(
                "SELECT owed_minor FROM movement_split_participants WHERE ledger_id = ? AND split_id = ? AND participant_key = ?",
                (ledger_id, split_id, participant_key),
            ).fetchone()
            other_settlements = conn.execute(
                "SELECT COALESCE(SUM(amount_minor), 0) AS total FROM movement_active_events WHERE ledger_id = ? "
                "AND event_type = 'split_settlement' AND subject_id = ? AND participant_name = ? AND event_id != ?",
                (ledger_id, split_id, participant_name, event_id),
            ).fetchone()["total"]
            if participant is None or other_settlements + new_amount > participant["owed_minor"]:
                _fail("invalid_input", "The new settlement amount would still exceed the participant allocation")
            entries = conn.execute(
                "SELECT account_id, delta_minor FROM movement_account_entries WHERE ledger_id = ? AND event_id = ?",
                (ledger_id, event_id),
            ).fetchall()
            for entry in entries:
                conn.execute(
                    "UPDATE movement_account_entries SET delta_minor = ? WHERE ledger_id = ? AND event_id = ? AND account_id = ?",
                    (-new_amount if entry["delta_minor"] < 0 else new_amount, ledger_id, event_id, entry["account_id"]),
                )
            conn.execute("UPDATE movement_events SET amount_minor = ? WHERE ledger_id = ? AND event_id = ?",
                         (new_amount, ledger_id, event_id))
            after = {"event_id": event_id, "amount_minor": new_amount}
        conn.execute("UPDATE movement_splits SET version = version + 1 WHERE ledger_id = ? AND split_id = ?", (ledger_id, split_id))
        action = resolution_type

    elif issue["type"] == "split_allocation_mismatch" and resolution_type == "sync_split_total":
        split_id = issue["affected"]["split_id"]
        split = conn.execute(
            "SELECT s.total_minor, s.personal_share_minor, t.amount_minor FROM movement_splits s "
            "JOIN ledger_transactions t ON t.ledger_id = s.ledger_id AND t.transaction_id = s.transaction_id "
            "WHERE s.ledger_id = ? AND s.split_id = ?",
            (ledger_id, split_id),
        ).fetchone()
        owed = conn.execute(
            "SELECT COALESCE(SUM(owed_minor), 0) AS total FROM movement_split_participants WHERE ledger_id = ? AND split_id = ?",
            (ledger_id, split_id),
        ).fetchone()["total"]
        if split["personal_share_minor"] + owed != split["amount_minor"]:
            _fail("conflict", "The existing participant allocations do not equal the expense; provide a reallocation")
        conn.execute(
            "UPDATE movement_splits SET total_minor = ?, version = version + 1 WHERE ledger_id = ? AND split_id = ?",
            (split["amount_minor"], ledger_id, split_id),
        )
        after = {"split_id": split_id, "total_minor": split["amount_minor"]}
        action = resolution_type

    elif issue["type"] == "split_allocation_mismatch" and resolution_type == "reallocate_split":
        split_id = issue["affected"]["split_id"]
        split = conn.execute(
            "SELECT s.version, s.transaction_id, t.amount_minor FROM movement_splits s "
            "JOIN ledger_transactions t ON t.ledger_id = s.ledger_id AND t.transaction_id = s.transaction_id "
            "WHERE s.ledger_id = ? AND s.split_id = ?",
            (ledger_id, split_id),
        ).fetchone()
        personal_share = resolution.get("personal_share_minor")
        participants = resolution.get("participants")
        if type(personal_share) is not int or personal_share < 0 or personal_share > split["amount_minor"]:
            _fail("invalid_input", "personal_share_minor must be from zero to the expense amount")
        if not isinstance(participants, list) or not participants:
            _fail("needs_clarification", "At least one participant allocation is required")
        parsed = []
        seen = set()
        for item in participants:
            if not isinstance(item, dict) or set(item) != {"name", "owed_minor"}:
                _fail("invalid_input", "Each participant needs only name and owed_minor")
            name = _text(item["name"], "participant.name")
            key = unicodedata.normalize("NFKC", name).casefold()
            if key in seen:
                _fail("invalid_input", "Participant names must be unique")
            seen.add(key)
            owed_minor = item["owed_minor"]
            if type(owed_minor) is not int or owed_minor <= 0:
                _fail("invalid_input", "owed_minor must be a positive integer")
            parsed.append((name, key, owed_minor))
        if personal_share + sum(item[2] for item in parsed) != split["amount_minor"]:
            _fail("invalid_input", "Personal share and participant amounts must equal the linked expense")
        settlements = conn.execute(
            "SELECT participant_name, SUM(amount_minor) AS total FROM movement_active_events WHERE ledger_id = ? "
            "AND event_type = 'split_settlement' AND subject_id = ? GROUP BY participant_name",
            (ledger_id, split_id),
        ).fetchall()
        settled_by_key = {unicodedata.normalize("NFKC", row["participant_name"]).casefold(): row["total"] for row in settlements}
        for name, key, owed in parsed:
            if settled_by_key.get(key, 0) > owed:
                _fail("conflict", "The selected allocation is below the amount already received from a participant", {"participant_name": name})
        if any(key not in seen and total > 0 for key, total in settled_by_key.items()):
            _fail("conflict", "A participant with an existing settlement cannot be removed")
        conn.execute("DELETE FROM movement_split_participants WHERE ledger_id = ? AND split_id = ?", (ledger_id, split_id))
        conn.executemany(
            "INSERT INTO movement_split_participants(ledger_id, split_id, participant_name, participant_key, owed_minor) VALUES (?, ?, ?, ?, ?)",
            [(ledger_id, split_id, name, key, owed) for name, key, owed in parsed],
        )
        conn.execute(
            "UPDATE movement_splits SET total_minor = ?, personal_share_minor = ?, version = version + 1 WHERE ledger_id = ? AND split_id = ?",
            (split["amount_minor"], personal_share, ledger_id, split_id),
        )
        after = {"split_id": split_id, "total_minor": split["amount_minor"], "personal_share_minor": personal_share,
                 "participants": [{"name": name, "owed_minor": owed} for name, _, owed in parsed]}
        action = resolution_type

    elif ((issue["type"] == "refund_overrun" and resolution_type in {"set_refund_amount", "remove_refund_event"}) or
          (issue["type"] == "orphan_refund" and resolution_type == "remove_orphan_refund")):
        event_id = _text(resolution.get("event_id"), "event_id")
        allowed_events = {row["event_id"]: row["amount_minor"] for row in issue["observed"]["refund_events"]}
        if event_id not in allowed_events:
            _fail("invalid_input", "event_id must be one of the refunds shown for this issue")
        event = conn.execute(
            "SELECT amount_minor, related_transaction_id FROM movement_active_events WHERE ledger_id = ? AND event_id = ? AND event_type = 'refund'",
            (ledger_id, event_id),
        ).fetchone()
        if resolution_type in {"remove_refund_event", "remove_orphan_refund"}:
            conn.execute("DELETE FROM movement_account_entries WHERE ledger_id = ? AND event_id = ?", (ledger_id, event_id))
            conn.execute("DELETE FROM movement_events WHERE ledger_id = ? AND event_id = ?", (ledger_id, event_id))
            after = {"event_id": event_id, "removed": True, "previous_amount_minor": event["amount_minor"]}
        else:
            new_amount = resolution.get("new_amount_minor")
            if type(new_amount) is not int or new_amount <= 0:
                _fail("invalid_input", "new_amount_minor must be a positive integer")
            original = conn.execute(
                "SELECT amount_minor FROM ledger_transactions WHERE ledger_id = ? AND transaction_id = ?",
                (ledger_id, event["related_transaction_id"]),
            ).fetchone()
            other_refunds = conn.execute(
                "SELECT COALESCE(SUM(amount_minor), 0) AS total FROM movement_active_events WHERE ledger_id = ? "
                "AND event_type = 'refund' AND related_transaction_id = ? AND event_id != ?",
                (ledger_id, event["related_transaction_id"], event_id),
            ).fetchone()["total"]
            if other_refunds + new_amount > original["amount_minor"]:
                _fail("invalid_input", "new_amount_minor would still exceed the remaining original expense")
            entries = conn.execute(
                "SELECT e.account_id, a.account_type FROM movement_account_entries e JOIN movement_accounts a "
                "ON a.ledger_id = e.ledger_id AND a.account_id = e.account_id WHERE e.ledger_id = ? AND e.event_id = ?",
                (ledger_id, event_id),
            ).fetchall()
            for entry in entries:
                conn.execute(
                    "UPDATE movement_account_entries SET delta_minor = ? WHERE ledger_id = ? AND event_id = ? AND account_id = ?",
                    (-new_amount if entry["account_type"] == "credit_card" else new_amount,
                     ledger_id, event_id, entry["account_id"]),
                )
            conn.execute(
                "UPDATE movement_events SET amount_minor = ? WHERE ledger_id = ? AND event_id = ?",
                (new_amount, ledger_id, event_id),
            )
            after = {"event_id": event_id, "amount_minor": new_amount}
        action = resolution_type

    elif issue["type"] == "orphan_reimbursement" and resolution_type == "remove_orphan_reimbursement":
        reimbursement_id = issue["affected"]["reimbursement_id"]
        conn.execute(
            "UPDATE movement_events SET subject_id = NULL, related_transaction_id = CASE WHEN EXISTS ("
            "SELECT 1 FROM ledger_transactions WHERE ledger_id = ? AND transaction_id = related_transaction_id) "
            "THEN related_transaction_id ELSE NULL END WHERE ledger_id = ? AND event_type = 'reimbursement_receipt' AND subject_id = ?",
            (ledger_id, ledger_id, reimbursement_id),
        )
        conn.execute("DELETE FROM movement_reimbursement_receipts WHERE ledger_id = ? AND reimbursement_id = ?",
                     (ledger_id, reimbursement_id))
        result = conn.execute("DELETE FROM movement_reimbursements WHERE ledger_id = ? AND reimbursement_id = ?",
                              (ledger_id, reimbursement_id))
        if result.rowcount != 1:
            _fail("conflict", "The orphan reimbursement changed after the preview")
        after = {"reimbursement_id": reimbursement_id, "removed": True}
        action = resolution_type

    elif issue["type"] == "reimbursement_overrun" and resolution_type == "set_reimbursement_amount":
        reimbursement_id = issue["affected"]["reimbursement_id"]
        new_amount = resolution.get("new_amount_minor")
        if type(new_amount) is not int or new_amount <= 0:
            _fail("invalid_input", "new_amount_minor must be a positive integer")
        row = conn.execute(
            "SELECT r.amount_minor, r.transaction_id, t.amount_minor AS transaction_amount FROM movement_reimbursements r "
            "JOIN ledger_transactions t ON t.ledger_id = r.ledger_id AND t.transaction_id = r.transaction_id "
            "WHERE r.ledger_id = ? AND r.reimbursement_id = ?",
            (ledger_id, reimbursement_id),
        ).fetchone()
        received = conn.execute(
            "SELECT COALESCE(SUM(e.amount_minor), 0) AS total FROM movement_reimbursement_receipts x "
            "JOIN movement_active_events e ON e.ledger_id = x.ledger_id AND e.event_id = x.event_id "
            "WHERE x.ledger_id = ? AND x.reimbursement_id = ?",
            (ledger_id, reimbursement_id),
        ).fetchone()["total"]
        if new_amount > row["transaction_amount"] or new_amount < received:
            _fail("invalid_input", "New reimbursement amount must cover receipts and not exceed the original expense")
        conn.execute(
            "UPDATE movement_reimbursements SET amount_minor = ?, version = version + 1 WHERE ledger_id = ? AND reimbursement_id = ?",
            (new_amount, ledger_id, reimbursement_id),
        )
        after = {"reimbursement_id": reimbursement_id, "amount_minor": new_amount}
        action = resolution_type

    elif issue["type"] == "orphan_project_entry" and resolution_type == "remove_orphan_project_entry":
        project_id = issue["affected"]["project_id"]
        transaction_id = issue["affected"]["transaction_id"]
        result = conn.execute(
            "DELETE FROM project_entries WHERE ledger_id = ? AND project_id = ? AND transaction_id = ?",
            (ledger_id, project_id, transaction_id),
        )
        if result.rowcount != 1:
            _fail("conflict", "The orphan project entry changed after the preview")
        after = {"project_id": project_id, "transaction_id": transaction_id, "removed": True}
        action = resolution_type

    elif issue["type"] == "orphan_attachment" and resolution_type == "remove_orphan_attachment":
        attachment_id = issue["affected"]["attachment_id"]
        result = conn.execute(
            "UPDATE maintenance_transaction_files SET content = NULL, size_bytes = 0, removed_at = ?, version = version + 1 "
            "WHERE ledger_id = ? AND attachment_id = ? AND removed_at IS NULL",
            (_now(), ledger_id, attachment_id),
        )
        if result.rowcount != 1:
            _fail("conflict", "The orphan attachment changed after the preview")
        after = {"attachment_id": attachment_id, "removed": True}
        action = resolution_type

    else:
        _fail("needs_clarification", "The selected resolution does not apply to this issue")

    remaining = _diagnose_issues(conn, ledger_id)
    conn.execute(
        "INSERT INTO maintenance_repair_events(ledger_id, repair_id, issue_id, issue_fingerprint, action, before_json, after_json, "
        "request_id, actor_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ledger_id, _id(), issue_id, issue_fingerprint, action,
         json.dumps(before, ensure_ascii=False, sort_keys=True), json.dumps(after, ensure_ascii=False, sort_keys=True),
         ctx["request_id"], ctx["principal_id"], _now()),
    )
    return {"issue_id": issue_id, "repaired": True, "action": action, "before": before, "after": after,
            "remaining_issue_count": len(remaining), "ledger": _ledger_info(ctx)}


def _list_security_access(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    start_date = _date(params.get("start_date"), "start_date")
    end_date = _date(params.get("end_date"), "end_date")
    events = list_security_access(conn, ctx, start_date, end_date,
                                  params.get("direction"), params.get("limit", 100))
    return {"events": events, "start_date": start_date, "end_date": end_date, "ledger": _ledger_info(ctx)}


_COLUMN_MAPPING = _object({
    "occurred_on": _STRING,
    "charged_on": _STRING,
    "kind": _STRING,
    "amount": _STRING,
    "merchant": _STRING,
    "note": _STRING,
    "external_id": _STRING,
    "payment_status": _STRING,
})
_STATEMENT_PROVIDER = {"type": "string", "enum": ["wechat", "alipay", "bank", "other"]}
_SPLIT_PARTICIPANT = _object({"name": _STRING, "owed_minor": _AMOUNT}, ("name", "owed_minor"))
_REPAIR_RESOLUTION = _object({
    "type": {"type": "string", "enum": [
        "remove_orphan_split", "sync_split_total", "reallocate_split", "set_split_settlement_amount",
        "remove_split_settlement", "set_refund_amount", "remove_refund_event", "remove_orphan_refund",
        "set_reimbursement_amount", "remove_orphan_reimbursement", "remove_orphan_project_entry", "remove_orphan_attachment",
    ]},
    "personal_share_minor": {"type": "integer", "minimum": 0},
    "participants": {"type": "array", "minItems": 1, "items": _SPLIT_PARTICIPANT},
    "event_id": _STRING,
    "new_amount_minor": _AMOUNT,
})


ACTIONS = {
    "list_csv_mapping_templates": {
        "description": "List this ledger's metadata-only CSV source mapping templates; templates contain no CSV rows or file body.",
        "inputSchema": _object({}),
        "scope": "own_read", "host_permission": "detail_read", "sources": ["web"],
        "run": _list_csv_mapping_templates,
    },
    "upsert_csv_mapping_template": {
        "description": "Create or update a ledger-scoped CSV source mapping template after an explicit header review.",
        "inputSchema": _object({
            "template_id": _STRING, "expected_version": {"type": "integer", "minimum": 1},
            "name": _STRING, "headers": {"type": "array", "minItems": 1, "items": _STRING},
            "column_mapping": _COLUMN_MAPPING,
            "date_format": {"type": "string", "enum": ["iso", "slash", "compact"]},
            "amount_unit": {"type": "string", "enum": ["yuan", "fen"]},
        }, ("name", "headers", "column_mapping", "date_format", "amount_unit")),
        "scope": "own_write", "host_permission": "write", "sources": ["web"],
        "run": _upsert_csv_mapping_template,
    },
    "delete_csv_mapping_template": {
        "description": "Delete one ledger-scoped CSV source mapping template after a matching version check.",
        "inputSchema": _object({"template_id": _STRING, "expected_version": {"type": "integer", "minimum": 1}},
                             ("template_id", "expected_version")),
        "scope": "own_write", "host_permission": "write", "sources": ["web"],
        "run": _delete_csv_mapping_template,
    },
    "preview_transaction_reconciliation": {
        "description": "Compare a user-selected local CSV to this ledger in an explicit inclusive date window; ambiguous rows require review and no ledger rows are written.",
        "inputSchema": _object({
            "file_name": _STRING, "content_base64": _STRING, "column_mapping": _COLUMN_MAPPING,
            "date_format": {"type": "string", "enum": ["iso", "slash", "compact"]},
            "amount_unit": {"type": "string", "enum": ["yuan", "fen"]},
            "start_date": _DATE, "end_date": _DATE, "payment_account_id": _STRING, "provider": _STATEMENT_PROVIDER,
        }, ("file_name", "content_base64", "column_mapping", "date_format", "amount_unit", "start_date", "end_date")),
        "scope": "own_write", "host_permission": "write",
        "run": _transaction_reconciliation_preview,
    },
    "get_transaction_reconciliation": {
        "description": "Get a pending CSV reconciliation preview or its completed result from your authenticated ledger.",
        "inputSchema": _object({"preview_id": _STRING}, ("preview_id",)),
        "scope": "own_read", "host_permission": "detail_read", "run": _get_transaction_reconciliation,
    },
    "commit_transaction_reconciliation": {
        "description": "Import only explicitly selected CSV-only rows from an unexpired reconciliation preview after confirmation and a matching ledger fingerprint.",
        "inputSchema": _object({
            "preview_id": _STRING, "preview_sha256": _STRING,
            "selected_csv_lines": {"type": "array", "minItems": 1, "uniqueItems": True,
                                  "items": {"type": "integer", "minimum": 2}},
            "confirmed": _CONFIRMED,
        }, ("preview_id", "preview_sha256", "selected_csv_lines", "confirmed")),
        "scope": "own_write", "host_permission": "write", "sources": ["web"],
        "run": _commit_transaction_reconciliation,
    },
    "preview_transaction_import": {
        "description": "Preview a user-selected local CSV transaction import. Returns validated rows, duplicate candidates, and a short-lived preview hash before any transaction is written.",
        "inputSchema": _object({
            "file_name": _STRING,
            "content_base64": _STRING,
            "column_mapping": _COLUMN_MAPPING,
            "date_format": {"type": "string", "enum": ["iso", "slash", "compact"]},
            "amount_unit": {"type": "string", "enum": ["yuan", "fen"]},
            "duplicate_policy": {"type": "string", "enum": ["skip", "include"]},
            "provider": _STATEMENT_PROVIDER,
        }, ("file_name", "content_base64")),
        "scope": "own_write", "host_permission": "write", "run": _transaction_import_preview,
    },
    "commit_transaction_import": {
        "description": "Import only the valid rows in an unexpired CSV preview after explicit confirmation; a preview can be committed only once and only while the ledger is unchanged.",
        "inputSchema": _object({
            "preview_id": _STRING, "preview_sha256": _STRING, "confirmed": _CONFIRMED,
        }, ("preview_id", "preview_sha256", "confirmed")),
        "scope": "own_write", "host_permission": "write", "run": _commit_transaction_import,
    },
    "attach_transaction_file": {
        "description": "Store a user-selected PDF or image as a SQLite BLOB linked to one current-ledger transaction; file paths are not accepted.",
        "inputSchema": _object({
            "transaction_id": _STRING, "expected_version": {"type": "integer", "minimum": 1},
            "file_name": _STRING, "mime_type": {"type": "string", "enum": ["application/pdf", "image/jpeg", "image/png", "image/webp"]},
            "content_base64": _STRING,
        }, ("transaction_id", "expected_version", "file_name", "mime_type", "content_base64")),
        "scope": "own_write", "host_permission": "write", "run": _attach_transaction_file,
    },
    "list_transaction_files": {
        "description": "List attachment metadata linked to one transaction in the caller's ledger.",
        "inputSchema": _object({"transaction_id": _STRING}, ("transaction_id",)),
        "scope": "own_read", "host_permission": "detail_read", "run": _list_transaction_files,
    },
    "get_transaction_attachment": {
        "description": "Read an attachment BLOB linked to a transaction in the trusted local web session.",
        "inputSchema": _object({"attachment_id": _STRING}, ("attachment_id",)),
        "scope": "own_read", "host_permission": "detail_read", "sources": ["web"], "run": _get_transaction_attachment,
    },
    "remove_transaction_file": {
        "description": "Remove the stored attachment content after matching its current version in the trusted local web session.",
        "inputSchema": _object({"attachment_id": _STRING, "expected_version": {"type": "integer", "minimum": 1}},
                             ("attachment_id", "expected_version")),
        "scope": "own_write", "host_permission": "write", "run": _remove_transaction_file,
    },
    "upsert_redaction_profile": {
        "description": "Create or update a named, ledger-scoped export redaction profile for merchant, note, and date precision.",
        "inputSchema": _object({
            "profile_id": _STRING, "expected_version": {"type": "integer", "minimum": 1}, "name": _STRING,
            "merchant_policy": {"type": "string", "enum": ["keep", "mask", "omit"]},
            "note_policy": {"type": "string", "enum": ["keep", "mask", "omit"]},
            "date_precision": {"type": "string", "enum": ["day", "month", "year", "omit"]},
        }, ("name", "merchant_policy", "note_policy", "date_precision")),
        "scope": "own_write", "host_permission": "write", "run": _upsert_redaction_profile,
    },
    "list_redaction_profiles": {
        "description": "List redaction profiles for the caller's current ledger.",
        "inputSchema": _object({}),
        "scope": "own_read", "host_permission": "detail_read", "run": _list_redaction_profiles,
    },
    "preview_redacted_export": {
        "description": "Preview the rows and fields that an export redaction profile will expose for an explicit period and optional project.",
        "inputSchema": _object({
            "profile_id": _STRING, "start_date": _DATE, "end_date": _DATE, "project_id": _STRING,
        }, ("profile_id", "start_date", "end_date")),
        "scope": "own_read", "host_permission": "detail_read", "run": _preview_redacted_export,
    },
    "list_security_access": {
        "description": "Read metadata-only incoming access events and outgoing token permission changes for an explicit date range.",
        "inputSchema": _object({
            "start_date": _DATE, "end_date": _DATE,
            "direction": {"type": "string", "enum": ["incoming", "outgoing"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
        }, ("start_date", "end_date")),
        "scope": "own_read", "host_permission": "detail_read", "run": _list_security_access,
    },
    "list_host_tokens": {
        "description": "List host token labels, active state, versions, and scopes for the authenticated local identity; never returns credentials.",
        "inputSchema": _object({}),
        "scope": "own_read", "host_permission": "detail_read", "run": _list_host_tokens,
    },
    "set_host_token_scopes": {
        "description": "Replace scopes on one of the caller's own host tokens after version confirmation; available only in the local web session.",
        "inputSchema": _object({
            "token_id": _STRING, "expected_version": {"type": "integer", "minimum": 1},
            "scopes": {"type": "array", "uniqueItems": True,
                        "items": {"type": "string", "enum": sorted(store.HOST_PERMISSIONS)}},
        }, ("token_id", "expected_version", "scopes")),
        "scope": "own_write", "host_permission": "write", "sources": ["web"], "run": _set_host_token_scopes,
    },
    "revoke_host_token": {
        "description": "Revoke one of the caller's own host tokens after version confirmation; available only in the local web session.",
        "inputSchema": _object({"token_id": _STRING, "expected_version": {"type": "integer", "minimum": 1}},
                             ("token_id", "expected_version")),
        "scope": "own_write", "host_permission": "write", "sources": ["web"], "run": _revoke_host_token,
    },
    "backup_drill": {
        "description": "Validate an existing backup in an isolated in-memory SQLite copy and compare ledger and recurring summaries without restoring the live database.",
        "inputSchema": _object({"backup_id": _STRING}, ("backup_id",)),
        "scope": "own_write", "host_permission": "write", "requires_scope": "maintenance",
        "run": _backup_drill,
    },
    "preview_maintenance_issues": {
        "description": "Diagnose split allocation mismatches, excessive or orphaned refunds and reimbursements, and orphan project links in the caller's ledger.",
        "inputSchema": _object({}),
        "scope": "own_read", "host_permission": "detail_read", "run": _preview_maintenance_issues,
    },
    "repair_maintenance_issue": {
        "description": "Apply one specific repair to one issue only after explicit confirmation and a matching issue fingerprint.",
        "inputSchema": _object({
            "issue_id": _STRING, "issue_fingerprint": _STRING, "confirmed": _CONFIRMED,
            "resolution": _REPAIR_RESOLUTION,
        }, ("issue_id", "issue_fingerprint", "confirmed", "resolution")),
        "scope": "own_write", "host_permission": "write", "requires_scope": "maintenance",
        "sources": ["web"], "run": _repair_maintenance_issue,
    },
}
