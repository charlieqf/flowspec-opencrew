from __future__ import annotations

import json
import re
from typing import Any


PUBLIC_EVENT_KINDS = {
    "user.message",
    "assistant.final",
    "session.created",
    "session.completed",
    "session.failed",
    "session.error",
    "session.cancelled",
    "session.rerun",
}

SECRET_KEY_RE = re.compile(r"(api[_-]?key|authorization|bearer|cookie|session[_-]?token|share[_-]?token|password|secret|proxy)", re.I)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PHONE_RE = re.compile(r"(?<!\w)(?:\+\d{8,15}|(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)|\d{2,4})[\s.-]\d{3,4}[\s.-]\d{3,4})(?!\w)")
BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.I)
AUTH_HEADER_RE = re.compile(r"(Authorization\s*[:=]\s*)([^\s,;]+)", re.I)
COOKIE_RE = re.compile(r"(Cookie\s*[:=]\s*)([^;\n]+(?:;[^\n]+)*)", re.I)


def event_family(kind: str) -> str:
    return (kind.split(".", 1)[0] or "session").strip() or "session"


def infer_event_defaults(kind: str, payload: dict[str, Any] | None = None) -> dict[str, str]:
    if kind in PUBLIC_EVENT_KINDS:
        return {
            "visibility": "public",
            "event_scope": "customer",
            "severity": "error" if kind in {"session.failed", "session.error"} else "info",
            "family": event_family(kind),
        }
    if kind.endswith(".failed") or kind.endswith(".error") or kind == "session.stream.error":
        severity = "error"
    elif kind.endswith(".warning") or kind.endswith(".blocked"):
        severity = "warning"
    else:
        severity = "info"
    return {"visibility": "internal", "event_scope": "debug", "severity": severity, "family": event_family(kind)}


def redact_text(value: str) -> str:
    text = BEARER_RE.sub("Bearer [REDACTED]", value)
    text = AUTH_HEADER_RE.sub(r"\1[REDACTED]", text)
    text = COOKIE_RE.sub(r"\1[REDACTED]", text)
    text = EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = PHONE_RE.sub("[REDACTED_PHONE]", text)
    if len(text) > 8000:
        text = f"{text[:8000]}...[TRUNCATED]"
    return text


def redact_payload(value: Any, *, key_hint: str = "") -> Any:
    if SECRET_KEY_RE.search(key_hint):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(key): redact_payload(item, key_hint=str(key)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_payload(item, key_hint=key_hint) for item in value]
    if isinstance(value, tuple):
        return [redact_payload(item, key_hint=key_hint) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def normalize_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return redact_payload(payload)
    return {"value": redact_payload(payload)}


def parse_payload(payload: str | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(payload, dict):
        return normalize_payload(payload)
    if not payload:
        return {}
    try:
        parsed = json.loads(payload)
    except Exception:
        return {"value": redact_payload(str(payload))}
    return normalize_payload(parsed)


def serialize_payload(payload: Any) -> str:
    return json.dumps(normalize_payload(payload), ensure_ascii=True)


def event_visible(row: dict[str, Any], audience: str) -> bool:
    if audience == "debug":
        return True
    kind = str(row.get("kind") or "")
    defaults = infer_event_defaults(kind)
    visibility = str(row.get("visibility") or defaults["visibility"] or "internal")
    event_scope = str(row.get("event_scope") or defaults["event_scope"] or "debug")
    if audience == "share":
        return visibility == "public" and event_scope in {"customer", "public"}
    if audience == "customer":
        return visibility == "public" and event_scope in {"customer", "public"}
    return False


def present_event(row: dict[str, Any]) -> dict[str, Any]:
    kind = str(row.get("kind") or "")
    defaults = infer_event_defaults(kind)
    payload = parse_payload(row.get("payload"))
    return {
        **row,
        "payload": payload,
        "visibility": row.get("visibility") or defaults["visibility"],
        "event_scope": row.get("event_scope") or defaults["event_scope"],
        "severity": row.get("severity") or defaults["severity"],
        "family": row.get("family") or defaults["family"],
    }


class SessionEventService:
    def __init__(self, session_repo: Any, now_ms: Any) -> None:
        self.session_repo = session_repo
        self.now_ms = now_ms

    def add_event(
        self,
        session_id: int,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        visibility: str | None = None,
        event_scope: str | None = None,
        severity: str | None = None,
        family: str | None = None,
        workflow_id: str | None = None,
        task_id: int | None = None,
        attempt_id: int | None = None,
        tool_id: str | None = None,
        step_id: str | None = None,
    ) -> int:
        created = self.now_ms()
        event_id = self.session_repo.add_event(
            session_id,
            kind,
            payload or {},
            created,
            visibility=visibility,
            event_scope=event_scope,
            severity=severity,
            family=family,
            workflow_id=workflow_id,
            task_id=task_id,
            attempt_id=attempt_id,
            tool_id=tool_id,
            step_id=step_id,
        )
        self.session_repo.update(session_id, updated_at=created)
        return event_id
