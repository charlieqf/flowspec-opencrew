from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..services.provider_resolver import resolve_endpoint
from ..services.session_events import redact_payload, redact_text
from .schemas import ModelCallAudit


AUTH_HEADER_RE = re.compile(r"Authorization\s*[:=]\s*[^\s,;\n]+", re.I)
COOKIE_HEADER_RE = re.compile(r"Cookie\s*[:=]\s*[^\n]+", re.I)
SK_TOKEN_RE = re.compile(r"\bsk-[A-Za-z0-9._~+/=-]+", re.I)
MIHOMO_URL_RE = re.compile(r"\bmihomo://[^\s\"']+", re.I)


def _sanitize_audit_text(value: str) -> str:
    text = redact_text(value)
    text = AUTH_HEADER_RE.sub("[REDACTED_AUTH_HEADER]", text)
    text = COOKIE_HEADER_RE.sub("[REDACTED_COOKIE]", text)
    text = SK_TOKEN_RE.sub("[REDACTED_SECRET]", text)
    text = MIHOMO_URL_RE.sub("[REDACTED_PROXY_URL]", text)
    return text


def _sanitize_audit_payload(value: Any) -> Any:
    redacted = redact_payload(value)
    if isinstance(redacted, dict):
        return {str(key): _sanitize_audit_payload(item) for key, item in redacted.items()}
    if isinstance(redacted, list):
        return [_sanitize_audit_payload(item) for item in redacted]
    if isinstance(redacted, str):
        return _sanitize_audit_text(redacted)
    return redacted


class UsageRecorder(Protocol):
    def record(self, **kwargs: Any) -> str:
        ...


class UsageRecordResult(Protocol):
    request_id: str
    inserted: bool
    local_usage_id: str


@dataclass(frozen=True)
class ModelBrokerRequest:
    provider: str
    model_id: str
    modality: str
    request_id: str
    idempotency_key: str
    payload: dict[str, Any] = field(default_factory=dict)
    provider_mode: str = "local_box"
    billing_mode: str = "local_usage_only"
    auth_ref: str = ""
    tool_use_session_id: str = ""
    step_index: int = 0
    step_id: str = ""
    tool_name: str = ""
    task_id: int | None = None
    opencrew_session_id: int | None = None
    opencode_session_id: str = ""
    attempt_id: int | None = None
    audit_path: Path | None = None


@dataclass(frozen=True)
class ModelBrokerResponse:
    request_id: str
    local_usage_id: str
    audit: ModelCallAudit
    response: dict[str, Any]


class ModelBroker:
    def __init__(self, usage_recorder: UsageRecorder | None = None) -> None:
        self.usage_recorder = usage_recorder
        self._completed_by_idempotency_key: dict[str, ModelBrokerResponse] = {}

    def submit_model_call(self, request: ModelBrokerRequest, *, fake_response: dict[str, Any] | None = None) -> ModelBrokerResponse:
        if not request.idempotency_key:
            raise ValueError("idempotency_key is required")
        cached = self._completed_by_idempotency_key.get(request.idempotency_key)
        if cached is not None:
            if request.audit_path is not None:
                self._write_audit(request.audit_path, cached.audit)
            return cached

        request_id = request.request_id or str(uuid.uuid4())
        endpoint = resolve_endpoint(request.provider, request.model_id, request.modality, request.auth_ref)
        response = fake_response or {"status": "ok", "provider": request.provider, "model_id": request.model_id}
        local_usage_id = ""
        if self.usage_recorder is not None:
            record_kwargs = {
                "provider": request.provider,
                "model_id": request.model_id,
                "modality": request.modality,
                "proxy_policy": endpoint.proxy_policy,
                "status": "ok",
                "request_id": request_id,
                "task_id": request.task_id,
                "attempt_id": request.attempt_id,
                "step_id": request.step_id,
                "idempotency_key": request.idempotency_key,
                "units": response.get("usage") if isinstance(response.get("usage"), dict) else {},
                "provider_mode": request.provider_mode,
                "billing_mode": request.billing_mode,
            }
            record_with_result = getattr(self.usage_recorder, "record_with_result", None)
            if callable(record_with_result):
                usage_result: UsageRecordResult = record_with_result(**record_kwargs)
                local_usage_id = usage_result.local_usage_id or usage_result.request_id
            else:
                local_usage_id = self.usage_recorder.record(**record_kwargs)
        usage_summary = _sanitize_audit_payload(response.get("usage")) if isinstance(response.get("usage"), dict) else {}
        audit = ModelCallAudit(
            tool_use_session_id=request.tool_use_session_id,
            step_index=request.step_index,
            step_id=request.step_id,
            tool_name=request.tool_name,
            task_id=request.task_id,
            opencrew_session_id=request.opencrew_session_id,
            opencode_session_id=request.opencode_session_id,
            attempt_id=request.attempt_id,
            model_provider=request.provider,
            model_id=request.model_id,
            output_summary=_sanitize_audit_text(str(response.get("summary") or response.get("status") or "")),
            error_summary=_sanitize_audit_text(str(response.get("error") or "")),
            request_id=request_id,
            local_usage_id=local_usage_id,
            provider_mode=request.provider_mode,
            billing_mode=request.billing_mode,
            usage_summary=usage_summary if isinstance(usage_summary, dict) else {},
            idempotency_key=request.idempotency_key,
        )
        result = ModelBrokerResponse(request_id=request_id, local_usage_id=local_usage_id, audit=audit, response=response)
        self._completed_by_idempotency_key[request.idempotency_key] = result
        if request.audit_path is not None:
            self._write_audit(request.audit_path, audit)
        return result

    def _write_audit(self, path: Path, audit: ModelCallAudit) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(audit.model_dump(), ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
