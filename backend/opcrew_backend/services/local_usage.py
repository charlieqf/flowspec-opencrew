from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class LocalUsageRecordResult:
    request_id: str
    inserted: bool
    local_usage_id: str = ""


class LocalUsageRecorder:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def record_with_result(
        self,
        *,
        provider: str,
        model_id: str,
        modality: str,
        proxy_policy: str = "",
        status: str = "ok",
        request_id: str | None = None,
        task_id: str | int | None = None,
        attempt_id: str | int | None = None,
        step_id: str | None = None,
        idempotency_key: str | None = None,
        units: dict[str, Any] | None = None,
        est_cost_micros: int | None = None,
        actual_cost_micros: int | None = None,
        actual_cost_currency: str = "",
        actual_cost_source: str = "",
        actual_cost_raw: dict[str, Any] | None = None,
        pricebook_version: str = "",
        billing_reconciled_at: int | None = None,
        error_code: str = "",
        started_at: int | None = None,
        finished_at: int | None = None,
        provider_mode: str = "local_box",
        billing_mode: str = "local_usage_only",
    ) -> LocalUsageRecordResult:
        req_id = request_id or str(uuid.uuid4())
        created_at = now_ms()
        is_sqlite = self.engine.dialect.name == "sqlite"
        id_column = "id, " if is_sqlite else ""
        id_value = ":id, " if is_sqlite else ""
        units_expr = ":units_json" if is_sqlite else "CAST(:units_json AS JSONB)"
        actual_cost_expr = ":actual_cost_raw_json" if is_sqlite else "CAST(:actual_cost_raw_json AS JSONB)"
        with self.engine.begin() as conn:
            existing_id = ""
            if idempotency_key:
                row = conn.execute(text("SELECT id FROM local_usage_log WHERE idempotency_key = :idempotency_key"), {"idempotency_key": idempotency_key}).first()
                if row is not None:
                    return LocalUsageRecordResult(request_id=req_id, inserted=False, local_usage_id=str(row[0]))
            sqlite_id = None
            if is_sqlite:
                sqlite_id = int(conn.execute(text("SELECT COALESCE(MAX(id), 0) + 1 FROM local_usage_log")).scalar_one())
            result = conn.execute(
                text(
                    f"""
INSERT INTO local_usage_log (
  {id_column}request_id, task_id, attempt_id, step_id, idempotency_key,
  provider, model_id, modality, provider_mode, billing_mode,
  proxy_policy, status, units_json, est_cost_micros,
  actual_cost_micros, actual_cost_currency, actual_cost_source, actual_cost_raw_json,
  pricebook_version, billing_reconciled_at, error_code,
  started_at, finished_at, created_at
) VALUES (
  {id_value}:request_id, :task_id, :attempt_id, :step_id, :idempotency_key,
  :provider, :model_id, :modality, :provider_mode, :billing_mode,
  :proxy_policy, :status, {units_expr}, :est_cost_micros,
  :actual_cost_micros, :actual_cost_currency, :actual_cost_source, {actual_cost_expr},
  :pricebook_version, :billing_reconciled_at, :error_code,
  :started_at, :finished_at, :created_at
)
ON CONFLICT (idempotency_key) DO NOTHING
"""
                ),
                {
                    "id": sqlite_id,
                    "request_id": req_id,
                    "task_id": str(task_id) if task_id not in (None, "") else None,
                    "attempt_id": str(attempt_id) if attempt_id not in (None, "") else None,
                    "step_id": step_id or None,
                    "idempotency_key": idempotency_key or None,
                    "provider": provider,
                    "model_id": model_id,
                    "modality": modality,
                    "provider_mode": provider_mode,
                    "billing_mode": billing_mode,
                    "proxy_policy": proxy_policy,
                    "status": status,
                    "units_json": json.dumps(units or {}, ensure_ascii=True),
                    "est_cost_micros": est_cost_micros,
                    "actual_cost_micros": actual_cost_micros,
                    "actual_cost_currency": actual_cost_currency,
                    "actual_cost_source": actual_cost_source,
                    "actual_cost_raw_json": json.dumps(actual_cost_raw or {}, ensure_ascii=True),
                    "pricebook_version": pricebook_version,
                    "billing_reconciled_at": billing_reconciled_at,
                    "error_code": error_code,
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "created_at": created_at,
                },
            )
            inserted = int(result.rowcount or 0) > 0
            if idempotency_key:
                row = conn.execute(text("SELECT id FROM local_usage_log WHERE idempotency_key = :idempotency_key"), {"idempotency_key": idempotency_key}).first()
                existing_id = str(row[0]) if row is not None else ""
            elif sqlite_id is not None and inserted:
                existing_id = str(sqlite_id)
        return LocalUsageRecordResult(request_id=req_id, inserted=inserted, local_usage_id=existing_id)

    def record(self, **kwargs: Any) -> str:
        return self.record_with_result(**kwargs).request_id

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """
SELECT id, request_id, provider, model_id, modality, provider_mode, billing_mode,
       task_id, attempt_id, step_id, idempotency_key,
       proxy_policy, status, units_json, est_cost_micros,
       actual_cost_micros, actual_cost_currency, actual_cost_source, actual_cost_raw_json,
       pricebook_version, billing_reconciled_at, error_code,
       started_at, finished_at, created_at
FROM local_usage_log
ORDER BY id DESC
LIMIT :limit
"""
                ),
                {"limit": max(1, min(int(limit), 1000))},
            ).mappings().all()
        return [dict(row) for row in rows]
