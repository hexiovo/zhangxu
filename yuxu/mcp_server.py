from __future__ import annotations

import asyncio
import json
import os
import uuid
from base64 import b64encode
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, Icon, ImageContent, ListToolsResult, TextContent, Tool, ToolAnnotations

from . import __version__
from .server import ensure_service, service_port

MCP_INSTRUCTIONS = (
    "At initialization, call get_current_account, get_workflow_guide, and list_accessible_accounts. "
    "Call get_account_access_policy to inspect the current account's incoming and outgoing read grants. get_current_account shows this host's current scopes; list_host_tokens requires detail_read and becomes available after the owner enables it. "
    "Use only account IDs and scopes those tools return. Omitting account_id always selects the current account; "
    "cross-account access requires an explicit listed target with the scope required by that action: admin_spending "
    "for spending reports or admin_goal for read-only goal progress. "
    "This host's grants constrain every call. export_summary permits generating local files and export_artifact permits retrieving shareable PNGs. "
    "the account owner can inspect or change them in the local web session. Changes apply to subsequent requests. Give writes a stable request_id; after an uncertain response, query get_request_status with target_request_id "
    "inspect data.outcome and retry only with the same request_id and arguments. Merchant names and notes are filtered by default; "
    "a matching single-use owner authorization is required to read selected fields from one transaction. "
    "Try get_workflow_guide with record, classify, receipt, correct, reconcile or export for step-by-step tool contracts. "
    "A committed preview or draft save only confirms that intermediate step. Report entries posted only when the commit receipt lists new transaction IDs or movement results. "
    "For CSV reconciliation or sensitive exports open the returned review_url in the owner's local Web session. "
    "Maintenance is a separate local grant. MCP may create backups and preview restores, but final restore is only "
    "available in the same-origin web session after explicit confirmation and a pre-restore backup. "
    "Shared stdio has no trusted conversation ID by default. Set YUXU_ISOLATED_CONVERSATION=1 only when the host "
    "starts this MCP process separately for one conversation; otherwise ask for an ID or use explicit filters."
)


def run() -> None:
    token = os.environ.get("YUXU_TOKEN", "").strip()
    if not token:
        raise RuntimeError("MCP 使用前请在当前 MCP 客户端进程环境中设置对应用户的 YUXU_TOKEN。")
    ensure_service()
    isolated_conversation = os.environ.get("YUXU_ISOLATED_CONVERSATION") == "1"
    conversation_id = str(uuid.uuid4()) if isolated_conversation else None

    def api_request(path: str, body: dict | None = None) -> dict:
        headers = {
            "X-Yuxu-Token": token,
            "Accept": "application/json",
        }
        if conversation_id is not None:
            headers["X-Yuxu-Conversation-ID"] = conversation_id
        data = None
        method = "GET"
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            method = "POST"
        request = Request(f"http://127.0.0.1:{service_port()}{path}", data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            try:
                payload = json.loads(error.read().decode("utf-8"))
            except (UnicodeError, ValueError):
                payload = None
        except (URLError, TimeoutError, OSError, UnicodeError, ValueError):
            return {
                "status": "unknown",
                "account_id": None,
                "account_name": None,
                "data": None,
                "request_id": body.get("request_id") if body else None,
                "error": {
                    "code": "service_unavailable",
                    "message": "本机服务暂时没有返回结果；写操作请使用原 request_id 查询或重试。",
                    "details": None,
                },
            }
        if not isinstance(payload, dict):
            return {
                "status": "unknown", "account_id": None, "account_name": None,
                "data": None, "request_id": body.get("request_id") if body else None,
                "error": {"code": "invalid_service_response", "message": "本机服务返回了无效响应。", "details": None},
            }
        return payload

    catalog_watch = None

    async def watch_catalog(session, previous) -> None:
        while True:
            await asyncio.sleep(5)
            payload = await asyncio.to_thread(api_request, "/api/mcp/catalog-state")
            current = payload.get("data") if payload.get("status") == "ok" else payload.get("status")
            if current != previous and current not in ("unknown", "internal_error"):
                await session.send_tool_list_changed()
                previous = current

    async def list_tools(context, _params) -> ListToolsResult:
        nonlocal catalog_watch
        payload = await asyncio.to_thread(api_request, "/api/tools")
        if payload.get("status") != "ok":
            detail = payload.get("error") or {}
            raise RuntimeError(f"{detail.get('code', 'catalog_unavailable')}: {detail.get('message', '无法读取本机工具清单。')}")
        data = payload["data"]
        if catalog_watch is None:
            catalog_watch = asyncio.create_task(watch_catalog(context.session, data["catalog_state"]))
        tools = []
        for entry in data["tools"]:
            read_only = entry["scope"] != "own_write" and entry["name"] not in {
                "get_local_review_link", "get_cross_tab_report", "get_purchase_frequency",
            }
            tools.append(Tool(
                name=entry["name"], description=entry["description"], inputSchema=entry["inputSchema"],
                outputSchema=entry.get("outputSchema"),
                annotations=ToolAnnotations(
                    readOnlyHint=read_only, openWorldHint=False,
                    destructiveHint=not read_only and entry["name"] != "create_category_or_tag"
                    and not entry["name"].startswith(("preview_", "add_", "get_")),
                    idempotentHint=read_only or (entry["scope"] == "own_write" and not entry["no_receipt"]),
                ),
            ))
        return ListToolsResult(tools=tools)

    def image_content(file_id: str) -> ImageContent:
        request = Request(f"http://127.0.0.1:{service_port()}/api/mcp/images/{file_id}",
                          headers={"X-Yuxu-Token": token})
        with urlopen(request, timeout=15) as response:
            return ImageContent(type="image", mimeType="image/png",
                                data=b64encode(response.read()).decode("ascii"))

    async def call_tool(_request_context, params) -> CallToolResult:
        arguments = dict(params.arguments or {})
        name = params.name
        if not name or not all(char in "abcdefghijklmnopqrstuvwxyz_0123456789" for char in name):
            result = {"status": "invalid_input", "account_id": None, "account_name": None,
                      "data": None, "request_id": None,
                      "error": {"code": "invalid_tool_name", "message": "工具名无效。", "details": None}}
        else:
            result = await asyncio.to_thread(api_request, f"/api/tools/{name}", arguments)
        contents = []
        if name == "get_export_artifact" and result.get("status") == "ok":
            data = result["data"]
            if data.get("inline_available") and arguments.get("inline") is True:
                try:
                    contents.append(await asyncio.to_thread(image_content, data["file_id"]))
                except (URLError, TimeoutError, OSError):
                    data["inline_available"] = False
                    data["delivery"] = "web"
                else:
                    data["delivery"] = "inline"
        contents.insert(0, TextContent(text=json.dumps(result, ensure_ascii=False, separators=(",", ":"))))
        return CallToolResult(
            content=contents,
            structuredContent=result,
            isError=result.get("status") not in ("ok", "committed"),
        )

    icon_path = Path(__file__).resolve().parent.parent / "web" / "yuxu-icon.png"
    icon_data = b64encode(icon_path.read_bytes()).decode("ascii")
    server = Server(
        "yuxu",
        version=__version__,
        instructions=MCP_INSTRUCTIONS,
        icons=[Icon(src=f"data:image/png;base64,{icon_data}", mime_type="image/png", sizes=["64x64"])],
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )

    async def serve_stdio() -> None:
        async with stdio_server() as (read_stream, write_stream):
            try:
                options = server.create_initialization_options(NotificationOptions(tools_changed=True))
                await server.run(read_stream, write_stream, options)
            finally:
                if catalog_watch is not None:
                    catalog_watch.cancel()
                    await asyncio.gather(catalog_watch, return_exceptions=True)

    asyncio.run(serve_stdio())
