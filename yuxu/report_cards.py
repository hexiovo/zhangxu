from __future__ import annotations

import base64
import calendar
import json
import os
import sqlite3
import subprocess
import uuid
from datetime import date, timedelta
from pathlib import Path

from . import files, insights, ledger, store
from .contract import BusinessError


def _money(value: int) -> str:
    whole, fraction = divmod(abs(value), 100)
    return f"{'-' if value < 0 else ''}¥{whole:,}.{fraction:02d}"


def _period(params: dict) -> tuple[str, str]:
    month = params.get("month")
    if not isinstance(month, str) or len(month) != 7:
        raise BusinessError("needs_clarification", "请指定 YYYY-MM 格式的月份。", code="report_month_required")
    start = files._date(month + "-01", "month")
    first = date.fromisoformat(start)
    return start, f"{month}-{calendar.monthrange(first.year, first.month)[1]:02d}"


def _overview(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    start, end = _period(params)
    basis = insights._date_basis(params.get("date_basis", "occurred_on"))
    first = date.fromisoformat(start)
    args = {"start_date": start, "end_date": end, "date_basis": basis}
    summary = ledger._get_period_summary(conn, ctx, args)
    previous = None
    if first != date.min:
        previous_end = first - timedelta(days=1)
        previous = ledger._get_period_summary(conn, ctx, {
            **args, "start_date": previous_end.replace(day=1).isoformat(), "end_date": previous_end.isoformat(),
        })
    groups = ledger._get_category_breakdown(conn, ctx, args)["items"]
    groups = [item for item in groups if item["expense_minor"] > 0]
    expense = summary["expense_minor"]
    if previous and previous["record_count"]:
        change = f"较上月 {'+' if expense > previous['expense_minor'] else ''}{_money(expense - previous['expense_minor'])}"
    else:
        change = "上月无记录"
    comparison = [
        {"label": str(item["path"]).replace("\n", " ")[:23],
         "value": f"{_money(item['expense_minor'])}  {item['share']:.1f}%",
         "fraction": item["expense_minor"] / expense if expense else 0}
        for item in groups[:7]
    ]
    if len(groups) > 7:
        amount = sum(item["expense_minor"] for item in groups[7:])
        comparison.append({"label": "其他", "value": _money(amount), "fraction": amount / expense if expense else 0})
    return {
        "title": "月度账本", "period": f"{start} 至 {end} · {'扣款日' if basis == 'charged_on' else '发生日'}",
        "metrics": [{"label": "收入", "value": _money(summary["income_minor"])},
                    {"label": "支出", "value": _money(expense)},
                    {"label": "净额", "value": _money(summary["net_minor"])}],
        "caption": f"{change}    ·    估算 {_money(summary['estimated_minor'])}    ·    待确认 {_money(summary['pending_minor'])}",
        "rows": comparison or [{"label": "此月无已确认支出", "value": "", "fraction": 0}],
        "facts": {"start_date": start, "end_date": end, "date_basis": basis,
                  "income_minor": summary["income_minor"], "expense_minor": expense,
                  "net_minor": summary["net_minor"], "estimated_minor": summary["estimated_minor"],
                  "pending_minor": summary["pending_minor"], "record_count": summary["record_count"]},
    }


def _saved_overview(conn, ctx, params):
    if params.get("snapshot_id"):
        snapshot = insights._get_monthly_report_snapshot(conn, ctx, {"snapshot_id": params["snapshot_id"]})
        result = snapshot["result"]
        version = "已保存的月报快照"
    else:
        start, end = _period(params)
        result = insights._run_monthly_report(conn, ctx, {"template_id": params["template_id"], "start_date": start, "end_date": end})
        version = "月报模板"
    metrics = result["metrics"]
    cash = next((item["value"] for item in metrics if item["type"] == "net_cash_flow"), {})
    groups = next((item["value"] for item in metrics if item["type"] == "expense_category"), {})
    if not cash and not groups:
        raise BusinessError("needs_clarification", "请选取包含分类支出或净现金流的模板或快照。", code="report_metrics_required")
    expense = cash.get("expense_minor", groups.get("expense_minor"))
    pending = cash.get("pending_expense_minor", groups.get("pending_expense_minor", 0)) + cash.get("pending_income_minor", 0)
    estimated = cash.get("estimated_minor", groups.get("estimated_minor", 0))
    items = sorted(groups.get("items", []), key=lambda item: (-item["expense_minor"], item["category_path"]))
    category_total = groups.get("expense_minor", 0)
    rows = [{"label": item["category_path"].replace("\n", " ")[:23],
             "value": f"{_money(item['expense_minor'])}  {100 * item['expense_minor'] / category_total:.1f}%",
             "fraction": item["expense_minor"] / category_total}
            for item in items[:8] if item["expense_minor"] > 0 and category_total]
    start = result["period"]["start_date"]
    end = result["period"]["end_date"]
    basis = result["date_basis"]
    captions = {"income_minor": "收入", "expense_minor": "支出", "cashflow_net_minor": "净现金流"}
    values = {**cash, "expense_minor": expense}
    return {
        "title": "月度账本", "period": f"{start} 至 {end} · {'扣款日' if basis == 'charged_on' else '发生日'}",
        "metrics": [{"label": label, "value": _money(values[key]) if values.get(key) is not None else "—"}
                    for key, label in captions.items()],
        "caption": f"{version}    ·    估算 {_money(estimated)}    ·    待确认 {_money(pending)}",
        "rows": rows,
        "facts": {"start_date": start, "end_date": end, "date_basis": basis, "expense_minor": expense,
                  "income_minor": cash.get("income_minor"), "net_minor": cash.get("cashflow_net_minor"),
                  "estimated_minor": estimated, "pending_minor": pending,
                  "record_count": cash.get("record_count"), "snapshot_id": params.get("snapshot_id")},
    }


def _receipt_card(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    target = files._text(params.get("target_request_id"), "target_request_id")
    prior = store.receipt(conn, ctx["principal_id"], ctx["ledger_id"], target)
    if not prior:
        raise BusinessError("not_found", "当前账本找不到该请求回执。", code="receipt_not_found")
    result = prior["result"]
    transactions = result.get("transactions", []) or ([result["transaction"]] if "transaction" in result else [])
    ids = result.get("transaction_ids") or [item["transaction_id"] for item in transactions]
    events = conn.execute(
        "SELECT amount_minor,event_type,occurred_on FROM movement_active_events WHERE ledger_id=? AND request_id=? "
        "AND event_type!='transaction_payment' ORDER BY occurred_on,event_id", (ctx["ledger_id"], target),
    ).fetchall()
    if not ids and not events:
        raise BusinessError("invalid_input", "该回执没有可制卡的已登记流水。", code="receipt_has_no_transactions")
    rows = [
        ledger._transaction_payload(conn, ctx["ledger_id"], transaction_id)
        for transaction_id in dict.fromkeys(ids)
    ]
    rows = [item for item in rows if not item["voided"]]
    totals = {"income": 0, "expense": 0}
    pending = 0
    estimated = 0
    for row in rows:
        amount = row["amount_minor"]
        if row["confirmation_status"] == "pending":
            pending += amount
        else:
            totals[row["kind"]] += amount
        if row["estimated"]:
            estimated += amount
    dates = [row["occurred_on"] for row in rows] + [event["occurred_on"] for event in events]
    period = min(dates) + " 至 " + max(dates) if dates else "回执中的流水已全部作废"
    return {
        "title": "记账回执", "period": period + " · 发生日",
        "metrics": [{"label": "当前有效流水", "value": f"{len(rows) + len(events)} 笔"},
                    {"label": "已确认收入", "value": _money(totals["income"])},
                    {"label": "已确认支出", "value": _money(totals["expense"])}],
        "caption": f"资金操作 {len(events)} 笔    ·    估算 {_money(estimated)}    ·    待确认 {_money(pending)}",
        "rows": [],
        "facts": {"record_count": len(rows) + len(events), "movement_count": len(events), "income_minor": totals["income"],
                  "expense_minor": totals["expense"], "pending_minor": pending,
                  "estimated_minor": estimated, "start_date": min(dates, default=None),
                  "end_date": max(dates, default=None), "date_basis": "occurred_on"},
    }


def _pdf_image(jpeg: bytes, width: int, height: int) -> bytes:
    page_width = 540
    page_height = round(page_width * height / width)
    draw = f"q {page_width} 0 0 {page_height} 0 0 cm /Report Do Q\n".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width} {page_height}] /Resources << /XObject << /Report 4 0 R >> >> /Contents 5 0 R >>".encode("ascii"),
        (f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} /ColorSpace /DeviceRGB "
         f"/BitsPerComponent 8 /Filter /DCTDecode /Length {len(jpeg)} >>\nstream\n").encode("ascii") + jpeg + b"\nendstream",
        f"<< /Length {len(draw)} >>\nstream\n".encode("ascii") + draw + b"endstream",
    ]
    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, content in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode("ascii") + content + b"\nendobj\n")
    start = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode("ascii"))
    pdf.extend("".join(f"{offset:010d} 00000 n \n" for offset in offsets[1:]).encode("ascii"))
    pdf.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode("ascii"))
    return bytes(pdf)


def _render(report: dict, format_name: str) -> bytes:
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    command = [str(executable), "-NoProfile", "-NonInteractive", "-File", str(Path(__file__).with_name("render_report.ps1"))]
    if format_name == "pdf":
        command.append("-Jpeg")
    try:
        rendered = subprocess.run(command, input=json.dumps(report, ensure_ascii=False).encode("utf-8"),
                                  capture_output=True, timeout=20, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        image = base64.b64decode(rendered.stdout.strip(), validate=True)
    except (OSError, ValueError, subprocess.SubprocessError):
        raise BusinessError("internal_error", "Windows 本机报表渲染失败。", code="local_render_failed") from None
    return _pdf_image(image, 960, 425 + 64 * len(report["rows"])) if format_name == "pdf" else image


def _export_report(conn: sqlite3.Connection, ctx: dict, params: dict) -> dict:
    style = params.get("report_type")
    format_name = params.get("format", "png")
    if style not in ("monthly", "receipt") or format_name not in ("png", "pdf"):
        raise BusinessError("invalid_input", "请选择月报或回执，以及 PNG 或 PDF。", code="invalid_report_export")
    if style == "receipt":
        report = _receipt_card(conn, ctx, params)
    elif params.get("snapshot_id") or params.get("template_id"):
        report = _saved_overview(conn, ctx, params)
    else:
        report = _overview(conn, ctx, params)
    content = _render(report, format_name)
    now = files._now()
    file_id = str(uuid.uuid4())
    filename = f"Yuxu-{style}-{file_id[:8]}.{format_name}"
    conn.execute("DELETE FROM yuxu_report_artifacts WHERE expires_at <= ?", (files._timestamp(now),))
    conn.execute("INSERT INTO yuxu_report_artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (file_id, ctx["principal_id"], ctx["ledger_id"], filename, format_name, content,
                  json.dumps(report["facts"], ensure_ascii=False), files._timestamp(now),
                  files._timestamp(now + timedelta(days=7))))
    return files._get_export_artifact(conn, ctx, {"file_id": file_id})


ACTIONS = {
    "export_report_card": {
        "description": "Render a privacy-safe PNG or PDF monthly card or bookkeeping receipt on this Windows PC. "
                       "Use month YYYY-MM for monthly, target_request_id for receipt, then get_export_artifact to deliver a PNG.",
        "inputSchema": files._object({
            "report_type": {"type": "string", "enum": ["monthly", "receipt"]},
            "month": {"type": "string", "pattern": "^\\d{4}-\\d{2}$"},
            "target_request_id": files._STRING,
            "template_id": files._STRING, "snapshot_id": files._STRING,
            "date_basis": {"type": "string", "enum": ["occurred_on", "charged_on"]},
            "format": {"type": "string", "enum": ["png", "pdf"]},
        }, ("report_type",)),
        "scope": "own_write", "host_permission": "export_summary", "run": _export_report,
    },
}
