from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import unicodedata
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import ledger, maintenance, movements, store
from .contract import BusinessError

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS bookkeeping_drafts (
    ledger_id TEXT NOT NULL REFERENCES yuxu_ledgers(ledger_id),
    principal_id TEXT NOT NULL REFERENCES yuxu_principals(principal_id),
    draft_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'committed', 'discarded')),
    items_json TEXT NOT NULL,
    evidence_name TEXT,
    evidence_mime TEXT,
    evidence_sha256 TEXT,
    evidence BLOB,
    result_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, draft_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_bookkeeping_evidence
ON bookkeeping_drafts(ledger_id, evidence_sha256)
WHERE evidence_sha256 IS NOT NULL AND status != 'discarded';

CREATE TABLE IF NOT EXISTS bookkeeping_sources (
    ledger_id TEXT NOT NULL REFERENCES yuxu_ledgers(ledger_id),
    provider TEXT NOT NULL,
    external_id_sha256 TEXT NOT NULL,
    action TEXT NOT NULL,
    target_id TEXT NOT NULL,
    PRIMARY KEY (ledger_id, provider, external_id_sha256)
);
"""

_STATES = ["open", "committed", "discarded", "all"]
_OPERATIONS = {
    "income": ("add_transactions", ("amount_minor", "occurred_on")),
    "expense": ("add_transactions", ("amount_minor", "occurred_on")),
    "transfer": ("transfer", ("amount_minor", "occurred_on", "source_account_id", "destination_account_id")),
    "credit_card_repayment": ("record_credit_card_repayment", ("amount_minor", "occurred_on", "account_id", "source_account_id")),
    "refund": ("record_refund", ("amount_minor", "occurred_on", "transaction_id", "expected_version")),
    "reimbursement_receipt": ("record_reimbursement_receipt", ("amount_minor", "occurred_on", "reimbursement_id", "expected_version")),
}
_MIMES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "application/pdf": ".pdf"}
_PROVIDERS = ["wechat", "alipay", "bank", "other"]
_ITEM = {
    "operation": {"type": "string", "enum": list(_OPERATIONS)},
    "amount_minor": {"type": "integer", "minimum": 1}, "occurred_on": ledger._DATE, "charged_on": ledger._DATE,
    "merchant": ledger._STRING, "note": ledger._STRING, "category_id": ledger._STRING,
    "payment_account_id": ledger._STRING, "source_account_id": ledger._STRING, "destination_account_id": ledger._STRING,
    "account_id": ledger._STRING, "transaction_id": ledger._STRING, "reimbursement_id": ledger._STRING,
    "expected_version": {"type": "integer", "minimum": 1},
    "provider": {"type": "string", "enum": _PROVIDERS}, "external_id": ledger._STRING,
}
_ITEMS = {"type": "array", "minItems": 1, "maxItems": 50, "items": ledger._object(_ITEM)}


def _fail(status: str, code: str, message: str, details: dict | None = None):
    raise BusinessError(status, message, details, code)


def _encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _items(raw: object) -> list[dict]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= 50:
        _fail("invalid_input", "invalid_items", "请提供 1 至 50 个候选条目。")
    items = []
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict) or item.keys() - _ITEM.keys():
            _fail("invalid_input", "invalid_item", "候选条目包含未知字段。", {"line": index})
        cleaned = {}
        for name, value in item.items():
            if ((name == "operation" and (not isinstance(value, str) or value not in _OPERATIONS))
                    or (name == "provider" and (not isinstance(value, str) or value not in _PROVIDERS))):
                _fail("invalid_input", "invalid_item", "候选条目的类型或来源不支持。", {"line": index, "field": name})
            if name in ("amount_minor", "expected_version"):
                ledger._amount(value, name)
            elif name in ("occurred_on", "charged_on"):
                ledger._date_value(value, name)
            elif not isinstance(value, str) or not 1 <= len(value.strip()) <= 300:
                _fail("invalid_input", "invalid_item", "候选条目的文本字段无效。", {"line": index, "field": name})
            cleaned[name] = value.strip() if isinstance(value, str) else value
        items.append(cleaned)
    return items


def _source(item: dict) -> tuple[str, str] | None:
    if item.get("provider") and item.get("external_id"):
        return item["provider"], hashlib.sha256(item["external_id"].encode("utf-8")).hexdigest()
    return None


def _known_source(conn, ledger_id, source):
    return conn.execute(
        "SELECT action, target_id FROM bookkeeping_sources WHERE ledger_id=? AND provider=? AND external_id_sha256=?",
        (ledger_id, *source),
    ).fetchone() if source else None


def _matches(conn, ctx, item):
    if item.get("operation") not in ("income", "expense") or not item.get("amount_minor") or not item.get("occurred_on"):
        return []
    rows = conn.execute(
        "SELECT t.transaction_id,t.version,t.category_id,t.occurred_on,t.amount_minor,t.kind,t.merchant,a.account_id "
        "FROM ledger_transactions t LEFT JOIN movement_transaction_accounts a "
        "ON a.ledger_id=t.ledger_id AND a.transaction_id=t.transaction_id "
        "WHERE t.ledger_id=? AND t.occurred_on=? AND t.kind=? AND t.amount_minor=? "
        "AND t.voided_at IS NULL ORDER BY t.created_at DESC, t.transaction_id LIMIT 10",
        (ctx["ledger_id"], item["occurred_on"], item["operation"], item["amount_minor"]),
    ).fetchall()
    matches = []
    for row in rows:
        reasons = ["same_date", "same_direction", "same_amount"]
        if item.get("merchant") and ledger._merchant_matches(conn, ctx["ledger_id"], row["merchant"], item["merchant"]):
            reasons.append("same_merchant")
        if item.get("payment_account_id") and row["account_id"] == item["payment_account_id"]:
            reasons.append("same_payment_account")
        matches.append({"transaction_id": row["transaction_id"], "version": row["version"],
                        "category_id": row["category_id"], "reasons": reasons})
    return matches


def _lines(conn, ctx, items):
    output = []
    seen_sources = set()
    for index, item in enumerate(items, 1):
        operation = item.get("operation")
        action, required = _OPERATIONS[operation] if operation else (None, ("operation", "amount_minor", "occurred_on"))
        missing = [field for field in required if field not in item]
        if bool(item.get("provider")) != bool(item.get("external_id")):
            missing.append("external_id" if "provider" in item else "provider")
        source = _source(item)
        known = _known_source(conn, ctx["ledger_id"], source)
        same_batch = source in seen_sources if source else False
        if source:
            seen_sources.add(source)
        normalized = {key: value for key, value in item.items() if key != "external_id"}
        if operation in ("income", "expense") and not missing:
            candidate = _record_fields(item)
            record = ledger._record_input(conn, ctx, candidate)
            normalized["category_id"] = record["category_id"]
            if item.get("payment_account_id"):
                account = movements._account(conn, ctx["ledger_id"], item["payment_account_id"])
                normalized["payment_account_name"] = account["name"]
        similar_rows = [
            other_index for other_index, other in enumerate(items, 1)
            if index != other_index and not missing and operation in ("income", "expense")
            and (operation, item["occurred_on"], item["amount_minor"]) ==
            (other.get("operation"), other.get("occurred_on"), other.get("amount_minor"))
            and not (source and _source(other) and source != _source(other))
        ]
        output.append({"line": index, "fields": normalized, "action": action, "missing_fields": missing,
                       "known_source": dict(known) if known else None, "same_source_in_batch": same_batch,
                       "duplicate_candidates": _matches(conn, ctx, item), "similar_draft_lines": similar_rows})
    return output


def _record_fields(item):
    keys = {"amount_minor", "occurred_on", "charged_on", "category_id", "merchant", "note"}
    return {"kind": item["operation"], **{key: value for key, value in item.items() if key in keys}}


def _draft(conn, ctx, draft_id):
    row = conn.execute(
        "SELECT * FROM bookkeeping_drafts WHERE ledger_id=? AND principal_id=? AND draft_id=?",
        (ctx["ledger_id"], ctx["principal_id"], ledger._required_text(draft_id, "draft_id")),
    ).fetchone()
    if row is None:
        _fail("not_found", "bookkeeping_draft_not_found", "当前账本找不到该待办草稿。")
    return row


def _draft_data(conn, ctx, row):
    data = {"draft_id": row["draft_id"], "version": row["version"], "status": row["status"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "source": "file" if row["evidence_sha256"] else "manual",
            "evidence_available": row["evidence"] is not None}
    if row["status"] == "open":
        data["items"] = _lines(conn, ctx, json.loads(row["items_json"]))
    else:
        data["result"] = json.loads(row["result_json"]) if row["result_json"] else None
    return data


def _get(conn, ctx, params):
    return _draft_data(conn, ctx, _draft(conn, ctx, params.get("draft_id")))


def _preview(conn, ctx, params):
    return {"items": _lines(conn, ctx, _items(params.get("items"))), "currency": "CNY",
            "business_date": ctx["today"], "timezone": ctx["timezone"]}


def _save(conn, ctx, params):
    if params.get("draft_id"):
        row = _draft(conn, ctx, params["draft_id"])
        _check_open(row, params.get("expected_version"))
        if params.get("items") is not None and params.get("patches") is not None:
            _fail("invalid_input", "invalid_item", "请选择替换全部条目或修改个别条目。")
        values = params.get("items")
        if values is None:
            values = json.loads(row["items_json"])
            patches = params.get("patches")
            if not isinstance(patches, list) or not patches:
                _fail("invalid_input", "invalid_item", "请提供需要修改的条目。")
            processed = set()
            for patch in patches:
                if (not isinstance(patch, dict) or type(patch.get("line")) is not int
                        or not 1 <= patch["line"] <= len(values) or patch["line"] in processed
                        or not isinstance(patch.get("fields"), dict)):
                    _fail("invalid_input", "invalid_item", "补充条目的编号或字段无效。")
                line = patch["line"]
                processed.add(line)
                merged = {**values[line - 1], **patch["fields"]}
                values[line - 1] = {key: value for key, value in merged.items() if value is not None}
        items = _items(values)
        _lines(conn, ctx, items)
        conn.execute("UPDATE bookkeeping_drafts SET items_json=?, version=version+1, updated_at=? WHERE ledger_id=? AND draft_id=?",
                     (_encode(items), store.utc_now(), ctx["ledger_id"], row["draft_id"]))
        return _get(conn, ctx, {"draft_id": row["draft_id"]})
    if "patches" in params or "expected_version" in params:
        _fail("invalid_input", "invalid_item", "请为已有草稿指定编号。")
    items = _items(params.get("items"))
    return _create(conn, ctx, items)


def _create(conn, ctx, items, name=None, mime=None, content=None, digest=None):
    _lines(conn, ctx, items)
    draft_id = str(uuid.uuid4())
    now = store.utc_now()
    conn.execute(
        "INSERT INTO bookkeeping_drafts VALUES (?,?,?,1,'open',?,?,?,?,?,NULL,?,?)",
        (ctx["ledger_id"], ctx["principal_id"], draft_id, _encode(items), name, mime, digest, content, now, now),
    )
    return _get(conn, ctx, {"draft_id": draft_id})


def _decode_receipt(params):
    mime = params.get("mime_type")
    extension = _MIMES.get(mime) if isinstance(mime, str) else None
    if not extension:
        _fail("invalid_input", "unsupported_receipt", "凭证支持 PNG、JPEG、WebP 或 PDF。")
    content = maintenance._decode_base64(params.get("content_base64"), "content_base64", 10 * 1024 * 1024)
    signatures = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff", "image/webp": b"RIFF", "application/pdf": b"%PDF-"}
    if not content.startswith(signatures[mime]) or mime == "image/webp" and content[8:12] != b"WEBP":
        _fail("invalid_input", "unsupported_receipt", "凭证内容与文件格式不符。")
    return mime, extension, content


def _local_ocr(content, extension):
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    with tempfile.TemporaryDirectory(prefix="yuxu-receipt-") as temp:
        path = Path(temp) / ("receipt" + extension)
        path.write_bytes(content)
        command = [str(executable), "-NoProfile", "-NonInteractive", "-File",
                   str(Path(__file__).with_name("read_receipt.ps1")), "-Path", str(path)]
        if extension == ".pdf":
            command.append("-Pdf")
        try:
            result = subprocess.run(command, capture_output=True, timeout=22, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
            return json.loads(result.stdout.decode("utf-8"))
        except (OSError, ValueError, subprocess.SubprocessError):
            return None


def _ocr_item(lines):
    item = {}
    amounts = set()
    dates = set()
    kinds = set()
    lines = [line for line in lines if isinstance(line, str)]
    for line in lines:
        text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", line)).replace("·", ".")
        text = re.sub(r"(?<=\d)[一–—−](?=\d)", "-", text)
        if re.search(r"支付成功|付款成功|交易成功|已扣款|已支付|已付款|Payment(?:successful|complete)", text, re.I):
            kinds.add("expense")
        if re.search(r"收款成功|已收款|到账成功|Received", text, re.I):
            kinds.add("income")
        if re.search(r"实付|实收|已付金额|支付金额|付款金额|Amountpaid|Paidamount", text, re.I):
            amounts.update(int(yuan.replace(",", "")) * 100 + int(fraction.ljust(2, "0") or 0)
                           for yuan, fraction in re.findall(r"(?<![\d.,])(\d[\d,]*)(?:\.(\d{1,2}))?(?![\d.])", text))
        if re.search(r"时间|日期|交易日|入账日|^日20|Date|Time", text, re.I):
            for year, month, day in re.findall(r"(20\d{2})[年/.-](\d{1,2})[月/.-](\d{1,2})", text):
                try:
                    dates.add(ledger._date_value(f"{year}-{int(month):02d}-{int(day):02d}", "occurred_on"))
                except BusinessError:
                    continue
    if len(kinds) == 1:
        item["operation"] = kinds.pop()
    if len(amounts) == 1 and next(iter(amounts)) > 0:
        item["amount_minor"] = amounts.pop()
    if len(dates) == 1:
        item["occurred_on"] = dates.pop()
    return item


def prepare_receipt(conn, ctx, params):
    mime, extension, content = _decode_receipt(params)
    digest = hashlib.sha256(content).hexdigest()
    known = conn.execute(
        "SELECT 1 FROM bookkeeping_drafts WHERE ledger_id=? AND evidence_sha256=? AND status!='discarded'",
        (ctx["ledger_id"], digest),
    ).fetchone()
    name = maintenance._safe_file_name(params.get("file_name"))
    if params.get("items") is not None:
        items = _items(params["items"])
        recognition = "provided_candidates"
    elif not known:
        lines = _local_ocr(content, extension)
        items = [_ocr_item(lines or [])]
        recognition = "local_ocr" if lines else "manual_review"
    else:
        items = [{}]
        recognition = "manual_review"
    return (name, mime, content, digest, items, recognition)


def _stage_receipt(conn, ctx, params):
    name, mime, content, digest, items, recognition = ctx.get("prepared_receipt") or prepare_receipt(conn, ctx, params)
    existing = conn.execute(
        "SELECT * FROM bookkeeping_drafts WHERE ledger_id=? AND principal_id=? AND evidence_sha256=? AND status!='discarded'",
        (ctx["ledger_id"], ctx["principal_id"], digest),
    ).fetchone()
    if existing:
        return {**_draft_data(conn, ctx, existing), "evidence_reused": True}
    return {**_create(conn, ctx, items, name, mime, content, digest), "recognition": recognition}


def _check_open(row, expected_version):
    if row["status"] != "open" or type(expected_version) is not int or expected_version != row["version"]:
        _fail("conflict", "bookkeeping_draft_changed", "草稿已更新或结束，请重新打开。",
              {"actual_version": row["version"], "status": row["status"]})


def _discard(conn, ctx, params):
    row = _draft(conn, ctx, params.get("draft_id"))
    _check_open(row, params.get("expected_version"))
    conn.execute("UPDATE bookkeeping_drafts SET status='discarded', version=version+1, evidence=NULL, updated_at=? WHERE ledger_id=? AND draft_id=?",
                 (store.utc_now(), ctx["ledger_id"], row["draft_id"]))
    return _get(conn, ctx, {"draft_id": row["draft_id"]})


def _list(conn, ctx, params):
    state = params.get("status", "open")
    source = params.get("source", "all")
    if state not in _STATES or source not in ("all", "file", "manual"):
        _fail("invalid_input", "invalid_draft_filter", "待办筛选无效。")
    limit = params.get("limit", 30)
    if type(limit) is not int or not 1 <= limit <= 100:
        _fail("invalid_input", "invalid_draft_filter", "每页最多 100 条。")
    clauses = ["ledger_id=?", "principal_id=?"]
    values = [ctx["ledger_id"], ctx["principal_id"]]
    if state != "all":
        clauses.append("status=?")
        values.append(state)
    if source != "all":
        clauses.append("evidence_sha256 IS " + ("NOT NULL" if source == "file" else "NULL"))
    bounds = {}
    for key, operator in (("start_date", ">="), ("end_date", "<")):
        if key in params:
            day = date.fromisoformat(ledger._date_value(params[key], key))
            bounds[key] = day
            if key == "end_date" and day == date.max:
                continue
            local_start = datetime.combine(day + timedelta(days=key == "end_date"), time.min, ZoneInfo(ctx["timezone"]))
            clauses.append(f"created_at {operator} ?")
            values.append(local_start.astimezone(timezone.utc).isoformat(timespec="seconds"))
    if len(bounds) == 2 and bounds["start_date"] > bounds["end_date"]:
        _fail("invalid_input", "invalid_draft_filter", "开始日期不能晚于结束日期。")
    rows = conn.execute("SELECT draft_id,version,status,created_at,updated_at,evidence_sha256 IS NOT NULL AS has_evidence "
                        "FROM bookkeeping_drafts WHERE " + " AND ".join(clauses)
                        + " ORDER BY updated_at DESC,draft_id LIMIT ?", (*values, limit + 1)).fetchall()
    return {"drafts": [dict(row) for row in rows[:limit]], "truncated": len(rows) > limit}


def _commit(conn, ctx, params):
    row = _draft(conn, ctx, params.get("draft_id"))
    _check_open(row, params.get("expected_version"))
    if params.get("confirmed") is not True:
        _fail("needs_clarification", "bookkeeping_confirmation_required", "请在看到草稿和疑似重复项后确认。")
    items = json.loads(row["items_json"])
    lines = _lines(conn, ctx, items)
    selections = params.get("decisions", [])
    if not isinstance(selections, list) or any(not isinstance(item, dict) or type(item.get("line")) is not int
                                               or not 1 <= item["line"] <= len(lines) for item in selections):
        _fail("invalid_input", "invalid_decisions", "查重选项无效。")
    decisions = {choice["line"]: choice for choice in selections}
    if len(decisions) != len(selections):
        _fail("invalid_input", "invalid_decisions", "每个条目只允许一个查重选择。")
    # A batch with funds operations has one transaction and one receipt for all changes.
    # Distinct ledger inserts are grouped below so their undo effects share an operation.
    to_add, earmarked, linked, events = [], [], [], []
    for line, item in zip(lines, items):
        index = line["line"]
        choice = decisions.get(index, {})
        decision = choice.get("decision")
        source = _source(item)
        known = _known_source(conn, ctx["ledger_id"], source)
        if known:
            linked.append({"line": index, "matched_source": True, **dict(known)})
            continue
        if decision not in (None, "separate", "link", "skip"):
            _fail("invalid_input", "invalid_decisions", "查重选项无效。")
        if decision == "skip":
            continue
        if line["missing_fields"]:
            _fail("needs_clarification", "bookkeeping_missing_fields", "候选账单缺少必填信息。",
                  {"line": index, "fields": line["missing_fields"]})
        if line["same_source_in_batch"]:
            _fail("conflict", "duplicate_source_in_batch", "同一来源订单在草稿中出现多次，请先合并。", {"line": index})
        if (line["duplicate_candidates"] or line["similar_draft_lines"]) and decision not in ("separate", "link"):
            _fail("needs_clarification", "bookkeeping_duplicate_choice", "请选择关联旧账、另记一笔或跳过。",
                  {"line": index, "candidates": line["duplicate_candidates"]})
        if decision == "link":
            target = choice.get("transaction_id")
            if not any(candidate["transaction_id"] == target and candidate["version"] == choice.get("expected_version")
                       for candidate in line["duplicate_candidates"]):
                _fail("conflict", "bookkeeping_candidate_changed", "疑似重复流水已变化，请重新核对。", {"line": index})
            linked.append({"line": index, "action": "add_transactions", "target_id": target})
            earmarked.append((source, "add_transactions", target))
        elif line["action"] == "add_transactions":
            to_add.append((line, item))
        else:
            action, required = _OPERATIONS[item["operation"]]
            fields = {key: item[key] for key in required}
            if item["operation"] == "refund":
                fields.update({key: item[key] for key in ("account_id", "charged_on") if key in item})
            if item["operation"] == "reimbursement_receipt" and "account_id" in item:
                fields["account_id"] = item["account_id"]
            result = movements.ACTIONS[action]["run"](conn, ctx, fields)
            event = conn.execute("SELECT event_id FROM movement_events WHERE ledger_id=? AND request_id=? ORDER BY rowid DESC LIMIT 1",
                                 (ctx["ledger_id"], ctx["request_id"])).fetchone()
            earmarked.append((source, action, event["event_id"]))
            events.append({"line": index, "action": action, "event_id": event["event_id"],
                           "result": {key: value for key, value in result.items() if key != "ledger"}})
    result = ledger._add_transactions(conn, ctx, {"transactions": [_record_fields(item) for _, item in to_add]}) if to_add else {}
    for (line, item), transaction in zip(to_add, result.get("transactions", [])):
        target = transaction["transaction_id"]
        earmarked.append((_source(item), "add_transactions", target))
        if item.get("payment_account_id"):
            movements._record_account_transaction(conn, ctx, {"transaction_id": target, "expected_version": transaction["version"],
                                                              "account_id": item["payment_account_id"]})
    if row["evidence"] is not None:
        targets = {entry["target_id"] for entry in linked if entry["action"] == "add_transactions"}
        targets.update(result.get("transaction_ids", []))
        encoded = base64.b64encode(row["evidence"]).decode("ascii")
        for target in targets:
            transaction = ledger._get_transaction(conn, ctx["ledger_id"], target)
            duplicate = conn.execute("SELECT 1 FROM maintenance_transaction_files WHERE ledger_id=? AND transaction_id=? "
                                     "AND content_sha256=? AND removed_at IS NULL",
                                     (ctx["ledger_id"], target, row["evidence_sha256"])).fetchone()
            if not duplicate and transaction["voided_at"] is None:
                maintenance._attach_transaction_file(conn, ctx, {"transaction_id": target, "expected_version": transaction["version"],
                    "file_name": row["evidence_name"], "mime_type": row["evidence_mime"], "content_base64": encoded})
    for source, action, target in earmarked:
        if source:
            conn.execute("INSERT INTO bookkeeping_sources VALUES (?,?,?,?,?)", (ctx["ledger_id"], *source, action, target))
    receipt = {"draft_id": row["draft_id"], "transaction_ids": result.get("transaction_ids", []),
               "transactions": result.get("transactions", []), "warnings": result.get("warnings", []),
               "linked": linked, "movement_results": events, "request_id": ctx["request_id"]}
    conn.execute("UPDATE bookkeeping_drafts SET status='committed', version=version+1, result_json=?, "
                 "evidence=NULL, updated_at=? WHERE ledger_id=? AND draft_id=?",
                 (_encode(receipt), store.utc_now(), ctx["ledger_id"], row["draft_id"]))
    return receipt


def _action(description, fields, required, run, write=False):
    return {"description": description, "inputSchema": ledger._object(fields, required),
            "scope": "own_write" if write else "own_read", "run": run}


_DRAFT = {"draft_id": ledger._STRING}
_VERSION = {"expected_version": {"type": "integer", "minimum": 1}}
ACTIONS = {
    "preview_bookkeeping": _action("Validate candidate entries, show the precise ledger date, category and possible duplicates before any write.",
                                   {"items": _ITEMS}, ("items",), _preview),
    "upsert_bookkeeping_draft": _action("Create a bookkeeping draft or replace/patch items in an existing versioned draft.",
                                       {**_DRAFT, **_VERSION, "items": _ITEMS,
                                        "patches": {"type": "array", "items": ledger._object({"line": {"type":"integer","minimum":1},
                                            "fields": ledger._object({key: {"anyOf": [schema, {"type": "null"}]}
                                                                     for key, schema in _ITEM.items()})}, ("line", "fields"))}}, (), _save, True),
    "stage_receipt_bookkeeping": _action("Create or recover a local screenshot, receipt or PDF draft. Omit items to use Windows OCR; review the result before posting.",
                                         {"file_name": ledger._STRING, "mime_type": {"type": "string", "enum": list(_MIMES)},
                                          "content_base64": ledger._STRING, "items": _ITEMS},
                                         ("file_name", "mime_type", "content_base64"), _stage_receipt, True),
    "get_bookkeeping_draft": _action("Read an individual bookkeeping draft and its live duplicate candidates.", _DRAFT, ("draft_id",), _get),
    "list_bookkeeping_drafts": _action("List your own continuing bookkeeping tasks across local hosts.",
                                      {"status": {"type": "string", "enum": _STATES}, "source": {"type": "string", "enum": ["all","file","manual"]},
                                       "start_date": ledger._DATE, "end_date": ledger._DATE, "limit": {"type":"integer","minimum":1,"maximum":100}},
                                      (), _list),
    "discard_bookkeeping_draft": _action("Discard this pending draft version, releasing its unposted evidence.",
                                         {**_DRAFT, **_VERSION}, ("draft_id","expected_version"), _discard, True),
    "commit_bookkeeping_draft": _action("Post explicitly confirmed approved lines from one draft version. Choose link, separate or skip for possible duplicates.",
                                        {**_DRAFT, **_VERSION, "confirmed": {"type":"boolean","const":True},
                                         "decisions": {"type":"array","items":ledger._object({
                                             "line":{"type":"integer","minimum":1}, "decision":{"type":"string","enum":["link","separate","skip"]},
                                             "transaction_id":ledger._STRING, **_VERSION}, ("line","decision"))}},
                                        ("draft_id","expected_version","confirmed"), _commit, True),
}
