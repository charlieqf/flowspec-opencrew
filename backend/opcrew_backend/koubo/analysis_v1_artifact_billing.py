from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.engine import Engine

from opcrew_backend.services.local_metering import capped_artifact_kb


class LocalUsageRecorderLike(Protocol):
    def record_with_result(self, **kwargs: Any) -> Any:
        ...


def safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def artifact_unit_for_path(path: Path) -> tuple[str, str] | None:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "json", "artifact_json_kb"
    if suffix == ".wav":
        return "wav", "artifact_wav_kb"
    if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        return "image", "artifact_image_kb"
    return None


def step_result_provider(workspace: Path, step_id: str, parsed: dict[str, Any]) -> str:
    provider_config = parsed.get("provider_config") if isinstance(parsed.get("provider_config"), dict) else {}
    provider = str(provider_config.get("provider") or "").strip()
    if provider:
        return provider
    if step_id == "02_01":
        for rel in ("SessionContext/ASR_Segments.json", "S3_02_01_AudioASR/Output/ASR_Segments.json"):
            provider = str(read_json_file(workspace / rel).get("provider") or "").strip()
            if provider:
                return provider
    return ""


def has_non_artifact_usage(engine: Engine, attempt_id: int, step_id: str) -> bool:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
SELECT id
FROM local_usage_log
WHERE attempt_id = :attempt_id
  AND step_id = :step_id
  AND modality <> 'local_artifact'
  AND status = 'ok'
LIMIT 1
"""
            ),
            {"attempt_id": str(attempt_id), "step_id": step_id},
        ).first()
    return row is not None


def collect_billable_artifacts(workspace: Path, patterns: list[str]) -> tuple[dict[str, float], list[dict[str, Any]]]:
    units: dict[str, float] = {}
    artifacts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pattern in patterns:
        for path in sorted(workspace.glob(str(pattern))):
            if not path.is_file():
                continue
            rel = path.relative_to(workspace).as_posix() if path.is_relative_to(workspace) else str(path)
            if rel in seen:
                continue
            seen.add(rel)
            unit_info = artifact_unit_for_path(path)
            if unit_info is None:
                continue
            artifact_type, unit_key = unit_info
            byte_size = path.stat().st_size
            raw_kb, billed_kb = capped_artifact_kb(unit_key, byte_size)
            if billed_kb <= 0:
                continue
            units[unit_key] = units.get(unit_key, 0.0) + billed_kb
            artifacts.append(
                {
                    "path": rel,
                    "type": artifact_type,
                    "unit_key": unit_key,
                    "bytes": byte_size,
                    "raw_kb": round(raw_kb, 6),
                    "billed_kb": round(billed_kb, 6),
                }
            )
    return units, artifacts


def record_local_artifacts(
    *,
    engine: Engine,
    recorder: LocalUsageRecorderLike,
    spec: dict[str, Any],
    task_id: int,
    attempt_id: int,
    workspace: Path,
    parsed: dict[str, Any],
    started_at: int,
    finished_at: int,
) -> dict[str, Any] | None:
    step_id = str(spec.get("id") or "")
    if not step_id or not spec.get("artifact_billable"):
        return None
    provider_allowlist = [str(item) for item in (spec.get("artifact_provider_allowlist") or [])]
    if provider_allowlist:
        provider = step_result_provider(workspace, step_id, parsed)
        if provider not in provider_allowlist:
            return {"status": "skipped", "reason": "provider_not_billable", "provider": provider}
    if has_non_artifact_usage(engine, attempt_id, step_id):
        return {"status": "skipped", "reason": "api_usage_already_metered"}
    units, artifacts = collect_billable_artifacts(workspace, [str(item) for item in (spec.get("billable_outputs") or [])])
    if not units or not artifacts:
        return {"status": "skipped", "reason": "no_billable_artifacts"}
    idempotency_key = f"artifact:{attempt_id}:{step_id}"
    record_result = recorder.record_with_result(
        provider="analysis_v1",
        model_id=str(spec.get("name") or step_id),
        modality="local_artifact",
        status="ok",
        request_id=idempotency_key,
        task_id=task_id,
        attempt_id=attempt_id,
        step_id=step_id,
        idempotency_key=idempotency_key,
        units=units,
        actual_cost_raw={"artifacts": artifacts},
        started_at=started_at,
        finished_at=finished_at,
    )
    status = "recorded" if bool(getattr(record_result, "inserted", False)) else "deduped"
    return {
        "status": status,
        "idempotency_key": idempotency_key,
        "local_usage_id": str(getattr(record_result, "local_usage_id", "") or ""),
        "units": {key: round(value, 6) for key, value in sorted(units.items())},
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
    }
