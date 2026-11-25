from __future__ import annotations

import copy
import hashlib
import hmac
import json
import mimetypes
import secrets
import threading
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

from . import __version__
from .contract import BusinessError, HTTP_STATUS, envelope, error_body, sanitize_mcp_output, tool_output_schema
from . import bookkeeping, files, insights, ledger, maintenance, movements, planning, recurring, report_cards, store

SESSION_COOKIE = "yuxu_session"
REQUEST_STATUS_ACTION = {
    "description": (
        "Check the durable receipt for a previous write using its target_request_id. "
        "If the result is unknown, retry the original write with the same request_id and identical arguments."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {"target_request_id": {"type": "string", "minLength": 1, "maxLength": 200}},
        "required": ["target_request_id"],
        "additionalProperties": False,
    },
    "scope": "own_read",
    "host_permission": "write",
}
SUMMARY_READ_ACTIONS = {
    "get_current_account", "list_accessible_accounts", "get_ledger_context", "get_workflow_guide",
    "get_account_access_policy", "get_period_summary", "get_category_breakdown", "get_spending_comparison",
    "get_cashflow_summary", "get_project_spending", "get_project_budget", "list_project_budgets",
    "get_monthly_category_budgets", "get_income_summary", "get_saving_goal_progress", "list_saving_goals",
    "evaluate_amount_expression", "list_categories", "list_payment_accounts", "get_credit_card_due",
    "get_reminder_settings", "list_backups", "list_export_presets",
}
HOST_PERMISSIONS = store.HOST_PERMISSIONS


def _host_permission(action_name: str, action: dict) -> str:
    declared = action.get("host_permission")
    if action_name == "get_request_status":
        if declared not in (None, "write"):
            raise ValueError("get_request_status must require host_permission=write")
        return "write"
    if action["scope"] == "own_write":
        if declared not in (None, "write", "export_summary"):
            raise ValueError(f"Write action {action_name} has an invalid host permission")
        return declared or "write"
    if declared is not None:
        if declared not in HOST_PERMISSIONS - {"write"}:
            raise ValueError(f"Read action {action_name} has an invalid host permission")
        return declared
    return "summary_read" if action_name in SUMMARY_READ_ACTIONS else "detail_read"


def _requested_host_permission(action_name: str, action: dict, params: dict) -> str:
    if action_name == "get_request_status":
        return "write"
    if action_name == "get_local_review_link" and params.get("target_kind") == "transaction":
        return "detail_read"
    if action_name == "get_cross_tab_report" and isinstance(params.get("dimensions"), list) and "merchant" in params["dimensions"]:
        return "detail_read"
    if action_name == "get_purchase_frequency" and params.get("group_by") == "merchant":
        return "detail_read"
    if action_name == "get_report_drilldown" and isinstance(params.get("group_values"), dict) and "merchant_ref" in params["group_values"]:
        return "detail_read"
    return _host_permission(action_name, action)


def _safe_audit_action(action_name: str) -> str:
    if action_name in (
        "authorize_private_transaction_fields", "set_host_token_scopes", "revoke_host_token",
        "create_host_token", "rotate_host_token",
    ):
        return "settings_update"
    return action_name


def _authorize_private_transaction_fields(conn, ctx: dict, params: dict) -> dict:
    if ctx["source"] != "web" or params.get("confirmed") is not True:
        raise BusinessError("forbidden", "请在本机网页中明确确认私密字段授权。", code="web_confirmation_required")
    token_id = params.get("token_id")
    transaction_id = params.get("transaction_id")
    fields = params.get("fields")
    if not isinstance(token_id, str) or not token_id.strip() or not isinstance(transaction_id, str):
        raise BusinessError("invalid_input", "宿主和流水必须明确指定。", code="invalid_private_field_grant")
    if not isinstance(fields, list) or not fields or any(not isinstance(field, str) or field not in ("merchant", "note") for field in fields) or len(fields) != len(set(fields)):
        raise BusinessError("invalid_input", "授权字段只能是商家或备注，且不得重复。", code="invalid_private_field_grant")
    token = conn.execute(
        "SELECT label, scopes_json FROM yuxu_api_tokens WHERE token_id = ? AND principal_id = ? AND revoked_at IS NULL",
        (token_id, ctx["principal_id"]),
    ).fetchone()
    if token is None or "detail_read" not in json.loads(token["scopes_json"]):
        raise BusinessError("forbidden", "所选宿主没有有效的明细读取权限。", code="host_permission_required")
    ledger._get_transaction(conn, ctx["ledger_id"], transaction_id)
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(minutes=3)).isoformat(timespec="seconds")
    conn.execute("DELETE FROM yuxu_private_read_grants WHERE expires_at <= ?", (now.isoformat(timespec="seconds"),))
    conn.execute(
        "INSERT INTO yuxu_private_read_grants "
        "(principal_id, ledger_id, token_id, transaction_id, fields_json, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(principal_id, ledger_id, token_id, transaction_id) DO UPDATE SET "
        "fields_json = excluded.fields_json, created_at = excluded.created_at, expires_at = excluded.expires_at",
        (ctx["principal_id"], ctx["ledger_id"], token_id, transaction_id,
         json.dumps(sorted(fields)), now.isoformat(timespec="seconds"), expires_at),
    )
    return {"token_label": token["label"], "transaction_id": transaction_id, "fields": sorted(fields), "expires_at": expires_at}


def _create_host_token(conn, ctx: dict, params: dict) -> dict:
    label = params.get("label")
    if not isinstance(label, str) or not 1 <= len(label.strip()) <= 80 or any(not char.isprintable() for char in label):
        raise BusinessError("invalid_input", "宿主名称须为 1 至 80 个可见字符。", code="invalid_host_label")
    scopes = params.get("scopes", list(store.DEFAULT_HOST_SCOPES))
    if not isinstance(scopes, list) or any(not isinstance(scope, str) or scope not in HOST_PERMISSIONS for scope in scopes) or len(scopes) != len(set(scopes)):
        raise BusinessError("invalid_input", "宿主权限必须为支持的权限，且不得重复。", code="invalid_host_scopes")
    return store.create_host_token(conn, ctx["principal_id"], label.strip(), sorted(scopes))


def _rotate_host_token(conn, ctx: dict, params: dict) -> dict:
    token_id = params.get("token_id")
    if not isinstance(token_id, str) or not token_id.strip():
        raise BusinessError("invalid_input", "必须指定要换发的宿主令牌。", code="invalid_host_token_id")
    return store.rotate_host_token(conn, ctx["principal_id"], token_id.strip())


def _get_local_review_link(conn, ctx: dict, params: dict) -> dict:
    target_kind = params.get("target_kind")
    if target_kind not in ("ledger", "period", "category", "transaction"):
        raise BusinessError("invalid_input", "目标类型无效。", code="invalid_review_target")
    target_fields = {
        "ledger": set(),
        "period": {"start_date", "end_date"},
        "category": {"category_id"},
        "transaction": {"transaction_id"},
    }[target_kind]
    extra_fields = set(params) - {"target_kind", "date_basis"} - target_fields
    if extra_fields:
        raise BusinessError("invalid_input", "目标类型不接受这些参数。", code="unexpected_parameters", details={"fields": sorted(extra_fields)})

    target = {"date_basis": insights._date_basis(params.get("date_basis", "occurred_on"))}
    if target_kind == "period":
        start_date = insights._date(params.get("start_date"), "start_date")
        end_date = insights._date(params.get("end_date"), "end_date")
        if end_date < start_date:
            raise BusinessError("invalid_input", "结束日期不能早于开始日期。", code="invalid_period")
        target.update({"start_date": start_date, "end_date": end_date})
    elif target_kind == "category":
        category_id = files._text(params.get("category_id"), "category_id")
        if conn.execute(
            "SELECT 1 FROM ledger_categories WHERE ledger_id = ? AND category_id = ?",
            (ctx["ledger_id"], category_id),
        ).fetchone() is None:
            raise BusinessError("not_found", "分类不存在。", code="not_found")
        target["category_id"] = category_id
    elif target_kind == "transaction":
        if ctx["cross_account"]:
            raise BusinessError("forbidden", "流水直达链接仅支持本人账本。", code="cross_account_forbidden")
        transaction_id = files._text(params.get("transaction_id"), "transaction_id")
        ledger._get_transaction(conn, ctx["ledger_id"], transaction_id)
        target["transaction_id"] = transaction_id

    now = datetime.now(timezone.utc)
    created_at = now.isoformat(timespec="seconds")
    expires_at = (now + timedelta(minutes=5)).isoformat(timespec="seconds")
    raw_ref = secrets.token_urlsafe(32)
    conn.execute("DELETE FROM yuxu_local_review_links WHERE expires_at <= ?", (created_at,))
    conn.execute(
        "INSERT INTO yuxu_local_review_links "
        "(review_ref_hash, principal_id, ledger_id, target_kind, target_json, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (store.hash_secret(raw_ref), ctx["principal_id"], ctx["ledger_id"], target_kind,
         json.dumps(target, sort_keys=True), created_at, expires_at),
    )
    return {"url": f"{ctx['origin']}/#review_ref={raw_ref}", "expires_at": expires_at, "target_kind": target_kind}


def _resolve_local_review_link(conn, ctx: dict, params: dict) -> dict:
    review_ref = params.get("review_ref")
    if not isinstance(review_ref, str) or not review_ref or len(review_ref) > 128:
        raise BusinessError("invalid_input", "直达引用无效。", code="invalid_review_ref")
    ref_hash = store.hash_secret(review_ref)
    row = conn.execute(
        "SELECT r.ledger_id, r.target_kind, r.target_json, r.expires_at, l.account_id "
        "FROM yuxu_local_review_links AS r JOIN yuxu_ledgers AS l ON l.ledger_id = r.ledger_id "
        "WHERE r.review_ref_hash = ? AND r.principal_id = ?",
        (ref_hash, ctx["principal_id"]),
    ).fetchone()
    if row is None:
        raise BusinessError("not_found", "直达链接不存在或已失效。", code="review_link_not_found")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if row["expires_at"] <= now:
        conn.execute("DELETE FROM yuxu_local_review_links WHERE review_ref_hash = ?", (ref_hash,))
        raise BusinessError("conflict", "直达链接已过期。", code="review_link_expired")
    target_scopes = store.access_scopes(conn, ctx["principal_id"], row["ledger_id"])
    if row["ledger_id"] == ctx["ledger_id"]:
        if "own_read" not in target_scopes:
            raise BusinessError("forbidden", "当前用户已无权访问此账本。", code="forbidden")
    elif "admin_spending" not in target_scopes:
        raise BusinessError("forbidden", "当前用户已无权跨账本访问此内容。", code="cross_account_forbidden")

    target = json.loads(row["target_json"])
    target["date_basis"] = insights._date_basis(target.get("date_basis", "occurred_on"))
    if row["target_kind"] == "category" and conn.execute(
        "SELECT 1 FROM ledger_categories WHERE ledger_id = ? AND category_id = ?",
        (row["ledger_id"], target["category_id"]),
    ).fetchone() is None:
        raise BusinessError("not_found", "分类不存在。", code="not_found")
    if row["target_kind"] == "transaction":
        if row["ledger_id"] != ctx["ledger_id"]:
            raise BusinessError("forbidden", "流水直达链接仅支持本人账本。", code="cross_account_forbidden")
        ledger._get_transaction(conn, row["ledger_id"], target["transaction_id"])
    return {"target_kind": row["target_kind"], "account_id": row["account_id"], **target}


def _get_transaction_with_private_fields(conn, ctx: dict, params: dict) -> dict:
    fields = params.get("private_fields")
    result = ledger.ACTIONS["get_transaction"]["run"](
        conn, ctx, {key: value for key, value in params.items() if key != "private_fields"},
    )
    if fields is None:
        return result
    if ctx["source"] != "mcp":
        raise BusinessError("forbidden", "私密字段读取只能由已授权的 MCP 宿主请求。", code="private_field_host_required")
    if not isinstance(fields, list) or not fields or any(not isinstance(field, str) or field not in ("merchant", "note") for field in fields) or len(fields) != len(set(fields)):
        raise BusinessError("invalid_input", "私密字段只能是商家或备注，且不得重复。", code="invalid_private_field_request")
    token_id = ctx.get("token_id")
    transaction_id = params.get("transaction_id")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    grant = conn.execute(
        "SELECT fields_json, expires_at FROM yuxu_private_read_grants "
        "WHERE principal_id = ? AND ledger_id = ? AND token_id = ? AND transaction_id = ?",
        (ctx["principal_id"], ctx["ledger_id"], token_id, transaction_id),
    ).fetchone()
    if grant is None or grant["expires_at"] <= now:
        if grant is not None:
            conn.execute(
                "DELETE FROM yuxu_private_read_grants WHERE principal_id = ? AND ledger_id = ? AND token_id = ? AND transaction_id = ?",
                (ctx["principal_id"], ctx["ledger_id"], token_id, transaction_id),
            )
        raise BusinessError("forbidden", "需要本人在本机网页为此宿主、这笔流水和指定字段进行有效的一次授权。", code="private_field_authorization_required")
    granted_fields = set(json.loads(grant["fields_json"]))
    if not set(fields).issubset(granted_fields):
        raise BusinessError("forbidden", "本次授权不包含请求的全部私密字段。", code="private_field_authorization_mismatch")
    conn.execute(
        "DELETE FROM yuxu_private_read_grants WHERE principal_id = ? AND ledger_id = ? AND token_id = ? AND transaction_id = ?",
        (ctx["principal_id"], ctx["ledger_id"], token_id, transaction_id),
    )
    result["_mcp_private_fields_authorized"] = sorted(fields)
    return result


_GET_TRANSACTION_ACTION = copy.deepcopy(ledger.ACTIONS["get_transaction"])
_GET_TRANSACTION_ACTION["description"] = (
    "Read one transaction in the caller's own ledger. Merchant details and notes are omitted by default; "
    "request private_fields only after the owner authorizes this host, transaction, and exact fields in the local web session."
)
_GET_TRANSACTION_ACTION["inputSchema"]["properties"]["private_fields"] = {
    "type": "array", "minItems": 1, "uniqueItems": True,
    "items": {"type": "string", "enum": ["merchant", "note"]},
}
_GET_TRANSACTION_ACTION["run"] = _get_transaction_with_private_fields
_GET_TRANSACTION_ACTION["host_permission"] = "detail_read"


PLATFORM_ACTIONS = {
    "get_local_review_link": {
        "description": "Create a five-minute local Web deep link for an authorized ledger, period, category, or own-ledger transaction. Transaction targets require detail_read and cannot target another ledger.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_kind": {"type": "string", "enum": ["ledger", "period", "category", "transaction"]},
                "date_basis": {"type": "string", "enum": ["occurred_on", "charged_on"]},
                "start_date": {"type": "string", "format": "date"},
                "end_date": {"type": "string", "format": "date"},
                "category_id": {"type": "string", "minLength": 1},
                "transaction_id": {"type": "string", "minLength": 1},
            },
            "required": ["target_kind"],
            "additionalProperties": False,
        },
        "scope": "admin_spending", "host_permission": "summary_read", "run": _get_local_review_link,
    },
    "resolve_local_review_link": {
        "description": "Resolve a short-lived local Web review reference after rechecking the authenticated principal and current ledger access.",
        "inputSchema": {
            "type": "object",
            "properties": {"review_ref": {"type": "string", "minLength": 1, "maxLength": 128}},
            "required": ["review_ref"],
            "additionalProperties": False,
        },
        "scope": "own_read", "host_permission": "summary_read", "sources": ["web"],
        "run": _resolve_local_review_link,
    },
    "get_current_account": {
        "description": "Return the authenticated local identity and its verified scopes on its own account.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "scope": "own_read",
    },
    "list_accessible_accounts": {
        "description": "List only accounts and role scopes explicitly granted to the authenticated local identity.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "scope": "own_read",
    },
    "get_ledger_context": {
        "description": "Return the current account context, or an explicitly selected account with its verified scopes. Omitting account_id always means the current account.",
        "inputSchema": {
            "type": "object",
            "properties": {"account_id": {"type": "string", "minLength": 1}},
            "additionalProperties": False,
        },
        "scope": "admin_spending",
    },
    "get_workflow_guide": {
        "description": "Return the full workflow guide, or query an exact base-01–base-20 / ext-01–ext-48 scenario for its visible tool order and required fields.",
        "inputSchema": {
            "type": "object",
            "properties": {"scenario": {"type": "string", "minLength": 1, "maxLength": 32}},
            "additionalProperties": False,
        },
        "scope": "own_read",
    },
    "get_account_access_policy": {
        "description": "Show the current user's own ledger access, incoming admin read grants, and outgoing admin read grants without exposing ledger IDs or records.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "scope": "own_read", "host_permission": "summary_read",
    },
    "authorize_private_transaction_fields": {
        "description": "After explicit confirmation in the local web session, authorize one listed detail_read host to read selected merchant or note fields on one transaction once within three minutes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "token_id": {"type": "string", "minLength": 1},
                "transaction_id": {"type": "string", "minLength": 1},
                "fields": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "string", "enum": ["merchant", "note"]}},
                "confirmed": {"type": "boolean", "enum": [True]},
            },
            "required": ["token_id", "transaction_id", "fields", "confirmed"],
            "additionalProperties": False,
        },
        "scope": "own_write", "host_permission": "write", "sources": ["web"], "no_receipt": True,
        "run": _authorize_private_transaction_fields,
    },
}
WORKFLOW_GUIDE = {
    "initialization": [
        "Call get_current_account to verify the identity attached to this MCP connection.",
        "Call get_workflow_guide and list_accessible_accounts before choosing an account or role.",
        "Call get_account_access_policy to inspect the current account's incoming and outgoing admin read grants; get_current_account shows this MCP host's own scopes. list_host_tokens requires detail_read and becomes available after the owner grants it.",
        "Omitting account_id always selects the current account. Cross-account spending reports require admin_spending; goal progress is read-only and requires admin_goal.",
        "Pass an explicit listed account_id to a cross-account action, and confirm the target's matching scope with get_ledger_context.",
    ],
    "writes": [
        "Give each write a stable request_id. Retry an identical write with the same request_id and identical arguments.",
        "If the result is uncertain, call get_request_status with target_request_id; do not invent a new request_id for the retry.",
        "apply_actions is limited to atomic project-budget/link and savings-goal/confirmed-progress combinations in the current ledger.",
        "Host token creation, rotation, scope changes, and one-time private-field grants intentionally have no write receipt; verify the token list with list_host_tokens and show a newly issued token only once.",
    ],
    "conversation_references": [
        "A shared MCP stdio process has no trusted conversation_id by default, so resolve_reference cannot identify 'the last' item across chats.",
        "Only a host that starts a separate MCP process for one conversation may set YUXU_ISOLATED_CONVERSATION=1; then the adapter supplies one connection-scoped ID.",
        "Without that trusted isolation, ask for a transaction ID or use explicit filters and dates. Tool arguments cannot set a trusted conversation_id.",
    ],
    "privacy": "MCP omits merchant details and notes by default. The owner may authorize one detail_read host in the same-origin web session for one transaction and selected merchant/note fields; the grant expires after three minutes and is consumed on one matching read. Cross-account private fields remain unavailable.",
    "host_permissions": [
        "A token can grant summary_read, detail_read, write, export_summary or export_artifact independently. The service checks them both when listing tools and when dispatching each call.",
        "The account owner can create a host token, rotate its key, change its scopes, or revoke it in the authenticated local web session; a newly issued token is displayed once and changes affect the next MCP request.",
    ],
    "recurring": [
        "Create a fixed-payment or subscription rule only after its first due date, interval, amount, and posting mode are clear.",
        "Use process_due_recurring to catch up missed periods; one rule and period can create only one transaction.",
        "Confirm a pending charge with confirm_recurring_transaction so the existing transaction is updated in place.",
        "Dated one-off income and expense plans appear in future cashflow only; they do not affect actual transaction totals.",
    ],
    "exports": [
        "Use export_report_card for a local PNG or PDF monthly report or bookkeeping receipt.",
        "Call preview_ledger_export first and show the ledger, date/project scope, row count, format, and selected fields. Sensitive fields require the owner to open review_url and confirm in Web.",
        "Get the file metadata with get_export_artifact. Set inline to true only for a privacy-safe PNG and an image-capable local host; otherwise use review_url.",
    ],
    "maintenance": [
        "Backup and restore preview actions require the independently selected local maintenance scope.",
        "MCP may create backups and preview them; the local web session can upload a ZIP to enter the same preview and two-ledger binding flow. Final restore is available only in the trusted same-origin web session after explicit confirmation and a pre-restore backup.",
    ],
    "private_fields": [
        "Merchant details and notes are omitted from MCP results by default.",
        "The owner can use authorize_private_transaction_fields in the authenticated local web session to authorize one active detail_read host, one own-ledger transaction, and selected merchant/note fields for one read within three minutes.",
        "Then request exactly those fields once with get_transaction.private_fields, or use merchant authorization once to copy a transaction into a draft. Cross-account reads and tool-parameter self-authorization are unavailable; MCP responses continue to filter private draft fields.",
    ],
    "entry_paths": [
        "Daily entries and queries (01–16): add_transactions for complete entries; upsert_transaction_draft then commit_transaction_draft for incomplete entries; copy_transaction_to_draft for a new draft from an existing record, followed by explicit new amount and occurrence date before commit; upsert_transaction_template/list_transaction_templates; search_transactions with filters/date_basis/page for matching records; get_period_summary, get_category_breakdown, get_spending_comparison, get_cashflow_summary, get_merchant_breakdown, and get_transaction. Corrections use update_transaction and list_transaction_revisions; estimates use list_estimated_transactions/confirm_estimated_transaction; bulk changes, category changes, tag merges, and duplicate merges require their preview action before the paired apply/merge action.",
        "Payment accounts and shared expenses (17–22): upsert_payment_account/list_payment_accounts/get_payment_account_activity/record_account_transaction; transfer and get_credit_card_due/record_credit_card_repayment; record_refund; record_reimbursement_advance/record_reimbursement_receipt/list_pending_reimbursements; record_personal_split/record_split_settlement; record_personal_loan/record_loan_repayment. Use returned IDs and versions for linked follow-up actions.",
        "Budgets, goals, and close (23–25, 30–32): upsert_project_budget/link_project_transaction/get_project_budget; upsert_monthly_category_budget/carry_forward_category_budget; upsert_saving_goal/record_saving_goal_change/get_saving_goal_progress; get_month_close_preview before close_month, and reopen_month with its explicit reason. apply_actions supports only the documented atomic project-budget/link and goal/progress combinations.",
        "Recurring plans and reminders (16, 26–28): create_recurring_rule, then process_due_recurring for missed periods; confirm_recurring_transaction updates the same pending transaction; set_recurring_exception and update_subscription_price revise a rule; create_cashflow_plan/list_cashflow_plans/cancel_cashflow_plan keep one-off plans separate from actual transactions. get_future_cashflow and get_reminder_settings/list_reminders/set_reminder_settings/refresh_reminders/mark_reminder_read cover projections, the four supported reminder types, and their state.",
        "Income and reports (29, 33–41): set_income_classification/get_income_summary; set_financial_cycle/get_financial_period_summary; get_cross_tab_report/get_purchase_frequency/get_rolling_spending_trend/get_spending_variance; get_expense_style_summary; create or update a budget scenario draft; upsert_monthly_report_template, run_monthly_report, save_monthly_report_snapshot, list_report_snapshots, get_monthly_report_snapshot, and compare_monthly_report_snapshot. Merchant grouping requires detail_read; reuse returned drilldown arguments and opaque merchant references unchanged.",
        "Import, sharing, and recovery (42–48): preview_transaction_import then commit_transaction_import; attach/list/get/remove a user-selected file with attach_transaction_file/list_transaction_files/get_transaction_attachment/remove_transaction_file; upsert_redaction_profile then preview_ledger_export and confirm_ledger_export; get_account_access_policy/list_security_access/list_host_tokens and manage own host scopes in Web; list_backups/create_backup/preview_restore/backup_drill as the separately selected maintenance user; preview_maintenance_issues then repair one fingerprinted issue in Web.",
        "Growth Step 01 (insights and local links): get_spending_comparison supports an explicit comparison period; get_budget_period_progress and get_annual_spending_calendar report period use and daily states; upsert_insight_view/list_insight_views/delete_insight_view manage saved views. In Web, clicking a category filters the same-page trend and recent rows. MCP can use get_local_review_link for a ledger, period, category, or own-ledger transaction; the short-lived link resolves through the logged-in Web session and current access checks.",
        "Growth Step 02 (reconciliation and maintenance): Web uses preview_transaction_reconciliation then confirmed commit_transaction_reconciliation for selected CSV-only rows; record_payment_account_balance_check/get_payment_account_balance_check save and read daily actual-versus-book snapshots; copy_transaction_to_draft creates a fresh draft; create_host_token/rotate_host_token issue one-time Web credentials; list/upsert/delete_csv_mapping_template save import column metadata; get_backup_schedule/set_backup_schedule control local retention, and ZIP upload enters preview_restore and the existing two-ledger binding flow.",
        "Use scenario record, receipt, correct, reconcile or export for the matching task. The MCP catalog contains only tools allowed by the current host scopes and account grants. Some maintenance and explicit-confirmation actions are Web-only; use the same-origin page when an action is absent from the catalog.",
    ],
}

WORKFLOW_SCENARIOS = {
    "record": ("记一笔或多笔", ["preview_bookkeeping", "upsert_bookkeeping_draft", "commit_bookkeeping_draft", "add_transactions", "get_request_status"]),
    "classify": ("新建分类、标签及为流水分类", ["list_categories", "list_tags", "create_category_or_tag", "apply_classification"]),
    "correct": ("按明确流水更正", ["search_transactions", "get_transaction", "update_transaction", "void_transaction", "undo_operation"]),
    "receipt": ("本机凭证转草稿", ["stage_receipt_bookkeeping", "list_bookkeeping_drafts", "get_bookkeeping_draft", "upsert_bookkeeping_draft", "commit_bookkeeping_draft"]),
    "reconcile": ("CSV 核对与网页选择入账", ["preview_transaction_reconciliation", "get_transaction_reconciliation", "commit_transaction_reconciliation"]),
    "export": ("生成和领取本机报表", ["export_report_card", "preview_ledger_export", "confirm_ledger_export", "get_export_artifact"]),
    "base-01": ("一次提交多笔收支", ["add_transactions"]),
    "base-02": ("缺少金额时保留日期和类别线索，补齐后提交", ["upsert_transaction_draft", "commit_transaction_draft"]),
    "base-03": ("唯一定位本对话上一笔并更正", ["resolve_reference", "get_transaction", "update_transaction"]),
    "base-04": ("撤销最近可撤销操作", ["undo_operation"]),
    "base-05": ("核对写入结果", ["get_request_status"]),
    "base-06": ("更新指定流水的分类和标签", ["get_transaction", "update_transaction"]),
    "base-07": ("按条件查询流水", ["search_transactions", "get_transaction"]),
    "base-08": ("按期间、标签和金额筛选并分页", ["search_transactions", "continue_transaction_search"]),
    "base-09": ("期间汇总与对比", ["get_period_summary", "get_spending_comparison"]),
    "base-10": ("分类图表与授权钻取", ["get_category_breakdown", "get_report_drilldown"]),
    "base-11": ("管理员指定账本的期间花销", ["get_current_account", "list_accessible_accounts", "get_ledger_context", "get_period_summary", "get_category_breakdown"]),
    "base-12": ("项目预算和关联流水", ["upsert_project_budget", "link_project_transaction", "get_project_budget", "apply_actions"]),
    "base-13": ("项目结项", ["get_project_budget", "close_project"]),
    "base-14": ("储蓄目标与进度", ["upsert_saving_goal", "record_saving_goal_change", "get_saving_goal_progress"]),
    "base-15": ("垫付与报销回收", ["record_reimbursement_advance", "record_reimbursement_receipt", "list_pending_reimbursements"]),
    "base-16": ("周期规则及漏期补跑", ["create_recurring_rule", "process_due_recurring", "confirm_recurring_transaction"]),
    "base-17": ("确认已有待确认续费账期", ["list_recurring_rules", "confirm_recurring_transaction"]),
    "base-18": ("提醒查看和处理", ["get_reminder_settings", "list_reminders", "refresh_reminders", "mark_reminder_read"]),
    "base-19": ("脱敏流水导出", ["list_export_presets", "preview_ledger_export", "confirm_ledger_export"]),
    "base-20": ("备份与恢复", ["create_backup", "list_backups", "preview_restore", "restore_backup"]),
    "ext-01": ("流水模板", ["upsert_transaction_template", "list_transaction_templates", "add_transactions"]),
    "ext-02": ("金额表达式", ["evaluate_amount_expression"]),
    "ext-03": ("未完成草稿", ["list_transaction_drafts", "upsert_transaction_draft", "commit_transaction_draft"]),
    "ext-04": ("估算确认", ["list_estimated_transactions", "confirm_estimated_transaction"]),
    "ext-05": ("拆分流水", ["set_transaction_splits", "get_transaction"]),
    "ext-06": ("商家归一名称", ["upsert_merchant_alias", "list_merchant_aliases", "search_transactions"]),
    "ext-07": ("合并分类", ["preview_category_change", "apply_category_change"]),
    "ext-08": ("合并标签", ["preview_tag_merge", "apply_tag_merge"]),
    "ext-09": ("分类规则", ["upsert_classification_rule", "preview_classification_rule", "apply_classification", "deactivate_classification_rule"]),
    "ext-10": ("批量更改流水", ["preview_transaction_bulk_change", "apply_transaction_bulk_change"]),
    "ext-11": ("合并重复流水", ["preview_duplicate_merge", "merge_duplicate_transactions"]),
    "ext-12": ("流水修订记录", ["update_transaction", "list_transaction_revisions"]),
    "ext-13": ("保存筛选条件", ["upsert_saved_transaction_filter", "list_saved_transaction_filters", "run_saved_transaction_filter"]),
    "ext-14": ("继续分页查询", ["search_transactions", "continue_transaction_search"]),
    "ext-15": ("月末核对清单", ["get_month_end_checklist", "get_month_close_preview"]),
    "ext-16": ("日期口径查询", ["search_transactions", "get_period_summary"]),
    "ext-17": ("支付账户", ["upsert_payment_account", "list_payment_accounts", "get_payment_account_activity", "record_account_transaction"]),
    "ext-18": ("账户间转账", ["record_account_transaction", "transfer"]),
    "ext-19": ("信用卡账单与还款", ["get_credit_card_due", "record_credit_card_repayment"]),
    "ext-20": ("退款登记", ["record_refund"]),
    "ext-21": ("个人分摊", ["record_personal_split", "record_split_settlement"]),
    "ext-22": ("个人借贷", ["record_personal_loan", "record_loan_repayment", "list_personal_loans"]),
    "ext-23": ("项目预算", ["upsert_project_budget", "link_project_transaction", "get_project_budget", "get_project_spending"]),
    "ext-24": ("分类月预算", ["upsert_monthly_category_budget", "get_monthly_category_budgets"]),
    "ext-25": ("预算结转", ["carry_forward_category_budget", "get_monthly_category_budgets"]),
    "ext-26": ("未来现金流计划", ["create_cashflow_plan", "list_cashflow_plans", "get_future_cashflow", "cancel_cashflow_plan"]),
    "ext-27": ("订阅涨价与单期例外", ["update_subscription_price", "set_recurring_exception", "list_recurring_rules"]),
    "ext-28": ("固定款单期跳过、改额或改期", ["set_recurring_exception", "get_future_cashflow", "list_recurring_rules"]),
    "ext-29": ("收入分类", ["set_income_classification", "get_income_summary"]),
    "ext-30": ("储蓄目标进度", ["list_saving_goals", "get_saving_goal_progress", "record_saving_goal_change"]),
    "ext-31": ("关联目标转账", ["associate_saving_goal_transfer", "get_saving_goal_progress"]),
    "ext-32": ("月结与重新开放", ["get_month_close_preview", "close_month", "reopen_month"]),
    "ext-33": ("财务周期", ["set_financial_cycle", "get_financial_cycle", "get_financial_period_summary"]),
    "ext-34": ("交叉报表及钻取", ["get_cross_tab_report", "get_report_drilldown"]),
    "ext-35": ("消费次数", ["get_purchase_frequency", "get_report_drilldown"]),
    "ext-36": ("期间支出归因", ["get_spending_variance", "get_report_drilldown"]),
    "ext-37": ("滚动趋势", ["get_rolling_spending_trend"]),
    "ext-38": ("刚性与弹性支出", ["upsert_expense_view_rule", "list_expense_view_rules", "get_expense_style_summary"]),
    "ext-39": ("预算情景试算", ["create_budget_scenario_draft", "update_budget_scenario_draft", "get_budget_scenario_draft", "list_budget_scenario_drafts"]),
    "ext-40": ("月报模板", ["upsert_monthly_report_template", "list_monthly_report_templates", "run_monthly_report"]),
    "ext-41": ("月报快照", ["save_monthly_report_snapshot", "list_report_snapshots", "get_monthly_report_snapshot", "compare_monthly_report_snapshot"]),
    "ext-42": ("CSV 导入", ["preview_transaction_import", "commit_transaction_import"]),
    "ext-43": ("流水附件", ["attach_transaction_file", "list_transaction_files", "get_transaction_attachment", "remove_transaction_file"]),
    "ext-44": ("脱敏导出配置", ["upsert_redaction_profile", "list_redaction_profiles", "preview_ledger_export", "confirm_ledger_export"]),
    "ext-45": ("访问记录和宿主权限", ["get_account_access_policy", "list_security_access", "list_host_tokens", "set_host_token_scopes", "revoke_host_token"]),
    "ext-46": ("创建、换发、调整或撤销 MCP 宿主令牌", ["list_host_tokens", "create_host_token", "rotate_host_token", "set_host_token_scopes", "revoke_host_token"]),
    "ext-47": ("隔离恢复演练", ["list_backups", "backup_drill"]),
    "ext-48": ("一致性诊断与修复", ["preview_maintenance_issues", "repair_maintenance_issue"]),
}


def _workflow_scenario_data(scenario: str, visible_catalog: list[dict]) -> dict:
    item = WORKFLOW_SCENARIOS.get(scenario)
    if item is None:
        return {
            "scenario": scenario,
            "found": False,
            "valid_scenarios": {"common": ["record", "classify", "correct", "receipt", "reconcile", "export"],
                                "base": [f"base-{number:02d}" for number in range(1, 21)],
                                "extension": [f"ext-{number:02d}" for number in range(1, 49)]},
        }
    title, order = item
    catalog_by_name = {tool["name"]: tool for tool in visible_catalog}
    visible = [catalog_by_name[name] for name in order if name in catalog_by_name]
    unavailable = [name for name in order if name not in catalog_by_name]
    has_preview = any(name.startswith("preview_") or name.endswith("_close_preview") for name in order)
    has_write = any(name in DOMAIN_ACTIONS and DOMAIN_ACTIONS[name]["scope"] == "own_write" for name in order)
    return {
        "scenario": scenario,
        "found": True,
        "title": title,
        "tool_order": [tool["name"] for tool in visible],
        "tools": [{"name": tool["name"], "description": tool["description"],
                   "required_fields": tool["inputSchema"].get("required", [])} for tool in visible],
        "unavailable_tools": unavailable,
        "unavailable_reasons": {
            name: "local_web_required" if (DOMAIN_ACTIONS.get(name) or PLATFORM_ACTIONS.get(name) or {}).get("sources") == ["web"]
            else "permission_required" for name in unavailable
        },
        "availability_note": "列表仅含当前身份、账本授权和宿主权限下可见的入口；缺失项需在本机网页处理或请账本所有者授予相应权限。" if unavailable else "全部入口当前可见。",
        "before_call": "先确认目标账本、日期/账期、金额、对象及归属；信息不全或存在多种解释时先询问用户。",
        "preview_and_confirmation": "存在预览入口时先展示真实预览及影响，获得用户确认后再调用对应提交/应用/恢复动作。" if has_preview else "按返回数据核对对象和版本；有明显歧义时先询问用户。",
        "write_receipt": "写操作使用稳定 request_id；结果不确定时先用 get_request_status(target_request_id) 查询，再按原 ID 和原参数重试。" if has_write else "此流程主要读取数据；如果含有明确写动作，仍应按该动作的幂等字段处理。",
    }

def _apply_actions(conn, ctx: dict, params: dict) -> dict:
    project_budget = params.get("project_budget")
    saving_goal = params.get("saving_goal")
    goal_progress = params.get("confirmed_goal_progress")
    if project_budget is None and saving_goal is None and goal_progress is None:
        raise BusinessError("invalid_input", "至少需要一项项目或目标操作。", code="empty_action_set")
    for field, value, action_name in (
        ("project_budget", project_budget, "upsert_project_budget"),
        ("saving_goal", saving_goal, "upsert_saving_goal"),
    ):
        if value is None:
            continue
        if not isinstance(value, dict):
            raise BusinessError("invalid_input", f"{field} 必须是对象。", code="invalid_composite_action")
        schema = planning.ACTIONS[action_name]["inputSchema"]
        allowed = set(schema["properties"])
        extra = set(value) - allowed
        missing = set(schema.get("required", ())) - set(value)
        if extra or missing:
            raise BusinessError(
                "invalid_input", f"{field} 参数与已声明的操作不匹配。", code="invalid_composite_action",
                details={"fields": sorted(extra), "missing": sorted(missing)},
            )
    if goal_progress is not None and not isinstance(goal_progress, dict):
        raise BusinessError("invalid_input", "confirmed_goal_progress 必须是对象。", code="invalid_goal_progress")
    if goal_progress is not None:
        allowed_progress = {"goal_id", "amount_minor", "occurred_on", "note"}
        extra = set(goal_progress) - allowed_progress
        missing = {"amount_minor", "note"} - set(goal_progress)
        if extra or missing:
            raise BusinessError(
                "invalid_input", "confirmed_goal_progress 参数不匹配。", code="invalid_goal_progress",
                details={"fields": sorted(extra), "missing": sorted(missing)},
            )
    if goal_progress is not None and saving_goal is None and not goal_progress.get("goal_id"):
        raise BusinessError("invalid_input", "更新既有目标进度时必须指定 goal_id。", code="goal_id_required")

    results = {}
    effects = []
    if project_budget is not None:
        project_result = planning.ACTIONS["upsert_project_budget"]["run"](conn, ctx, project_budget)
        results["project_budget"] = project_result
        effects.append("project_budget_upserted")
        if project_result.get("linked_transaction"):
            effects.append("transaction_linked_to_project")

    goal_id = None
    if saving_goal is not None:
        goal_result = planning.ACTIONS["upsert_saving_goal"]["run"](conn, ctx, saving_goal)
        results["saving_goal"] = goal_result
        goal_id = goal_result["goal"]["goal_id"]
        effects.append("saving_goal_upserted")

    if goal_progress is not None:
        progress_goal_id = goal_progress.get("goal_id")
        if goal_id is not None and progress_goal_id is not None and progress_goal_id != goal_id:
            raise BusinessError("invalid_input", "goal_id 必须与本次设置的目标一致。", code="goal_id_mismatch")
        progress_result = planning.ACTIONS["record_saving_goal_change"]["run"](conn, ctx, {
            **goal_progress,
            "goal_id": goal_id or progress_goal_id,
        })
        results["confirmed_goal_progress"] = progress_result
        effects.append("confirmed_goal_progress_recorded")

    return {"applied_actions": effects, "results": results}


DOMAIN_ACTIONS = {
    **ledger.ACTIONS,
    **planning.ACTIONS,
    **movements.ACTIONS,
    **recurring.ACTIONS,
    **files.ACTIONS,
    **insights.ACTIONS,
    **maintenance.ACTIONS,
    **bookkeeping.ACTIONS,
    **report_cards.ACTIONS,
    "get_transaction": _GET_TRANSACTION_ACTION,
    "apply_actions": {
        "description": "Atomically apply only explicit project-budget/link and savings-goal/progress combinations in this ledger; all changes share one request receipt and transaction.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_budget": planning.ACTIONS["upsert_project_budget"]["inputSchema"],
                "saving_goal": planning.ACTIONS["upsert_saving_goal"]["inputSchema"],
                "confirmed_goal_progress": {
                    "type": "object",
                    "properties": {
                        "goal_id": {"type": "string", "minLength": 1},
                        "amount_minor": {"type": "integer", "not": {"const": 0}},
                        "occurred_on": {"type": "string", "format": "date"},
                        "note": {"type": "string", "minLength": 1},
                    },
                    "required": ["amount_minor", "note"],
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        },
        "scope": "own_write",
        "run": _apply_actions,
    },
}
DOMAIN_ACTIONS["copy_transaction_to_draft"] = {
    **DOMAIN_ACTIONS["copy_transaction_to_draft"],
    "description": "Create a new draft from permitted fields of one source transaction. MCP requires this host's write and detail_read scopes plus the owner's single-use merchant authorization; provide a new amount and occurrence date before committing.",
}
DOMAIN_ACTIONS["create_host_token"] = {
    "description": "Create a host token for the authenticated local identity. The new token is shown once; omitted scopes default to summary_read.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "label": {"type": "string", "minLength": 1, "maxLength": 80},
            "scopes": {"type": "array", "uniqueItems": True, "items": {"type": "string", "enum": sorted(HOST_PERMISSIONS)}},
        },
        "required": ["label"],
        "additionalProperties": False,
    },
    "scope": "own_write", "host_permission": "write", "sources": ["web"], "no_receipt": True,
    "run": _create_host_token,
}
DOMAIN_ACTIONS["rotate_host_token"] = {
    "description": "Replace one active host token for the authenticated local identity while preserving its label and scopes. The new token is shown once.",
    "inputSchema": {
        "type": "object",
        "properties": {"token_id": {"type": "string", "minLength": 1}},
        "required": ["token_id"],
        "additionalProperties": False,
    },
    "scope": "own_write", "host_permission": "write", "sources": ["web"], "no_receipt": True,
    "run": _rotate_host_token,
}
for _action_name in ("set_host_token_scopes", "revoke_host_token", "create_host_token", "rotate_host_token"):
    DOMAIN_ACTIONS[_action_name] = {**DOMAIN_ACTIONS[_action_name], "no_receipt": True}
DOMAIN_ACTIONS["list_expense_view_rules"] = {**DOMAIN_ACTIONS["list_expense_view_rules"], "host_permission": "detail_read"}


class YuxuApplication:
    def __init__(self, control_token: str, started_at: str, owner: str):
        self.control_token = control_token
        self.started_at = started_at
        self.owner = owner
        self.http_server = None
        self.web_root = Path(__file__).resolve().parent.parent / "web"
        self._runtime_events = deque(maxlen=100)
        self._runtime_lock = threading.Lock()

    def _record_runtime_event(self, source: str, action_name: str, outcome: str, host_label: str, request_id: object = None) -> None:
        event = {
            "at": store.utc_now(), "source": source,
            "host": host_label if source == "mcp" else "本机网页",
            "action": action_name, "outcome": outcome,
        }
        if isinstance(request_id, str):
            try:
                event["request_id"] = str(uuid.UUID(request_id))
            except ValueError:
                pass
        with self._runtime_lock:
            self._runtime_events.append(event)

    def _runtime_log(self) -> list[dict]:
        with self._runtime_lock:
            return list(self._runtime_events)

    @property
    def port(self) -> int:
        return int(self.http_server.server_address[1]) if self.http_server else 0

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def handle(self, request_handler) -> None:
        try:
            self._check_local_host(request_handler)
            path = unquote(urlsplit(request_handler.path).path)
            if path.startswith("/api/"):
                if request_handler.command == "GET":
                    self._get_api(request_handler, path)
                elif request_handler.command == "POST":
                    self._post_api(request_handler, path)
                else:
                    raise BusinessError("invalid_input", "仅支持 GET 和 POST。", code="method_not_allowed")
                return
            if request_handler.command in ("GET", "HEAD"):
                self._serve_static(request_handler, path, send_body=request_handler.command == "GET")
                return
            raise BusinessError("not_found", "请求路径不存在。", code="not_found")
        except BusinessError as error:
            body = error_body(error)
            if request_handler.headers.get("X-Yuxu-Token") is not None:
                body = sanitize_mcp_output(body)
            self._send_json(request_handler, HTTP_STATUS.get(error.status, 400), envelope(status=error.status, error=body))
        except Exception:
            error = BusinessError("internal_error", "本机服务处理失败。")
            self._send_json(request_handler, 500, envelope(status=error.status, error=error_body(error)))

    def _check_local_host(self, request_handler) -> None:
        if request_handler.client_address[0] not in ("127.0.0.1", "::1"):
            raise BusinessError("forbidden", "服务仅接受本机连接。", code="loopback_only")
        if request_handler.headers.get("Host") != f"127.0.0.1:{self.port}":
            raise BusinessError("forbidden", "请求 Host 不符合本机服务地址。", code="invalid_host")

    def _same_origin(self, request_handler, *, required: bool) -> bool:
        origin = request_handler.headers.get("Origin")
        referer = request_handler.headers.get("Referer")
        if origin and origin != self.origin:
            return False
        if referer:
            parts = urlsplit(referer)
            if f"{parts.scheme}://{parts.netloc}" != self.origin:
                return False
        if required and not origin and not referer:
            return False
        return True

    def _content_type_is_json(self, request_handler) -> bool:
        return request_handler.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() == "application/json"

    def _read_json(self, request_handler, *, required: bool = True) -> dict:
        raw_length = request_handler.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError:
            raise BusinessError("invalid_input", "Content-Length 无效。", code="invalid_json") from None
        if length < 0 or length > 16 * 1024 * 1024:
            raise BusinessError("invalid_input", "请求正文大小无效。", code="invalid_json")
        if length == 0 and not required:
            return {}
        if not self._content_type_is_json(request_handler):
            raise BusinessError("invalid_input", "请求必须使用 application/json。", code="invalid_content_type")
        try:
            value = json.loads(request_handler.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise BusinessError("invalid_input", "请求正文不是有效 JSON。", code="invalid_json") from None
        if not isinstance(value, dict):
            raise BusinessError("invalid_input", "请求正文必须是 JSON 对象。", code="invalid_json")
        return value

    def _send_json(self, request_handler, status_code: int, payload: dict, headers: dict | None = None) -> None:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request_handler.send_response(status_code)
        request_handler.send_header("Content-Type", "application/json; charset=utf-8")
        request_handler.send_header("Content-Length", str(len(raw)))
        request_handler.send_header("Cache-Control", "no-store")
        request_handler.send_header("X-Content-Type-Options", "nosniff")
        for name, value in (headers or {}).items():
            request_handler.send_header(name, value)
        request_handler.end_headers()
        request_handler.wfile.write(raw)

    def _get_api(self, request_handler, path: str) -> None:
        if path == "/api/status":
            state = "ready" if store.is_initialized() else "setup_required"
            payload = envelope(
                status="ok",
                data={
                    "state": state,
                    "version": __version__,
                    "host": "127.0.0.1",
                    "port": self.port,
                    "started_at": self.started_at,
                    "owner": self.owner,
                    "database": "local SQLite",
                },
            )
            self._send_json(request_handler, 200, payload)
            return
        if path == "/api/session":
            self._get_session(request_handler)
            return
        if path == "/api/runtime-log":
            self._get_runtime_log(request_handler)
            return
        if path in ("/api/tools", "/api/mcp/catalog-state"):
            conn = self._connect_initialized()
            try:
                actor, source, _ = self._authenticate(request_handler, conn)
                self._require_browser_origin(request_handler, source, required=False)
                state = {"version": __version__,
                         "token_version": actor.get("token_version"),
                         "host_scopes": sorted(actor.get("host_scopes", [])),
                         "ledger_scopes": sorted(store.access_scopes(conn, actor["principal_id"], actor["ledger_id"]))}
                if path == "/api/tools":
                    data = {"tools": self._tool_catalog(conn, actor, source), "catalog_state": state}
                else:
                    data = state
                self._send_json(request_handler, 200, envelope(
                    status="ok", account_id=actor["account_id"], account_name=actor["account_name"], data=data,
                ))
            finally:
                conn.close()
            return
        if path.startswith(("/api/files/", "/api/mcp/images/")):
            inline = path.startswith("/api/mcp/images/")
            file_id = path.rsplit("/", 1)[-1]
            conn = self._connect_initialized()
            try:
                actor, source, _ = self._authenticate(request_handler, conn)
                self._require_browser_origin(request_handler, source, required=True)
                if inline and (source != "mcp" or "export_artifact" not in actor["host_scopes"]):
                    raise BusinessError("forbidden", "本机宿主缺少图片领取权限。", code="host_permission_required")
                if not inline and source != "web":
                    raise BusinessError("forbidden", "文件须由本人在本机网页下载。", code="web_download_required")
                if "own_read" not in store.access_scopes(conn, actor["principal_id"], actor["ledger_id"]):
                    raise BusinessError("forbidden", "当前用户无权读取此账本。", code="forbidden")
                item = files.find_export(conn, actor["principal_id"], actor["ledger_id"], file_id)
                if item is None or inline and (item["format"] != "png" or not item["shareable"]):
                    raise BusinessError("not_found", "导出文件不存在或已过期。", code="export_not_found")
                content = item["content"] if "content" in item else item["path"].read_bytes()
                request_handler.send_response(200)
                request_handler.send_header("Content-Type", item["content_type"])
                request_handler.send_header("Content-Length", str(len(content)))
                request_handler.send_header("Content-Disposition", f'attachment; filename="{item["filename"]}"')
                request_handler.send_header("Cache-Control", "no-store")
                request_handler.send_header("X-Content-Type-Options", "nosniff")
                request_handler.end_headers()
                request_handler.wfile.write(content)
            finally:
                conn.close()
            return
        raise BusinessError("not_found", "请求路径不存在。", code="not_found")

    def _get_session(self, request_handler) -> None:
        if not store.is_initialized():
            self._send_json(request_handler, 200, envelope(status="ok", data={"authenticated": False, "setup_required": True}))
            return
        raw_session = self._session_cookie(request_handler)
        if not raw_session:
            self._send_json(request_handler, 200, envelope(status="ok", data={"authenticated": False, "setup_required": False}))
            return
        conn = store.connect()
        try:
            info = store.session_info(conn, raw_session)
            if not info:
                self._send_json(request_handler, 200, envelope(status="ok", data={"authenticated": False, "setup_required": False}))
                return
            accounts = store.accounts_for_principal(conn, info["principal_id"])
            data = {
                "authenticated": True,
                "username": info["username"],
                "csrf_token": info["csrf_token"],
                "conversation_id": info["conversation_id"],
                "accounts": accounts,
            }
            self._send_json(request_handler, 200, envelope(
                status="ok",
                account_id=info["account_id"],
                account_name=info["account_name"],
                data=data,
            ))
        finally:
            conn.close()

    def _get_runtime_log(self, request_handler) -> None:
        conn = self._connect_initialized()
        try:
            actor, source, _ = self._authenticate(request_handler, conn)
            self._require_browser_origin(request_handler, source, required=True)
            if source != "web":
                raise BusinessError("forbidden", "运行日志只能由本机网页查看。", code="web_runtime_log_required")
            if "own_read" not in store.access_scopes(conn, actor["principal_id"], actor["ledger_id"]):
                raise BusinessError("forbidden", "当前用户无权读取此账本。", code="forbidden")
            self._send_json(request_handler, 200, envelope(
                status="ok", account_id=actor["account_id"], account_name=actor["account_name"],
                data={"events": self._runtime_log()},
            ))
        finally:
            conn.close()

    def _post_api(self, request_handler, path: str) -> None:
        if path == "/api/control/stop":
            self._stop_service(request_handler)
            return
        if path == "/api/login":
            self._login(request_handler)
            return
        if path == "/api/register":
            self._register(request_handler)
            return
        if path == "/api/logout":
            self._logout(request_handler)
            return
        if path == "/api/backups/upload":
            self._upload_backup(request_handler)
            return
        if path.startswith("/api/tools/"):
            self._call_tool(request_handler, path.removeprefix("/api/tools/"))
            return
        raise BusinessError("not_found", "请求路径不存在。", code="not_found")

    def _upload_backup(self, request_handler) -> None:
        if request_handler.headers.get("Origin") != self.origin or not self._same_origin(request_handler, required=True):
            raise BusinessError("forbidden", "备份上传必须来自本机同源页面。", code="cross_origin")
        transfer_encoding = request_handler.headers.get_all("Transfer-Encoding", [])
        if transfer_encoding:
            raise BusinessError("invalid_input", "备份上传必须使用固定 Content-Length。", code="invalid_upload_length")
        content_lengths = request_handler.headers.get_all("Content-Length", [])
        if len(content_lengths) != 1:
            raise BusinessError("invalid_input", "备份上传必须提供有效 Content-Length。", code="invalid_upload_length")
        raw_length = content_lengths[0]
        if not raw_length.isascii() or not raw_length.isdecimal():
            raise BusinessError("invalid_input", "备份上传必须提供有效 Content-Length。", code="invalid_upload_length")
        content_length = int(raw_length)
        if not 1 <= content_length <= 256 * 1024 * 1024:
            raise BusinessError("invalid_input", "备份 ZIP 大小必须在 1 字节至 256 MiB 之间。", code="invalid_upload_size")
        content_types = request_handler.headers.get_all("Content-Type", [])
        if len(content_types) != 1 or content_types[0].split(";", 1)[0].strip().lower() != "application/zip":
            raise BusinessError("invalid_input", "备份正文必须使用 application/zip。", code="invalid_content_type")
        conn = self._connect_initialized()
        try:
            actor, source, raw_session = self._authenticate(request_handler, conn)
            if source != "web" or not raw_session:
                raise BusinessError("forbidden", "备份上传只能由本机登录会话执行。", code="web_confirmation_required")
            if not store.csrf_matches(conn, raw_session, request_handler.headers.get("X-CSRF-Token", "")):
                raise BusinessError("forbidden", "CSRF 校验失败。", code="csrf_failed")
            if "maintenance" not in store.access_scopes(conn, actor["principal_id"], actor["ledger_id"]):
                raise BusinessError("forbidden", "当前用户缺少本机维护授权。", code="maintenance_scope_required")
            uploaded = files.install_uploaded_backup(conn, request_handler.rfile, content_length)
            data = {key: uploaded[key] for key in ("backup_id", "filename", "format_version", "created_at", "sha256", "ledgers")}
            ctx = {
                "principal_id": actor["principal_id"], "ledger_id": actor["ledger_id"],
                "source": "web", "host_label": "本机网页", "host_permission": "write",
            }
            with store.transaction(conn, write=True):
                maintenance.record_access_event(conn, ctx, "backup_upload", "backup", outcome="installed")
            self._record_runtime_event("web", "backup_upload", "ok", "")
            self._send_json(request_handler, 200, envelope(
                status="ok", account_id=actor["account_id"], account_name=actor["account_name"], data=data,
            ))
        finally:
            conn.close()

    def _register(self, request_handler) -> None:
        if not self._same_origin(request_handler, required=True):
            raise BusinessError("forbidden", "注册请求必须来自本机同源页面。", code="cross_origin")
        if store.is_initialized():
            raise BusinessError("conflict", "本机账号已注册，请登录。", code="already_initialized")
        payload = self._read_json(request_handler)
        accounts = payload.get("accounts")
        if not isinstance(accounts, list) or len(accounts) != 2:
            raise BusinessError("invalid_input", "请填写两位本机使用者的注册信息。", code="two_users_required")
        limits = {"username": 80, "account_name": 80, "password": 256}
        for account in accounts:
            if not isinstance(account, dict) or any(
                not isinstance(account.get(field), str) or len(account[field]) > limit
                for field, limit in limits.items()
            ):
                raise BusinessError("invalid_input", "请检查用户名、账本名称和密码的长度。", code="invalid_registration")
        admin_username = payload.get("admin_username")
        maintenance_username = payload.get("maintenance_username")
        if admin_username is not None and not isinstance(admin_username, str):
            raise BusinessError("invalid_input", "请选择一位使用者担任支出管理员，或设为不选。", code="invalid_admin")
        if not isinstance(maintenance_username, str) or not maintenance_username.strip():
            raise BusinessError("invalid_input", "请选择一位使用者担任本机维护者。", code="invalid_maintenance_user")

        conn = store.connect()
        try:
            store.create_local_accounts(
                conn, accounts, admin_username, maintenance_username, create_initial_tokens=False
            )
        finally:
            conn.close()
        self._send_json(request_handler, 201, envelope(status="ok", data={"registered": True}))

    def _login(self, request_handler) -> None:
        if not self._same_origin(request_handler, required=True):
            raise BusinessError("forbidden", "登录请求必须来自本机同源页面。", code="cross_origin")
        if not store.is_initialized():
            raise BusinessError("needs_clarification", "请先在本机登录页注册账号。", code="setup_required")
        payload = self._read_json(request_handler)
        username = payload.get("username")
        password = payload.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            raise BusinessError("invalid_input", "用户名和密码必须是文本。", code="invalid_credentials")
        conn = store.connect()
        try:
            with store.transaction(conn, write=True):
                principal = store.authenticate(conn, username, password)
                raw_session, csrf_token, expires_at = store.issue_session(conn, principal["principal_id"])
            info = store.session_info(conn, raw_session)
            accounts = store.accounts_for_principal(conn, principal["principal_id"])
            payload = envelope(
                status="ok",
                account_id=info["account_id"],
                account_name=info["account_name"],
                data={
                    "authenticated": True,
                    "username": info["username"],
                    "csrf_token": csrf_token,
                    "conversation_id": info["conversation_id"],
                    "accounts": accounts,
                },
            )
            cookie = f"{SESSION_COOKIE}={raw_session}; HttpOnly; SameSite=Strict; Path=/; Max-Age={max(1, int(expires_at - datetime.now().timestamp()))}"
            self._send_json(request_handler, 200, payload, {"Set-Cookie": cookie})
        finally:
            conn.close()

    def _logout(self, request_handler) -> None:
        if not self._same_origin(request_handler, required=True):
            raise BusinessError("forbidden", "退出登录请求必须来自本机同源页面。", code="cross_origin")
        conn = self._connect_initialized()
        try:
            actor, source, raw_session = self._authenticate(request_handler, conn)
            if source != "web" or not raw_session:
                raise BusinessError("forbidden", "此操作仅接受浏览器会话。", code="session_required")
            csrf_token = request_handler.headers.get("X-CSRF-Token", "")
            if not store.csrf_matches(conn, raw_session, csrf_token):
                raise BusinessError("forbidden", "CSRF 校验失败。", code="csrf_failed")
            with store.transaction(conn, write=True):
                store.revoke_session(conn, raw_session)
            self._send_json(request_handler, 200, envelope(status="ok", account_id=actor["account_id"], account_name=actor["account_name"], data={"authenticated": False}), {
                "Set-Cookie": f"{SESSION_COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
            })
        finally:
            conn.close()

    def _call_tool(self, request_handler, action_name: str) -> None:
        if not action_name or "/" in action_name:
            raise BusinessError("not_found", "工具不存在。", code="not_found")
        payload = self._read_json(request_handler)
        conn = self._connect_initialized()
        try:
            actor, source, session_or_conversation = self._authenticate(request_handler, conn)
            self._require_browser_origin(request_handler, source, required=True)
            if source == "web":
                if not store.csrf_matches(conn, session_or_conversation, request_handler.headers.get("X-CSRF-Token", "")):
                    raise BusinessError("forbidden", "CSRF 校验失败。", code="csrf_failed")
                conversation_id = actor["conversation_id"]
            else:
                conversation_id = session_or_conversation
            try:
                self._dispatch_tool(conn, request_handler, actor, source, conversation_id, action_name, payload)
            except BusinessError as error:
                self._record_access_event(conn, actor, source, action_name, payload, error.code or error.status)
                known_action = action_name in PLATFORM_ACTIONS or action_name in DOMAIN_ACTIONS or action_name == "get_request_status"
                self._record_runtime_event(source, _safe_audit_action(action_name) if known_action else "unknown_action", error.code or error.status, actor.get("label", ""))
                raise
            action = REQUEST_STATUS_ACTION if action_name == "get_request_status" else PLATFORM_ACTIONS.get(action_name, DOMAIN_ACTIONS.get(action_name))
            self._record_runtime_event(
                source,
                _safe_audit_action(action_name),
                "committed" if action and action["scope"] == "own_write" else "ok",
                actor.get("label", ""),
                payload.get("request_id"),
            )
        finally:
            conn.close()

    def _record_access_event(self, conn, actor: dict, source: str, action_name: str, params: dict, outcome: str, ctx: dict | None = None) -> None:
        from . import maintenance

        actions = {**PLATFORM_ACTIONS, **DOMAIN_ACTIONS, "get_request_status": REQUEST_STATUS_ACTION}
        known = action_name in actions
        action = actions.get(action_name)
        safe_action = _safe_audit_action(action_name) if known else "unknown_action"
        permission = _requested_host_permission(safe_action, action, params) if action else "detail_read"
        if ctx is None:
            ctx = {
                "principal_id": actor["principal_id"], "ledger_id": actor["ledger_id"],
                "account_id": actor["account_id"], "account_name": actor["account_name"],
                "source": source,
            }
        ctx["host_label"] = actor.get("label", "本机网页") if source == "mcp" else "本机网页"
        ctx["host_permission"] = permission
        local_review_actions = ("get_local_review_link", "resolve_local_review_link")
        if action_name in local_review_actions:
            ctx["requested_start"] = None
            ctx["requested_end"] = None
        else:
            filters = params.get("filters") if isinstance(params.get("filters"), dict) else {}
            for field, value in (
                ("requested_start", params.get("start_date", filters.get("start_date"))),
                ("requested_end", params.get("end_date", filters.get("end_date"))),
            ):
                if isinstance(value, str) and len(value) == 10:
                    try:
                        valid_date = datetime.strptime(value, "%Y-%m-%d").date().isoformat() == value
                    except ValueError:
                        valid_date = False
                    ctx[field] = value if valid_date else None
                else:
                    ctx[field] = None
        resource_id = None
        if action_name not in (
            "authorize_private_transaction_fields", "set_host_token_scopes", "revoke_host_token",
            "create_host_token", "rotate_host_token", *local_review_actions,
        ):
            for key in ("transaction_id", "project_id", "category_id", "goal_id", "rule_id", "preview_id", "backup_id", "attachment_id"):
                value = params.get(key)
                if isinstance(value, str):
                    try:
                        resource_id = str(uuid.UUID(value))
                    except ValueError:
                        pass
                    if resource_id:
                        break
        maintenance.record_access_event(conn, ctx, safe_action, "action", resource_id, outcome)

    def _dispatch_tool(self, conn, request_handler, actor: dict, source: str, conversation_id: str | None, action_name: str, payload: dict) -> None:
        if "conversation_id" in payload:
            raise BusinessError("invalid_input", "conversation_id 由已验证会话提供，不能作为工具参数。", code="untrusted_conversation_id")
        action = REQUEST_STATUS_ACTION if action_name == "get_request_status" else PLATFORM_ACTIONS.get(action_name, DOMAIN_ACTIONS.get(action_name))
        if action is None:
            raise BusinessError("not_found", "工具不存在。", code="not_found")
        host_permission = _requested_host_permission(action_name, action, payload)
        export_receipt = action_name == "get_request_status" and "export_summary" in actor.get("host_scopes", ())
        if source == "mcp" and host_permission not in actor["host_scopes"] and not export_receipt:
            raise BusinessError("forbidden", "当前 MCP 宿主没有此操作所需权限。", code="host_permission_required",
                                details={"required_scope": host_permission})
        schema_properties = action.get("inputSchema", {}).get("properties", {})
        cross_account_scopes = {"admin_spending", "admin_goal"}
        routes_account_id = action["scope"] in cross_account_scopes
        accepted_fields = set(schema_properties)
        if action["scope"] == "own_write" and not action.get("no_receipt"):
            accepted_fields.add("request_id")
        if routes_account_id:
            accepted_fields.add("account_id")
        extra_fields = set(payload) - accepted_fields
        if extra_fields:
            raise BusinessError(
                "invalid_input", "请求包含此工具未声明的参数。", code="unexpected_parameters",
                details={"fields": sorted(extra_fields)},
            )
        own_ledger_id = actor["ledger_id"]
        own_scopes = store.access_scopes(conn, actor["principal_id"], own_ledger_id)
        scope = action["scope"]
        if scope == "own_read" and "own_read" not in own_scopes:
            raise BusinessError("forbidden", "当前用户无权读取此账本。", code="forbidden")
        if scope == "own_write" and "own_write" not in own_scopes:
            raise BusinessError("forbidden", "当前用户无权修改此账本。", code="forbidden")
        if scope == "admin_spending" and "own_read" not in own_scopes:
            raise BusinessError("forbidden", "当前用户无权访问此工具。", code="forbidden")
        if scope == "admin_goal" and "own_read" not in own_scopes:
            raise BusinessError("forbidden", "当前用户无权访问此工具。", code="forbidden")
        required_scope = action.get("requires_scope")
        if required_scope and required_scope not in own_scopes:
            raise BusinessError("forbidden", "当前用户缺少本机维护授权。", code="maintenance_scope_required")
        allowed_sources = action.get("sources")
        if allowed_sources and source not in allowed_sources:
            raise BusinessError("forbidden", "此操作只能由本机网页执行。", code="web_confirmation_required")

        if action_name in ("get_current_account", "list_accessible_accounts", "get_workflow_guide", "get_account_access_policy"):
            if "account_id" in payload:
                raise BusinessError("invalid_input", "此工具不接受 account_id。", code="invalid_input")
            if action_name == "get_current_account":
                data = {
                    "username": actor["username"],
                    "account_id": actor["account_id"],
                    "account_name": actor["account_name"],
                    "verified_scopes": sorted(own_scopes),
                }
                if source == "mcp":
                    data["host_permissions"] = {
                        "token_id": actor["token_id"], "label": actor["label"],
                        "scopes": sorted(actor["host_scopes"]), "version": actor["token_version"],
                    }
            elif action_name == "list_accessible_accounts":
                data = {"accounts": store.verified_access_accounts(conn, actor["principal_id"])}
            elif action_name == "get_account_access_policy":
                data = store.account_access_policy(conn, actor["principal_id"], own_ledger_id)
            else:
                scenario = payload.get("scenario")
                if scenario is None:
                    data = WORKFLOW_GUIDE
                elif not isinstance(scenario, str) or not 1 <= len(scenario.strip()) <= 32:
                    raise BusinessError("invalid_input", "scenario 必须是有效的需求编号。", code="invalid_scenario")
                else:
                    data = _workflow_scenario_data(scenario.strip().lower(), self._tool_catalog(conn, actor, source))
            with store.transaction(conn, write=True):
                self._record_access_event(conn, actor, source, action_name, payload, "ok")
            self._send_json(request_handler, 200, envelope(
                status="ok", account_id=actor["account_id"], account_name=actor["account_name"], data=data,
            ))
            return

        if action_name == "get_request_status":
            if "account_id" in payload or "request_id" in payload:
                raise BusinessError("invalid_input", "状态查询只接受 target_request_id。", code="invalid_input")
            target_request_id = self._required_request_id(payload.get("target_request_id"), "target_request_id")
            with store.transaction(conn, write=True):
                previous = store.receipt(conn, actor["principal_id"], own_ledger_id, target_request_id)
                self._record_access_event(conn, actor, source, action_name, payload, "ok")
            data = {"target_request_id": target_request_id, "outcome": "unknown"}
            if previous and source == "mcp":
                prior_action = DOMAIN_ACTIONS.get(previous["action_name"])
                if prior_action is None or _host_permission(previous["action_name"], prior_action) not in actor["host_scopes"]:
                    previous = None
            if previous:
                data = {
                    "target_request_id": target_request_id,
                    "outcome": "committed",
                    "action_name": previous["action_name"],
                    "result": previous["result"],
                }
            self._send_json(request_handler, 200, envelope(
                status="ok", account_id=actor["account_id"], account_name=actor["account_name"],
                data=sanitize_mcp_output(data) if source == "mcp" else data,
            ))
            return

        explicit_account_id = payload.get("account_id") if routes_account_id else None
        if explicit_account_id is not None and (not isinstance(explicit_account_id, str) or not explicit_account_id.strip()):
            raise BusinessError("invalid_input", "account_id 必须是非空文本。", code="invalid_account_id")
        target = store.ledger_by_account_id(conn, explicit_account_id) if explicit_account_id is not None else {
            "ledger_id": actor["ledger_id"], "account_id": actor["account_id"], "account_name": actor["account_name"]
        }
        if target is None:
            raise BusinessError("not_found", "指定账本不存在。", code="not_found")
        cross_account = target["ledger_id"] != own_ledger_id
        target_scopes = store.access_scopes(conn, actor["principal_id"], target["ledger_id"])
        target_scope = "admin_goal" if scope == "admin_goal" else "admin_spending"
        if cross_account and (scope not in cross_account_scopes or target_scope not in target_scopes):
            raise BusinessError("forbidden", "当前用户无权跨账本访问。", code="cross_account_forbidden")

        if action_name == "get_ledger_context":
            data = {
                "account_id": target["account_id"],
                "account_name": target["account_name"],
                "is_current_account": not cross_account,
                "cross_account": cross_account,
                "verified_scopes": sorted(target_scopes),
            }
            with store.transaction(conn, write=True):
                self._record_access_event(conn, actor, source, action_name, payload, "ok", {
                    "principal_id": actor["principal_id"], "ledger_id": target["ledger_id"],
                    "account_id": target["account_id"], "account_name": target["account_name"],
                    "cross_account": cross_account, "source": source,
                })
            self._send_json(request_handler, 200, envelope(
                status="ok", account_id=target["account_id"], account_name=target["account_name"], data=data,
            ))
            return

        request_id_value = payload.get("request_id")
        request_id = self._required_request_id(request_id_value, "request_id") if scope == "own_write" and not action.get("no_receipt") else None
        handler_request_id = request_id
        if action_name in (
            "set_host_token_scopes", "revoke_host_token", "create_host_token", "rotate_host_token",
        ) and action.get("no_receipt"):
            handler_request_id = str(uuid.uuid4())
        params = {
            key: value for key, value in payload.items()
            if key != "request_id" and not (routes_account_id and key == "account_id")
        }
        ctx = {
            "ledger_id": target["ledger_id"],
            "account_id": target["account_id"],
            "account_name": target["account_name"],
            "principal_id": actor["principal_id"],
            "source": source,
            "conversation_id": conversation_id,
            "request_id": handler_request_id,
            "cross_account": cross_account,
            "host_scopes": actor.get("host_scopes", []),
            "token_id": actor.get("token_id"),
            "host_label": actor.get("label", "本机网页") if source == "mcp" else "本机网页",
            "host_permission": host_permission,
            "today": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
            "timezone": "Asia/Shanghai",
            "origin": self.origin,
        }
        fingerprint = hashlib.sha256(json.dumps(
            {"action": action_name, "ledger_id": target["ledger_id"], "params": params},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()

        if action_name == "stage_receipt_bookkeeping" and not store.receipt(conn, actor["principal_id"], target["ledger_id"], request_id):
            ctx["prepared_receipt"] = bookkeeping.prepare_receipt(conn, ctx, params)
        with store.transaction(conn, write=True):
            previous = store.receipt(conn, actor["principal_id"], target["ledger_id"], request_id) if request_id else None
            if previous and (previous["action_name"] != action_name or previous["params_hash"] != fingerprint):
                raise BusinessError("conflict", "request_id 已用于不同操作，请为新操作使用新值。", code="idempotency_conflict")
            if action_name == "copy_transaction_to_draft" and source == "mcp":
                if not {"write", "detail_read"}.issubset(actor["host_scopes"]):
                    raise BusinessError("forbidden", "此宿主需要写入和明细权限才能复制流水。", code="host_permission_required")
            if action_name == "copy_transaction_to_draft" and source == "mcp" and previous is None:
                transaction_id = params.get("transaction_id")
                if not isinstance(transaction_id, str) or not transaction_id.strip():
                    raise BusinessError("invalid_input", "必须指定要复制的流水。", code="invalid_transaction_id")
                ledger._get_transaction(conn, target["ledger_id"], transaction_id.strip())
                now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                grant = conn.execute(
                    "SELECT fields_json, expires_at FROM yuxu_private_read_grants "
                    "WHERE principal_id = ? AND ledger_id = ? AND token_id = ? AND transaction_id = ?",
                    (actor["principal_id"], target["ledger_id"], actor["token_id"], transaction_id.strip()),
                ).fetchone()
                if grant is None or grant["expires_at"] <= now or "merchant" not in json.loads(grant["fields_json"]):
                    raise BusinessError("forbidden", "请先在本机网页为此宿主和流水单独授权商家字段。", code="private_field_authorization_required")
                conn.execute(
                    "DELETE FROM yuxu_private_read_grants WHERE principal_id = ? AND ledger_id = ? AND token_id = ? AND transaction_id = ?",
                    (actor["principal_id"], target["ledger_id"], actor["token_id"], transaction_id.strip()),
                )
                ctx["copy_merchant_authorized"] = True
            if scope == "own_write":
                if action.get("no_receipt"):
                    result = action["run"](conn, ctx, params)
                else:
                    if previous:
                        result = previous["result"]
                    else:
                        result = action["run"](conn, ctx, params)
                        if not isinstance(result, dict):
                            raise BusinessError("internal_error", "工具返回了无效结果。", code="invalid_tool_result")
                        store.save_receipt(conn, actor["principal_id"], target["ledger_id"], request_id, action_name, fingerprint, result)
                if not isinstance(result, dict):
                    raise BusinessError("internal_error", "工具返回了无效结果。", code="invalid_tool_result")
            else:
                result = action["run"](conn, ctx, params)
                if not isinstance(result, dict):
                    raise BusinessError("internal_error", "工具返回了无效结果。", code="invalid_tool_result")
            if action_name in ("set_host_token_scopes", "revoke_host_token", "rotate_host_token"):
                conn.execute(
                    "DELETE FROM yuxu_private_read_grants WHERE principal_id = ? AND token_id = ?",
                    (actor["principal_id"], params.get("token_id")),
                )
                conn.execute(
                    "DELETE FROM insight_merchant_refs WHERE principal_id = ? AND token_id = ?",
                    (actor["principal_id"], params.get("token_id")),
                )
            self._record_access_event(conn, actor, source, action_name, params, "committed" if scope == "own_write" else "ok", ctx)

        if source == "mcp":
            authorized = result.get("_mcp_private_fields_authorized", ()) if action_name == "get_transaction" else ()
            result = sanitize_mcp_output(result, frozenset(authorized))
        self._send_json(request_handler, 200, envelope(
            status="committed" if scope == "own_write" else "ok",
            account_id=target["account_id"],
            account_name=target["account_name"],
            data=result,
            request_id=request_id,
        ))

    def _tool_catalog(self, conn, actor: dict, source: str) -> list[dict]:
        own_scopes = store.access_scopes(conn, actor["principal_id"], actor["ledger_id"])
        catalog = []
        actions = {**PLATFORM_ACTIONS, **DOMAIN_ACTIONS}
        actions["get_request_status"] = REQUEST_STATUS_ACTION
        for name, action in actions.items():
            scope = action["scope"]
            host_permission = _host_permission(name, action)
            export_receipt = name == "get_request_status" and "export_summary" in actor.get("host_scopes", ())
            if source == "mcp" and host_permission not in actor["host_scopes"] and not export_receipt:
                continue
            if name == "copy_transaction_to_draft" and source == "mcp" and "detail_read" not in actor["host_scopes"]:
                continue
            if scope == "own_read" and "own_read" not in own_scopes:
                continue
            if scope == "own_write" and "own_write" not in own_scopes:
                continue
            if scope == "admin_spending" and "own_read" not in own_scopes:
                continue
            if scope == "admin_goal" and "own_read" not in own_scopes:
                continue
            if action.get("requires_scope") and action["requires_scope"] not in own_scopes:
                continue
            if action.get("sources") and source not in action["sources"]:
                continue
            schema = copy.deepcopy(action["inputSchema"])
            properties = schema.setdefault("properties", {})
            description = action["description"]
            if source == "mcp" and name == "add_transactions":
                item = properties["transactions"]["items"]
                item["anyOf"] = [{"required": ["kind"]}, {"required": ["template_id"]}]
            if name == "get_report_drilldown":
                group_properties = properties["group_values"].get("properties", {})
                group_properties.pop("merchant_ref" if source == "web" else "merchant", None)
            if name == "preview_restore" and source == "mcp":
                properties.pop("ledger_bindings", None)
                description += " Cross-device account binding is available only in the authenticated local web session."
            if name in ("get_cross_tab_report", "get_purchase_frequency"):
                description += " Merchant grouping requires the detail_read host permission."
            if source == "mcp" and "detail_read" not in actor["host_scopes"]:
                if name == "get_cross_tab_report":
                    properties["dimensions"]["items"]["enum"] = [item for item in properties["dimensions"]["items"]["enum"] if item != "merchant"]
                elif name == "get_purchase_frequency":
                    properties["group_by"]["enum"] = [item for item in properties["group_by"]["enum"] if item != "merchant"]
            if name == "resolve_reference":
                description += " Shared stdio has no trusted conversation ID by default; use this only with an isolated per-conversation host configured with YUXU_ISOLATED_CONVERSATION=1."
            if scope == "own_write" and not action.get("no_receipt"):
                properties["request_id"] = {
                    "type": "string", "minLength": 1, "maxLength": 200,
                    "description": "稳定幂等键；对原请求重试时复用相同值和相同参数。",
                }
                if source == "mcp":
                    schema.setdefault("required", []).append("request_id")
                description += " Supply a stable request_id and reuse it unchanged when retrying the same write."
            if scope == "admin_spending":
                properties["account_id"] = {
                    "type": "string", "minLength": 1,
                    "description": "Optional explicit target account. Cross-account access requires the locally granted admin_spending scope.",
                }
            if scope == "admin_goal":
                properties["account_id"] = {
                    "type": "string", "minLength": 1,
                    "description": "Optional explicit target account. Omitting it uses your own ledger; reading another account's goal requires its locally granted admin_goal scope.",
                }
            if source == "mcp":
                description += f" Requires this host permission: {host_permission}."
            catalog.append({"name": name, "description": description, "inputSchema": schema, "scope": scope, "host_permission": host_permission, "no_receipt": bool(action.get("no_receipt")),
                            "outputSchema": tool_output_schema(name)})
        return catalog

    def _authenticate(self, request_handler, conn) -> tuple[dict, str, str | None]:
        raw_token = request_handler.headers.get("X-Yuxu-Token")
        if raw_token is not None:
            actor = store.resolve_api_token(conn, raw_token)
            if actor is None:
                raise BusinessError("unauthorized", "MCP 本机令牌无效或已撤销。", code="invalid_token")
            conversation_id = request_handler.headers.get("X-Yuxu-Conversation-ID")
            return actor, "mcp", conversation_id
        raw_session = self._session_cookie(request_handler)
        if not raw_session:
            raise BusinessError("unauthorized", "请先登录本机账本。", code="login_required")
        info = store.session_info(conn, raw_session)
        if info is None:
            raise BusinessError("unauthorized", "本机登录会话已失效。", code="session_expired")
        return info, "web", raw_session

    def _session_cookie(self, request_handler) -> str | None:
        cookie = SimpleCookie()
        cookie.load(request_handler.headers.get("Cookie", ""))
        morsel = cookie.get(SESSION_COOKIE)
        return morsel.value if morsel else None

    def _require_browser_origin(self, request_handler, source: str, *, required: bool) -> None:
        if source == "web" and not self._same_origin(request_handler, required=required):
            raise BusinessError("forbidden", "浏览器请求必须来自本机同源页面。", code="cross_origin")

    def _connect_initialized(self):
        if not store.is_initialized():
            raise BusinessError("needs_clarification", "请先在本机登录页注册账号。", code="setup_required")
        return store.connect()

    def _required_request_id(self, value: object, field: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise BusinessError("invalid_input", f"{field} 必须是 1 至 200 个字符。", code="invalid_request_id")
        return value.strip()

    def _stop_service(self, request_handler) -> None:
        supplied = request_handler.headers.get("X-Yuxu-Control-Token", "")
        if not hmac.compare_digest(supplied, self.control_token):
            raise BusinessError("forbidden", "本机停止令牌无效。", code="invalid_control_token")
        self._send_json(request_handler, 200, envelope(status="ok", data={"stopping": True}))
        threading.Thread(target=self.http_server.shutdown, daemon=True).start()

    def _serve_static(self, request_handler, request_path: str, *, send_body: bool) -> None:
        relative = "index.html" if request_path in ("", "/") else request_path.lstrip("/")
        root = self.web_root.resolve()
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            raise BusinessError("not_found", "网页资源不存在。", code="not_found")
        raw = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        request_handler.send_response(200)
        request_handler.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") or content_type in ("application/javascript", "application/json") else content_type)
        request_handler.send_header("Content-Length", str(len(raw)))
        request_handler.send_header("Cache-Control", "no-cache")
        request_handler.send_header("X-Content-Type-Options", "nosniff")
        request_handler.end_headers()
        if send_body:
            request_handler.wfile.write(raw)
