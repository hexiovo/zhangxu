from __future__ import annotations


PRIVATE_TEXT_KEYS = {
    "note", "private_note", "notes", "private_notes", "memo", "private_memo",
    "comment", "private_comment", "void_reason", "note_text", "private_note_text",
    "note_keyword", "private_note_keyword", "keyword", "raw_text", "ocr_text",
    "merchant", "merchant_canonical", "original_names", "match_key", "match_value",
    "content_base64", "attachment_base64", "file_content",
    "_mcp_private_fields_authorized",
}


def sanitize_mcp_output(value, authorized=frozenset(), path=()):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            normalized = key.casefold().replace("-", "_")
            single_transaction = path == ("transaction",)
            allowed = single_transaction and (normalized in authorized or
                (normalized == "merchant_canonical" and "merchant" in authorized))
            if normalized not in PRIVATE_TEXT_KEYS or allowed:
                result[key] = sanitize_mcp_output(item, authorized, path + (normalized,))
        return result
    if isinstance(value, list):
        return [sanitize_mcp_output(item, authorized, path + (index,)) for index, item in enumerate(value)]
    return value


class BusinessError(Exception):
    def __init__(self, status: str, message: str, details: object = None, code: str | None = None):
        self.status = status
        self.message = message
        self.details = details
        self.code = code or status
        super().__init__(message)


HTTP_STATUS = {
    "needs_clarification": 422,
    "invalid_input": 400,
    "not_found": 404,
    "forbidden": 403,
    "unauthorized": 401,
    "conflict": 409,
    "unknown": 400,
    "internal_error": 500,
}


def envelope(
    status: str,
    account_id: str | None = None,
    account_name: str | None = None,
    data: object = None,
    request_id: str | None = None,
    error: dict | None = None,
) -> dict:
    return {
        "status": status,
        "account_id": account_id,
        "account_name": account_name,
        "data": data,
        "request_id": request_id,
        "error": error,
    }


def error_body(error: BusinessError) -> dict:
    return {
        "code": error.code,
        "message": error.message,
        "details": error.details,
    }


def tool_output_schema(name: str) -> dict:
    required_by_tool = {
        "get_request_status": ["outcome", "target_request_id"],
        "add_transactions": ["transactions", "transaction_ids", "warnings"],
        "create_category_or_tag": ["type", "created"],
        "preview_bookkeeping": ["items", "business_date", "timezone", "currency"],
        "commit_bookkeeping_draft": ["draft_id", "transactions", "transaction_ids", "linked", "movement_results"],
        "list_bookkeeping_drafts": ["drafts"],
        "get_bookkeeping_draft": ["draft_id", "version", "status"],
        "get_export_artifact": ["file_id", "content_type", "format", "delivery", "inline_available", "review_url"],
    }
    data = {"type": ["object", "null"]}
    required = required_by_tool.get(name)
    if required:
        data["required"] = required
    return {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["ok", "committed", "needs_clarification", "invalid_input",
                "not_found", "forbidden", "unauthorized", "conflict", "unknown", "internal_error"]},
            "account_id": {"type": ["string", "null"]}, "account_name": {"type": ["string", "null"]},
            "data": data, "request_id": {"type": ["string", "null"]},
            "error": {"type": ["object", "null"]},
        },
        "required": ["status", "account_id", "account_name", "data", "request_id", "error"],
    }
