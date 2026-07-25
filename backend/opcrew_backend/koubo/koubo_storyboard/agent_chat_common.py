from __future__ import annotations

from typing import Any


KOUBO_AGENT_CHAT_DISABLED_TOOLS = {
    "bash": False,
    "read": False,
    "glob": False,
    "grep": False,
    "edit": False,
    "write": False,
    "task": False,
    "webfetch": False,
    "websearch": False,
    "codesearch": False,
    "repo_clone": False,
    "repo_overview": False,
    "skill": False,
    "apply_patch": False,
    "question": False,
    "todowrite": False,
    "lsp": False,
    "plan_exit": False,
}

KOUBO_AGENT_CHAT_MESSAGE_LIMIT = 500


def _text(value: Any, default: str = "") -> str:
    if value is None or value == "":
        value = default
    return str(value or "").strip()


def safe_opencode_info(value: Any) -> dict[str, Any]:
    info = value if isinstance(value, dict) else {}
    payload: dict[str, Any] = {}
    for key in ("id", "sessionID", "role", "time"):
        if key in info:
            payload[key] = info[key]
    return payload


def safe_opencode_part(value: Any) -> dict[str, Any]:
    part = value if isinstance(value, dict) else {}
    payload: dict[str, Any] = {}
    for key in ("id", "messageID", "sessionID", "type", "time"):
        if key in part:
            payload[key] = part[key]
    if _text(part.get("type")) == "text":
        payload["text"] = str(part.get("text") if part.get("text") is not None else "")[:50000]
    return payload


def safe_opencode_message(value: Any) -> dict[str, Any]:
    message = value if isinstance(value, dict) else {}
    info = message.get("info") if isinstance(message.get("info"), dict) else message
    parts = message.get("parts") if isinstance(message.get("parts"), list) else []
    return {
        "info": safe_opencode_info(info),
        "parts": [safe_opencode_part(part) for part in parts if isinstance(part, dict)],
    }


def sanitize_opencode_event(payload: dict[str, Any]) -> dict[str, Any] | None:
    event_type = _text(payload.get("type"))
    properties = payload.get("properties") if isinstance(payload.get("properties"), dict) else {}
    if not event_type:
        return None
    if event_type == "message.updated":
        message = properties.get("message")
        if isinstance(message, dict):
            return {"type": event_type, "properties": {"message": safe_opencode_message(message)}}
        parts = properties.get("parts") if isinstance(properties.get("parts"), list) else []
        return {"type": event_type, "properties": {
            "info": safe_opencode_info(properties.get("info")),
            "parts": [safe_opencode_part(part) for part in parts if isinstance(part, dict)],
        }}
    if event_type == "message.part.updated":
        return {"type": event_type, "properties": {"part": safe_opencode_part(properties.get("part") or properties)}}
    if event_type == "message.part.delta":
        field = _text(properties.get("field"), "text")
        return {"type": event_type, "properties": {
            "messageID": _text(properties.get("messageID") or properties.get("messageId") or properties.get("message_id")),
            "partID": _text(properties.get("partID") or properties.get("partId") or properties.get("id")),
            "field": field if field == "text" else "",
            "delta": str(properties.get("delta") if properties.get("delta") is not None else "")[:8000],
        }}
    if event_type == "message.part.removed":
        return {"type": event_type, "properties": {
            "messageID": _text(properties.get("messageID") or properties.get("messageId") or properties.get("message_id")),
            "partID": _text(properties.get("partID") or properties.get("partId") or properties.get("id")),
        }}
    if event_type == "session.status":
        return {"type": event_type, "properties": {"status": properties.get("status") or {"type": _text(properties.get("type"), "idle")}}}
    if event_type == "session.stream.error":
        message = properties.get("message") if isinstance(properties, dict) else ""
        return {"type": event_type, "properties": {"message": _text(message)[:1000]}}
    return None


def opencode_event_has_tool_use(payload: dict[str, Any]) -> bool:
    event_type = _text(payload.get("type")).lower()
    if "tool" in event_type:
        return True
    properties = payload.get("properties") if isinstance(payload.get("properties"), dict) else {}
    part = properties.get("part") if isinstance(properties.get("part"), dict) else properties
    part_type = _text(part.get("type")).lower() if isinstance(part, dict) else ""
    return part_type in {"tool", "tool_call"}
